"""DeepRAG — Parser API facade.

Public API for document parsing. Auto-selects between Docling and MinerU
backends based on file type.
"""

from __future__ import annotations

from pathlib import Path

from parser.fallback import create_parser_for_file, parse_document
from parser.models import ParseResult


class ParserAPI:
    """Public API for document parsing.

    Usage:
        api = ParserAPI()
        result = api.parse("document.pdf")
        results = api.parse_batch(["doc1.pdf", "doc2.docx"])
    """

    def parse(self, file_path: str | Path) -> ParseResult:
        """Parse a single document file.

        Auto-selects the best parser backend:
        - PDF files → MinerU (better Chinese support)
        - DOCX/PPTX/HTML/MD → Docling

        Args:
            file_path: Path to the document file.

        Returns:
            ParseResult with content_list, markdown_text, images_dir, etc.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the format is unsupported.
        """
        return parse_document(str(file_path))

    def parse_batch(self, file_paths: list[str]) -> list[ParseResult]:
        """Parse multiple documents.

        Args:
            file_paths: List of file paths.

        Returns:
            List of ParseResult objects (one per file).
        """
        return [self.parse(p) for p in file_paths]

    def supported_formats(self) -> list[str]:
        """Return list of supported file extensions."""
        return [".pdf", ".docx", ".pptx", ".html", ".md", ".txt"]

    @staticmethod
    def create_parser(file_path: str):
        """Factory: get the appropriate parser for a file.

        Args:
            file_path: Path to the document.

        Returns:
            An AbstractParser instance (DoclingParser or MinerUParser).
        """
        return create_parser_for_file(file_path)
