"""DeepRAG — LLM Backend abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from .types import LLMRequest, LLMResponse, Message


class LLMBackend(ABC):
    """Abstract base class for all LLM backends.

    All LLM calls in DeepRAG go through this interface, allowing
    any OpenAI-compatible or custom backend to be plugged in.

    Usage:
        backend = OpenAICompatBackend(config)
        resp = backend.chat([
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Hello"),
        ])
    """

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of Message objects (system, user, assistant).
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature (0.0 = deterministic).
            **kwargs: Backend-specific extra parameters.

        Returns:
            LLMResponse with content, model, usage, and optional error.
        """
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs,
    ) -> Iterator[str]:
        """Stream chat completion tokens.

        Args:
            messages: List of Message objects.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.
            **kwargs: Backend-specific extra parameters.

        Yields:
            Text chunks as they arrive from the API.
        """
        ...

    def chat_with_request(self, request: LLMRequest) -> LLMResponse:
        """Convenience: chat using an LLMRequest object."""
        return self.chat(
            messages=request.messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            **request.extra,
        )

    def chat_stream_with_request(self, request: LLMRequest) -> Iterator[str]:
        """Convenience: stream chat using an LLMRequest object."""
        return self.chat_stream(
            messages=request.messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            **request.extra,
        )

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name used by this backend."""
        ...

    @property
    @abstractmethod
    def provider(self) -> str:
        """Return the provider identifier."""
        ...
