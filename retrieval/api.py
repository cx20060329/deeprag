"""DeepRAG — Retrieval API facade.

Public API for the 9-stage retrieval pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from domain.config import DomainConfig


class RetrievalAPI:
    """Public API for document retrieval.

    Usage:
        api = RetrievalAPI(domain=domain, data_dir="output/PA2A")
        api.load()
        results = api.search("GlobalClose的触发条件是什么？")
    """

    def __init__(
        self,
        domain: "DomainConfig | None" = None,
        data_dir: str | Path | None = None,
    ):
        self._domain = domain
        self._data_dir = Path(data_dir) if data_dir else None
        self._pipeline = None

    def load(self) -> dict:
        """Load all retrieval indices (KG, vectors, BM25, section tree).

        Returns:
            Dict with stats: graph_nodes, graph_edges, chunks, vocabulary, etc.
        """
        from retrieval.pipeline import RetrievalPipeline
        self._pipeline = RetrievalPipeline(
            data_dir=str(self._data_dir) if self._data_dir else None,
            domain=self._domain,
        )
        return self._pipeline.load()

    def search(self, query: str, top_k: int = 10) -> dict:
        """Execute the full 9-stage retrieval pipeline.

        Args:
            query: User query string.
            top_k: Number of results to return.

        Returns:
            SearchResponse with merged results, evidence, and optional LLM answer.
        """
        self._ensure_loaded()
        return self._pipeline.search(query, top_k=top_k)

    def search_stream(self, query: str) -> AsyncIterator[str]:
        """Stream LLM answer tokens via SSE.

        Args:
            query: User query string.

        Yields:
            SSE-formatted text chunks.
        """
        self._ensure_loaded()
        return self._pipeline.search_stream(query)

    def get_modules(self) -> list[str]:
        """List all modules in the knowledge graph."""
        self._ensure_loaded()
        return self._pipeline.get_modules()

    def search_entities(self, q: str, entity_type: str | None = None) -> list[dict]:
        """Search entities in the knowledge graph.

        Args:
            q: Search query.
            entity_type: Optional filter by entity type.

        Returns:
            List of matching entity dicts.
        """
        self._ensure_loaded()
        return self._pipeline.search_entities(q, entity_type=entity_type)

    def get_stats(self) -> dict:
        """Return current pipeline statistics."""
        self._ensure_loaded()
        return self._pipeline.get_stats()

    @property
    def pipeline(self):
        """Access the underlying RetrievalPipeline (advanced use)."""
        self._ensure_loaded()
        return self._pipeline

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self):
        if self._pipeline is None:
            self.load()
