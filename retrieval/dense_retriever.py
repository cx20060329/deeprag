"""BCM-RAG Retrieval — Dense Vector Retriever.

Dense vector search over embedded chunks.
Supports cosine similarity, hybrid (dense + sparse) search, and metadata filtering.

Backend selection via environment:
  BCM_USE_QDRANT=1 → QdrantVectorStore (production)
  (default)        → NumpyVectorStore (development)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from retrieval.embedder import EmbeddingGenerator


class DenseRetriever:
    """Dense vector similarity search over embedded chunks.

    Usage:
        dr = DenseRetriever()
        dr.load("output/content_analysis/vector_points.json")
        results = dr.search("车窗防夹功能", top_k=10)
    """

    def __init__(self, vector_store=None):
        from retrieval.vector_store import create_vector_store

        self._store = vector_store or create_vector_store()
        self.chunks: list[dict] = []  # payload references
        self.model_name: str = ""
        self._loaded = False
        self._embedder: EmbeddingGenerator | None = None

    # ---- Load ----------------------------------------------------------------

    def load(
        self,
        points_path: str | Path = "output/content_analysis/vector_points.json",
        embedder: EmbeddingGenerator | None = None,
        auto_embed: bool = True,
    ) -> "DenseRetriever":
        """Load vector points from JSON.

        Supports two formats:
        1. points format (from build_embeddings): {points: [{id, vector, payload}, ...]}
        2. text_chunks format (from VectorStoreExporter): {text_chunks: [{id, vector: null, payload}, ...]}

        If vectors are null, auto-generate embeddings if auto_embed is True.
        """
        with open(points_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Support both formats
        if "points" in data:
            raw_points = data["points"]
        elif "text_chunks" in data:
            raw_points = data["text_chunks"]
            if "image_chunks" in data:
                raw_points = raw_points + data["image_chunks"]
        else:
            raise ValueError(f"Unknown format in {points_path}: keys={list(data.keys())}")

        self.model_name = data.get("model", "")

        if not raw_points:
            raise ValueError(f"No points found in {points_path}")

        # Check if vectors are present
        has_vectors = all(p.get("vector") for p in raw_points)

        if not has_vectors and auto_embed:
            print("DenseRetriever: vectors are null, auto-generating embeddings...")
            if embedder:
                self._embedder = embedder
            else:
                self._embedder = EmbeddingGenerator()
                self._embedder.load()

            # Generate embeddings for all points
            texts = []
            for p in raw_points:
                payload = p.get("payload", {})
                text = payload.get("text", "") or payload.get("embedding_text", "")
                texts.append(text)

            print(f"  Encoding {len(texts)} texts with {self._embedder.model_name}...")
            embeddings = self._embedder.encode(texts)

            # Build points format for store
            points_for_store = []
            for i, emb in enumerate(embeddings):
                points_for_store.append({
                    "id": raw_points[i].get("id", f"chunk_{i}"),
                    "vector": emb.tolist(),
                    "payload": raw_points[i].get("payload", {}),
                })

            # Write back in points format
            output_data = {
                "model": self._embedder.model_name,
                "dim": self._embedder.dim,
                "count": len(points_for_store),
                "points": points_for_store,
            }
            with open(points_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)

            raw_points = points_for_store
            self.model_name = self._embedder.model_name
        else:
            self._embedder = embedder

        # Load into vector store
        self._store.connect()
        # Write temporary points-format file if needed
        if "points" not in data:
            # Already wrote above in auto_embed path; for pre-embedded, convert
            if has_vectors:
                tmp_data = {
                    "model": self.model_name,
                    "dim": self._store.dim or len(raw_points[0].get("vector", [])),
                    "count": len(raw_points),
                    "points": raw_points,
                }
                tmp_path = Path(points_path).with_suffix(".tmp.json")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(tmp_data, f, ensure_ascii=False, indent=2)
                self._store.load_from_json(tmp_path)
                tmp_path.unlink(missing_ok=True)
            else:
                self._store.load_from_json(points_path)
        else:
            self._store.load_from_json(points_path)

        # Build chunks reference list
        self.chunks = [p.get("payload", {}) for p in raw_points]

        self._loaded = True
        print(f"DenseRetriever: {len(raw_points)} points, {self.dim}-dim")
        return self

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def dim(self) -> int:
        return self._store.dim

    @property
    def stats(self) -> dict:
        return {
            "points": self._store.count(),
            "dim": self._store.dim,
            "model": self.model_name,
        }

    # ---- Search --------------------------------------------------------------

    def _get_query_embedding(self, query: str) -> np.ndarray:
        """Get query embedding, lazy-loading embedder if needed."""
        if self._embedder and self._embedder.is_loaded:
            return self._embedder.encode_query(query)
        if self._embedder is None:
            self._embedder = EmbeddingGenerator()
            self._embedder.load()
        return self._embedder.encode_query(query)

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_module: str = "",
        filter_chunk_type: str = "",
        min_score: float = 0.0,
    ) -> list[dict]:
        """Dense vector search using cosine similarity.

        Args:
            query: Search query text
            top_k: Number of results
            filter_module: Optional module filter
            filter_chunk_type: Optional chunk type filter
            min_score: Minimum similarity threshold

        Returns:
            List of {chunk, score} dicts sorted by descending score
        """
        if not self._loaded:
            return []

        query_vec = self._get_query_embedding(query)

        store_results = self._store.search(
            query_vec,
            top_k=top_k,
            filter_module=filter_module or None,
            filter_type=filter_chunk_type or None,
        )

        results = []
        for sr in store_results:
            if sr["score"] < min_score:
                continue
            results.append({
                "chunk": sr["payload"],
                "score": sr["score"],
                "source": "dense",
            })

        return results

    def hybrid_search(
        self,
        text_query: str,
        entity_ids: list[str],
        top_k: int = 10,
        dense_weight: float = 0.7,
    ) -> list[dict]:
        """Hybrid search: dense vectors + entity boost.

        Args:
            text_query: Search query
            entity_ids: Entity IDs from graph retrieval for boosting
            top_k: Number of results
            dense_weight: Weight of dense score vs entity boost

        Returns:
            List of {chunk, score} dicts
        """
        # Dense search (get 2x results for re-ranking)
        results = self.search(text_query, top_k=top_k * 2, min_score=0.1)

        # Boost chunks containing graph-matched entities
        entity_set = set(entity_ids)
        for r in results:
            chunk_entities = set(r["chunk"].get("entities", []))
            entity_match = len(chunk_entities & entity_set)

            if entity_match > 0:
                entity_boost = min(entity_match * 0.15, 0.5)  # cap at 0.5
                r["entity_boost"] = entity_match
                r["score"] = (
                    dense_weight * r["score"]
                    + (1 - dense_weight) * entity_boost
                )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def batch_search(
        self,
        queries: list[str],
        top_k: int = 10,
    ) -> list[list[dict]]:
        """Batch search for multiple queries."""
        return [self.search(q, top_k=top_k) for q in queries]
