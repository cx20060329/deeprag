"""DeepRAG — Unified LLM Backend Library.

Provides a single abstraction over all LLM providers used in the system.
All LLM calls throughout DeepRAG go through this module.

Quick start:
    from llm import LLMFactory

    # Auto-detect from environment
    backend = LLMFactory.from_env()

    # Or specify provider
    backend = LLMFactory.create("deepseek")

    # Use it
    from llm.types import Message
    resp = backend.chat([Message("user", "Hello")])
    print(resp.content)
"""

from .base import LLMBackend
from .config import LLMConfig
from .factory import LLMFactory
from .openai_compat import OpenAICompatBackend
from .types import LLMRequest, LLMResponse, LLMUsage, Message

__all__ = [
    "LLMBackend",
    "LLMConfig",
    "LLMFactory",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "Message",
    "OpenAICompatBackend",
]
