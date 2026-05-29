from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

from ohbot import ohbot


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
    async def nod(self) -> None: ...

    @abstractmethod
    async def look_left(self) -> None: ...

    @abstractmethod
    async def look_right(self) -> None: ...

    @abstractmethod
    async def blink(self) -> None: ...

    @abstractmethod
    async def shake_head(self) -> None: ...

    @abstractmethod
    async def set_emotion(self, emotion: str) -> None: ...


class DemoObotController(ObotController):
    """Console-based controller for development and testing."""

    #todo Replace these print statements with real OhBot SDK calls once the hardware is wired up over USB. Each method should drive the corresponding servo.
    def __init__(self):
        super().__init__()
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

        ohbot.reset()
        for joint_id, neutral in self._neutral_positions.items():
            ohbot.move(joint_id, neutral)

        self._blend_thread = threading.Thread(target=self._blend_offsets_loop, daemon=True)
        self._blend_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._blend_thread.is_alive():
            self._blend_thread.join(timeout=1.0)

    def __del__(self) -> None:
        self.close()

    def _enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> None:
        duration_s = max(0.0, duration_s)
        with self._offset_lock:
            self._offset_requests.append(MotionOffset(joint_id=joint_id, delta=delta, duration_s=duration_s))

    def _blend_offsets_loop(self) -> None:
        #TODO implement offset averaging
        #Runn this as a specified frequency and each loop you can reduce the time for each active offset until it is at 0 where it can then be removed

    async def speak_sentence(self, sentence: str) -> None:
        print(f"[speech] {sentence}")
        estimated_duration = min(0.2 + len(sentence) / 80, 1.5)
        if not any(character.isalnum() for character in sentence):
            await asyncio.sleep(estimated_duration)
            return
        ohbot.setVoice("-vzira")
        ohbot.say(sentence)
        await asyncio.sleep(estimated_duration)

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
