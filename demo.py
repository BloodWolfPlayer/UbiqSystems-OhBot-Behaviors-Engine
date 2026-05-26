from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

#* Only the lightweight imports live at the top level. The Gemini, Ollama and SSH modules
#* are imported only inside the functions that need them, 
#* so the scripted demo runs even when httpx, sshtunnel and paramiko are not installed.
from controller import DemoObotController
from llm_client import ScriptedLLMClient
from orchestrator import RobotPipeline

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.txt"


def _read_system_prompt() -> str:
    if not SYSTEM_PROMPT_FILE.exists():
        raise FileNotFoundError(f"missing system prompt file: {SYSTEM_PROMPT_FILE}")
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")


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
    return parser


async def _scripted_demo(text: str, chunk_size: int) -> None:
    llm_client = ScriptedLLMClient(text, chunk_size=chunk_size)
    controller = DemoObotController()
    pipeline = RobotPipeline(llm_client=llm_client, controller=controller)
    await pipeline.run(prompt=text)


def _prompt_mode() -> str:
    print("Pick a backend:")
    print("  [1] Online (Gemini)")
    print("  [2] Remote Ollama (over SSH)")
    print("  [3] Scripted demo")
    while True:
        choice = input("> ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        print("Enter 1, 2, or 3.")


async def _chat_loop(pipeline: RobotPipeline) -> None:
    print("\nType a message (empty line or :quit to exit).")
    loop = asyncio.get_running_loop()
    while True:
        try:
            #* input() blocks the event loop. Running it in the default executor keeps
            #* the rest of the async code (HTTP streams, SSH tunnel) responsive while
            #* we wait on stdin.
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            return
        line = line.strip()
        if not line or line == ":quit":
            return
        await pipeline.run(prompt=line)


async def _run_gemini(cfg) -> None:
    from gemini_client import GeminiAPIError, GeminiLLMClient
    from model_picker import pick_gemini_model

    try:
        model = await pick_gemini_model(cfg.gemini_api_key, cfg.recent_gemini_models)
    except GeminiAPIError as exc:
        print(f"error: {exc}")
        return
    cfg.record_gemini_model(model)
    controller = DemoObotController()
    async with GeminiLLMClient(cfg.gemini_api_key, model, _read_system_prompt()) as client:
        pipeline = RobotPipeline(llm_client=client, controller=controller)
        await _chat_loop(pipeline)


async def _run_ollama(cfg) -> None:
    from model_picker import pick_ollama_model
    from ollama_client import OllamaAPIError, OllamaLLMClient
    from ssh_tunnel import SSHTunnelError, open_ollama_tunnel

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
            controller = DemoObotController()
            async with OllamaLLMClient(base_url, model, _read_system_prompt()) as client:
                pipeline = RobotPipeline(llm_client=client, controller=controller)
                await _chat_loop(pipeline)
    except SSHTunnelError as exc:
        print(f"error: {exc}")


async def main() -> None:
    args = build_parser().parse_args()

    if args.text is not None:
        await _scripted_demo(args.text, args.chunk_size)
        return

    from config import load_config

    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return

    choice = _prompt_mode()
    if choice == "1":
        await _run_gemini(cfg)
    elif choice == "2":
        await _run_ollama(cfg)
    else:
        text = input("Scripted text: ").strip()
        if text:
            await _scripted_demo(text, chunk_size=24)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
