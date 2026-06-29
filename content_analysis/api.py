"""DeepRAG — Content Analysis API facade.

Public API for the content analysis pipeline:
Section Tree → Entity Extraction → Chunk Building → Export.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from content_analysis.chunk_builder import ChunkBuilder
from content_analysis.entity_extractor import EntityExtractor
from content_analysis.models import ChunkList, SectionTree
from content_analysis.pipeline import ContentAnalysisPipeline
from content_analysis.section_tree import SectionTreeBuilder
from parser.models import ParseResult

if TYPE_CHECKING:
    from domain.config import DomainConfig


class ContentAnalysisAPI:
    """Public API for content analysis.

    Usage:
        from domain import load_domain_config
        domain = load_domain_config("bcm")

        api = ContentAnalysisAPI(domain=domain)
        result = api.analyze(parse_result)
    """

    def __init__(self, domain: "DomainConfig | None" = None):
        self._domain = domain
        self._section_builder = SectionTreeBuilder()
        self._entity_extractor = EntityExtractor(domain=domain)
        self._chunk_builder = ChunkBuilder(domain=domain)
        self._pipeline = ContentAnalysisPipeline()

    @property
    def domain(self) -> "DomainConfig | None":
        return self._domain

    def analyze(self, parse_result: ParseResult) -> dict:
        """Run the full content analysis pipeline.

        Args:
            parse_result: Output from ParserAPI.parse().

        Returns:
            Dict with section_tree, entities, relationships, chunks, etc.
        """
        return self._pipeline.run(parse_result)

    def build_section_tree(self, parse_result: ParseResult) -> SectionTree:
        """Build the document section tree from parsed content.

        Args:
            parse_result: Output from ParserAPI.parse().

        Returns:
            SectionTree with hierarchical section structure.
        """
        return self._section_builder.build(parse_result.content_list)

    def extract_entities(
        self, parse_result: ParseResult, tree: SectionTree,
    ) -> tuple[list, list]:
        """Extract entities and relationships.

        Args:
            parse_result: Parsed document.
            tree: Section tree from build_section_tree().

        Returns:
            (entities, relationships) tuple.
        """
        return self._entity_extractor.extract(parse_result.content_list, tree)

    def build_chunks(
        self,
        parse_result: ParseResult,
        tree: SectionTree,
        entities: list,
        vlm_results: list | None = None,
    ) -> ChunkList:
        """Build semantic chunks from parsed content.

        Args:
            parse_result: Parsed document.
            tree: Section tree.
            entities: Extracted entities.
            vlm_results: Optional VLM analysis results.

        Returns:
            ChunkList with logical-unit chunks.
        """
        return self._chunk_builder.build(
            parse_result.content_list,
            tree,
            entities,
            images_dir=parse_result.images_dir or "",
            vlm_results=vlm_results,
        )
