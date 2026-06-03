from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final
from unittest import case
import random

#* ohbot is imported lazily inside HardwareObotController.__init__ so the COM port scan
#* does not block startup. Module-level None until the hardware controller is first created.
ohbot = None  # type: ignore


def _kill_espeak() -> None:
    """Kill any running espeak TTS subprocess so speech stops immediately."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "espeak.exe"], capture_output=True, timeout=1)
            subprocess.run(["taskkill", "/F", "/IM", "espeak-ng.exe"], capture_output=True, timeout=1)
        else:
            subprocess.run(["pkill", "-f", "espeak"], capture_output=True, timeout=1)
    except Exception:
        pass


def _import_ohbot(preferred_port: str | None = None) -> None:
    """Import the ohbot module, trying preferred_port first to avoid a slow full scan."""
    global ohbot
    import serial.tools.list_ports as _lp

    _orig = None
    if preferred_port:
        _orig = _lp.comports

        def _preferred_first():
            all_ports = _orig()
            preferred = [p for p in all_ports if p[0] == preferred_port]
            rest = [p for p in all_ports if p[0] != preferred_port]
            return preferred + rest

        _lp.comports = _preferred_first

    try:
        from ohbot import ohbot as _mod
        ohbot = _mod
    except ImportError:
        ohbot = None
    finally:
        if _orig is not None:
            _lp.comports = _orig


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
    count: float

class ObotController(ABC):
    """Robot-facing commands live here."""

    @abstractmethod
    async def speak_sentence(self, sentence: str) -> None:
        """Speak a completed sentence."""

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

    async def stop_speaking(self) -> None:
        """Stop current speech immediately. Override to actually halt TTS."""

    def close(self) -> None:
        """Release hardware resources. Override to actually stop threads/hardware."""


class HardwareObotController(ObotController):
    """Drives the physical Obot via the ohbot library (servos + espeak TTS)."""

    def __init__(self, port: str | None = None):
        global ohbot
        super().__init__()
        if ohbot is None:
            _import_ohbot(port)
        if ohbot is None:
            raise RuntimeError(
                "the 'ohbot' library is not installed, so the hardware controller "
                "cannot start. Install it on the robot/Pi, or use ConsoleObotController "
                "(the demo falls back to it automatically)."
            )

        self._offset_requests: list[MotionOffset] = []
        self._ohbot_lock = threading.Lock()  #* Serialise access to the ohbot library, which is not thread-safe.
        self._offset_lock = threading.Lock()
        self._speech_stopped = threading.Event()
        self._stop_event = threading.Event()

        ohbot.reset()

        self._main_thread = threading.Thread(target=self.mainThread, daemon=True)
        self._autoblink_thread = threading.Thread(target=self._auto_blink, daemon=True)
        self._main_thread.start()
        self._autoblink_thread.start()

    def close(self) -> None:
        #* Defensive: if __init__ bailed early (e.g. ohbot missing) these attributes may
        #* not exist, and __del__ must not raise during garbage collection.
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        main_thread = getattr(self, "_main_thread", None)
        if main_thread is not None and main_thread.is_alive():
            main_thread.join(timeout=1.0)

        autoblinkThread = getattr(self, "_autoblink_thread", None)
        if autoblinkThread is not None and autoblinkThread.is_alive():
            autoblinkThread.join(timeout=1.0)

    def __del__(self) -> None:
        self.close()

    def mainThread(self) -> None:
        """Run a no-op on the main thread to ensure the COM objects for speech are created there."""
        duration = 0.5

        while not self._stop_event.is_set():
            with self._offset_lock:
                motionStates: list[MotionState] = [MotionState(joint_id=i, position=0.0, count=1) for i in range(6)]
                itemsToRemove = []

                for i in range(len(self._offset_requests)):
                    if self._offset_requests[i].duration_s <= 0:
                        # add item to list of items to remove
                        itemsToRemove.append(self._offset_requests[i])
                    else:
                        #* Apply the offset relative to the current position, not the hardware's absolute position, so multiple overlapping offsets blend together instead of fighting.

                        motionStates[self._offset_requests[i].joint_id].position += self._offset_requests[i].delta
                        motionStates[self._offset_requests[i].joint_id].count += 1

                        self._offset_requests[i].duration_s -= duration

                # remove all items marked as removeal
                for i in range(len(itemsToRemove)):
                    self._offset_requests.remove(itemsToRemove[i])

                for i in range(len(motionStates)):
                    averagedPosition = motionStates[i].position / motionStates[i].count
                    motionStates[i].position = averagedPosition + 5.0
                    #print(f"Index: {i}, position: {motionStates[i].position}")


            # apply the average state to the Ohbot hardware.
            with self._ohbot_lock:
                for joint in motionStates:
                    ohbot.move(joint.joint_id, joint.position)
            
            time.sleep(duration)

    def _auto_blink(self) -> None:
        while not self._stop_event.is_set():
            duration = random.randrange(2, 5)

            asyncio.run(self.blink())

            time.sleep(duration)

    def _enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> None:
        duration_s = max(0.0, duration_s)
        with self._offset_lock:
            self._offset_requests.append(MotionOffset(joint_id=joint_id, delta=delta, duration_s=duration_s))


    async def speak_sentence(self, sentence: str) -> None:
        print(f"[speech] {sentence}")
        #* Cleared per sentence so a stop from the previous sentence doesn't suppress this one.
        self._speech_stopped.clear()

        if not any(character.isalnum() for character in sentence):
            await asyncio.sleep(0.3)
            return

        with self._ohbot_lock:
            ohbot.setVoice("-vzira")
            try:
                #* block=False returns immediately; we wait below so the event loop stays
                #* free for servo movements, interrupt handling, and emotion changes.
                ohbot.say(sentence, False, True)
            except Exception as exc:
                print(f"[speech] ohbot.say failed: {exc}")
                return

        #* Word-count-based estimate is far more accurate than the old char/80 formula.
        words = len(sentence.split())
        estimated_duration = words / 2.5 + 0.4
        elapsed = 0.0
        step = 0.05
        while elapsed < estimated_duration:
            if self._speech_stopped.is_set():
                _kill_espeak()
                return
            await asyncio.sleep(step)
            elapsed += step

    async def stop_speaking(self) -> None:
        self._speech_stopped.set()
        _kill_espeak()

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
        ## Emotions basics: Happy, Sad
        ## Need Testing
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
        print("[action][sim] nod")
        await asyncio.sleep(0.3)

    async def look_left(self) -> None:
        print("[action][sim] look_left")
        await asyncio.sleep(0.3)

    async def look_right(self) -> None:
        print("[action][sim] look_right")
        await asyncio.sleep(0.3)

    async def blink(self) -> None:
        print("[action][sim] blink")
        await asyncio.sleep(0.2)

    async def wink(self) -> None:
        print("[action][sim] wink")
        await asyncio.sleep(0.2)

    async def shake_head(self) -> None:
        print("[action][sim] shake_head")
        await asyncio.sleep(0.4)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion][sim] {emotion}")


#* Backwards-compatible name. Historically the demo used "DemoObotController". resolve it to whichever controller actually works here so old imports keep functioning.
#todo make sure this is right though.
#DemoObotController = HardwareObotController if ohbot is not None else ConsoleObotController
DemoObotController = HardwareObotController
