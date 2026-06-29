"""BCM-RAG Retrieval — Embedding Generator.

Generates dense vector embeddings for chunks using BGE models.
Primary: BAAI/bge-m3 (1024-dim, multilingual).
Fallbacks: BAAI/bge-small-zh-v1.5 (512-dim), BAAI/bge-base-zh-v1.5 (768-dim).

Models are cached in the standard HuggingFace hub cache (~/.cache/huggingface/).
Project-local models/ directory is checked first if available.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np


# Project-local model cache directory (relative to this file)
_LOCAL_MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "BAAI"


class EmbeddingGenerator:
    """BGE embedding generator using sentence-transformers.

    Usage:
        gen = EmbeddingGenerator()
        gen.load()
        embeddings = gen.encode(["text1", "text2", ...])

        # Or use as a context manager:
        with EmbeddingGenerator() as gen:
            embeddings = gen.encode(texts)
    """

    # Model priority: BGE-M3 first (1024-dim, multilingual, best quality)
    MODELS = [
        "BAAI/bge-m3",             # 1024-dim, multilingual, best quality
        "BAAI/bge-small-zh-v1.5",  # 512-dim, fast
        "BAAI/bge-base-zh-v1.5",   # 768-dim
    ]

    def __init__(self, model_name: str | None = None, device: str = "auto"):
        self.model_name = model_name
        self.device = self._detect_device() if device == "auto" else device
        self._model = None
        self._dim: int = 0

    @staticmethod
    def _detect_device() -> str:
        """Auto-detect best available device: cuda > mps > cpu."""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _resolve_local_path(self, name: str) -> str | None:
        """Check if model exists in project-local models/ directory.

        Returns the local path if available and valid, None otherwise.
        """
        model_slug = name.split("/")[-1]
        local_dir = _LOCAL_MODELS_DIR / model_slug
        if not local_dir.is_dir():
            return None
        # Check for weight files (pytorch_model.bin or model.safetensors)
        has_weights = (
            (local_dir / "model.safetensors").exists() or
            (local_dir / "pytorch_model.bin").exists()
        )
        if (local_dir / "config.json").exists() and has_weights:
            return str(local_dir)
        return None

    def load(self) -> "EmbeddingGenerator":
        """Load the best available embedding model.

        Tries in order:
        1. Project-local models/ directory (fast, offline)
        2. HF Hub cache / download (standard sentence-transformers behavior)
        3. Falls through MODELS list on failure

        Setting LOCAL_FILES_ONLY=1 forces offline-only loading.
        """
        from sentence_transformers import SentenceTransformer

        if self.model_name:
            candidates = [self.model_name]
        else:
            candidates = self.MODELS

        force_local = os.environ.get("LOCAL_FILES_ONLY", "") == "1"

        last_err = None
        for name in candidates:
            # Try local path first if available
            local_path = self._resolve_local_path(name)

            paths_to_try: list[tuple[str, bool]] = []
            if local_path:
                paths_to_try.append((local_path, True))
                paths_to_try.append((name, force_local))
            else:
                paths_to_try.append((name, force_local))

            for path, local_only in paths_to_try:
                try:
                    self._model = SentenceTransformer(
                        path,
                        device=self.device,
                        local_files_only=local_only,
                    )
                    self._dim = self._model.get_embedding_dimension() \
                        if hasattr(self._model, 'get_embedding_dimension') \
                        else self._model.get_sentence_embedding_dimension()
                    self.model_name = name
                    src = "local" if path == local_path else "HF"
                    print(f"EmbeddingGenerator: loaded {name} ({self._dim}-dim) [{src}]")
                    return self
                except Exception as e:
                    last_err = e
                    continue

        raise RuntimeError(
            f"Failed to load any embedding model. Last error: {last_err}"
        )

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def encode(
        self,
        texts: list[str],
        batch_size: int = 32,
        show_progress: bool = True,
        normalize: bool = True,
    ) -> np.ndarray:
        """Encode texts to embeddings.

        Args:
            texts: List of text strings to encode
            batch_size: Encoding batch size
            show_progress: Show progress bar
            normalize: L2-normalize embeddings (recommended for cosine similarity)

        Returns:
            numpy array of shape (len(texts), dim)
        """
        if not self._model:
            raise RuntimeError("Model not loaded. Call .load() first.")

        # BGE models expect "为这个句子生成表示以用于检索相关文章：" prefix for queries
        # But for document chunks, we use the text as-is
        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=normalize,
        )
        return np.asarray(embeddings, dtype=np.float32)

    def encode_query(self, query: str) -> np.ndarray:
        """Encode a query with the BGE instruction prefix."""
        if not self._model:
            raise RuntimeError("Model not loaded. Call .load() first.")

        # BGE instruction prefix for retrieval
        prefixed = f"为这个句子生成表示以用于检索相关文章：{query}"
        embedding = self._model.encode(
            [prefixed],
            normalize_embeddings=True,
        )
        return np.asarray(embedding[0], dtype=np.float32)

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, *args):
        del self._model
        self._model = None


def build_embeddings(
    chunks_path: str | Path | None = None,
    output_path: str | Path | None = None,
    model_name: str | None = None,
) -> Path:
    """Build embeddings for all chunks and save to vector_points.json.

    Args:
        chunks_path: Path to chunks.json (defaults to CONTENT_ANALYSIS_DIR/chunks.json)
        output_path: Output path for vector_points.json (defaults to CONTENT_ANALYSIS_DIR/vector_points.json)
        model_name: Override model (default: auto-select)

    Returns:
        Path to the saved vector_points.json
    """
    from config import CONTENT_ANALYSIS_DIR
    if chunks_path is None:
        chunks_path = CONTENT_ANALYSIS_DIR / "chunks.json"
    if output_path is None:
        output_path = CONTENT_ANALYSIS_DIR / "vector_points.json"
    chunks_path = Path(chunks_path)
    output_path = Path(output_path)

    # Load chunks
    with open(chunks_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("text_chunks", [])
    if not chunks:
        raise ValueError(f"No text_chunks found in {chunks_path}")

    # Prepare embedding texts
    texts = []
    for chunk in chunks:
        # Use embedding_text (cleaned) or fallback to text
        text = chunk.get("embedding_text", "") or chunk.get("text", "")
        texts.append(text)

    print(f"Generating embeddings for {len(texts)} chunks...")

    # Generate embeddings
    with EmbeddingGenerator(model_name=model_name) as gen:
        embeddings = gen.encode(texts)

    print(f"  Model: {gen.model_name}")
    print(f"  Dim:   {gen.dim}")
    print(f"  Shape: {embeddings.shape}")

    # Build vector points in Qdrant-compatible format
    points = []
    for i, chunk in enumerate(chunks):
        points.append({
            "id": chunk.get("chunk_id", f"chunk_{i}"),
            "vector": embeddings[i].tolist(),
            "payload": {
                "chunk_id": chunk.get("chunk_id", ""),
                "chunk_type": chunk.get("chunk_type", ""),
                "module": chunk.get("module", ""),
                "section_path": chunk.get("section_path", ""),
                "section_title": chunk.get("section_title", ""),
                "text": chunk.get("text", ""),
                "embedding_text": chunk.get("embedding_text", ""),
                "token_count": chunk.get("token_count", 0),
                "has_table": chunk.get("has_table", False),
                "has_image": chunk.get("has_image", False),
                "has_image_refs": chunk.get("image_refs", []),
                "signals": chunk.get("signals", [])[:10],
                "states": chunk.get("states", [])[:10],
                "parameters": chunk.get("parameters", [])[:10],
                "entities": chunk.get("entities", [])[:20],
            },
        })

    output_data = {
        "model": gen.model_name,
        "dim": gen.dim,
        "count": len(points),
        "points": points,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  Saved: {output_path} ({len(points)} points)")
    return output_path


if __name__ == "__main__":
    import sys
    from config import CONTENT_ANALYSIS_DIR
    output = build_embeddings(
        chunks_path=sys.argv[1] if len(sys.argv) > 1 else CONTENT_ANALYSIS_DIR / "chunks.json",
        output_path=sys.argv[2] if len(sys.argv) > 2 else CONTENT_ANALYSIS_DIR / "vector_points.json",
    )
    print(f"Done: {output}")
