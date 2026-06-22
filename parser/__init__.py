"""BCM-RAG Parser Layer — Multi-backend document parsing.

Primary:   Docling (structured document understanding)
Fallback:  MinerU  (magic-pdf based .docx analysis)

Usage:
    from parser import parse_document, ParseResult

    result = parse_document("input.docx", parser="auto")
    # result.content_list   — RagAnything-compatible list
    # result.markdown_text  — full markdown
    # result.structured_doc — DoclingDocument (when Docling is used)
"""

from parser.base import AbstractParser
from parser.models import ParseResult, StructuredDocumentModel
from parser.fallback import parse_document, create_parser

__all__ = [
    "AbstractParser",
    "ParseResult",
    "StructuredDocumentModel",
    "parse_document",
    "create_parser",
]
