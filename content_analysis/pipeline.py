"""BCM-RAG Content Analysis — Full Pipeline (v3).

Integrated with parser package:
  Accepts ParseResult directly → propagates metadata throughout.

Wires together:
  1. SectionTreeBuilder   → Section Tree (with page refs from parser)
  2. EntityExtractor      → Entities + Relationships (text, 10 types)
  3. VLMAnalyzer          → Image analysis → entities + relationships (image)
  4. ChunkBuilder         → Context-aware chunks (with page + parser info)
  5. KG Exporter          → Neo4j Cypher + JSON
  6. Vector Exporter      → Qdrant-compatible points

Entry points:
  - pipeline.run_from_result(parse_result)   ← primary (uses parser package)
  - pipeline.run(content_list_path)          ← legacy (backward compat)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

from content_analysis.section_tree import SectionTreeBuilder
from content_analysis.entity_extractor import EntityExtractor
from content_analysis.chunk_builder import ChunkBuilder
from content_analysis.kg_exporter import KnowledgeGraphExporter
from content_analysis.vector_exporter import VectorStoreExporter
from content_analysis.vlm_analyzer import VLMAnalyzer
from content_analysis.models import (
    Entity, Relationship, RelType, EntityType,
    PipelineMeta, PipelineOutput,
)


class ContentAnalysisPipeline:
    """End-to-end content analysis: ParseResult → KG + Chunks + Vectors."""

    def __init__(
        self,
        output_dir: str = "output/content_analysis",
        vlm_api_key: str | None = None,
        vlm_model: str | None = None,
        enable_vlm: bool = True,
        vlm_backend: str = "auto",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tree_builder = SectionTreeBuilder()
        self.entity_extractor = EntityExtractor()
        self.chunk_builder = ChunkBuilder()
        self.kg_exporter = KnowledgeGraphExporter()
        self.vector_exporter = VectorStoreExporter()
        self.enable_vlm = enable_vlm
        if enable_vlm:
            self.vlm_analyzer = VLMAnalyzer(
                api_key=vlm_api_key, model=vlm_model, backend=vlm_backend,
            )
        else:
            self.vlm_analyzer = None

    # =====================================================================
    # Primary entry point: accept ParseResult from parser package
    # =====================================================================

    def run_from_result(
        self, parse_result, images_dir: str = "",
    ) -> PipelineOutput:
        """Run content analysis from a parser ParseResult.

        Args:
            parse_result: ParseResult from parser.parse_document()
            images_dir: Override images directory (uses parse_result.images_dir if empty)

        Returns:
            PipelineOutput with all analysis results + metadata.
        """
        meta = PipelineMeta(
            source_file=parse_result.source_file,
            parser_name=parse_result.parser_name,
            parser_warnings=parse_result.warnings,
            parse_time_seconds=parse_result.parse_time_seconds,
        )

        print("=" * 60)
        print("Content Analysis Pipeline v3")
        print(f"  Parser:  {meta.parser_name}")
        print(f"  Source:  {os.path.basename(meta.source_file)}")
        if self.enable_vlm:
            print("  VLM:     ENABLED")
        else:
            print("  VLM:     DISABLED")
        print("=" * 60)

        # ---- Resolve content list ------------------------------------------
        raw_cl = parse_result.content_list
        is_page_wrapped = (
            isinstance(raw_cl, list) and len(raw_cl) > 0
            and isinstance(raw_cl[0], list)
        )

        if is_page_wrapped:
            flat_to_page: dict[int, int] = {}
            flat_list: list[dict] = []
            for page_no, page_items in enumerate(raw_cl):
                for item in page_items:
                    flat_to_page[len(flat_list)] = page_no + 1
                    flat_list.append(item)
            meta.total_pages = len(raw_cl)
            meta.total_items = len(flat_list)
            print(f"  Items:   {len(flat_list)} across {len(raw_cl)} pages")
        else:
            flat_to_page = {}
            flat_list = raw_cl
            meta.total_pages = 1
            meta.total_items = len(flat_list)
            print(f"  Items:   {len(flat_list)} (flat format)")

        if not images_dir:
            images_dir = parse_result.images_dir

        # ---- Stage 1: Section Tree -----------------------------------------
        print("\n[1/5] Building Section Tree...")
        tree = self.tree_builder.build(raw_cl if is_page_wrapped else flat_list)
        tree.source_parser = meta.parser_name
        tree.total_pages = meta.total_pages
        print(f"  Sections: {len(tree.nodes)}")
        print(f"  Pages:    {len(tree.page_index)} tracked")
        print(f"  Tables:   {len(tree.table_owner)} owned by sections")

        # ---- Stage 2: Entity Extraction ------------------------------------
        print("\n[2/5] Extracting Text Entities...")
        entities, relationships = self.entity_extractor.extract(flat_list, tree)

        # Add BELONGS_TO for all entities to their sections
        for e in entities:
            if e.section_path:
                section_entity_id = f"section_{e.section_path.replace('.', '_')}"
                relationships.append(Relationship(
                    source_id=e.entity_id,
                    target_id=section_entity_id,
                    rel_type=RelType.BELONGS_TO,
                    weight=0.1,  # auto-generated, low signal for retrieval
                ))

        # Add section entities
        seen_sections = set()
        for e in entities:
            if e.section_path and e.section_path not in seen_sections:
                seen_sections.add(e.section_path)
                seid = f"section_{e.section_path.replace('.', '_')}"
                entities.append(Entity(
                    entity_id=seid,
                    entity_type=EntityType.MODULE,
                    name=f"Section {e.section_path}",
                    module=e.module,
                    section_path=e.section_path,
                ))

        print(f"  Text entities:      {len(entities)}")
        print(f"  Text relationships: {len(relationships)}")

        # ---- Stage 3: VLM Image Analysis -----------------------------------
        vlm_results = []
        if self.enable_vlm and self.vlm_analyzer:
            print("\n[3/5] Analyzing Images with VLM...")
            img_items = [item for item in flat_list if item.get("type") == "image"]
            print(f"  Images to analyze: {len(img_items)}")

            if img_items:
                img_paths = []
                section_contexts = []

                # Build title index for section lookup
                title_index: dict[int, str] = {}
                current_sid = "root"
                for idx, item in enumerate(flat_list):
                    if item.get("type") == "title":
                        title_text = self._extract_title_text(item)
                        for nid, node in tree.nodes.items():
                            if node.title == title_text:
                                current_sid = nid
                                break
                    title_index[idx] = current_sid

                for idx, item in enumerate(img_items):
                    img_path = item.get("content", {}).get("image_source", {}).get("path", "")
                    if img_path and os.path.exists(img_path):
                        img_paths.append(img_path)

                        sid = "root"
                        for j in range(idx, -1, -1):
                            if j in title_index:
                                sid = title_index[j]
                                break
                        node = tree.nodes.get(sid)
                        module = self._get_module(node, tree) if node else ""
                        section_num = node.number if node else ""

                        adjacent = self._get_adjacent_text(flat_list, idx)

                        is_sm = any(
                            kw in (node.title if node else "")
                            for kw in ("状态图", "状态迁移", "流程图", "State Machine")
                        )

                        section_contexts.append({
                            "module": module,
                            "section_number": section_num,
                            "section_title": node.title if node else "",
                            "adjacent_text": adjacent,
                            "is_state_machine": is_sm,
                        })

                if img_paths:
                    vlm_results = self.vlm_analyzer.analyze_images(img_paths, section_contexts)
                    print(f"  VLM analyzed: {len(vlm_results)} images")

                    vlm_entities, vlm_relationships = self.vlm_analyzer.results_to_entities(vlm_results)
                    entities.extend(vlm_entities)
                    relationships.extend(vlm_relationships)
                    print(f"  VLM entities added:      {len(vlm_entities)}")
                    print(f"  VLM relationships added: {len(vlm_relationships)}")

        # Stats after merge
        etype_counts = Counter(e.entity_type.value for e in entities)
        rtype_counts = Counter(r.rel_type.value for r in relationships)
        print(f"\n  Total entities:      {len(entities)}")
        for et, c in sorted(etype_counts.items()):
            print(f"    {et}: {c}")
        print(f"  Total relationships: {len(relationships)}")
        for rt, c in sorted(rtype_counts.items()):
            print(f"    {rt}: {c}")

        # ---- Stage 4: Chunk Building ---------------------------------------
        stage_label = "4" if self.enable_vlm else "3"
        print(f"\n[{stage_label}/5] Building Context-Aware Chunks...")
        chunks = self.chunk_builder.build(
            flat_list, tree, entities, images_dir, vlm_results,
        )

        # Enrich chunks with page + parser info
        for c in chunks.text_chunks:
            c.source_parser = meta.parser_name
            if c.source_indices:
                # Get page from the first source index
                c.page = flat_to_page.get(c.source_indices[0], -1)

        for c in chunks.image_chunks:
            c.source_parser = meta.parser_name
            c.page = flat_to_page.get(c.source_index, -1)

        print(f"  Text chunks:  {len(chunks.text_chunks)}")
        img_chunks = sum(1 for c in chunks.text_chunks if c.has_image)
        print(f"  With images:  {img_chunks}")
        text_types = Counter(c.chunk_type for c in chunks.text_chunks)
        for ct, count in sorted(text_types.items()):
            print(f"    {ct}: {count}")

        # Page coverage in chunks
        pages_in_chunks = sorted(set(
            c.page for c in chunks.text_chunks if c.page > 0
        ))
        if pages_in_chunks:
            print(f"  Pages covered: {min(pages_in_chunks)}–{max(pages_in_chunks)}")

        # Show sample
        for c in chunks.text_chunks:
            if c.has_image:
                print(f"\n  Sample chunk (w/ image):")
                print(f"    parser={c.source_parser} page={c.page}")
                print(f"    module={c.module} section={c.section_path}")
                print(f"    text[:200]: {c.text[:200]}")
                break

        # ---- Stage 5: Export -----------------------------------------------
        export_label = "5" if self.enable_vlm else "4"
        print(f"\n[{export_label}/5] Exporting...")

        self._export_all(tree, entities, relationships, chunks, meta)

        print("\n" + "=" * 60)
        print("Pipeline Complete")
        print("=" * 60)

        return PipelineOutput(
            meta=meta,
            tree=tree,
            entities=entities,
            relationships=relationships,
            chunks=chunks,
            vlm_results=vlm_results,
        )

    # =====================================================================
    # Legacy entry point (backward compatible)
    # =====================================================================

    def run(self, content_list_path: str, images_dir: str = "") -> dict:
        """Legacy entry: load content_list from file, then delegate.

        Prefer run_from_result() with a parser ParseResult.
        """
        import json as _json
        with open(content_list_path, "r", encoding="utf-8") as f:
            cl_data = _json.load(f)

        # Build a minimal parse_result-like object
        class _MinimalResult:
            source_file = content_list_path
            parser_name = "legacy"
            warnings = []
            parse_time_seconds = 0.0

        mr = _MinimalResult()
        mr.content_list = cl_data
        mr.images_dir = images_dir

        output = self.run_from_result(mr, images_dir)
        return {
            "sections": len(output.tree.nodes) if output.tree else 0,
            "entities": len(output.entities),
            "relationships": len(output.relationships),
            "text_chunks": len(output.chunks.text_chunks),
            "image_chunks": len(output.chunks.image_chunks),
            "vlm_images_analyzed": len(output.vlm_results),
            "output_dir": str(self.output_dir),
        }

    # =====================================================================
    # Export
    # =====================================================================

    def _export_all(
        self, tree, entities, relationships, chunks, meta: PipelineMeta,
    ) -> None:
        """Export all artifacts to output_dir."""
        import json as _json

        # -- Pipeline metadata --
        meta_dict = {
            "source_file": meta.source_file,
            "parser_name": meta.parser_name,
            "parser_warnings": meta.parser_warnings,
            "parse_time_seconds": meta.parse_time_seconds,
            "total_pages": meta.total_pages,
            "total_items": meta.total_items,
            "pipeline_version": meta.pipeline_version,
            "created_at": meta.created_at,
            "entities_count": len(entities),
            "relationships_count": len(relationships),
            "text_chunks": len(chunks.text_chunks),
            "image_chunks": len(chunks.image_chunks),
        }
        (self.output_dir / "pipeline_meta.json").write_text(
            _json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  Meta:      pipeline_meta.json")

        # -- KG --
        cypher = self.kg_exporter.export_cypher(entities, relationships)
        (self.output_dir / "knowledge_graph.cypher").write_text(cypher, encoding="utf-8")
        print(f"  KG Cypher: knowledge_graph.cypher ({len(cypher):,} bytes)")

        kg_json = self.kg_exporter.export_json(entities, relationships)
        (self.output_dir / "knowledge_graph.json").write_text(
            _json.dumps(kg_json, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  KG JSON:   knowledge_graph.json")

        # -- Chunks (with page + parser info) --
        chunk_list = {
            "meta": {
                "source_parser": meta.parser_name,
                "total_pages": meta.total_pages,
            },
            "text_chunks": [
                {
                    "chunk_id": c.chunk_id, "chunk_type": c.chunk_type,
                    "module": c.module, "section_path": c.section_path,
                    "section_title": c.section_title,
                    "text": c.text, "embedding_text": c.embedding_text,
                    "entities": c.entities, "signals": c.signals,
                    "states": c.states, "parameters": c.parameters,
                    "has_table": c.has_table, "has_image": c.has_image,
                    "image_refs": c.image_refs,
                    "source_indices": c.source_indices,
                    "token_count": c.token_count,
                    "page": c.page,
                    "source_parser": c.source_parser,
                }
                for c in chunks.text_chunks
            ],
        }
        (self.output_dir / "chunks.json").write_text(
            _json.dumps(chunk_list, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  Chunks:    chunks.json")

        # -- Vector points --
        vec_data = self.vector_exporter.export_all(chunks)
        (self.output_dir / "vector_points.json").write_text(
            _json.dumps(vec_data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  Vectors:   vector_points.json")

        # -- Section tree (with parser info) --
        tree_data = {
            "source_parser": tree.source_parser,
            "total_pages": tree.total_pages,
            "root_id": tree.root_id,
            "nodes": {
                nid: {
                    "section_id": node.section_id,
                    "title": node.title, "level": node.level,
                    "number": node.number, "parent_id": node.parent_id,
                    "children": node.children,
                    "entities": node.entities, "chunk_ids": node.chunk_ids,
                    "page": node.page,
                    "page_range": list(node.page_range),
                    "table_count": node.table_count,
                    "table_indices": node.table_indices,
                }
                for nid, node in tree.nodes.items()
            },
            "page_index": {
                str(k): v for k, v in tree.page_index.items()
            },
            "table_owner": {
                str(k): v for k, v in tree.table_owner.items()
            },
        }
        (self.output_dir / "section_tree.json").write_text(
            _json.dumps(tree_data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"  Tree:      section_tree.json")

    # =====================================================================
    # Helpers
    # =====================================================================

    @staticmethod
    def _extract_title_text(item: dict) -> str:
        content = item.get("content", {})
        parts = []
        for tc in content.get("title_content", []):
            if tc.get("type") == "text":
                parts.append(tc.get("content", ""))
        return "".join(parts).strip()

    @staticmethod
    def _get_adjacent_text(content_list: list[dict], idx: int, window: int = 3) -> str:
        texts = []
        for j in range(max(0, idx - window), min(len(content_list), idx + window + 1)):
            if j == idx:
                continue
            item = content_list[j]
            if item.get("type") == "title":
                t = ContentAnalysisPipeline._extract_title_text(item)
                if t.strip():
                    texts.append(f"[{t.strip()}]")
            elif item.get("type") in ("paragraph", "list"):
                parts = []
                for pc in item.get("content", {}).get("paragraph_content", []):
                    if pc.get("type") == "text":
                        parts.append(pc.get("content", ""))
                for li in item.get("content", {}).get("list_items", []):
                    for ic in li.get("item_content", []):
                        if ic.get("type") == "text":
                            parts.append(ic.get("content", ""))
                t = "".join(parts).strip()
                if t:
                    texts.append(t[:200])
        return " ".join(texts)

    @staticmethod
    def _get_module(node, tree) -> str:
        from content_analysis.section_tree import _CHAPTER_TO_MODULE
        current = node
        for _ in range(10):
            chapter_num = current.number.split(".")[0] if current.number else ""
            mod = _CHAPTER_TO_MODULE.get(chapter_num)
            if mod:
                return mod
            if current.parent_id and current.parent_id in tree.nodes:
                current = tree.nodes[current.parent_id]
            else:
                break
        return ""
