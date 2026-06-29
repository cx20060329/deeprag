"""DeepRAG — LLM Backend configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class LLMConfig:
    """Configuration for an LLM backend.

    Usage:
        config = LLMConfig(
            provider="deepseek",
            api_key="sk-xxx",
            model="deepseek-chat",
        )
        backend = OpenAICompatBackend(config)
    """

    provider: str = ""
    """Provider identifier: 'deepseek', 'zhipu', 'ark', 'openai', etc."""

    api_key: str = ""
    """API key for the provider."""

    base_url: str = ""
    """API base URL. If empty, inferred from provider preset."""

    model: str = ""
    """Model name. If empty, inferred from provider preset."""

    max_tokens: int = 2048
    """Default max tokens per request."""

    temperature: float = 0.1
    """Default sampling temperature."""

    timeout: float = 60.0
    """HTTP request timeout in seconds."""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """Additional HTTP headers."""

    # --- Known provider presets (class-level, not per-instance) ---

    PROVIDERS: ClassVar[dict[str, dict[str, str]]] = {
        "ark": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-vision-pro-32k",
        },
        "zhipu": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model": "glm-4-flash",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
        },
    }

    def resolve(self) -> LLMConfig:
        """Fill in base_url and model from provider preset if not set.

        Returns self for chaining.
        """
        if self.provider and self.provider in self.PROVIDERS:
            preset = self.PROVIDERS[self.provider]
            if not self.base_url:
                self.base_url = preset["base_url"]
            if not self.model:
                self.model = preset["model"]
        return self
