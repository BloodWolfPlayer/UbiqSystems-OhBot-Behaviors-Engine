from __future__ import annotations

import re
from dataclasses import dataclass

from models import PipelineEvent

TOKEN_RE = re.compile(r"\[[^\[\]]+\]|[.!?]")


@dataclass
class StreamProcessor:
    """Converts raw LLM text into sentence and action events."""

    strip_action_tags: bool = True

    def __post_init__(self) -> None:
        self._buffer = ""
        self._sentence_parts: list[str] = []
        self._pending_actions: list[str] = []

    def _normalize_sentence(self, sentence: str) -> str:
        return re.sub(r"\s+", " ", sentence).strip()

    def feed(self, text: str) -> list[PipelineEvent]:
        self._buffer += text
        events: list[PipelineEvent] = []
        position = 0

        while True:
            match = TOKEN_RE.search(self._buffer, position)
            if match is None:
                break

            part_before = self._buffer[position:match.start()]
            self._sentence_parts.append(part_before)
            token = match.group(0)
            position = match.end()

            if token.startswith("[") and token.endswith("]"):
                action_name = token[1:-1].strip()
                if action_name:
                    # record char index within the sentence so we can sync actions
                    current_len = len("".join(self._sentence_parts))
                    self._pending_actions.append(f"{action_name}@{current_len}")
                continue

            self._sentence_parts.append(token)
            sentence = self._normalize_sentence("".join(self._sentence_parts))
            actions = list(self._pending_actions)
            self._pending_actions.clear()
            self._sentence_parts.clear()
            if sentence:
                metadata = {}
                if actions:
                    metadata["actions"] = ",".join(actions)
                events.append(PipelineEvent(kind="sentence", payload=sentence, metadata=metadata))

        remaining = self._buffer[position:]
        last_open = remaining.rfind("[")
        last_close = remaining.rfind("]")

        if last_open > last_close:
            self._sentence_parts.append(remaining[:last_open])
            # keep any incomplete action token in the buffer
            self._buffer = remaining[last_open:]
        else:
            self._sentence_parts.append(remaining)
            self._buffer = ""

        return events

    def flush(self) -> list[PipelineEvent]:
        if self._buffer:
            clean_tail = re.sub(r"\[[^\]]*$", "", self._buffer)
            self._sentence_parts.append(clean_tail)
            self._buffer = ""

        sentence = self._normalize_sentence("".join(self._sentence_parts))
        actions = list(self._pending_actions)
        self._pending_actions.clear()
        self._sentence_parts.clear()

        if not sentence:
            return []

        metadata = {}
        if actions:
            metadata["actions"] = ",".join(actions)

        return [PipelineEvent(kind="sentence", payload=sentence, metadata=metadata)]
