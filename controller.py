from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from ohbot import ohbot

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
        ohbot.reset()
        ohbot.move(ohbot.HEADNOD, 5)
        ohbot.move(ohbot.EYETURN, 5)
        ohbot.move(ohbot.LIDBLINK, 5)

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
        ohbot.move(ohbot.HEADNOD, 8)
        await asyncio.sleep(0.5)
        ohbot.move(ohbot.HEADNOD, 2)
        await asyncio.sleep(0.75)
        ohbot.move(ohbot.HEADNOD, 5)

    async def look_left(self) -> None:
        print("[action] look_left")
        ohbot.move(ohbot.EYETURN,10)
        await asyncio.sleep(1.5)
        ohbot.move(ohbot.EYETURN,5)

    async def look_right(self) -> None:
        print("[action] look_right")
        ohbot.move(ohbot.EYETURN,0)
        await asyncio.sleep(1.5)
        ohbot.move(ohbot.EYETURN,5)

    async def blink(self) -> None:
        print("[action] blink")
        ohbot.move(ohbot.LIDBLINK, 0)
        await asyncio.sleep(0.5)
        ohbot.move(ohbot.LIDBLINK, 5)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        ohbot.move(ohbot.HEADTURN,3)
        ohbot.move(ohbot.EYETURN,7)
        await asyncio.sleep(0.5)
        ohbot.move(ohbot.HEADTURN,7)
        ohbot.move(ohbot.EYETURN,3)
        await asyncio.sleep(1)
        ohbot.move(ohbot.HEADTURN,3)
        ohbot.move(ohbot.EYETURN,7)
        await asyncio.sleep(0.5)
        ohbot.move(ohbot.HEADTURN,5)
        ohbot.move(ohbot.EYETURN,5)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")

        # Advanced, need to change the voice synthesizer 
        #if emotion == "Happy":
        #    ohbot.move(ohbot.TOPLIP, 8)
        #    ohbot.move(ohbot.BOTTOMLIP, 9)
        #elif emotion == "Sad":
        #    ohbot.move(ohbot.TOPLIP, 1)
        #    ohbot.move(ohbot.BOTTOMLIP, 2)
