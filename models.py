from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal["sentence", "action", "status", "emotion", "delay"]


@dataclass(frozen=True)
class StreamChunk:
    """A small piece of raw text streamed from the LLM."""

    text: str
    source: str = "llm"


@dataclass(frozen=True)
class PipelineEvent:
    """A normalized event passed between pipeline stages."""

    kind: EventKind
    payload: str
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineEvent":
        return cls(
            kind=data["kind"],
            payload=data["payload"],
            metadata=dict(data.get("metadata", {})),
        )
