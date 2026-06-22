"""BCM-RAG Content Analysis — Vector Store Exporter.

Exports text and image chunks to Qdrant-compatible format.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from content_analysis.models import TextChunk, ImageChunk, ChunkList


class VectorStoreExporter:
    """Export chunks for Qdrant vector storage."""

    def export_text_chunks(self, chunks: list[TextChunk]) -> list[dict]:
        """Export text chunks as Qdrant point dicts.

        Each point has:
        - id: chunk_id (string UUID or hash)
        - vector: embedding (filled by embedding model)
        - payload: metadata for filtering + image paths for retrieval
        """
        points = []
        for c in chunks:
            # Extract image storage paths (NOT image bytes — object storage refs)
            image_paths = [
                ref.get("storage_path", "") for ref in c.image_refs
                if ref.get("storage_path")
            ]
            points.append({
                "id": c.chunk_id,
                "vector": None,  # filled by embedding pipeline
                "payload": {
                    "chunk_id": c.chunk_id,
                    "chunk_type": c.chunk_type,
                    "module": c.module,
                    "section_path": c.section_path,
                    "section_title": c.section_title,
                    "text": c.text[:5000],  # truncated for payload
                    "embedding_text": c.embedding_text[:5000],
                    "entities": c.entities,
                    "signals": c.signals,
                    "states": c.states,
                    "parameters": c.parameters,
                    "has_table": c.has_table,
                    "has_image": c.has_image,
                    "image_paths": image_paths,  # object storage paths, not blob data
                    "token_count": c.token_count,
                },
            })
        return points

    def export_image_chunks(self, chunks: list[ImageChunk]) -> list[dict]:
        """Export image chunks as Qdrant point dicts."""
        points = []
        for c in chunks:
            points.append({
                "id": c.chunk_id,
                "vector": None,  # filled by embedding pipeline
                "payload": {
                    "chunk_id": c.chunk_id,
                    "chunk_type": "image",
                    "module": c.module,
                    "section_path": c.section_path,
                    "section_title": c.section_title,
                    "image_path": c.image_path,
                    "caption": c.caption,
                    "description": c.description,
                    "embedding_text": c.embedding_text[:5000],
                    "token_count": c.token_count,
                },
            })
        return points

    def export_qdrant_collection_schema(self, collection_name: str = "bcm_chunks") -> dict:
        """Generate Qdrant collection schema.

        Uses dense + sparse hybrid search:
        - dense: BGE-M3 (1024d) for semantic matching
        - sparse: BGE-M3 sparse vector for keyword matching
        """
        return {
            "collection_name": collection_name,
            "vectors": {
                "dense": {
                    "size": 1024,
                    "distance": "Cosine",
                },
            },
            "sparse_vectors": {
                "sparse": {},
            },
            "optimizers_config": {
                "default_segment_number": 2,
            },
            "hnsw_config": {
                "m": 16,
                "ef_construct": 200,
            },
            "quantization_config": None,
        }

    def export_all(
        self, chunks: ChunkList,
    ) -> dict:
        """Export everything as a single JSON structure."""
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "text_chunks": self.export_text_chunks(chunks.text_chunks),
            "image_chunks": self.export_image_chunks(chunks.image_chunks),
            "stats": {
                "total_text_chunks": len(chunks.text_chunks),
                "total_image_chunks": len(chunks.image_chunks),
                "total_chunks": len(chunks.text_chunks) + len(chunks.image_chunks),
            },
        }
