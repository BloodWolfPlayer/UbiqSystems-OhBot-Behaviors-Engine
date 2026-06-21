from __future__ import annotations

import argparse
import asyncio
import contextlib
from pathlib import Path

#* Only the lightweight imports live at the top level. The Gemini, Ollama and SSH modules
#* are imported only inside the functions that need them,
#* so the scripted demo runs even when httpx, sshtunnel and paramiko are not installed.
from .core.orchestrator import RobotPipeline
from .llm.client import ScriptedLLMClient
from .robot.controller import ConsoleObotController, ObotController

#* Prompt and script files live at the repo root (two levels up from src/obot/).
_REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT_FILE = _REPO_ROOT / "system_prompt.txt"
EXAMPLE_SCRIPT_FILE = _REPO_ROOT / "example_script.txt"


def _read_system_prompt() -> str:
    if not SYSTEM_PROMPT_FILE.exists():
        raise FileNotFoundError(f"missing system prompt file: {SYSTEM_PROMPT_FILE}")
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")


def make_controller(force_console: bool = False, ohbot_port: str = "COM7") -> ObotController:
    """Pick a controller: real hardware when available, console otherwise.

    Passing ``force_console`` (or running on a machine without the ohbot library) gives
    the hardware-free controller so the whole pipeline — including LLM streaming and the
    microphone path — can be tested without a robot.
    """
    if not force_console:
        try:
            from .robot.controller import HardwareObotController

            return HardwareObotController(port=ohbot_port)
        except Exception as exc:
            print(f"[controller] hardware unavailable ({exc});\n"
                  f"[controller] falling back to the console controller.")

    print("using console controller")
    return ConsoleObotController()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Obot voice pipeline.")
    parser.add_argument(
        "--text",
        help="Run the scripted demo (single shot) with this text and exit.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=24,
        help="Chunk size used by the scripted LLM source.",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Force the hardware-free console controller (no ohbot/servos needed).",
    )
    return parser


async def _scripted_demo(text: str, chunk_size: int, console: bool = False) -> None:
    from .audio.keyboard import KeyListener

    llm_client = ScriptedLLMClient(text, chunk_size=chunk_size)
    controller = make_controller(force_console=console)
    pipeline = RobotPipeline(llm_client=llm_client, controller=controller)

    loop = asyncio.get_running_loop()
    quit_event = asyncio.Event()

    def on_key(ch: str) -> None:
        if ch in ("q", "Q", "\x1b"):
            loop.call_soon_threadsafe(quit_event.set)

    keys = KeyListener(on_key)
    keys.start()
    if keys.available:
        print("Press [q] or [Esc] to stop the demo early.\n")

    pipeline_task = asyncio.create_task(pipeline.run(prompt=text))
    quit_task = asyncio.create_task(quit_event.wait())

    done, pending = await asyncio.wait(
        {pipeline_task, quit_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    keys.stop()

    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    if quit_task in done:
        await controller.stop_speaking()
        print("\n[killswitch] Scripted demo stopped.")

    controller.close()


def _load_example_script() -> str:
    if not EXAMPLE_SCRIPT_FILE.exists():
        raise FileNotFoundError(f"missing example script: {EXAMPLE_SCRIPT_FILE}")
    lines = EXAMPLE_SCRIPT_FILE.read_text(encoding="utf-8").splitlines()
    return " ".join(
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    )


def _prompt_mode() -> str:
    print("Pick a backend:")
    print("  [1] Online (Gemini)")
    print("  [2] Remote Ollama (over SSH)")
    print("  [3] Scripted demo")
    print("  [4] Example script (full capability demo)")
    while True:
        choice = input("> ").strip()
        if choice in {"1", "2", "3", "4"}:
            return choice
        print("Enter 1, 2, 3, or 4.")


_CONTROLS_BANNER = (
    "\nTalk to the bot. While it speaks, just start talking (or press SPACE) to cut it off.\n"
    "Keys:  [SPACE] interrupt   [m] mute/unmute   [p] push-to-talk (press to capture)\n"
    "       [o] open mic (auto)  [c] type instead (console)   [q]/[Esc] quit\n"
    "Stuck? Press [c] to enter console mode, then type 'exit' to force-quit the process.\n"
)

_CONSOLE_BANNER = (
    "\n[console mode] Type a message and press Enter to send.\n"
    "Empty line (or ':voice') returns to the microphone.\n"
    "Type 'exit' or ':quit' to force-quit the program.\n"
)


async def _voice_session(cfg, pipeline: RobotPipeline) -> None:
    """Wire the microphone, interrupt controller and keyboard controls to a pipeline."""
    from .audio.input import (
        AudioInput,
        AudioInputError,
        pick_input_device,
        pick_stt_engine,
    )
    from .audio.keyboard import KeyListener
    from .core.interrupt import InterruptController

    loop = asyncio.get_running_loop()

    #* All the input()-based pickers must run BEFORE the raw-mode key listener starts,
    #* otherwise the listener would swallow the keystrokes the pickers are waiting on.
    try:
        device_index = pick_input_device(cfg.audio.input_device_index)
        backend = pick_stt_engine(cfg.audio.vosk_model_path, default=cfg.audio.stt_engine)
    except AudioInputError as exc:
        print(f"error: {exc}")
        return
    cfg.record_audio(input_device_index=device_index, stt_engine=backend.name)

    try:
        audio = AudioInput(device_index, backend, loop=loop)
    except AudioInputError as exc:
        print(f"error: {exc}")
        return

    interrupt = InterruptController(loop)
    audio.on_barge_in = lambda: interrupt.trigger("voice")
    quit_event = asyncio.Event()
    #* Set by the 'c' key (from the listener thread) to flip into typed console mode.
    console_event = asyncio.Event()

    def on_key(ch: str) -> None:
        if ch == " ":
            #* Only meaningful while the bot is talking; ignore otherwise so we never
            #* arm an interrupt for a turn that hasn't started.
            if audio.is_speaking():
                interrupt.trigger("keyboard")
        elif ch in ("m", "M"):
            audio.set_mode("vad" if audio.mode == "muted" else "muted")
        elif ch in ("p", "P"):
            if audio.mode != "ptt":
                audio.set_mode("ptt")
            audio.trigger_ptt()
        elif ch in ("o", "O"):
            audio.set_mode("vad")
        elif ch in ("c", "C"):
            loop.call_soon_threadsafe(console_event.set)
        elif ch in ("q", "Q", "\x1b"):
            loop.call_soon_threadsafe(quit_event.set)

    keys = KeyListener(on_key)
    audio.start()
    keys.start()
    if not keys.available:
        print("[keys] no interactive terminal detected — keyboard controls disabled "
              "(voice barge-in still works).")

    print(_CONTROLS_BANNER)
    try:
        await _voice_chat_loop(pipeline, audio, interrupt, quit_event, console_event, keys)
    finally:
        keys.stop()
        audio.stop()


async def _run_turn(pipeline: RobotPipeline, audio, interrupt, user_text: str) -> None:
    """Speak one response, allowing barge-in/keyboard interruption."""
    print(f"[you] {user_text}")
    interrupt.clear()
    audio.set_speaking(True)
    try:
        result = await pipeline.run(user_text, interrupt)
    finally:
        audio.set_speaking(False)
    if result.interrupted:
        reason = interrupt.signal.reason if interrupt.signal else "?"
        print(f"[interrupted via {reason}] the bot knows it was cut off and what it "
              f"hadn't said yet.")


async def _voice_chat_loop(
    pipeline: RobotPipeline,
    audio,
    interrupt,
    quit_event: asyncio.Event,
    console_event: asyncio.Event,
    keys,
) -> None:
    loop = asyncio.get_running_loop()
    console_mode = False

    while not quit_event.is_set():
        if console_mode:
            #* Typed input. The raw key listener is stopped while we own stdin, so the
            #* way back to voice is an empty line or ':voice' (the 'c' key can't be read
            #* here). 'audio' is muted so the mic doesn't fire in the background.
            line = await loop.run_in_executor(None, _read_console_line)
            if line is None or line == "" or line.lower() == ":voice":
                console_mode = False
                audio.drain_queue()
                audio.set_mode("vad")
                keys.start()
                print(_CONTROLS_BANNER)
                continue
            if line.lower() in ("exit", "quit", ":quit"):
                print("[killswitch] Exiting.")
                return
            await _run_turn(pipeline, audio, interrupt, line)
            continue

        #* Voice mode: wait for the next utterance, a quit, or a console-toggle.
        utterance_task = asyncio.create_task(audio.next_utterance())
        quit_task = asyncio.create_task(quit_event.wait())
        console_task = asyncio.create_task(console_event.wait())
        done, pending = await asyncio.wait(
            {utterance_task, quit_task, console_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if quit_task in done:
            print()
            return
        if console_task in done:
            console_event.clear()
            console_mode = True
            keys.stop()           # release stdin so input() works
            audio.set_mode("muted")  # don't capture while typing
            print(_CONSOLE_BANNER)
            continue

        user_text = utterance_task.result().strip()
        if user_text.upper() in ("EXIT", "QUIT"):
            print("[killswitch] Exiting.")
            return
        if user_text:
            await _run_turn(pipeline, audio, interrupt, user_text)


def _read_console_line() -> str | None:
    """Blocking stdin read used inside the console-mode executor."""
    try:
        return input("[console] > ")
    except (EOFError, KeyboardInterrupt):
        return None


async def _run_gemini(cfg, console: bool = False) -> None:
    from .llm.gemini import GeminiAPIError, GeminiLLMClient
    from .llm.picker import pick_gemini_model

    try:
        model = await pick_gemini_model(cfg.gemini_api_key, cfg.recent_gemini_models)
    except GeminiAPIError as exc:
        print(f"error: {exc}")
        return
    cfg.record_gemini_model(model)
    controller = make_controller(force_console=console, ohbot_port=cfg.ohbot_port)
    try:
        async with GeminiLLMClient(cfg.gemini_api_key, model, _read_system_prompt()) as client:
            pipeline = RobotPipeline(llm_client=client, controller=controller)
            await _voice_session(cfg, pipeline)
    finally:
        controller.close()


async def _run_ollama(cfg, console: bool = False) -> None:
    from .llm.ollama import OllamaAPIError, OllamaLLMClient
    from .llm.picker import pick_ollama_model
    from .net.ssh_tunnel import SSHTunnelError, open_ollama_tunnel

    try:
        with open_ollama_tunnel(cfg.ollama_ssh) as bound_port:
            base_url = f"http://127.0.0.1:{bound_port}"
            print(f"[ssh] tunnel up on local port {bound_port}")
            try:
                model = await pick_ollama_model(base_url, cfg.recent_ollama_models)
            except OllamaAPIError as exc:
                print(f"error: {exc}")
                return
            cfg.record_ollama_model(model)
            controller = make_controller(force_console=console, ohbot_port=cfg.ohbot_port)
            try:
                async with OllamaLLMClient(base_url, model, _read_system_prompt()) as client:
                    pipeline = RobotPipeline(llm_client=client, controller=controller)
                    await _voice_session(cfg, pipeline)
            finally:
                controller.close()
    except SSHTunnelError as exc:
        print(f"error: {exc}")


async def main() -> None:
    args = build_parser().parse_args()

    if args.text is not None:
        await _scripted_demo(args.text, args.chunk_size, console=args.console)
        return

    from .config import load_config

    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return

    choice = _prompt_mode()
    if choice == "1":
        await _run_gemini(cfg, console=args.console)
    elif choice == "2":
        await _run_ollama(cfg, console=args.console)
    elif choice == "3":
        text = input("Scripted text: ").strip()
        if text:
            await _scripted_demo(text, chunk_size=24, console=args.console)
    else:
        try:
            script = _load_example_script()
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return
        await _scripted_demo(script, chunk_size=24, console=args.console)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
