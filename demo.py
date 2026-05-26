from __future__ import annotations

import argparse
import asyncio

from controller import DemoObotController
from llm_client import ScriptedLLMClient
from orchestrator import RobotPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Obot voice pipeline skeleton.")
    parser.add_argument(
        "--text",
        required=True,
        help="Finished text to replay as if it came from the LLM.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=24,
        help="Chunk size used by the scripted LLM source.",
    )
    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    llm_client = ScriptedLLMClient(args.text, chunk_size=args.chunk_size)
    controller = DemoObotController()
    pipeline = RobotPipeline(llm_client=llm_client, controller=controller)
    await pipeline.run(prompt=args.text)


if __name__ == "__main__":
    asyncio.run(main())
