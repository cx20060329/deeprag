"""BCM-RAG Parser — Unified data models.

ParseResult: unified output from any parser backend
StructuredDocumentModel: intermediate document representation
  (before conversion to RagAnything content_list)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Structured Document Model — intermediate representation
# ---------------------------------------------------------------------------

@dataclass
class StructuredDocumentModel:
    """Intermediate document representation.

    This is the canonical intermediate format between parser output
    and downstream analysis (section tree, entity extraction, chunking).

    It preserves the document structure independently of the parser backend.
    """
    # Document metadata
    title: str = ""
    source_path: str = ""
    parser_name: str = ""

    # Pages: dict[page_number, PageContent]
    pages: dict[int, "PageContent"] = field(default_factory=dict)

    # Flat content list (RagAnything-compatible)
    content_list: list[dict] = field(default_factory=list)

    # Raw markdown (for reference)
    markdown_text: str = ""

    # Statistics
    total_pages: int = 0
    image_count: int = 0
    table_count: int = 0
    total_chars: int = 0

    @property
    def flat_items(self) -> list[dict]:
        """Return flat list of all content items (unwrapped from pages)."""
        if not self.content_list:
            return []
        if isinstance(self.content_list, list) and len(self.content_list) > 0:
            if isinstance(self.content_list[0], list):
                # Page-wrapped: [[page0_items], [page1_items], ...]
                items = []
                for page in self.content_list:
                    items.extend(page)
                return items
        return self.content_list


@dataclass
class PageContent:
    """Single page content."""
    page_number: int
    width: float = 0.0
    height: float = 0.0
    items: list[dict] = field(default_factory=list)
    image_path: str = ""  # page snapshot if available


# ---------------------------------------------------------------------------
# Unified Parse Result
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Unified parse result from any parser backend.

    Compatible with existing mineru-based code while adding Docling support.
    """
    # Identification
    source_file: str
    source_size_bytes: int
    parser_name: str = "unknown"

    # Output paths
    output_dir: str = ""
    markdown_path: str = ""
    content_list_path: str = ""
    images_dir: str = ""

    # Content
    markdown_text: str = ""
    content_list: list = field(default_factory=list)

    # Stats
    image_count: int = 0
    table_count: int = 0
    total_chars: int = 0
    parse_time_seconds: float = 0.0

    # Structured intermediate model (Docling path only)
    structured_doc: StructuredDocumentModel | None = None

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: list[str] = field(default_factory=list)

    @property
    def flat_items(self) -> list[dict]:
        """Return flat list of all content items (unwrapped from pages)."""
        if not self.content_list:
            return []
        if isinstance(self.content_list, list) and len(self.content_list) > 0:
            if isinstance(self.content_list[0], list):
                items = []
                for page in self.content_list:
                    items.extend(page)
                return items
        return self.content_list
