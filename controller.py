from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class ObotController(ABC):
    """Robot-facing commands live here."""

    @abstractmethod
    async def speak_sentence(self, sentence: str) -> None:
        """Speak a completed sentence."""

    @abstractmethod
    async def nod(self) -> None:
        """Perform a nod motion."""

    @abstractmethod
    async def wave(self) -> None:
        """Perform a wave motion."""

    @abstractmethod
    async def look_left(self) -> None:
        """Look left."""

    @abstractmethod
    async def look_right(self) -> None:
        """Look right."""


class DemoObotController(ObotController):
    """Console-based controller for development and testing."""

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
