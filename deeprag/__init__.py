"""DeepRAG — Domain-Adaptable Enterprise RAG Framework.

A three-layer RAG system with Document Tree, Knowledge Graph, and Vector Index.
Supports pluggable DomainConfig for any document domain.

Quick start:
    from deeprag import DeepRAG

    # Use built-in BCM domain
    rag = DeepRAG(domain="bcm")

    # Parse and analyze a document
    result = rag.parse("document.pdf")
    analysis = rag.analyze(result)

    # Search
    results = rag.search("What is GlobalClose?")
    print(results["answer"])

    # Ask (agent-powered)
    response = rag.ask("How does GlobalClose depend on PEPS_UsageMode?")

Or start the HTTP server:
    deeprag-server
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.config import DomainConfig


class DeepRAG:
    """Main entry point for the DeepRAG framework.

    Provides a unified API for the full RAG pipeline:
    parse → analyze → index → search → ask

    Usage:
        rag = DeepRAG(domain="bcm", data_dir="output/PA2A")
        rag.parse("doc.pdf")
        analysis = rag.analyze(result)
        rag.index(analysis)
        answer = rag.search("query")
    """

    def __init__(
        self,
        domain: str | "DomainConfig" = "bcm",
        data_dir: str | Path | None = None,
    ):
        from domain.loader import load_domain_config

        if isinstance(domain, str):
            self._domain = load_domain_config(domain)
        else:
            self._domain = domain

        self._data_dir = Path(data_dir) if data_dir else None

        # Lazy-loaded components
        self._parser_api = None
        self._analysis_api = None
        self._retrieval_api = None
        self._agent_api = None

    @property
    def domain(self) -> "DomainConfig":
        return self._domain

    # ------------------------------------------------------------------
    # Pipeline methods
    # ------------------------------------------------------------------

    def parse(self, file_path: str | Path) -> "ParseResult":
        """Parse a document file.

        Args:
            file_path: Path to the document (.pdf, .docx, etc.).

        Returns:
            ParseResult with content_list, markdown_text, images_dir.
        """
        from parser.api import ParserAPI
        if self._parser_api is None:
            self._parser_api = ParserAPI()
        return self._parser_api.parse(file_path)

    def analyze(self, parse_result: "ParseResult") -> dict:
        """Run content analysis on a parsed document.

        Args:
            parse_result: Output from parse().

        Returns:
            Dict with section_tree, entities, relationships, chunks.
        """
        from content_analysis.api import ContentAnalysisAPI
        if self._analysis_api is None:
            self._analysis_api = ContentAnalysisAPI(domain=self._domain)
        return self._analysis_api.analyze(parse_result)

    def load(self) -> dict:
        """Load retrieval indices from data_dir.

        Returns:
            Dict with pipeline stats.
        """
        from retrieval.api import RetrievalAPI
        if self._retrieval_api is None:
            self._retrieval_api = RetrievalAPI(
                domain=self._domain,
                data_dir=self._data_dir,
            )
        return self._retrieval_api.load()

    def search(self, query: str, top_k: int = 10) -> dict:
        """Execute the full retrieval pipeline.

        Args:
            query: User query string.
            top_k: Number of results to return.

        Returns:
            SearchResponse with merged results, evidence, and LLM answer.
        """
        if self._retrieval_api is None:
            self.load()
        return self._retrieval_api.search(query, top_k=top_k)

    def ask(self, question: str) -> dict:
        """Ask a question using the DAG reasoning agent.

        Args:
            question: User question string.

        Returns:
            AgentResponse with answer, confidence, reasoning trace.
        """
        from agent.api import AgentAPI
        if self._agent_api is None:
            if self._retrieval_api is None:
                self.load()
            self._agent_api = AgentAPI(
                retrieval_api=self._retrieval_api,
                domain=self._domain,
            )
        return self._agent_api.query(question)

    # ------------------------------------------------------------------
    # Domain management
    # ------------------------------------------------------------------

    @classmethod
    def list_domains(cls) -> list[str]:
        """List all available domain config names."""
        from domain.loader import list_domains
        return list_domains()

    @classmethod
    def register_domain(cls, config: "DomainConfig") -> None:
        """Register a custom domain config."""
        from domain.loader import register_domain_config
        register_domain_config(config)

    @classmethod
    def create_domain(cls, name: str, **kwargs) -> "DomainConfig":
        """Create and register a new domain config."""
        from domain.loader import get_or_create_domain
        return get_or_create_domain(name, **kwargs)


# Version
__version__ = "0.3.0"
__all__ = ["DeepRAG", "__version__"]
