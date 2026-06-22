"""BCM-RAG Content Analysis Layer.

Architecture (RagAnything-inspired):

    content_list.json (1,757 items)
            │
    ┌───────┴────────┐
    │                │
    ▼                ▼
    Text Pipeline    Multimodal Pipeline
    (title/para/     (images)
     table/list)
    │                │
    ├─ SectionTree   ├─ ImageAnalyzer
    ├─ EntityExtract │
    ├─ ChunkBuilder  ├─ ImageChunk
    ├─ Embedding     ├─ Embedding
    │                │
    └──────┬─────────┘
           │
           ▼
    ┌──────────────────┐
    │  Knowledge Graph  │  Neo4j (shared)
    ├──────────────────┤
    │  Chunk List       │  JSON
    ├──────────────────┤
    │  Vector Store     │  Qdrant
    └──────────────────┘

Text items → structured entities + chunks + embeddings
Image items → visual descriptions + chunks + embeddings
Both share the same Knowledge Graph and Vector Store.
"""

from content_analysis.models import (
    Entity, EntityType, Relationship, RelType,
    SectionNode, SectionTree,
    TextChunk, ImageChunk, ChunkList,
)
from content_analysis.section_tree import SectionTreeBuilder
from content_analysis.entity_extractor import EntityExtractor
from content_analysis.chunk_builder import ChunkBuilder
from content_analysis.kg_exporter import KnowledgeGraphExporter
from content_analysis.vector_exporter import VectorStoreExporter
from content_analysis.pipeline import ContentAnalysisPipeline

__all__ = [
    "Entity", "EntityType", "Relationship", "RelType",
    "SectionNode", "SectionTree",
    "TextChunk", "ImageChunk", "ChunkList",
    "SectionTreeBuilder",
    "EntityExtractor",
    "ChunkBuilder",
    "KnowledgeGraphExporter",
    "VectorStoreExporter",
    "ContentAnalysisPipeline",
]
