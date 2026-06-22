"""BCM-RAG Retrieval — Graph Retriever.

Loads KG JSON into NetworkX (or Neo4j) for graph traversal queries.
Supports: entity lookup, 1-hop/2-hop neighbor expansion, dependency chain tracing.

Backend selection via environment:
  BCM_USE_NEO4J=1  → Neo4jGraphStore (production)
  (default)        → NetworkXGraphStore (development)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import networkx as nx


class GraphRetriever:
    """Knowledge graph retriever with pluggable backend.

    Usage:
        gr = GraphRetriever()
        gr.load("output/content_analysis/knowledge_graph.json")
        neighbors = gr.expand("signal_VMM_PEPS_UsageMode", hops=2)
    """

    def __init__(self, store=None):
        # Lazy-import to avoid circular dependency
        from retrieval.graph_store import create_graph_store, NetworkXGraphStore

        self._store = store or create_graph_store()
        self._loaded = False

    # ---- Load --------------------------------------------------------------

    def load(self, kg_path: str | Path) -> "GraphRetriever":
        """Load knowledge graph from JSON export."""
        self._store.connect()
        self._store.load_from_json(kg_path)
        self._loaded = True
        return self

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def stats(self) -> dict:
        st = self._store.stats
        return {
            "nodes": st.get("nodes", 0),
            "edges": st.get("edges", 0),
        }

    # ---- Search ------------------------------------------------------------

    def search_entities(self, query: str, entity_type: str = "") -> list[dict]:
        """Find entities by name substring match."""
        results = self._store.search_entities(query)
        if entity_type:
            results = [r for r in results if r.get("entity_type") == entity_type]
        return results

    def get_entity(self, entity_id: str) -> dict | None:
        """Get entity by ID."""
        return self._store.get_entity(entity_id)

    def get_by_name(self, name: str, entity_type: str = "") -> list[dict]:
        """Get entities by exact name."""
        return self._store.get_by_name(name, entity_type or None)

    # ---- Graph Traversal ---------------------------------------------------

    def expand(
        self,
        entity_id: str,
        hops: int = 1,
        rel_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """Expand from an entity, returning neighbor entities.

        Args:
            entity_id: Starting entity ID
            hops: Number of hops (1 or 2 recommended)
            rel_types: Filter by relationship types (None = all)
            direction: "out", "in", or "both"

        Returns:
            List of {entity, relationship, distance} dicts
        """
        raw = self._store.expand([entity_id], hops=hops)
        results = []
        for item in raw:
            ent = item.get("entity", {})
            rel = item.get("relation", "")
            if rel_types and rel not in rel_types:
                continue
            results.append({
                "entity": ent,
                "relationship": rel,
                "properties": {},
                "weight": 1.0,
                "distance": item.get("distance", 1),
            })
        return results

    def trace_dependency_chain(
        self, entity_id: str, max_depth: int = 5,
    ) -> list[list[dict]]:
        """Trace dependency chains from an entity.

        Follows DEPENDS_ON, REQUIRES, TRIGGERED_BY, CONTROLS edges.
        Returns list of chains (each chain is a list of {entity, rel_type}).
        """
        raw = self._store.trace_dependency_chain(entity_id, max_depth)
        # Group by path
        chains: dict[tuple, list[dict]] = {}
        for item in raw:
            path_key = tuple(item.get("path", []))
            if path_key not in chains:
                chains[path_key] = []
            chains[path_key].append({
                "entity": item.get("entity", {}),
                "rel_type": item.get("relation", ""),
                "weight": 1.0,
            })
        return list(chains.values())

    def get_subgraph(
        self, entity_ids: list[str], expand_hops: int = 0,
    ) -> dict:
        """Get a subgraph containing specified entities and their neighbors."""
        return self._store.get_subgraph(entity_ids)
