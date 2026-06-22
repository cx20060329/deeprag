"""BCM-RAG Reranking — Cross-Encoder based semantic reranking.

Replaces Jaccard similarity with BGE-Reranker Cross-Encoder for Stage 6.
Falls back to Jaccard if model is unavailable.

Architecture:
  Stage 6 input: merged candidates (typically 20 items)
  Stage 6 output: reranked candidates with semantic scores

Models (tried in order):
  1. BAAI/bge-reranker-v2-m3 — best multilingual, cross-encoder (preferred)
  2. BAAI/bge-reranker-base — lighter, cross-encoder
  3. Jaccard similarity — always available lexical fallback
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

# Project-local model cache directory (relative to this file)
_LOCAL_MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "BAAI"


def _resolve_model_path(name: str) -> str | None:
    """Resolve model name to local path if cached in project models/ dir.

    Returns the local path if available and valid, None otherwise.
    """
    model_slug = name.split("/")[-1]
    local_dir = _LOCAL_MODELS_DIR / model_slug
    if not local_dir.is_dir():
        return None
    if not (local_dir / "config.json").exists():
        return None
    # Check for weight files
    has_weights = (
        (local_dir / "model.safetensors").exists() or
        (local_dir / "pytorch_model.bin").exists()
    )
    if has_weights:
        return str(local_dir)
    return None


class CrossEncoderReranker:
    """Cross-Encoder based semantic reranker.

    Usage:
        reranker = CrossEncoderReranker()
        reranker.load()
        candidates = reranker.rerank(query, candidates)

        # Or with fallback guaranteed:
        candidates = reranker.rerank_safe(query, candidates)
    """

    # Model preference order: Cross-Encoder first
    CROSS_ENCODER_MODELS = [
        "BAAI/bge-reranker-v2-m3",
        "BAAI/bge-reranker-base",
    ]

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name
        self._model = None
        self._loaded = False
        self._rerank_mode = "jaccard"  # cross_encoder | jaccard

    # ---- Load ----------------------------------------------------------------

    def load(self, model_name: str | None = None) -> "CrossEncoderReranker":
        """Load the best available reranker.

        Tries in order:
        1. Cross-Encoder (bge-reranker-v2-m3) — from local cache or HF Hub
        2. Cross-Encoder (bge-reranker-base) — lighter alternative
        3. Jaccard fallback — always available

        Setting LOCAL_FILES_ONLY=1 in environment forces local-only loading.
        """
        local_only = os.environ.get("LOCAL_FILES_ONLY", "") == "1"

        # Try Cross-Encoder models
        candidates = [model_name] if model_name else self.CROSS_ENCODER_MODELS
        for name in candidates:
            # Check local models/ directory first
            local_path = _resolve_model_path(name)

            paths_to_try: list[tuple[str, bool]] = []
            if local_path:
                paths_to_try.append((local_path, True))
                paths_to_try.append((name, local_only))
            else:
                paths_to_try.append((name, local_only))

            for path, lo in paths_to_try:
                try:
                    from sentence_transformers import CrossEncoder
                    self._model = CrossEncoder(
                        path, device="cpu",
                        local_files_only=lo,
                    )
                    self.model_name = name
                    self._loaded = True
                    self._rerank_mode = "cross_encoder"
                    source = "local" if path == local_path else "HF"
                    print(f"CrossEncoderReranker: {name} [{source}]")
                    return self
                except Exception:
                    continue

        # Fallback: Jaccard (complementary lexical signal)
        print("CrossEncoderReranker: Jaccard fallback (no Cross-Encoder available)")
        self._loaded = False
        self._rerank_mode = "jaccard"
        return self

    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._model is not None

    # ---- Rerank --------------------------------------------------------------

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int | None = None,
    ) -> list[dict]:
        """Rerank candidates using best available method.

        Cross-Encoder > Bi-Encoder (semantic cosine) > Jaccard
        """
        if not candidates:
            return candidates

        if self._rerank_mode == "cross_encoder":
            return self._cross_encoder_rerank(query, candidates, top_k)
        elif self._rerank_mode == "bi_encoder":
            return self._bi_encoder_rerank(query, candidates, top_k)
        else:
            return self._jaccard_rerank(query, candidates, top_k)

    def _cross_encoder_rerank(
        self, query: str, candidates: list[dict], top_k: int | None,
    ) -> list[dict]:
        """Cross-Encoder: score (query, chunk_text) pairs directly."""
        pairs = []
        for entry in candidates:
            chunk = entry.get("chunk", {})
            text = chunk.get("embedding_text", "") or chunk.get("text", "")
            pairs.append((query, text[:2000]))

        try:
            scores = self._model.predict(pairs, show_progress_bar=False)
        except Exception as e:
            print(f"CrossEncoder predict failed ({e}), falling back to Bi-Encoder")
            return self._bi_encoder_rerank(query, candidates, top_k)

        for i, entry in enumerate(candidates):
            semantic = float(scores[i]) if i < len(scores) else 0.0
            if semantic > 1.0:
                semantic = 1.0 / (1.0 + np.exp(-semantic))
            entry["semantic_score"] = min(max(semantic, 0.0), 1.0)
            entry["score"] = 0.6 * entry["semantic_score"] + 0.4 * entry["score"]

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k] if top_k else candidates

    def _bi_encoder_rerank(
        self, query: str, candidates: list[dict], top_k: int | None,
    ) -> list[dict]:
        """Bi-Encoder: cosine similarity between query embedding and chunk embeddings.

        Uses the cached BGE model. Query gets the instruction prefix for retrieval.
        Chunk embeddings are computed on-the-fly (cached per session).
        """
        # Encode query once
        query_embedding = self._model.encode(
            [f"为这个句子生成表示以用于检索相关文章：{query}"],
            normalize_embeddings=True,
        )[0]

        # Encode chunk texts in batch
        texts = []
        for entry in candidates:
            chunk = entry.get("chunk", {})
            text = chunk.get("embedding_text", "") or chunk.get("text", "")
            texts.append(text[:2000])

        chunk_embeddings = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False,
        )

        # Cosine similarity (vectors are normalized, so dot product = cosine)
        similarities = np.dot(chunk_embeddings, query_embedding)

        for i, entry in enumerate(candidates):
            semantic = float(similarities[i])
            entry["semantic_score"] = (semantic + 1.0) / 2.0  # [-1,1] → [0,1]
            # Blend: 50% semantic + 50% original (bi-encoder is weaker than cross-encoder)
            entry["score"] = 0.5 * entry["semantic_score"] + 0.5 * entry["score"]

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k] if top_k else candidates

    def rerank_safe(self, query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
        """Rerank with guaranteed Jaccard fallback."""
        try:
            return self.rerank(query, candidates, top_k)
        except Exception as e:
            print(f"CrossEncoderReranker: error ({e}), using Jaccard fallback")
            return self._jaccard_rerank(query, candidates, top_k)

    # ---- Jaccard Fallback ----------------------------------------------------

    def _jaccard_rerank(
        self, query: str, candidates: list[dict], top_k: int | None = None,
    ) -> list[dict]:
        """Jaccard similarity reranker (fallback)."""
        from retrieval.vector_retriever import KeywordRetriever
        query_terms = set(KeywordRetriever._tokenize(query))

        for entry in candidates:
            text = entry["chunk"].get("embedding_text", "") or entry["chunk"].get("text", "")
            chunk_terms = set(KeywordRetriever._tokenize(text))

            if query_terms and chunk_terms:
                overlap = len(query_terms & chunk_terms)
                union = len(query_terms | chunk_terms)
                jaccard = overlap / union if union > 0 else 0
                entry["semantic_score"] = jaccard
                entry["score"] = entry["score"] * (0.5 + 0.5 * jaccard)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if top_k:
            return candidates[:top_k]
        return candidates


# ---------------------------------------------------------------------------
# Integration with Pipeline
# ---------------------------------------------------------------------------

def integrate_reranker(pipeline) -> bool:
    """Integrate Cross-Encoder reranker into the pipeline.

    Replaces pipeline._rerank_semantic with Cross-Encoder version.

    Args:
        pipeline: A loaded RetrievalPipeline instance

    Returns:
        True if Cross-Encoder loaded successfully, False if using Jaccard
    """
    reranker = CrossEncoderReranker()
    reranker.load()

    # Monkey-patch the semantic rerank method
    original_rerank = pipeline._rerank_semantic

    def cross_encoder_rerank(candidates, query):
        return reranker.rerank_safe(query, candidates)

    pipeline._rerank_semantic = cross_encoder_rerank
    pipeline._reranker = reranker  # Store for later access

    status = "Cross-Encoder" if reranker.is_loaded else "Jaccard fallback"
    print(f"Pipeline reranker: {status}")
    return reranker.is_loaded
