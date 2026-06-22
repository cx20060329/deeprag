"""BCM-RAG — Qdrant connection configuration.

Reads from environment variables, with sensible defaults for local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class QdrantConfig:
    """Qdrant connection parameters.

    Environment variables:
        QDRANT_URL     — Qdrant server URL (default: http://localhost:6333)
        QDRANT_API_KEY — Optional API key for cloud deployments
        QDRANT_COLLECTION — Collection name (default: bcm_chunks)
    """

    url: str = field(default_factory=lambda: os.environ.get(
        "QDRANT_URL", "http://localhost:6333",
    ))
    api_key: str | None = field(default_factory=lambda: os.environ.get(
        "QDRANT_API_KEY",
    ) or None)
    collection_name: str = field(default_factory=lambda: os.environ.get(
        "QDRANT_COLLECTION", "bcm_chunks",
    ))
    vector_size: int = 1024  # BGE-M3 default; overridden at runtime
    distance: str = "Cosine"

    @property
    def enabled(self) -> bool:
        """Whether Qdrant is explicitly enabled via env var."""
        return os.environ.get("BCM_USE_QDRANT", "").lower() in ("1", "true", "yes")

    @classmethod
    def from_env(cls) -> "QdrantConfig":
        return cls()
