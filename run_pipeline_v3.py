"""BCM-RAG Full Pipeline — Parser + Content Analysis integrated.

Parser → Section Tree → Entity Extraction → Chunking → KG + Vectors.

Usage:
    python run_pipeline_v3.py <input.docx> [--parser auto|docling|mineru] [--vlm]
"""

import sys
import os

from parser.fallback import parse_document
from content_analysis.pipeline import ContentAnalysisPipeline


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not input_path:
        print("Usage: python run_pipeline_v3.py <input.docx|input.pdf> [--parser auto|docling|mineru] [--vlm]")
        print()
        print("Examples:")
        print("  python run_pipeline_v3.py data/spec.docx")
        print("  python run_pipeline_v3.py data/spec.docx --parser docling")
        print("  python run_pipeline_v3.py data/spec.docx --vlm")
        sys.exit(1)

    # Parse CLI flags
    parser_name = "auto"
    enable_vlm = False
    args_iter = iter(sys.argv[2:])
    for arg in args_iter:
        if arg.startswith("--parser="):
            parser_name = arg.split("=", 1)[1]
        elif arg == "--parser":
            try:
                parser_name = next(args_iter)
            except StopIteration:
                print("ERROR: --parser requires a value (auto|docling|mineru)")
                sys.exit(1)
        elif arg in ("--vlm",):
            enable_vlm = True

    # =================================================================
    # Phase 1: Parse document
    # =================================================================
    print("=" * 60)
    print("PHASE 1: Document Parsing")
    print("=" * 60)

    parse_result = parse_document(input_path, parser=parser_name)

    print(f"\n  Parser:      {parse_result.parser_name}")
    print(f"  Time:        {parse_result.parse_time_seconds:.1f}s")
    print(f"  Items:       {len(parse_result.flat_items)}")
    print(f"  Tables:      {parse_result.table_count}")
    print(f"  Images:      {parse_result.image_count}")
    if parse_result.warnings:
        for w in parse_result.warnings:
            print(f"  ⚠ {w}")

    # =================================================================
    # Phase 2: Content Analysis
    # =================================================================
    print()
    print("=" * 60)
    print("PHASE 2: Content Analysis")
    print("=" * 60)

    pipeline = ContentAnalysisPipeline(
        output_dir="output/content_analysis",
        enable_vlm=enable_vlm,
    )

    output = pipeline.run_from_result(parse_result)

    # =================================================================
    # Summary
    # =================================================================
    print()
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Parser:          {output.meta.parser_name}")
    print(f"  Pages:           {output.meta.total_pages}")
    print(f"  Items:           {output.meta.total_items}")
    print(f"  Sections:        {len(output.tree.nodes) if output.tree else 0}")
    print(f"  Entities:        {len(output.entities)}")
    print(f"  Relationships:   {len(output.relationships)}")
    print(f"  Text chunks:     {len(output.chunks.text_chunks)}")
    print(f"  Image chunks:    {len(output.chunks.image_chunks)}")
    print(f"  VLM images:      {len(output.vlm_results)}")
    print(f"  Output:          {pipeline.output_dir}")


if __name__ == "__main__":
    main()
