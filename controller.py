from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final
from unittest import case

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

@dataclass(slots=True)
class MotionState:
    """Current position of each servo, tracked internally to calculate blended offsets."""
    joint_id: int
    position: float
    count: int

@dataclass(slots=True)
class OhbotState:
    """Current position of each servo, tracked internally to calculate blended offsets."""
    MotionState: list[MotionState] = [MotionState(joint_id=i, position=0.0, count=1) for i in range(6)]


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
        
        self._offset_requests: list[MotionOffset] = []
        self._current_state = OhbotState()
        self._ohbot_lock = threading.Lock()  #* Serialise access to the ohbot library, which is not thread-safe.
        self._offset_lock = threading.Lock()
        self._stop_event = threading.Event()
        #* Tripped by stop_speaking() so a sentence still queued for the TTS thread is
        #* skipped, and any future stop hook can be honoured the instant it is set.
        self._speech_stopped = threading.Event()

        ohbot.reset()

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

    def mainThread(self) -> None:
        """Run a no-op on the main thread to ensure the COM objects for speech are created there."""
        duration = 0.1

        with self._offset_lock:
            motionStates = list[MotionState] = [MotionState(joint_id=i, position=0.0, count=1) for i in range(6)]

            for i in range(len(self._offset_requests)):
                if self._offset_requests[i].duration_s <= 0:
                    self._offset_requests.pop(i)
                else:
                    #* Apply the offset relative to the current position, not the hardware's absolute position, so multiple overlapping offsets blend together instead of fighting.

                    motionStates[self._offset_requests[i].joint_id].position += self._offset_requests[i].delta
                    motionStates[self._offset_requests[i].joint_id].count += 1

                    self._offset_requests[i].duration_s -= duration

            for i in range(len(motionStates))
                averagedPosition = motionStates.position / motionStates.count
                motionStates[i] = averagedPosition


        # apply the average state to the Ohbot hardware.
        with self._ohbot_lock:
            for joint in motionStates:
                ohbot.move(joint.joint_id, joint.position)
        
        time.sleep(duration)

    def _enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> None:
        duration_s = max(0.0, duration_s)
        with self._offset_lock:
            self._offset_requests.append(MotionOffset(joint_id=joint_id, delta=delta, duration_s=duration_s))


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
        with self._ohbot_lock:
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
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.HEADNOD, +3.0, 0.5)
        await asyncio.sleep(0.5)
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.HEADNOD, -3.0, 0.75)
        await asyncio.sleep(0.75)

    async def look_left(self) -> None:
        print("[action] look_left")
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.EYETURN, +5.0, 1.5)
        await asyncio.sleep(1.5)

    async def look_right(self) -> None:
        print("[action] look_right")
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.EYETURN, -5.0, 1.5)
        await asyncio.sleep(1.5)

    async def blink(self) -> None:
        print("[action] blink")
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.LIDBLINK, -5.0, 0.5)
        await asyncio.sleep(0.5)

    async def wink(self) -> None:
        #* Obot has a single shared lid servo, so a wink is rendered as a quick, snappier blink the closest the hardware can manage.
        #todo @Aquiler please check if done right
        print("[action] wink")
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.LIDBLINK, -5.0, 0.2)
        await asyncio.sleep(0.3)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.HEADTURN, -2.0, 0.5)
            self._enqueue_offset(ohbot.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.HEADTURN, +2.0, 1.0)
            self._enqueue_offset(ohbot.EYETURN, -2.0, 1.0)
        await asyncio.sleep(1)
        with self._ohbot_lock:
            self._enqueue_offset(ohbot.HEADTURN, -2.0, 0.5)
            self._enqueue_offset(ohbot.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")
        #TODO use new offset system
        ### Emotions basics: Happy, Sad
        ### Need Testing
        if emotion == "Happy":
            with self._ohbot_lock:
                self._enqueue_offset(ohbot.TOPLIP, +3.0, 1.0)
                self._enqueue_offset(ohbot.BOTTOMLIP, +3.0, 1.0)
                self._enqueue_offset(ohbot.EYETURN, +2.0, 1.0)
        elif emotion == "Sad":
            with self._ohbot_lock:
                self._enqueue_offset(ohbot.TOPLIP, -3.0, 1.0)
                self._enqueue_offset(ohbot.BOTTOMLIP, -3.0, 1.0)
                self._enqueue_offset(ohbot.EYETURN, -2.0, 1.0)


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

    async def shake_head(self) -> None:
        print("[action] shake_head")
        await asyncio.sleep(0.4)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")


#* Backwards-compatible name. Historically the demo used "DemoObotController". resolve it to whichever controller actually works here so old imports keep functioning.
# Thanks Claude #todo make sure this is right though.
DemoObotController = HardwareObotController if ohbot is not None else ConsoleObotController
