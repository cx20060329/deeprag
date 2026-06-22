"""BCM-RAG Parser — MinerU (magic-pdf) backend.

Refactored from main.py into the parser package.
Produces RagAnything-compatible content_list natively.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from parser.base import AbstractParser
from parser.models import ParseResult


class MinerUParser(AbstractParser):
    """Parser backed by MinerU (magic-pdf).

    Native content_list output — no format conversion needed.
    """

    # ---- AbstractParser impl -----------------------------------------------

    @property
    def name(self) -> str:
        return "mineru"

    @classmethod
    def supports_format(cls, suffix: str) -> bool:
        suffix = suffix.lower().lstrip(".")
        # MinerU primarily supports .docx via office_docx_analyze
        return suffix in ("docx",)

    def parse(
        self, input_path: str | Path, output_dir: str | None = None,
    ) -> ParseResult:
        """Parse a .docx file using MinerU."""
        start_time = datetime.now(timezone.utc)

        src = Path(input_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"File not found: {input_path}")
        if src.suffix.lower() not in (".docx",):
            raise ValueError(
                f"MinerU only supports .docx, got: {src.suffix}"
            )

        if output_dir is None:
            output_dir = os.path.join("output", src.stem)
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        print(f"[mineru] Analyzing: {src.name} ({src.stat().st_size:,} bytes)")

        # Step 1: Read + analyze with MinerU
        with open(src, "rb") as f:
            file_bytes = f.read()

        from mineru.backend.office.docx_analyze import office_docx_analyze
        from mineru.data.data_reader_writer import FileBasedDataWriter
        from mineru.backend.office.office_middle_json_mkcontent import union_make
        from mineru.utils.enum_class import MakeMode

        images_dir = out / "images"
        images_dir.mkdir(exist_ok=True)
        image_writer = FileBasedDataWriter(str(images_dir))

        middle_json, _raw_results = office_docx_analyze(
            file_bytes, image_writer=image_writer,
        )
        pdf_info = middle_json.get("pdf_info", middle_json)

        # Step 2: Generate markdown + content_list
        md_content = union_make(
            pdf_info, MakeMode.MM_MD, img_buket_path=str(images_dir),
        )
        content_list = union_make(
            pdf_info, MakeMode.CONTENT_LIST_V2, img_buket_path=str(images_dir),
        )

        # Step 3: Save outputs
        md_path = out / f"{src.stem}.md"
        md_path.write_text(md_content, encoding="utf-8")

        cl_path = out / "content_list.json"
        with open(cl_path, "w", encoding="utf-8") as f:
            json.dump(content_list, f, ensure_ascii=False, indent=2)

        # Step 4: Stats — count images actually referenced in content_list
        image_paths: set[str] = set()
        for page_or_item in content_list:
            items = page_or_item if isinstance(page_or_item, list) else [page_or_item]
            for item in items:
                if item.get("type") == "image":
                    p = item.get("content", {}).get("image_source", {}).get("path", "")
                    if p:
                        image_paths.add(p)
        image_count = len(image_paths)
        html_tables = len(re.findall(r"<table>", md_content))
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        return ParseResult(
            source_file=str(src),
            source_size_bytes=src.stat().st_size,
            parser_name="mineru",
            output_dir=str(out),
            markdown_path=str(md_path),
            content_list_path=str(cl_path),
            images_dir=str(images_dir),
            markdown_text=md_content,
            content_list=content_list,
            image_count=image_count,
            table_count=html_tables,
            total_chars=len(md_content),
            parse_time_seconds=elapsed,
        )
