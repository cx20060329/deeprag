"""BCM-RAG Retrieval — Vector / Keyword Retriever.

TF-IDF inspired keyword retrieval over chunk text.
No external embedding model needed — works with pure numpy.
Replace with BGE-M3 embeddings for production.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np


class KeywordRetriever:
    """Lightweight BM25-style retrieval for development/testing.

    Production path: replace with BGE-M3 + Qdrant/FAISS.
    """

    def __init__(self):
        self.chunks: list[dict] = []
        # Inverted index: term → [(chunk_idx, tf)]
        self.inverted_index: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.doc_freq: dict[str, int] = {}  # term → document frequency
        self.chunk_vectors: dict[int, np.ndarray] = {}  # chunk_idx → sparse vector
        self.avg_doc_len: float = 0.0
        self._loaded = False

    # ---- Load --------------------------------------------------------------

    def load(self, chunks_path: str | Path) -> "KeywordRetriever":
        """Load chunks from JSON and build inverted index."""
        with open(chunks_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.chunks = data.get("text_chunks", [])
        if not self.chunks:
            raise ValueError("No text_chunks found in chunks JSON")

        # Build inverted index with TF-IDF
        doc_lens = []
        for idx, chunk in enumerate(self.chunks):
            text = chunk.get("embedding_text", "") or chunk.get("text", "")
            terms = self._tokenize(text)
            doc_lens.append(len(terms))

            # Term frequencies in this chunk
            tf: dict[str, float] = defaultdict(float)
            for t in terms:
                tf[t] += 1.0

            # Normalize by doc length
            if terms:
                for t, f in tf.items():
                    tf[t] = f / len(terms)

            # Add to inverted index
            for t, f in tf.items():
                self.inverted_index[t].append((idx, f))

            # Document frequency
            for t in set(terms):
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

        self.avg_doc_len = np.mean(doc_lens) if doc_lens else 1.0
        self._loaded = True
        return self

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def stats(self) -> dict:
        return {
            "chunks": len(self.chunks),
            "vocabulary": len(self.inverted_index),
            "avg_doc_len": self.avg_doc_len,
        }

    # ---- Search ------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_module: str = "",
        filter_chunk_type: str = "",
    ) -> list[dict]:
        """Search chunks by keyword relevance.

        Uses BM25-like scoring: TF * IDF / doc_len normalization.

        Args:
            query: Search query text
            top_k: Number of results
            filter_module: Optional module filter
            filter_chunk_type: Optional chunk type filter

        Returns:
            List of {chunk, score, rank} dicts
        """
        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        num_docs = len(self.chunks)
        scores: dict[int, float] = defaultdict(float)

        # BM25 scoring
        k1 = 1.5  # term frequency saturation
        b = 0.75   # length normalization

        for term in query_terms:
            postings = self.inverted_index.get(term, [])
            df = self.doc_freq.get(term, 0)
            if df == 0:
                continue

            # IDF
            idf = np.log(1 + (num_docs - df + 0.5) / (df + 0.5))

            for chunk_idx, tf in postings:
                chunk = self.chunks[chunk_idx]
                doc_text = chunk.get("embedding_text", "") or chunk.get("text", "")
                doc_len = len(self._tokenize(doc_text))

                # BM25 term score
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * doc_len / max(self.avg_doc_len, 1))
                scores[chunk_idx] += idf * numerator / denominator

        # Filter and rank
        results = []
        for chunk_idx, score in scores.items():
            chunk = self.chunks[chunk_idx]

            # Apply filters
            if filter_module and chunk.get("module", "") != filter_module:
                continue
            if filter_chunk_type and chunk.get("chunk_type", "") != filter_chunk_type:
                continue

            results.append({
                "chunk": chunk,
                "score": float(score),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def hybrid_search(
        self,
        text_query: str,
        entity_ids: list[str],
        top_k: int = 10,
    ) -> list[dict]:
        """Hybrid search: text query + entity filter.

        Prioritizes chunks that contain specified entity IDs.
        """
        # Base text search
        results = self.search(text_query, top_k=top_k * 2)

        # Boost chunks containing target entities
        for r in results:
            chunk_entities = set(r["chunk"].get("entities", []))
            entity_match = len(chunk_entities & set(entity_ids))
            r["entity_boost"] = entity_match
            r["score"] = r["score"] * (1 + 0.5 * entity_match)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ---- Helpers -----------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize Chinese + English text into unigrams/bigrams."""
        if not text:
            return []

        # Split on non-alphanumeric, keep Chinese chars + ASCII words
        tokens = []

        # Extract CJK characters as unigrams
        cjk = re.findall(r"[一-鿿]", text)
        tokens.extend(cjk)

        # Extract ASCII words (2+ chars)
        ascii_words = re.findall(r"[A-Za-z0-9_]{2,}", text)
        tokens.extend(w.lower() for w in ascii_words)

        # Also split Chinese into bigrams for better matching
        for i in range(len(cjk) - 1):
            tokens.append(cjk[i] + cjk[i + 1])

        return tokens
