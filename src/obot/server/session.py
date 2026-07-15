"""The server-side conversation session — the RPC-driven twin of ``_voice_session``.

Where ``obot.__main__._voice_session`` wires the microphone, interrupt controller
and keyboard to a pipeline for the *console* app, :class:`ServerSession` owns the
same pieces (controller + pipeline + interrupt + behaviors + mic) but is driven by
method calls from the control server instead of the keyboard. One session at a time
lives inside a running ``--serve`` process.

Turn-taking is serialized through a single queue: both typed input (``send_text``)
and recognized microphone utterances land on it, and one worker voices them one at a
time, so a mic phrase and a typed line can never talk over each other. Interruption,
mic mode and live state all go through the same event bus the rest of the engine uses.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from ..core import events
from ..core.interrupt import InterruptController
from ..robot.behaviors import BehaviorManager
from ..robot.controller import (
    ConsoleObotController,
    ObotController,
    VirtualObotController,
)
from ..core.orchestrator import RobotPipeline

# repo root is four levels up: server/session.py -> server -> obot -> src -> root
_REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_PROMPT_FILE = _REPO_ROOT / "system_prompt.txt"

# Sentinel pushed onto the turn queue to unblock and end the worker on stop().
_STOP = object()

VALID_CONTROLLERS = ("virtual", "sim", "hardware", "console")
VALID_BACKENDS = ("scripted", "gemini", "ollama")
VALID_MIC_MODES = ("vad", "ptt", "muted")


def _read_system_prompt() -> str:
    if SYSTEM_PROMPT_FILE.exists():
        return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    # The persona file is optional for the server; a terse default keeps gemini/ollama
    # usable even on a bare checkout without system_prompt.txt.
    return "You are Ms. Mimic, a friendly robot. Keep replies short and spoken-friendly."


class _EchoLLMClient:
    """Scripted backend: streams the user's own text back, so the whole pipeline
    (sentence splitting, [Action]/(Emotion) markers, interruption) can be exercised
    with no API key or network. The bot "says what you type"."""

    def __init__(self, chunk_size: int = 24) -> None:
        self.chunk_size = max(1, chunk_size)

    async def stream_response(self, prompt: str) -> AsyncIterator[str]:
        for i in range(0, len(prompt), self.chunk_size):
            await asyncio.sleep(0.05)
            yield prompt[i : i + self.chunk_size]

    def register_interruption(self, spoken: list[str], unspoken: list[str]) -> None:
        del spoken, unspoken


class ServerSession:
    """One live conversation. Created per ``session_start``, torn down on ``session_stop``."""

    def __init__(self, cfg, loop: asyncio.AbstractEventLoop) -> None:
        self._cfg = cfg
        self._loop = loop

        self.backend: str = ""
        self.model: str = ""
        self.controller_kind: str = ""

        self._controller: ObotController | None = None
        self._pipeline: RobotPipeline | None = None
        self._behaviors: BehaviorManager | None = None
        self._interrupt: InterruptController | None = None
        self._audio = None
        self._llm_client = None
        self._tunnel_cm = None  # SSH tunnel context manager for the Ollama backend

        self._turn_queue: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._mic_consumer: asyncio.Task | None = None

        self._mic_mode: str = "muted"
        self._mic_started = False
        self._speaking = False
        self._listening = False
        self._state = "idle"
        self._active_engine: str | None = None
        self._running = False
        self._unsub_speech = None

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self, backend: str, model: str, controller: str) -> dict:
        if self._running:
            raise RuntimeError("a session is already running; stop it first.")

        backend = (backend or "scripted").lower().strip()
        controller = (controller or "virtual").lower().strip()
        if backend not in VALID_BACKENDS:
            raise ValueError(f"unknown backend '{backend}' (use one of {VALID_BACKENDS}).")
        if controller not in VALID_CONTROLLERS:
            raise ValueError(f"unknown controller '{controller}' (use one of {VALID_CONTROLLERS}).")

        self.backend = backend
        self.model = model or ""
        self.controller_kind = controller

        self._controller = self._make_controller(controller)
        self._llm_client = await self._make_llm_client(backend, self.model)
        self._pipeline = RobotPipeline(llm_client=self._llm_client, controller=self._controller)
        self._interrupt = InterruptController(self._loop)
        self._behaviors = BehaviorManager(self._controller, self._cfg.behaviors)
        self._behaviors.start()

        # Watch speech events to keep the "active TTS engine" badge current for get_state.
        self._unsub_speech = events.subscribe(events.SPEECH, self._on_speech_event)

        self._setup_audio()  # best-effort; mic stays muted until set_mic_mode opens it

        self._worker = asyncio.create_task(self._turn_worker())
        self._running = True
        events.emit(events.LOG, {"level": "info",
                                 "message": f"session started: {backend}/{self.model or '-'} on {controller}"})
        self._emit_state()
        return self.state()

    async def stop(self) -> dict:
        if not self._running:
            return {"session": False}
        self._running = False

        # Cut any in-flight speech and unblock the worker.
        if self._interrupt is not None:
            self._interrupt.trigger("keyboard")
        if self._controller is not None:
            with contextlib.suppress(Exception):
                await self._controller.stop_speaking()

        if self._mic_consumer is not None:
            self._mic_consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mic_consumer
            self._mic_consumer = None

        if self._audio is not None:
            with contextlib.suppress(Exception):
                self._audio.stop()
            self._audio = None

        # Drop any turns still queued so stop() doesn't voice a backlog on the way out.
        while not self._turn_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._turn_queue.get_nowait()
        await self._turn_queue.put(_STOP)
        if self._worker is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

        if self._behaviors is not None:
            with contextlib.suppress(Exception):
                await self._behaviors.stop()
            self._behaviors = None

        if self._unsub_speech is not None:
            self._unsub_speech()
            self._unsub_speech = None

        if self._controller is not None:
            with contextlib.suppress(Exception):
                self._controller.close()
            self._controller = None

        await self._close_llm_client()

        self._speaking = self._listening = False
        self._active_engine = None
        events.emit(events.LOG, {"level": "info", "message": "session stopped"})
        self._emit_state()
        return {"session": False}

    # -- construction helpers ----------------------------------------------------------

    def _make_controller(self, kind: str) -> ObotController:
        cfg = self._cfg
        if kind == "console":
            return ConsoleObotController()
        if kind == "hardware":
            from ..robot.controller import HardwareObotController

            return HardwareObotController(
                port=cfg.ohbot_port, speech_settings=cfg.speech,
                motion_settings=cfg.motion, gemini_api_key=cfg.gemini_api_key,
            )
        if kind == "sim":
            # Opens the tkinter face window on the engine host (self-threaded). If that
            # fails (no display), fall back to the headless virtual controller.
            try:
                from ..robot.controller import SimulatedObotController
                from ..sim.face import FaceWindow

                face = FaceWindow(mouth_settings=cfg.speech.mouth, on_save=cfg.save, tuning=True)
                return SimulatedObotController(
                    face, speech_settings=cfg.speech,
                    motion_settings=cfg.motion, gemini_api_key=cfg.gemini_api_key,
                )
            except Exception as exc:
                events.emit(events.LOG, {"level": "warn",
                                         "message": f"sim window unavailable ({exc}); using virtual controller."})
        return VirtualObotController(
            speech_settings=cfg.speech, motion_settings=cfg.motion,
            gemini_api_key=cfg.gemini_api_key,
        )

    async def _make_llm_client(self, backend: str, model: str):
        if backend == "scripted":
            return _EchoLLMClient()
        if backend == "gemini":
            from ..llm.gemini import GeminiLLMClient

            if not model:
                raise ValueError("the gemini backend needs a model (call list_gemini_models).")
            return GeminiLLMClient(self._cfg.gemini_api_key, model, _read_system_prompt())
        if backend == "ollama":
            from ..net.ssh_tunnel import open_ollama_tunnel
            from ..llm.ollama import OllamaLLMClient

            if not model:
                raise ValueError("the ollama backend needs a model (call list_ollama_models).")
            # Keep the tunnel open for the session's lifetime (closed in stop()).
            self._tunnel_cm = open_ollama_tunnel(self._cfg.ollama_ssh)
            bound_port = self._tunnel_cm.__enter__()
            base_url = f"http://127.0.0.1:{bound_port}"
            return OllamaLLMClient(base_url, model, _read_system_prompt())
        raise ValueError(f"unknown backend '{backend}'.")

    async def _close_llm_client(self) -> None:
        client = self._llm_client
        self._llm_client = None
        if client is not None:
            aexit = getattr(client, "__aexit__", None)
            if aexit is not None:
                with contextlib.suppress(Exception):
                    await aexit(None, None, None)
        if self._tunnel_cm is not None:
            with contextlib.suppress(Exception):
                self._tunnel_cm.__exit__(None, None, None)
            self._tunnel_cm = None

    def _setup_audio(self) -> None:
        """Best-effort microphone wiring. A missing mic/deps is non-fatal: typed input
        still works and the session just has no voice channel."""
        cfg = self._cfg
        try:
            from ..audio.input import (
                AudioInput,
                AudioInputError,
                GoogleBackend,
                VoskBackend,
            )
        except Exception as exc:  # noqa: BLE001
            events.emit(events.LOG, {"level": "warn", "message": f"audio input unavailable ({exc})"})
            return

        try:
            if cfg.audio.stt_engine == "vosk":
                stt = VoskBackend(cfg.audio.vosk_model_path)
            else:
                stt = GoogleBackend()
        except AudioInputError as exc:
            events.emit(events.LOG, {"level": "warn",
                                     "message": f"STT setup failed ({exc}); mic disabled."})
            return

        try:
            audio = AudioInput(cfg.audio.input_device_index, stt, loop=self._loop)
        except AudioInputError as exc:
            events.emit(events.LOG, {"level": "warn",
                                     "message": f"microphone unavailable ({exc}); mic disabled."})
            return

        audio.on_barge_in = lambda: self._interrupt and self._interrupt.trigger("voice")
        audio.on_user_speech_start = self._on_speech_start
        audio.on_user_speech_end = self._on_speech_end
        audio.on_partial = self._emit_partial
        audio.set_mode("muted")
        self._audio = audio

    # -- microphone thread callbacks (called off the event loop) -----------------------

    def _on_speech_start(self) -> None:
        if self._behaviors is not None:
            self._behaviors.set_listening(True)
        self._loop.call_soon_threadsafe(self._set_listening, True)
        # Show the GUI a live "listening…" line the moment the user starts talking.
        events.emit(events.TRANSCRIPT, {"role": "user", "text": "", "partial": True})

    def _on_speech_end(self) -> None:
        if self._behaviors is not None:
            self._behaviors.set_listening(False)
        self._loop.call_soon_threadsafe(self._set_listening, False)

    # -- commands ----------------------------------------------------------------------

    def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self._running:
            return
        self._emit_transcript(text)
        self._turn_queue.put_nowait(text)

    def interrupt(self) -> None:
        if self._interrupt is not None:
            # Same path as SPACE / voice barge-in: stop at the next word boundary.
            self._interrupt.trigger("keyboard")

    def set_mic_mode(self, mode: str) -> None:
        mode = (mode or "").lower().strip()
        if mode not in VALID_MIC_MODES:
            raise ValueError(f"unknown mic mode '{mode}' (use one of {VALID_MIC_MODES}).")
        self._mic_mode = mode
        if self._audio is None:
            if mode != "muted":
                events.emit(events.LOG, {"level": "warn",
                                         "message": "no microphone available; mic mode ignored."})
            return
        # Open the device lazily: the capture thread (and its ambient-noise calibration)
        # only starts the first time the mic is actually un-muted.
        if mode != "muted" and not self._mic_started:
            self._audio.start()
            self._mic_started = True
            self._mic_consumer = asyncio.create_task(self._mic_consumer_loop())
        self._audio.set_mode(mode)
        # PTT parks the mic until the user presses the talk button / hotkey (trigger_ptt);
        # VAD listens continuously. We do not auto-arm a capture on the mode switch itself.

    def trigger_ptt(self) -> None:
        """Arm one push-to-talk capture (from the GUI talk button or hotkey)."""
        if self._audio is not None:
            self._audio.trigger_ptt()

    def set_joint(self, joint_id: int, position: float) -> None:
        """Hold one joint at an absolute position (GUI manual control panel)."""
        if self._controller is not None:
            self._controller.set_manual_joint(joint_id, position)

    def release_joint(self, joint_id: int) -> None:
        """Release a manually-held joint back to ambient/automatic control."""
        if self._controller is not None:
            self._controller.set_manual_joint(joint_id, None)

    def release_all_joints(self) -> None:
        """Release every manually-held joint at once."""
        if self._controller is not None:
            self._controller.release_all_manual_joints()

    async def set_emotion(self, emotion: str) -> None:
        """Trigger an emotion's default pose -- the same path the LLM's (Emotion)
        markers use in RobotPipeline, exposed for the GUI's manual control panel."""
        if self._controller is not None:
            await self._controller.set_emotion(emotion)

    # -- turn worker -------------------------------------------------------------------

    async def _mic_consumer_loop(self) -> None:
        assert self._audio is not None
        while True:
            text = (await self._audio.next_utterance()).strip()
            # Commit the final transcript (empty clears the GUI's live "listening…" line);
            # only a real utterance becomes a conversation turn.
            events.emit(events.TRANSCRIPT, {"role": "user", "text": text, "partial": False})
            if text:
                await self._turn_queue.put(text)

    async def _turn_worker(self) -> None:
        while True:
            text = await self._turn_queue.get()
            if text is _STOP:
                return
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_turn(text)

    async def _run_turn(self, text: str) -> None:
        assert self._pipeline is not None and self._interrupt is not None
        self._interrupt.clear()
        self._set_speaking(True)
        if self._audio is not None:
            self._audio.set_speaking(True)
        if self._behaviors is not None:
            self._behaviors.set_speaking(True)
        try:
            result = await self._pipeline.run(text, self._interrupt)
            if result.interrupted:
                events.emit(events.SPEECH, {"text": None, "event": "interrupted", "engine": self._active_engine})
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the session
            events.emit(events.ERROR, {"where": "turn", "message": str(exc)})
        finally:
            if self._audio is not None:
                self._audio.set_speaking(False)
            if self._behaviors is not None:
                self._behaviors.set_speaking(False)
            self._set_speaking(False)

    # -- state / events ----------------------------------------------------------------

    def _on_speech_event(self, _topic: str, data) -> None:
        engine = (data or {}).get("engine")
        if engine:
            self._active_engine = engine

    def _set_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        self._emit_state()

    def _set_listening(self, listening: bool) -> None:
        self._listening = listening
        self._emit_state()

    def _emit_state(self) -> None:
        state = "speaking" if self._speaking else "listening" if self._listening else "idle"
        if state != self._state:
            self._state = state
            events.emit(events.STATE, {"state": state})

    def _emit_transcript(self, text: str) -> None:
        events.emit(events.TRANSCRIPT, {"role": "user", "text": text, "partial": False})

    def _emit_partial(self, text: str) -> None:
        # Called from the mic capture thread with interim words (Vosk streaming).
        events.emit(events.TRANSCRIPT, {"role": "user", "text": text, "partial": True})

    def state(self) -> dict:
        return {
            "session": self._running,
            "backend": self.backend or None,
            "model": self.model or None,
            "controller": self.controller_kind or None,
            "state": self._state,
            "mic_mode": self._mic_mode,
            "mic_available": self._audio is not None,
            "tts_engine_active": self._active_engine,
            "emotion": self._controller.current_emotion if self._controller is not None else "Neutral",
        }
