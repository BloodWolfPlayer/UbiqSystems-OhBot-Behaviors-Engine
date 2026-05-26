from __future__ import annotations

import asyncio

from actions import ActionRegistry, default_action_registry
from controller import ObotController
from llm_client import ScriptedLLMClient
from processor import StreamProcessor


class RobotPipeline:
    """Coordinates the LLM source, text processor, and robot controller."""

    def __init__(
        self,
        llm_client: ScriptedLLMClient,
        controller: ObotController,
        processor: StreamProcessor | None = None,
        action_registry: ActionRegistry | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.controller = controller
        self.processor = processor or StreamProcessor()
        self.action_registry = action_registry or default_action_registry()
        self._speech_queue: asyncio.Queue[object | None] = asyncio.Queue()

    async def run(self, prompt: str) -> None:
        speech_worker = asyncio.create_task(self._speech_loop())

        try:
            async for chunk in self.llm_client.stream_response(prompt):
                for event in self.processor.feed(chunk):
                    await self._dispatch(event)

            for event in self.processor.flush():
                await self._dispatch(event)
        finally:
            await self._speech_queue.put(None)
            await asyncio.gather(speech_worker)

    async def _dispatch(self, event) -> None:
        # we only enqueue sentence events; actions are attached to sentence metadata
        if event.kind == "sentence":
            await self._speech_queue.put(event)
            return

    async def _speech_loop(self) -> None:
        while True:
            event = await self._speech_queue.get()
            if event is None:
                return

            sentence = event.payload
            actions = []
            action_specs: list[tuple[str, int]] = []
            if event.metadata and "actions" in event.metadata:
                for spec in event.metadata["actions"].split(","):
                    if not spec:
                        continue
                    if "@" in spec:
                        name, pos = spec.split("@", 1)
                        try:
                            pos_i = int(pos)
                        except ValueError:
                            pos_i = 0
                    else:
                        name = spec
                        pos_i = 0
                    action_specs.append((name, pos_i))

            # estimate speak duration using the same heuristic as DemoObotController
            est_duration = min(0.2 + len(sentence) / 80, 1.5)

            # start speaking
            speak_task = asyncio.create_task(self.controller.speak_sentence(sentence))

            # schedule actions to run at approx the fractional position within the sentence
            action_tasks = []
            for name, pos in action_specs:
                frac = 0.0
                if len(sentence) > 0:
                    frac = max(0.0, min(1.0, pos / len(sentence)))
                delay = frac * est_duration
                # ensure actions are scheduled slightly before speech end
                epsilon = min(0.05, est_duration * 0.1)
                max_delay = max(0.0, est_duration - epsilon)
                if delay > max_delay:
                    delay = max_delay

                async def run_action_after(delay: float, action_name: str) -> None:
                    await asyncio.sleep(delay)
                    await self.action_registry.execute(action_name, self.controller)

                action_tasks.append(asyncio.create_task(run_action_after(delay, name)))

            # wait for speech to finish and then ensure actions completed
            await speak_task
            if action_tasks:
                await asyncio.gather(*action_tasks)
