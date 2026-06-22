"""BCM-RAG Content Analysis — Data Models.

All data models for entities, relationships, sections, chunks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Entity Types
# ---------------------------------------------------------------------------

class EntityType(Enum):
    MODULE = "module"
    STATE = "state"
    SIGNAL = "signal"
    FUNCTION = "function"
    PARAMETER = "parameter"
    FAULT = "fault"
    CAN_MESSAGE = "can_message"
    HARDWARE_PIN = "hardware_pin"


class RelType(Enum):
    BELONGS_TO = "belongs_to"
    TRANSITION_TO = "transition_to"
    TRIGGERED_BY = "triggered_by"
    DEPENDS_ON = "depends_on"
    CONTROLS = "controls"
    OUTPUTS = "outputs"
    REQUIRES = "requires"
    CONFIGURES = "configures"
    REPORTS = "reports"
    REFERENCES = "references"


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    entity_id: str
    entity_type: EntityType
    name: str
    module: str = ""
    section_path: str = ""
    properties: dict = field(default_factory=dict)
    source_item_index: int = -1  # index in content_list


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------

@dataclass
class Relationship:
    source_id: str
    target_id: str
    rel_type: RelType
    properties: dict = field(default_factory=dict)
    weight: float = 1.0  # 0.0–1.0, for retrieval ranking. BELONGS_TO=0.1, table-extracted=0.8


# ---------------------------------------------------------------------------
# Section Tree
# ---------------------------------------------------------------------------

@dataclass
class SectionNode:
    section_id: str
    title: str
    level: int           # 1-5
    number: str          # e.g. "3.3.4.2"
    parent_id: str | None
    children: list[str] = field(default_factory=list)
    item_range: tuple[int, int] = (0, 0)  # (start_idx, end_idx) in flattened content_list
    entities: list[str] = field(default_factory=list)  # entity IDs
    chunk_ids: list[str] = field(default_factory=list)
    # Page reference: physical page number(s) in the original document.
    # -1 means unknown. For multi-page sections, this is the first page.
    page: int = -1
    page_range: tuple[int, int] = (-1, -1)  # (first_page, last_page)
    # Table ownership: indices of table items belonging to this section
    table_indices: list[int] = field(default_factory=list)
    table_count: int = 0


@dataclass
class SectionTree:
    root_id: str = "root"
    nodes: dict[str, SectionNode] = field(default_factory=dict)
    number_index: dict[str, str] = field(default_factory=dict)  # "3.3.4" → node_id
    # Page-to-section mapping
    page_index: dict[int, list[str]] = field(default_factory=dict)  # page_no → [section_ids]
    # Table index lookup
    table_owner: dict[int, str] = field(default_factory=dict)  # flat_item_index → section_id
    # Parser integration
    source_parser: str = ""
    total_pages: int = 0


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

@dataclass
class TextChunk:
    chunk_id: str
    chunk_type: str          # "state_transition", "signal_table", "function_desc", etc.
    text: str
    embedding_text: str       # text optimized for embedding
    module: str
    section_path: str
    section_title: str
    entities: list[str] = field(default_factory=list)   # entity IDs in this chunk
    signals: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    has_table: bool = False
    has_image: bool = False
    image_refs: list[dict] = field(default_factory=list)  # [{storage_path, description, image_type}]
    source_indices: list[int] = field(default_factory=list)  # content_list indices
    token_count: int = 0
    # Parser integration
    page: int = -1            # physical page number (-1 unknown)
    source_parser: str = ""   # "docling" | "mineru"
    created_at: str = ""


@dataclass
class ImageChunk:
    chunk_id: str
    image_path: str
    caption: str = ""
    description: str = ""     # filled by VLM later
    embedding_text: str = ""  # description text for embedding
    module: str = ""
    section_path: str = ""
    section_title: str = ""
    source_index: int = -1
    token_count: int = 0
    page: int = -1
    source_parser: str = ""
    created_at: str = ""


@dataclass
class ChunkList:
    text_chunks: list[TextChunk] = field(default_factory=list)
    image_chunks: list[ImageChunk] = field(default_factory=list)

    @property
    def all_chunks(self) -> list[TextChunk | ImageChunk]:
        return self.text_chunks + self.image_chunks


# ---------------------------------------------------------------------------
# Pipeline metadata
# ---------------------------------------------------------------------------

@dataclass
class PipelineMeta:
    """Metadata about a pipeline run — source, timings, parser info."""
    source_file: str = ""          # original document path
    parser_name: str = ""          # "docling" | "mineru"
    parser_warnings: list[str] = field(default_factory=list)
    parse_time_seconds: float = 0.0
    total_pages: int = 0
    total_items: int = 0
    pipeline_version: str = "3.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PipelineOutput:
    """Complete pipeline output — everything produced by a pipeline run."""
    meta: PipelineMeta = field(default_factory=PipelineMeta)
    tree: SectionTree | None = None
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    chunks: ChunkList = field(default_factory=ChunkList)
    vlm_results: list[dict] = field(default_factory=list)
