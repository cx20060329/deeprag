"""BCM-RAG — Neo4j connection configuration.

Reads from environment variables, with sensible defaults for local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Neo4jConfig:
    """Neo4j connection parameters.

    Environment variables:
        NEO4J_URI      — Bolt URI (default: bolt://localhost:7687)
        NEO4J_USER     — Username (default: neo4j)
        NEO4J_PASSWORD — Password (default: password)
        NEO4J_DATABASE — Database name (default: neo4j)
    """

    uri: str = field(default_factory=lambda: os.environ.get(
        "NEO4J_URI", "bolt://localhost:7687",
    ))
    user: str = field(default_factory=lambda: os.environ.get(
        "NEO4J_USER", "neo4j",
    ))
    password: str = field(default_factory=lambda: os.environ.get(
        "NEO4J_PASSWORD", "password",
    ))
    database: str = field(default_factory=lambda: os.environ.get(
        "NEO4J_DATABASE", "neo4j",
    ))
    max_connection_lifetime: int = 3600
    max_connection_pool_size: int = 10
    connection_acquisition_timeout: float = 10.0

    @property
    def enabled(self) -> bool:
        """Whether Neo4j is explicitly enabled via env var."""
        return os.environ.get("BCM_USE_NEO4J", "").lower() in ("1", "true", "yes")

    @classmethod
    def from_env(cls) -> "Neo4jConfig":
        return cls()
