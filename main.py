"""BCM-RAG Document Parser — Multi-backend .docx/.pdf parser.

Primary:   Docling (structured document understanding)
Fallback:  MinerU  (magic-pdf based .docx analysis)

Outputs:
  1. <name>.md           — Full markdown with images + tables
  2. content_list.json   — RagAnything-style structured content list
  3. images/             — Extracted images

Usage:
    python main.py <input.docx> [output_dir] [--parser auto|docling|mineru]
"""

import sys
from pathlib import Path

from parser.fallback import parse_document, create_parser, _list_available
from parser.models import ParseResult  # re-export for backward compatibility


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <input.docx|input.pdf> [output_dir] [--parser auto|docling|mineru]")
        print("Example: python main.py data/PA2A_中央集控器20250813(1).docx")
        print()
        available = _list_available()
        print(f"Available parsers: {available if available else ['NONE — install docling or magic-pdf']}")
        sys.exit(1)

    input_path = sys.argv[1]

    # Parse optional args
    output_dir = None
    parser_name = "auto"
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
        elif not arg.startswith("--"):
            output_dir = arg

    print("=" * 60)
    print(f"BCM-RAG Document Parser")
    print(f"  Backend: {parser_name}")
    print("=" * 60)

    try:
        result = parse_document(input_path, output_dir, parser=parser_name)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("=" * 60)
    print("PARSE COMPLETE")
    print("=" * 60)
    print(f"  Parser:        {result.parser_name}")
    print(f"  Source:        {result.source_file} ({result.source_size_bytes:,} bytes)")
    print(f"  Output:        {result.output_dir}")
    print(f"  Markdown:      {result.markdown_path} ({result.total_chars:,} chars)")
    print(f"  Content list:  {result.content_list_path}")
    print(f"  Images:        {result.images_dir} ({result.image_count} files)")
    print(f"  Tables:        {result.table_count}")
    print(f"  Parse time:    {result.parse_time_seconds:.1f}s")
    if result.warnings:
        print(f"  Warnings:      {len(result.warnings)}")
        for w in result.warnings:
            print(f"    - {w}")


if __name__ == "__main__":
    main()
