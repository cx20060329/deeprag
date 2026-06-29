"""BCM-RAG Parser — Docling backend.

Converts DoclingDocument → RagAnything content_list format.

Docling supports: .docx, .pdf, .pptx, .html, .md, .xlsx, .csv, .epub, .xml, .latex
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from parser.base import AbstractParser
from parser.models import ParseResult, StructuredDocumentModel, PageContent


class DoclingParser(AbstractParser):
    """Parser backed by IBM Docling.

    Produces unified ParseResult from DoclingDocument.
    Optionally falls back to another parser on failure.
    """

    def __init__(self, fallback: AbstractParser | None = None):
        self._fallback = fallback

    # ---- AbstractParser impl -----------------------------------------------

    @property
    def name(self) -> str:
        return "docling"

    @classmethod
    def supports_format(cls, suffix: str) -> bool:
        suffix = suffix.lower().lstrip(".")
        return suffix in (
            "docx", "pdf", "pptx", "html", "htm", "md", "markdown",
            "xlsx", "csv", "epub", "xml", "latex", "tex", "jpg", "jpeg",
            "png", "tiff", "bmp",
        )

    def parse(
        self, input_path: str | Path, output_dir: str | None = None,
    ) -> ParseResult:
        """Parse document using Docling, with optional fallback."""
        start_time = datetime.now(timezone.utc)

        src = Path(input_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"File not found: {input_path}")

        suffix = src.suffix.lower()
        if not self.supports_format(suffix):
            if self._fallback:
                return self._fallback.parse(input_path, output_dir)
            raise ValueError(f"Docling does not support format: {suffix}")

        if output_dir is None:
            from config import PARSER_OUTPUT_DIR
            output_dir = str(PARSER_OUTPUT_DIR / src.stem)
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        try:
            return self._parse_with_docling(src, out, start_time)
        except Exception as exc:
            if self._fallback:
                import sys
                print(f"  [docling] Parse failed: {exc}", file=sys.stderr)
                print(f"  [docling] Falling back to: {self._fallback.name}", file=sys.stderr)
                result = self._fallback.parse(input_path, output_dir)
                result.warnings.append(f"Docling failed ({exc}), used {self._fallback.name}")
                return result
            raise RuntimeError(f"Docling parse failed (no fallback): {exc}") from exc

    # ---- internal ----------------------------------------------------------

    def _parse_with_docling(
        self, src: Path, out: Path, start_time: datetime,
    ) -> ParseResult:
        from docling.document_converter import DocumentConverter

        print(f"[docling] Parsing: {src.name} ({src.stat().st_size:,} bytes)")

        converter = DocumentConverter()
        conv_result = converter.convert(str(src))
        doc = conv_result.document

        # Collect items in document order
        ordered_items, page_map = self._collect_items(doc, out)

        # Count VML/EMF images: pictures with no extractable image data
        vml_count = sum(1 for p in doc.pictures if not p.image)
        if vml_count > 0:
            import sys as _sys
            msg = (
                f"{vml_count} VML/EMF image(s) could not be extracted. "
                "Install LibreOffice for full image support, "
                "or use --parser mineru for .docx files with VML content."
            )
            print(f"  [docling] ⚠ {msg}", file=_sys.stderr)

        # Build content_list (page-wrapped format for RagAnything compatibility)
        content_list = self._build_content_list(ordered_items, page_map)

        # Export markdown
        md_text = doc.export_to_markdown()
        md_path = out / f"{src.stem}.md"
        md_path.write_text(md_text, encoding="utf-8")

        # Save content_list.json
        cl_path = out / "content_list.json"
        with open(cl_path, "w", encoding="utf-8") as f:
            json.dump(content_list, f, ensure_ascii=False, indent=2)

        # Build structured document model
        structured_doc = StructuredDocumentModel(
            title=doc.name or src.stem,
            source_path=str(src),
            parser_name="docling",
            content_list=content_list,
            markdown_text=md_text,
            total_pages=len(page_map),
            image_count=sum(1 for it in ordered_items if it["type"] == "image"),
            table_count=sum(1 for it in ordered_items if it["type"] == "table"),
            total_chars=len(md_text),
        )

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        warnings: list[str] = []
        if vml_count > 0:
            warnings.append(
                f"{vml_count} VML/EMF image(s) not extracted. "
                "Install LibreOffice for full image support, "
                "or use --parser mineru for .docx with VML content."
            )

        return ParseResult(
            source_file=str(src),
            source_size_bytes=src.stat().st_size,
            parser_name="docling",
            output_dir=str(out),
            markdown_path=str(md_path),
            content_list_path=str(cl_path),
            images_dir=str(out / "images"),
            markdown_text=md_text,
            content_list=content_list,
            image_count=structured_doc.image_count,
            table_count=structured_doc.table_count,
            total_chars=len(md_text),
            parse_time_seconds=elapsed,
            structured_doc=structured_doc,
            warnings=warnings,
        )

    def _collect_items(
        self, doc, out: Path,
    ) -> tuple[list[dict], dict[int, list[dict]]]:
        """Traverse DoclingDocument body tree in order, collect items.

        Docling structure: body.children references items (texts, tables,
        pictures) AND groups. Groups can reference other groups (nested).
        We build a unified lookup covering all referencable entities.

        Returns:
            ordered_items: flat list of content_list-format dicts
            page_map: dict[int, list[dict]] — items grouped by page
        """
        ordered_items: list[dict] = []
        page_map: dict[int, list[dict]] = {}

        # Build unified lookup: self_ref → item/group
        unified: dict[str, object] = {}
        for item in doc.texts:
            unified[item.self_ref] = item
        for item in doc.tables:
            unified[item.self_ref] = item
        for item in doc.pictures:
            unified[item.self_ref] = item
        for item in doc.key_value_items:
            unified[item.self_ref] = item
        for item in doc.groups:
            unified[item.self_ref] = item

        # Also index form_items, field_items if present
        for item in getattr(doc, 'form_items', []):
            unified[item.self_ref] = item
        for item in getattr(doc, 'field_items', []):
            unified[item.self_ref] = item

        images_dir = out / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        seen = set()

        def resolve_and_collect(cref: str):
            """Resolve a cref and collect the item if it's a leaf node.
            Recurse into groups. Returns True if something was collected."""
            if cref in seen:
                return False
            seen.add(cref)

            entity = unified.get(cref)
            if entity is None:
                return False

            from docling_core.types.doc.document import GroupItem

            if isinstance(entity, GroupItem):
                # Recurse into group children
                for child_ref in entity.children:
                    resolve_and_collect(child_ref.cref)
                return True

            # Leaf item: convert and collect
            converted = self._convert_item(entity, images_dir)
            if converted is not None:
                ordered_items.append(converted)
                page_no = self._get_page(entity)
                if page_no not in page_map:
                    page_map[page_no] = []
                page_map[page_no].append(converted)
                return True
            return False

        # Walk body children
        for child_ref in doc.body.children:
            resolve_and_collect(child_ref.cref)

        # Supplement: items not reachable from body tree.
        # In .docx files, some tables/images may be "orphan" — they exist
        # in doc.tables/doc.pictures but are not referenced by body.
        # We append them at the end to avoid data loss.
        supplement = []
        for item in doc.texts:
            if item.self_ref not in seen:
                converted = self._convert_item(item, images_dir)
                if converted:
                    supplement.append(converted)
        for item in doc.tables:
            if item.self_ref not in seen:
                converted = self._convert_item(item, images_dir)
                if converted:
                    supplement.append(converted)
        for item in doc.pictures:
            if item.self_ref not in seen:
                converted = self._convert_item(item, images_dir)
                if converted:
                    supplement.append(converted)

        if supplement:
            ordered_items.extend(supplement)

        return ordered_items, page_map

    def _convert_item(self, item, images_dir: Path) -> dict | None:
        """Convert a single Docling item to content_list format."""
        from docling_core.types.doc.document import (
            TitleItem, SectionHeaderItem, TextItem, ListItem,
            PictureItem, TableItem, KeyValueItem,
            DocItemLabel,
        )

        label = item.label

        # --- Title / Section Header ---
        if isinstance(item, (TitleItem, SectionHeaderItem)):
            text = item.text or ""
            if not text.strip():
                return None
            level = getattr(item, "level", 1)
            return {
                "type": "title",
                "content": {
                    "level": level,
                    "title_content": [{"type": "text", "content": text.strip()}],
                },
            }

        # --- Text / Paragraph ---
        if isinstance(item, TextItem):
            text = item.text or ""
            if not text.strip():
                return None
            return {
                "type": "paragraph",
                "content": {
                    "paragraph_content": [{"type": "text", "content": text}],
                },
            }

        # --- List Item ---
        if isinstance(item, ListItem):
            text = item.text or ""
            if not text.strip():
                return None
            return {
                "type": "list",
                "content": {
                    "list_items": [
                        {"item_content": [{"type": "text", "content": text}]}
                    ],
                },
            }

        # --- Picture / Image ---
        if isinstance(item, PictureItem):
            return self._convert_picture(item, images_dir)

        # --- Table ---
        if isinstance(item, TableItem):
            return self._convert_table(item)

        # --- KeyValue ---
        if isinstance(item, KeyValueItem):
            text = f"{item.key}: {item.value}" if item.key else (item.value or "")
            if not text.strip():
                return None
            return {
                "type": "paragraph",
                "content": {
                    "paragraph_content": [{"type": "text", "content": text}],
                },
            }

        return None

    def _convert_picture(self, item, images_dir: Path) -> dict | None:
        """Extract image, save to storage, return content_list format.

        Handles: file paths, base64 data URIs, and AnyUrl types.
        """
        if not item.image or not item.image.uri:
            return None

        img_uri = item.image.uri
        img_bytes: bytes | None = None
        suffix = ".png"

        uri_str = str(img_uri)

        if uri_str.startswith("data:"):
            # Base64 data URI: "data:image/png;base64,<data>"
            import base64 as _b64
            try:
                header, data = uri_str.split(",", 1)
                # Extract MIME type for suffix
                if ":" in header:
                    mime = header.split(":")[1].split(";")[0]
                    if "/" in mime:
                        suffix = "." + mime.split("/")[1]
                img_bytes = _b64.b64decode(data)
            except Exception:
                return None

        elif os.path.exists(uri_str):
            img_bytes = Path(uri_str).read_bytes()
            suffix = Path(uri_str).suffix or ".png"

        else:
            # VML/EMF images: Pillow can't decode these legacy formats.
            # The image.uri exists but points to a BytesIO that Pillow rejected.
            # Solution: LibreOffice can render these. MinerU extracts them directly.
            return None  # logged as warning in _parse_with_docling

        if img_bytes is None:
            return None

        # Save image with hash-based dedup name
        img_hash = hashlib.md5(img_bytes).hexdigest()[:12]
        img_name = f"{img_hash}{suffix}"
        img_path = images_dir / img_name
        if not img_path.exists():
            img_path.write_bytes(img_bytes)

        # Caption from referenced captions
        caption = ""
        if item.captions:
            for cap_ref in item.captions:
                # Caption text is in referenced TextItem — resolve from doc
                pass

        return {
            "type": "image",
            "content": {
                "image_source": {"path": str(img_path.resolve())},
                "caption": caption,
            },
        }

    def _convert_table(self, item) -> dict:
        """Convert Docling TableItem to content_list table format (HTML)."""
        data = item.data
        html_parts = ["<table>"]

        # Build rows from table data
        cells = data.table_cells
        num_rows = data.num_rows
        num_cols = data.num_cols

        # Group cells by row
        for row_idx in range(num_rows):
            html_parts.append("<tr>")
            row_cells = [
                c for c in cells
                if c.start_row_offset_idx <= row_idx <= c.end_row_offset_idx
            ]
            for col_idx in range(num_cols):
                col_cells = [
                    c for c in row_cells
                    if c.start_col_offset_idx <= col_idx <= c.end_col_offset_idx
                ]
                if col_cells:
                    cell = col_cells[0]
                    tag = "th" if (cell.column_header or cell.row_header) else "td"
                    attrs = ""
                    if cell.row_span > 1:
                        attrs += f' rowspan="{cell.row_span}"'
                    if cell.col_span > 1:
                        attrs += f' colspan="{cell.col_span}"'
                    html_parts.append(
                        f"<{tag}{attrs}>{_escape_html(cell.text)}</{tag}>"
                    )
            html_parts.append("</tr>")

        html_parts.append("</table>")
        html = "\n".join(html_parts)

        return {
            "type": "table",
            "content": {"html": html},
        }

    @staticmethod
    def _get_page(item) -> int:
        """Extract page number from item provenance."""
        if hasattr(item, "prov") and item.prov:
            return item.prov[0].page_no
        return 0

    @staticmethod
    def _build_content_list(
        items: list[dict], page_map: dict[int, list[dict]],
    ) -> list[list[dict]]:
        """Build page-wrapped content_list."""
        if not page_map:
            return [items]

        # If only page 0 (no page info), return single page
        if len(page_map) == 1 and 0 in page_map:
            return [page_map.get(0, items)]

        # Sort by page number
        result = []
        for page_no in sorted(page_map.keys()):
            result.append(page_map[page_no])
        return result


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
