"""BCM-RAG Parser — Fallback / auto-detection logic.

Provides:
  - create_parser(name) → AbstractParser
  - parse_document(path, parser) → ParseResult
"""

from __future__ import annotations

import sys
from pathlib import Path
from parser.base import AbstractParser
from parser.models import ParseResult


def _check_docling() -> bool:
    """Check if Docling is installed and importable."""
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        return True
    except ImportError:
        return False


def _check_mineru() -> bool:
    """Check if MinerU (magic-pdf) is installed and importable."""
    try:
        from mineru.backend.office.docx_analyze import office_docx_analyze  # noqa: F401
        return True
    except ImportError:
        return False


def _list_available() -> list[str]:
    """List all available parser backends."""
    available = []
    if _check_docling():
        available.append("docling")
    if _check_mineru():
        available.append("mineru")
    return available


def create_parser(name: str = "auto") -> AbstractParser:
    """Create a parser instance by name.

    Args:
        name: Parser name — "auto", "docling", "mineru", or "fallback".
              "auto" tries Docling first, then MinerU.

    Returns:
        AbstractParser instance.

    Raises:
        RuntimeError: if no parser backend is available.
    """
    name = name.lower().strip()

    if name == "auto":
        available = _list_available()
        if not available:
            raise RuntimeError(
                "No parser backend available. Install docling or magic-pdf (mineru)."
            )

        # Prefer Docling as primary
        if "docling" in available:
            from parser.docling_parser import DoclingParser
            # If both are available, use Docling with MinerU fallback
            if "mineru" in available:
                from parser.mineru_parser import MinerUParser
                return DoclingParser(fallback=MinerUParser())
            return DoclingParser()

        if "mineru" in available:
            from parser.mineru_parser import MinerUParser
            return MinerUParser()

    elif name == "docling":
        if not _check_docling():
            raise RuntimeError("Docling is not installed. Run: pip install docling")
        from parser.docling_parser import DoclingParser
        return DoclingParser()

    elif name == "mineru":
        if not _check_mineru():
            raise RuntimeError(
                "MinerU is not installed. Run: pip install magic-pdf"
            )
        from parser.mineru_parser import MinerUParser
        return MinerUParser()

    elif name == "fallback":
        primary = None
        if _check_docling():
            from parser.docling_parser import DoclingParser
            primary = DoclingParser()
        if _check_mineru():
            from parser.mineru_parser import MinerUParser
            if primary:
                primary.fallback = MinerUParser()
            else:
                primary = MinerUParser()
        if primary is None:
            raise RuntimeError("No parser backend available.")
        return primary

    else:
        raise ValueError(
            f"Unknown parser: '{name}'. Choices: auto, docling, mineru, fallback"
        )


def parse_document(
    input_path: str | Path,
    output_dir: str | None = None,
    parser: str = "auto",
) -> ParseResult:
    """Parse a document with automatic backend selection.

    This is the main entry point for document parsing.

    Args:
        input_path: Path to input document (.docx, .pdf, etc.)
        output_dir: Output directory (auto-generated if None)
        parser: Parser name — "auto", "docling", "mineru", or "fallback"

    Returns:
        ParseResult with content_list, markdown_text, and stats.

    Example:
        result = parse_document("data/spec.docx")
        print(f"Parsed {result.total_chars} chars with {result.parser_name}")
        content_list = result.content_list  # Ready for SectionTreeBuilder
    """
    parser_instance = create_parser(parser)
    return parser_instance.parse(input_path, output_dir)
