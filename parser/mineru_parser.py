"""BCM-RAG Parser — MinerU (magic-pdf) backend.

Supports .pdf (primary) via magic_pdf.tools.common.do_parse.
.docx is redirected to Docling (magic-pdf 1.3.10 no longer bundles the
old office_docx_analyze / union_make API).

Output structure from magic_pdf:
  {output_dir}/
    auto/
      {stem}/
        mm_markdown/
          {stem}.md
          {stem}_content_list.json
        images/
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from parser.base import AbstractParser
from parser.models import ParseResult


class MinerUParser(AbstractParser):
    """Parser backed by MinerU (magic-pdf >= 1.3).

    PDF:  magic_pdf.tools.common.do_parse → reads back md + content_list
    DOCX: raises NotImplementedError → caller should use Docling fallback
    """

    # ---- AbstractParser impl -----------------------------------------------

    @property
    def name(self) -> str:
        return "mineru"

    @classmethod
    def supports_format(cls, suffix: str) -> bool:
        suffix = suffix.lower().lstrip(".")
        return suffix in ("pdf",)

    def parse(
        self, input_path: str | Path, output_dir: str | None = None,
    ) -> ParseResult:
        """Parse a PDF file using magic_pdf."""
        start_time = datetime.now(timezone.utc)

        src = Path(input_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"File not found: {input_path}")

        suffix = src.suffix.lower()
        if suffix == ".docx":
            raise NotImplementedError(
                "MinerU (magic-pdf >= 1.3) no longer supports .docx directly. "
                "Use DoclingParser for .docx files."
            )
        if suffix != ".pdf":
            raise ValueError(f"MinerU only supports .pdf, got: {suffix}")

        if output_dir is None:
            from config import PARSER_OUTPUT_DIR
            output_dir = str(PARSER_OUTPUT_DIR / src.stem)
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        return self._parse_pdf(src, out, start_time)

    # ---- PDF parsing -------------------------------------------------------

    def _parse_pdf(
        self, src: Path, out: Path, start_time: datetime,
    ) -> ParseResult:
        """Use magic_pdf Python API to parse PDF."""
        # Set config path if available
        config_path = Path(__file__).resolve().parent.parent / "magic-pdf.json"
        if config_path.exists():
            os.environ.setdefault("MINERU_TOOLS_CONFIG_JSON", str(config_path))

        print(f"[mineru] Parsing PDF: {src.name} ({src.stat().st_size:,} bytes)")

        pdf_bytes = src.read_bytes()

        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.tools.common import do_parse

        # Build dataset and parse
        ds = PymuDocDataset(pdf_bytes)
        auto_dir = str(out / "auto")

        do_parse(
            output_dir=auto_dir,
            pdf_file_name=src.stem,
            pdf_bytes_or_dataset=ds,
            model_list=[],  # empty = use built-in models
            parse_method="auto",
            lang="zh",  # Chinese automotive docs
            f_draw_span_bbox=False,
            f_draw_layout_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=False,
            f_dump_model_json=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
        )

        # Read back MinerU outputs.
        # do_parse writes to: {auto_dir}/{stem}/mm_markdown/{stem}.md
        inner_dir = Path(auto_dir) / src.stem
        md_path = inner_dir / "mm_markdown" / f"{src.stem}.md"
        cl_path = inner_dir / "mm_markdown" / f"{src.stem}_content_list.json"
        images_dir = inner_dir / "images"

        # Read outputs
        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        content_list = []
        if cl_path.exists():
            with open(cl_path, "r", encoding="utf-8") as f:
                content_list = json.load(f)

        # Copy content_list to outer dir for downstream compatibility
        outer_cl = out / "content_list.json"
        if cl_path.exists():
            outer_cl.write_bytes(cl_path.read_bytes())

        # Copy markdown to outer dir
        outer_md = out / f"{src.stem}.md"
        if md_path.exists():
            outer_md.write_bytes(md_path.read_bytes())

        image_count = len(list(images_dir.iterdir())) if images_dir.exists() else 0
        html_tables = len(re.findall(r"<table>", md_text))
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        return ParseResult(
            source_file=str(src),
            source_size_bytes=src.stat().st_size,
            parser_name="mineru",
            output_dir=str(out),
            markdown_path=str(outer_md),
            content_list_path=str(outer_cl),
            images_dir=str(images_dir),
            markdown_text=md_text,
            content_list=content_list,
            image_count=image_count,
            table_count=html_tables,
            total_chars=len(md_text),
            parse_time_seconds=elapsed,
        )
