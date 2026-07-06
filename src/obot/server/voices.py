"""Voice enumeration and a one-off "speak this test sentence" helper for the GUI.

The Setup page needs to show the voices available per TTS engine and let the user
audition one before committing. Both operations are synchronous/blocking (COM
enumeration, synthesis, audio playback), so the server calls them via
``asyncio.to_thread`` and never on the event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..speech.config import (
    GeminiTTSSettings,
    LocalTTSSettings,
    PiperTTSSettings,
)
from ..speech.tts import GeminiTTS, LocalTTS, PiperTTS, TTSError

#* Gemini's prebuilt voice names are a fixed catalogue (not fetched per key), so a
#* static list is correct and keeps the Setup page working offline.
GEMINI_PREBUILT_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]


def _piper_cached_voices(configured: str) -> list[str]:
    """Piper voices already downloaded to ohbotData/piper (offline-usable), plus the
    configured one so it always appears even before its first download."""
    voices: list[str] = []
    voices_dir = PiperTTS.VOICES_DIR
    try:
        if voices_dir.is_dir():
            voices = sorted(p.stem for p in voices_dir.glob("*.onnx"))
    except OSError:
        voices = []
    if configured and configured not in voices:
        voices.insert(0, configured)
    return voices


def _sapi_voices() -> list[str]:
    """Installed Windows SAPI voice names (short forms), enumerated on a COM thread."""
    try:
        import comtypes
        from comtypes.client import CreateObject
    except ImportError:
        return []
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    try:
        voice = CreateObject("SAPI.SpVoice")
        names = []
        for v in voice.GetVoices():
            desc = v.GetDescription()
            #* SAPI descriptions look like "Microsoft Zira Desktop - English (United States)";
            #* keep the leading name so it matches the config substring style ("zira").
            names.append(desc)
        return names
    except Exception:
        return []


def _pyttsx_voices() -> list[str]:
    try:
        import pyttsx3
    except ImportError:
        return []
    try:
        engine = pyttsx3.init()
        return [v.name or v.id for v in engine.getProperty("voices")]
    except Exception:
        return []


def list_tts_voices(cfg) -> dict:
    """Return the available voices per engine for the Setup page.

    ``{"gemini": [...], "piper": [...], "local": [...]}``. Piper lists what is
    downloaded locally (usable offline); ``local`` is SAPI on Windows / espeak
    elsewhere.
    """
    local = _sapi_voices() if sys.platform == "win32" else _pyttsx_voices()
    return {
        "gemini": list(GEMINI_PREBUILT_VOICES),
        "piper": _piper_cached_voices(cfg.speech.tts.piper.voice),
        "local": local,
    }


def _play(samples, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, samplerate=sample_rate)
    sd.wait()


def speak_test_blocking(cfg, engine: str, voice: str, text: str) -> str:
    """Synthesize ``text`` with one engine/voice and play it on the host. Blocking.

    Returns the engine name that actually produced audio. Raises :class:`TTSError`
    on synthesis failure so the server reports it as a failed RPC. Meant to run in a
    worker thread.
    """
    import asyncio

    text = (text or "").strip() or "Hello, I am Ms. Mimic. This is a voice test."
    engine = (engine or "").lower().strip()

    if engine == "gemini":
        settings = GeminiTTSSettings.from_dict(
            {**cfg.speech.tts.gemini.__dict__, "voice": voice or cfg.speech.tts.gemini.voice}
        )
        tts = GeminiTTS(cfg.gemini_api_key, settings)
    elif engine == "piper":
        settings = PiperTTSSettings.from_dict(
            {**cfg.speech.tts.piper.__dict__, "voice": voice or cfg.speech.tts.piper.voice,
             "warm_up": False}
        )
        tts = PiperTTS(settings)
    else:  # "local" / anything else
        settings = LocalTTSSettings.from_dict(
            {**cfg.speech.tts.local.__dict__, "voice": voice or cfg.speech.tts.local.voice}
        )
        tts = LocalTTS(settings)

    try:
        result = asyncio.run(tts.synthesize(text))
        _play(result.samples, result.sample_rate)
        return result.engine
    finally:
        tts.close()
