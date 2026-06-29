"""DeepRAG — LLM Backend factory.

Creates LLMBackend instances from configuration or environment variables.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .config import LLMConfig

if TYPE_CHECKING:
    from .base import LLMBackend


# Env var → provider mapping for auto-detection
_PROVIDER_ENV_VARS: dict[str, str] = {
    "DEEPSEEK_API_KEY": "deepseek",
    "ZHIPU_API_KEY": "zhipu",
    "ARK_API_KEY": "ark",
    "OPENAI_API_KEY": "openai",
}


class LLMFactory:
    """Factory for creating LLMBackend instances.

    Usage:
        # From provider name (auto-reads API key from env)
        backend = LLMFactory.create("deepseek")

        # With explicit config
        config = LLMConfig(provider="deepseek", api_key="sk-xxx")
        backend = LLMFactory.create_from_config(config)

        # Auto-detect from environment
        backend = LLMFactory.from_env()
    """

    @staticmethod
    def create(
        provider: str = "",
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        timeout: float = 60.0,
    ) -> "LLMBackend":
        """Create an LLM backend from parameters.

        If api_key is not provided, reads from environment variables
        specific to the provider.

        Args:
            provider: Provider name (deepseek, zhipu, ark, openai, etc.).
            api_key: API key. Auto-detected from env if not set.
            model: Model name. Uses provider default if not set.
            base_url: API base URL. Uses provider default if not set.
            max_tokens: Default max tokens.
            temperature: Default temperature.
            timeout: HTTP timeout in seconds.

        Returns:
            An LLMBackend instance (typically OpenAICompatBackend).

        Raises:
            ValueError: If no API key can be found.
        """
        # Auto-detect provider from env vars if not specified
        if not provider:
            provider = LLMFactory._detect_provider()

        # Resolve API key
        if not api_key:
            api_key = LLMFactory._resolve_api_key(provider)
        if not api_key:
            raise ValueError(
                "No API key found. Set one of: "
                + ", ".join(_PROVIDER_ENV_VARS.keys())
                + " environment variables, or pass api_key=."
            )

        config = LLMConfig(
            provider=provider,
            api_key=api_key,
            model=model or "",
            base_url=base_url or "",
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

        return LLMFactory.create_from_config(config)

    @staticmethod
    def create_from_config(config: LLMConfig) -> "LLMBackend":
        """Create an LLM backend from an LLMConfig.

        Args:
            config: LLMConfig with provider, api_key, etc.

        Returns:
            An LLMBackend instance.
        """
        config.resolve()

        # Currently all providers use OpenAI-compatible API
        from .openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(config)

    @staticmethod
    def from_env() -> "LLMBackend":
        """Auto-detect the best available LLM backend from environment.

        Checks for API keys in order: DEEPSEEK, ZHIPU, ARK, OPENAI.
        Uses the first one found.

        Also reads DEEPRAG_LLM_PROVIDER env var for explicit selection.

        Returns:
            An LLMBackend instance.

        Raises:
            ValueError: If no API key is found in environment.
        """
        provider = os.getenv("DEEPRAG_LLM_PROVIDER", "")
        if provider:
            return LLMFactory.create(provider=provider)

        return LLMFactory.create()

    @staticmethod
    def list_providers() -> list[str]:
        """Return the list of known provider names."""
        return list(LLMConfig.PROVIDERS.keys())

    @staticmethod
    def list_available_providers() -> list[str]:
        """Return providers that have API keys set in the environment."""
        available = []
        for env_var, provider in _PROVIDER_ENV_VARS.items():
            if os.getenv(env_var):
                available.append(provider)
        return available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_provider() -> str:
        """Auto-detect which provider to use based on available env vars."""
        # Check explicit config first
        explicit = os.getenv("DEEPRAG_LLM_PROVIDER", "")
        if explicit:
            return explicit

        # Check each provider's API key
        for env_var, provider in _PROVIDER_ENV_VARS.items():
            if os.getenv(env_var):
                return provider

        return ""

    @staticmethod
    def _resolve_api_key(provider: str) -> str:
        """Resolve API key for a provider from environment variables.

        Provider-specific keys take priority over the generic OPENAI_API_KEY.
        """
        provider_key_map: dict[str, list[str]] = {
            "deepseek": ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"],
            "zhipu": ["ZHIPU_API_KEY", "OPENAI_API_KEY"],
            "ark": ["ARK_API_KEY", "OPENAI_API_KEY"],
            "openai": ["OPENAI_API_KEY"],
        }

        env_vars = provider_key_map.get(provider, ["OPENAI_API_KEY"])
        for var in env_vars:
            key = os.getenv(var, "")
            if key:
                return key

        # Fallback: try any known key
        for var in _PROVIDER_ENV_VARS:
            key = os.getenv(var, "")
            if key:
                return key

        return ""
