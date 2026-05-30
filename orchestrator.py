from __future__ import annotations

import asyncio
import contextlib

from actions import ActionRegistry, default_action_registry
from controller import ObotController
from interrupt import InterruptController
from llm_client import LLMClient
from models import InterruptionResult
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
    """Coordinates the LLM source, text processor, and robot controller.

    A turn runs two cooperating tasks:

    * a **producer** that streams the LLM, feeds the :class:`StreamProcessor`, records
      every generated sentence, and queues sentence events for speech;
    * a **consumer** (the speech loop) that voices queued sentences one at a time.

    When an :class:`InterruptController` trips, a watcher tells the controller to stop
    the current utterance, the consumer drops every remaining sentence, but the producer
    keeps draining the (cheap, text-only) stream so the full intended message is known.
    The difference between what was generated and what was spoken is "the rest it would
    have said", which is reported back to the LLM via ``register_interruption``.
    """

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

    async def run(
        self,
        prompt: str,
        interrupt: InterruptController | None = None,
    ) -> InterruptionResult:
        speech_queue: asyncio.Queue[object | None] = asyncio.Queue()
        generated: list[str] = []
        spoken: list[str] = []

        producer = asyncio.create_task(self._produce(prompt, generated, speech_queue))
        consumer = asyncio.create_task(self._speech_loop(speech_queue, spoken, interrupt))

        watcher: asyncio.Task | None = None
        if interrupt is not None:
            watcher = asyncio.create_task(self._interrupt_watcher(interrupt))

        try:
            #* Await the producer first: it always finishes the stream (even after an
            #* interrupt) and posts the sentinel, so the consumer can then drain and exit.
            await producer
            await consumer
        finally:
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

        interrupted = interrupt is not None and interrupt.is_set()
        unspoken = generated[len(spoken):] if interrupted else []
        if interrupted:
            #* Optional protocol method — Gemini/Ollama implement it, Scripted no-ops.
            register = getattr(self.llm_client, "register_interruption", None)
            if callable(register):
                register(spoken, unspoken)

        return InterruptionResult(interrupted=interrupted, spoken=spoken, unspoken=unspoken)

    async def _produce(
        self,
        prompt: str,
        generated: list[str],
        speech_queue: asyncio.Queue[object | None],
    ) -> None:
        try:
            async for chunk in self.llm_client.stream_response(prompt):
                for event in self.processor.feed(chunk):
                    if event.kind == "sentence":
                        generated.append(event.payload)
                        await speech_queue.put(event)

            for event in self.processor.flush():
                if event.kind == "sentence":
                    generated.append(event.payload)
                    await speech_queue.put(event)
        finally:
            #* Sentinel: always posted so the consumer cannot hang, even if streaming
            #* raised partway through.
            await speech_queue.put(None)

    async def _interrupt_watcher(self, interrupt: InterruptController) -> None:
        #* Fires stop_speaking the instant the interrupt trips, instead of waiting for the
        #* consumer to reach its next-sentence check, so the current utterance is cut as
        #* promptly as the hardware allows.
        await interrupt.wait()
        await self.controller.stop_speaking()

    async def _speech_loop(
        self,
        speech_queue: asyncio.Queue[object | None],
        spoken: list[str],
        interrupt: InterruptController | None,
    ) -> None:
        while True:
            event = await speech_queue.get()
            if event is None:
                return
            if event.kind != "sentence":
                continue
            if interrupt is not None and interrupt.is_set():
                #* Interrupted: drop this and every later sentence, but keep consuming so
                #* the producer's sentinel is reached and the turn can finish cleanly.
                continue

            spoken.append(event.payload)
            await self._speak_event(event)

    async def _speak_event(self, event) -> None:
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
