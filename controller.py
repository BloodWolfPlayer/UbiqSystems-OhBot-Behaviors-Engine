from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class ObotController(ABC):
    """Robot-facing commands live here."""

    @abstractmethod
    async def speak_sentence(self, sentence: str) -> None:
        """Speak a completed sentence."""

    @abstractmethod
    async def nod(self) -> None: ...

    @abstractmethod
    async def wave(self) -> None: ...

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


class DemoObotController(ObotController):
    """Console-based controller for development and testing."""

    #todo Replace these print statements with real OhBot SDK calls once the hardware is wired up over USB. Each method should drive the corresponding servo.

    async def speak_sentence(self, sentence: str) -> None:
        print(f"[speech] {sentence}")
        await asyncio.sleep(min(0.2 + len(sentence) / 80, 1.5))

    async def nod(self) -> None:
        print("[action] nod")
        await asyncio.sleep(0.2)

    async def wave(self) -> None:
        print("[action] wave")
        await asyncio.sleep(0.2)

    async def look_left(self) -> None:
        print("[action] look_left")
        await asyncio.sleep(0.1)

    async def look_right(self) -> None:
        print("[action] look_right")
        await asyncio.sleep(0.1)

    async def blink(self) -> None:
        print("[action] blink")
        await asyncio.sleep(0.05)

    async def wink(self) -> None:
        print("[action] wink")
        await asyncio.sleep(0.1)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        await asyncio.sleep(0.2)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")
