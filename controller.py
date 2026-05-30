from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

#* ohbot drives real servos and is only present on the robot (and the Pi deploy image).
#* Import it lazily/guarded so the rest of the program  pipeline, LLM clients,
#* the console controller runs on a plain dev machine for testing LLM interactions.
try:
    from ohbot import ohbot
except ImportError:
    ohbot = None  # type: ignore


@dataclass(slots=True)
class MotionOffset:
    """Relative servo movement request consumed by the blender thread."""

    joint_id: int
    delta: float
    duration_s: float

class ObotController(ABC):
    """Robot-facing commands live here."""

    @abstractmethod
    async def speak_sentence(self, sentence: str) -> None:
        """Speak a completed sentence."""

    @abstractmethod
    async def stop_speaking(self) -> None:
        """Best-effort: cut off the current utterance and suppress queued speech."""

    @abstractmethod
    async def nod(self) -> None: ...

    @abstractmethod
    async def look_left(self) -> None: ...

    @abstractmethod
    async def look_right(self) -> None: ...

    @abstractmethod
    async def blink(self) -> None: ...

    @abstractmethod
    async def wink(self) -> None: ...

   # @abstractmethod
   # async def wave(self) -> None: ...

    @abstractmethod
    async def shake_head(self) -> None: ...

    @abstractmethod
    async def set_emotion(self, emotion: str) -> None: ...


class HardwareObotController(ObotController):
    """Drives the physical Obot via the ohbot library (servos + espeak TTS)."""

    def __init__(self):
        super().__init__()
        if ohbot is None:
            raise RuntimeError(
                "the 'ohbot' library is not installed, so the hardware controller "
                "cannot start. Install it on the robot/Pi, or use ConsoleObotController "
                "(the demo falls back to it automatically)."
            )
        self._neutral_positions: dict[int, float] = {
            ohbot.HEADNOD: 5.0,
            ohbot.HEADTURN: 5.0,
            ohbot.EYETURN: 5.0,
            ohbot.LIDBLINK: 5.0,
        }
        self._offset_requests: list[MotionOffset] = []
        self._active_offsets: list[tuple[int, float, float]] = []
        self._offset_lock = threading.Lock()
        self._stop_event = threading.Event()
        #* Tripped by stop_speaking() so a sentence still queued for the TTS thread is
        #* skipped, and any future stop hook can be honoured the instant it is set.
        self._speech_stopped = threading.Event()

        ohbot.reset()
        for joint_id, neutral in self._neutral_positions.items():
            ohbot.move(joint_id, neutral)

        self._blend_thread = threading.Thread(target=self._blend_offsets_loop, daemon=True)
        self._blend_thread.start()

    def close(self) -> None:
        #* Defensive: if __init__ bailed early (e.g. ohbot missing) these attributes may
        #* not exist, and __del__ must not raise during garbage collection.
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        blend_thread = getattr(self, "_blend_thread", None)
        if blend_thread is not None and blend_thread.is_alive():
            blend_thread.join(timeout=1.0)

    def __del__(self) -> None:
        self.close()

    def _enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> None:
        duration_s = max(0.0, duration_s)
        with self._offset_lock:
            self._offset_requests.append(MotionOffset(joint_id=joint_id, delta=delta, duration_s=duration_s))

    def _blend_offsets_loop(self) -> None:
        #TODO implement offset averaging so multiple overlapping requests blend together instead of fighting (e.g. nod + shake_head).
        #Run this at a specified frequency and each loop reduce the time for each active
        #offset until it is at 0, where it can then be removed.
        while not self._stop_event.wait(0.05):
            pass

    async def speak_sentence(self, sentence: str) -> None:
        print(f"[speech] {sentence}")
        #* Cleared per sentence: a stop raised for the *previous* sentence must not silently swallow this one.
        self._speech_stopped.clear()
        estimated_duration = min(0.2 + len(sentence) / 80, 1.5)
        if not any(character.isalnum() for character in sentence):
            await asyncio.sleep(estimated_duration)
            return
        if self._speech_stopped.is_set():
            return
        #! ohbot.say MUST run on this (main) thread. On Windows ohbot speaks via SAPI COM
        #! objects (sapivoice/sapistream) created in ohbot.init() on the main thread; COM
        #! objects are apartment-bound, so calling say() from a thread-pool executor throws
        #! a cross-apartment error that ohbot swallows ("Speech being generated too
        #! quickly"), leaving the speech wav unwritten -> PermissionError on read.
        #! say() blocks until the utterance ends; the event loop pausing for one sentence
        #! is fine because interruption takes effect at sentence boundaries anyway (ohbot
        #! cannot stop an utterance mid-flight).
        #todo check out this in detail. This was a quick Claude Fix.
        ohbot.setVoice("-vzira")
        try:
            ohbot.say(sentence)
        except Exception as exc:
            print(f"[speech] ohbot.say failed: {exc}")

    async def stop_speaking(self) -> None:
        self._speech_stopped.set()
        #! Best-effort mid-utterance cut. The ohbot library exposes no guaranteed stop
        #! across platforms, so probe for one; if none exists the current sentence runs
        #! to its end and the orchestrator drops everything after it (sentence boundary).
        #todo check out this in detail. This was a quick Claude Fix.
        for attr in ("stopSpeaking", "stopVoice", "stop"):
            fn = getattr(ohbot, attr, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break

    async def nod(self) -> None:
        print("[action] nod")
        self._enqueue_offset(ohbot.HEADNOD, +3.0, 0.5)
        await asyncio.sleep(0.5)
        self._enqueue_offset(ohbot.HEADNOD, -3.0, 0.75)
        await asyncio.sleep(0.75)

    async def look_left(self) -> None:
        print("[action] look_left")
        self._enqueue_offset(ohbot.EYETURN, +5.0, 1.5)
        await asyncio.sleep(1.5)

    async def look_right(self) -> None:
        print("[action] look_right")
        self._enqueue_offset(ohbot.EYETURN, -5.0, 1.5)
        await asyncio.sleep(1.5)

    async def blink(self) -> None:
        print("[action] blink")
        self._enqueue_offset(ohbot.LIDBLINK, -5.0, 0.5)
        await asyncio.sleep(0.5)

    async def wink(self) -> None:
        #* Obot has a single shared lid servo, so a wink is rendered as a quick, snappier blink the closest the hardware can manage.
        #todo @Aquiler please check if done right
        print("[action] wink")
        self._enqueue_offset(ohbot.LIDBLINK, -5.0, 0.2)
        await asyncio.sleep(0.3)

   # async def wave(self) -> None:
   #     #* No arm on the Obot, so "wave" is a friendly head waggle (turn side to side).
   #     print("[action] wave")
   #     self._enqueue_offset(ohbot.HEADTURN, +3.0, 0.3)
   #     await asyncio.sleep(0.3)
   #     self._enqueue_offset(ohbot.HEADTURN, -3.0, 0.6)
   #     await asyncio.sleep(0.6)
   #     self._enqueue_offset(ohbot.HEADTURN, +3.0, 0.3)
   #     await asyncio.sleep(0.3)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        self._enqueue_offset(ohbot.HEADTURN, -2.0, 0.5)
        self._enqueue_offset(ohbot.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)
        self._enqueue_offset(ohbot.HEADTURN, +2.0, 1.0)
        self._enqueue_offset(ohbot.EYETURN, -2.0, 1.0)
        await asyncio.sleep(1)
        self._enqueue_offset(ohbot.HEADTURN, -2.0, 0.5)
        self._enqueue_offset(ohbot.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")
        #TODO use new offset system
        # Advanced, need to change the voice synthesizer
        #if emotion == "Happy":
        #    ohbot.move(ohbot.TOPLIP, 8)
        #    ohbot.move(ohbot.BOTTOMLIP, 9)
        #elif emotion == "Sad":
        #    ohbot.move(ohbot.TOPLIP, 1)
        #    ohbot.move(ohbot.BOTTOMLIP, 2)


class ConsoleObotController(ObotController):
    """Hardware-free controller: prints what the robot *would* do and simulates timing.

    This is the reusable "run the whole program without an Obot" path — it needs no
    ohbot library and no servos, so the full pipeline (LLM streaming, the processor,
    interruption, and microphone input) can be exercised on a plain dev machine. Speech
    "playback" is modelled as a short sleep, and :meth:`stop_speaking` lets an interrupt
    cut that simulated playback short, mirroring the hardware contract.
    """

    def __init__(self) -> None:
        super().__init__()
        #* Tripped by stop_speaking so an in-flight simulated utterance ends early and a
        #* still-queued one is skipped — the same semantics as the hardware controller.
        self._speech_stopped = threading.Event()

    async def speak_sentence(self, sentence: str) -> None:
        print(f"[speech] {sentence}")
        self._speech_stopped.clear()
        #* Roughly track real TTS pacing so barge-in timing feels realistic, but poll the
        #* stop flag so an interrupt can cut the "playback" mid-sentence.
        estimated_duration = min(0.2 + len(sentence) / 80, 1.5)
        elapsed = 0.0
        step = 0.05
        while elapsed < estimated_duration:
            if self._speech_stopped.is_set():
                print("[speech] (cut off)")
                return
            await asyncio.sleep(step)
            elapsed += step

    async def stop_speaking(self) -> None:
        self._speech_stopped.set()

    async def nod(self) -> None:
        print("[action] nod")
        await asyncio.sleep(0.3)

    async def look_left(self) -> None:
        print("[action] look_left")
        await asyncio.sleep(0.3)

    async def look_right(self) -> None:
        print("[action] look_right")
        await asyncio.sleep(0.3)

    async def blink(self) -> None:
        print("[action] blink")
        await asyncio.sleep(0.2)

    async def wink(self) -> None:
        print("[action] wink")
        await asyncio.sleep(0.2)

   # async def wave(self) -> None:
   #     print("[action] wave")
   #     await asyncio.sleep(0.4)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        await asyncio.sleep(0.4)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")


#* Backwards-compatible name. Historically the demo used "DemoObotController". resolve it to whichever controller actually works here so old imports keep functioning.
# Thanks Claude #todo make sure this is right though.
DemoObotController = HardwareObotController if ohbot is not None else ConsoleObotController
