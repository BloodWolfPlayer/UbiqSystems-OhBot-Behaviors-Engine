from __future__ import annotations

import asyncio

from actions import ActionRegistry, default_action_registry
from controller import ObotController
from llm_client import LLMClient
from processor import StreamProcessor


def _parse_specs(raw: str) -> list[tuple[str, int]]:
    #* Metadata format from the processor: "name@pos,name@pos,..."
    #* The integer pos is the character index inside the sentence where the marker appeared,
    #* used later to schedule the action or emotion at the matching point during speech.
    specs: list[tuple[str, int]] = []
    for spec in raw.split(","):
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
        specs.append((name, pos_i))
    return specs


class RobotPipeline:
    """Coordinates the LLM source, text processor, and robot controller."""

    def __init__(
        self,
        llm_client: LLMClient,
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
        if event.kind == "sentence":
            await self._speech_queue.put(event)

    async def _speech_loop(self) -> None:
        while True:
            event = await self._speech_queue.get()
            if event is None:
                return

            sentence = event.payload
            action_specs = _parse_specs(event.metadata.get("actions", "")) if event.metadata else []
            emotion_specs = _parse_specs(event.metadata.get("emotions", "")) if event.metadata else []
            delay_specs = _parse_specs(event.metadata.get("delays", "")) if event.metadata else []

            est_duration = min(0.2 + len(sentence) / 80, 1.5)

            def _delay_for(pos: int) -> float:
                #* Map a character index inside the sentence onto a real time delay,
                #* so an action tagged in the middle of a sentence fires halfway through speech.
                #* The epsilon nudge keeps actions from landing right on the speech end boundary.
                frac = 0.0
                if len(sentence) > 0:
                    frac = max(0.0, min(1.0, pos / len(sentence)))
                d = frac * est_duration
                epsilon = min(0.05, est_duration * 0.1)
                return min(d, max(0.0, est_duration - epsilon))

            #* Emotions placed at the very start of a sentence are applied before speech starts,
            #* so the face is already in the right shape when the first word comes out.
            pre_emotions = [name for name, pos in emotion_specs if pos == 0]
            remaining_emotions = [(name, pos) for name, pos in emotion_specs if pos > 0]
            for name in pre_emotions:
                await self.controller.set_emotion(name)

            speak_task = asyncio.create_task(self.controller.speak_sentence(sentence))

            scheduled: list[asyncio.Task] = []

            for name, pos in action_specs:
                d = _delay_for(pos)

                async def run_action(d: float, name: str) -> None:
                    await asyncio.sleep(d)
                    await self.action_registry.execute(name, self.controller)

                scheduled.append(asyncio.create_task(run_action(d, name)))

            for name, pos in remaining_emotions:
                d = _delay_for(pos)

                async def run_emotion(d: float, name: str) -> None:
                    await asyncio.sleep(d)
                    await self.controller.set_emotion(name)

                scheduled.append(asyncio.create_task(run_emotion(d, name)))

            await speak_task
            if scheduled:
                await asyncio.gather(*scheduled)

            #! A spoken sentence cannot be paused mid utterance once it is handed to TTS,
            #! so any !DelayX inside a sentence is treated as a pause AFTER that sentence,
            #! before the next one begins. Multiple delays on one sentence are summed.
            total_pause_ms = sum(int(ms) for ms, _ in delay_specs if ms.isdigit())
            if total_pause_ms:
                await asyncio.sleep(total_pause_ms / 1000)
