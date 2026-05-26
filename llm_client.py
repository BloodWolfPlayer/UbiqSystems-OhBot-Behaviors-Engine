from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class ScriptedLLMClient:
    """Development-only source that replays a finished string as streamed output."""

    def __init__(self, response_text: str, chunk_size: int = 24) -> None:
        self.response_text = response_text
        self.chunk_size = max(1, chunk_size)

    async def stream_response(self, prompt: str) -> AsyncIterator[str]:
        del prompt

        for index in range(0, len(self.response_text), self.chunk_size):
            await asyncio.sleep(0.1)
            yield self.response_text[index : index + self.chunk_size]
