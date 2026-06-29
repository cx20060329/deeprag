"""DeepRAG — LLM Backend type definitions.

Shared types used by all LLM backend implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Message role types
Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    """A single chat message."""

    role: Role
    content: str


@dataclass
class LLMRequest:
    """Request to send to an LLM backend."""

    messages: list[Message]
    max_tokens: int = 2048
    temperature: float = 0.1
    top_p: float = 1.0
    stop: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMUsage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Response from an LLM backend."""

    content: str
    model: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = "stop"
    raw: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
