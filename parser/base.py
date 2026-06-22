"""BCM-RAG Parser — Abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from parser.models import ParseResult


class AbstractParser(ABC):
    """Abstract parser interface.

    Each parser backend implements this interface,
    producing a unified ParseResult.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return parser name: 'docling', 'mineru', etc."""
        ...

    @abstractmethod
    def parse(self, input_path: str | Path, output_dir: str | None = None) -> ParseResult:
        """Parse a document and return unified ParseResult.

        Args:
            input_path: Path to input document (.docx, .pdf, etc.)
            output_dir: Output directory (auto-generated if None)

        Returns:
            ParseResult with content_list, markdown_text, and stats.

        Raises:
            FileNotFoundError: if input file doesn't exist.
            ValueError: if format is unsupported.
            RuntimeError: on parsing failure.
        """
        ...

    @classmethod
    @abstractmethod
    def supports_format(cls, suffix: str) -> bool:
        """Check whether this parser supports the given file extension."""
        ...

    @classmethod
    def is_available(cls) -> bool:
        """Check whether this parser's dependencies are installed."""
        return True
