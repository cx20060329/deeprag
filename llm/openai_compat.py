"""DeepRAG — OpenAI-compatible LLM backend.

Supports any API that follows the OpenAI chat completions format:
- DeepSeek (api.deepseek.com)
- Zhipu GLM (open.bigmodel.cn)
- Ark / Doubao (ark.cn-beijing.volces.com)
- OpenAI (api.openai.com)
- Any self-hosted vLLM / Ollama / etc.
"""

from __future__ import annotations

from typing import Iterator

from .base import LLMBackend
from .config import LLMConfig
from .types import LLMResponse, LLMUsage, Message


class OpenAICompatBackend(LLMBackend):
    """OpenAI-compatible chat completion backend.

    Usage:
        config = LLMConfig(provider="deepseek", api_key="sk-xxx")
        backend = OpenAICompatBackend(config)
        resp = backend.chat([Message("user", "Hello")])
    """

    def __init__(self, config: LLMConfig):
        config.resolve()
        self._config = config
        self._client = None

    @property
    def client(self):
        """Lazy-init the OpenAI client."""
        if self._client is None:
            from openai import OpenAI

            kwargs = {
                "api_key": self._config.api_key,
                "base_url": self._config.base_url,
                "timeout": self._config.timeout,
            }
            if self._config.extra_headers:
                kwargs["default_headers"] = self._config.extra_headers

            self._client = OpenAI(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request."""
        max_tokens = max_tokens or self._config.max_tokens
        temperature = temperature if temperature is not None else self._config.temperature

        api_messages = [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self.client.chat.completions.create(
                model=self._config.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        except Exception as exc:
            return LLMResponse(
                content="",
                model=self._config.model,
                error=str(exc),
            )

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model or self._config.model,
            usage=LLMUsage(
                prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                completion_tokens=response.usage.completion_tokens if response.usage else 0,
                total_tokens=response.usage.total_tokens if response.usage else 0,
            ),
            finish_reason=choice.finish_reason or "stop",
            raw=response,
        )

    def chat_stream(
        self,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs,
    ) -> Iterator[str]:
        """Stream chat completion tokens."""
        max_tokens = max_tokens or self._config.max_tokens
        temperature = temperature if temperature is not None else self._config.temperature

        api_messages = [{"role": m.role, "content": m.content} for m in messages]

        try:
            stream = self.client.chat.completions.create(
                model=self._config.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                **kwargs,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            yield f"[LLM Error] {exc}"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._config.model

    @property
    def provider(self) -> str:
        return self._config.provider

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def config(self) -> LLMConfig:
        """Access the underlying config (read-only)."""
        return self._config
