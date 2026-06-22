"""BCM-RAG — Vector Store abstraction layer.

Provides pluggable backends for vector search:
  - NumpyVectorStore: in-memory cosine similarity (default, zero-dependency)
  - QdrantVectorStore: persistent, production-grade

Usage:
    store = create_vector_store()        # auto-detect from env
    store = NumpyVectorStore()           # explicit in-memory
    store = QdrantVectorStore(config)    # explicit Qdrant
    store.connect()
    store.load_from_json("vector_points.json")
    results = store.search(query_vector, top_k=20)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Abstract Base
# ---------------------------------------------------------------------------

class BaseVectorStore(ABC):
    """Abstract interface for vector storage and search."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def load_from_json(self, path: str | Path) -> int:
        """Load vector points from vector_points.json. Returns number of points."""
        ...

    @abstractmethod
    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_module: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        """Search for nearest neighbors. Returns [{id, score, payload}, ...]."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Return total number of stored vectors."""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """Return vector dimension."""
        ...


# ---------------------------------------------------------------------------
# Numpy Backend (in-memory, default)
# ---------------------------------------------------------------------------

class NumpyVectorStore(BaseVectorStore):
    """In-memory vector store using NumPy dot-product cosine similarity.

    Suitable for development and small-to-medium datasets (<100K vectors).
    """

    def __init__(self):
        self._vectors: np.ndarray | None = None
        self._points: list[dict] = []
        self._model_name: str = ""
        self._vector_dim: int = 0

    def connect(self) -> bool:
        return True

    def load_from_json(self, path: str | Path) -> int:
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._model_name = data.get("model", "")
        self._vector_dim = data.get("dim", 0)

        # Support both "points" and "text_chunks" formats
        if "points" in data:
            points = data["points"]
        elif "text_chunks" in data:
            points = data["text_chunks"]
            if "image_chunks" in data:
                points = points + data["image_chunks"]
        else:
            points = []

        vectors = []
        for pt in points:
            vec = pt.get("vector")
            if vec is not None:
                vectors.append(np.asarray(vec, dtype=np.float32))
            elif self._vector_dim and self._vector_dim > 0:
                vectors.append(np.zeros(self._vector_dim, dtype=np.float32))

        if vectors:
            self._vectors = np.stack(vectors)
            if self._vector_dim == 0:
                self._vector_dim = self._vectors.shape[1]
        else:
            self._vectors = np.zeros((0, self._vector_dim)) if self._vector_dim else None

        self._points = points
        return len(points)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_module: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        if self._vectors is None or len(self._vectors) == 0:
            return []

        # Cosine similarity (vectors are L2-normalized)
        scores = np.dot(self._vectors, query_vector)

        # Build results
        results = []
        for i, score in enumerate(scores):
            pt = self._points[i]
            payload = pt.get("payload", {})

            # Apply filters
            if filter_module and payload.get("module") != filter_module:
                continue
            if filter_type and payload.get("chunk_type") != filter_type:
                continue

            results.append({
                "id": pt.get("id", f"chunk_{i}"),
                "score": float(score),
                "payload": payload,
            })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def count(self) -> int:
        return len(self._points)

    @property
    def dim(self) -> int:
        return self._vector_dim


# ---------------------------------------------------------------------------
# Qdrant Backend (persistent, production)
# ---------------------------------------------------------------------------

class QdrantVectorStore(BaseVectorStore):
    """Qdrant-backed vector store for production deployment.

    Requires: pip install qdrant-client
    Requires: running Qdrant instance (docker or cloud)
    """

    def __init__(self, config=None):
        from retrieval.qdrant_config import QdrantConfig
        self.config = config or QdrantConfig.from_env()
        self._client = None
        self._vector_dim: int = 0

    def connect(self) -> bool:
        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(
                url=self.config.url,
                api_key=self.config.api_key,
            )
            # Verify connection
            self._client.get_collections()
            print(f"QdrantVectorStore: connected to {self.config.url}")
            return True
        except ImportError:
            print("QdrantVectorStore: qdrant-client not installed. Run: pip install qdrant-client")
            return False
        except Exception as e:
            print(f"QdrantVectorStore: connection failed ({e})")
            return False

    def load_from_json(self, path: str | Path) -> int:
        """Import vector points to Qdrant."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._vector_dim = data.get("dim", 1024)
        points = data.get("points", [])

        if not self._client:
            if not self.connect():
                return 0

        from qdrant_client.models import (
            Distance, VectorParams, PointStruct, OptimizersConfigDiff,
        )

        # Create collection if not exists
        collections = [c.name for c in self._client.get_collections().collections]
        if self.config.collection_name not in collections:
            self._client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config=VectorParams(
                    size=self._vector_dim,
                    distance=Distance.COSINE,
                ),
                optimizers_config=OptimizersConfigDiff(
                    default_segment_number=2,
                ),
            )
            print(f"QdrantVectorStore: created collection '{self.config.collection_name}'")

        # Batch upsert points
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            qdrant_points = []
            for pt in batch:
                vec = pt.get("vector")
                if vec is None:
                    continue
                qdrant_points.append(PointStruct(
                    id=pt.get("id", i),
                    vector=vec,
                    payload=pt.get("payload", {}),
                ))
            if qdrant_points:
                self._client.upsert(
                    collection_name=self.config.collection_name,
                    points=qdrant_points,
                )

        print(f"QdrantVectorStore: loaded {len(points)} points")
        return len(points)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_module: str | None = None,
        filter_type: str | None = None,
    ) -> list[dict]:
        if not self._client:
            return []

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        query_filter = None
        conditions = []
        if filter_module:
            conditions.append(FieldCondition(
                key="module", match=MatchValue(value=filter_module),
            ))
        if filter_type:
            conditions.append(FieldCondition(
                key="chunk_type", match=MatchValue(value=filter_type),
            ))
        if conditions:
            query_filter = Filter(must=conditions)

        try:
            results = self._client.search(
                collection_name=self.config.collection_name,
                query_vector=query_vector.tolist(),
                limit=top_k,
                query_filter=query_filter,
            )
            return [
                {
                    "id": r.id,
                    "score": r.score,
                    "payload": r.payload or {},
                }
                for r in results
            ]
        except Exception as e:
            print(f"QdrantVectorStore: search failed ({e})")
            return []

    def count(self) -> int:
        if not self._client:
            return 0
        try:
            info = self._client.get_collection(self.config.collection_name)
            return info.points_count
        except Exception:
            return 0

    @property
    def dim(self) -> int:
        return self._vector_dim


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_vector_store() -> BaseVectorStore:
    """Create vector store backend based on environment configuration.

    Set BCM_USE_QDRANT=1 to use Qdrant, otherwise defaults to Numpy.
    """
    from retrieval.qdrant_config import QdrantConfig

    config = QdrantConfig.from_env()
    if config.enabled:
        store = QdrantVectorStore(config)
        if store.connect():
            return store
        print("WARNING: Qdrant connection failed, falling back to Numpy")

    return NumpyVectorStore()
