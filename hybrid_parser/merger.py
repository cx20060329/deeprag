"""BCM-RAG Hybrid Parser — 数据融合层。

融合 Docling (层次完整) 和 MinerU (图片+HTML表格) 的优势。

原理:
    Docling: 标题层次、段落文本        → TreeParser 主线
    MinerU:  真实图片引用、HTML表格     → 补充增强

输出: 增强后的 DocumentTree，其中:
    - 每个 <!-- image --> 占位符被替换为 ![](images/xxx.jpg)
    - 每个有合并单元格的表格附加了 HTML table_body
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# MinerU content item
# ---------------------------------------------------------------------------

@dataclass
class MinerUImage:
    """MinerU 提取的图片引用。"""
    img_path: str               # "images/abc123.jpg"
    page_idx: int               # 页码


@dataclass
class MinerUTable:
    """MinerU 提取的 HTML 表格。"""
    table_body: str             # HTML <table>...</table>
    has_rowspan: bool
    has_colspan: bool
    caption: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class MinerULoader:
    """加载 MinerU 的 content_list.json 并提取图片和表格。"""

    def __init__(self, content_list_path: str) -> None:
        with open(content_list_path, "r", encoding="utf-8") as f:
            self._raw = json.load(f)
        self._images: list[MinerUImage] = []
        self._tables: list[MinerUTable] = []
        self._text_items: list[str] = []
        self._parse()

    # ---- public -----------------------------------------------------------

    @property
    def images(self) -> list[MinerUImage]:
        return self._images

    @property
    def tables(self) -> list[MinerUTable]:
        return self._tables

    @property
    def image_count(self) -> int:
        return len(self._images)

    @property
    def table_count(self) -> int:
        return len(self._tables)

    # ---- internal ---------------------------------------------------------

    def _parse(self) -> None:
        for item in self._raw:
            t = item.get("type", "")
            if t == "image":
                self._images.append(MinerUImage(
                    img_path=item.get("img_path", ""),
                    page_idx=item.get("page_idx", 0),
                ))
            elif t == "table":
                body = item.get("table_body", "")
                caption_list = item.get("table_caption", [])
                caption = caption_list[0] if caption_list else ""
                self._tables.append(MinerUTable(
                    table_body=body,
                    has_rowspan="rowspan" in body,
                    has_colspan="colspan" in body,
                    caption=caption,
                ))
            elif t == "text":
                self._text_items.append(item.get("text", ""))


# ---------------------------------------------------------------------------
# Hybrid merger
# ---------------------------------------------------------------------------

class HybridDocumentMerger:
    """将 MinerU 的图片和表格注入 Docling 的 Markdown 输出。

    用法::

        merger = HybridDocumentMerger(mineru_loader)
        enhanced_md = merger.enhance(docling_markdown)
    """

    # Docling 图片占位符
    _IMAGE_PLACEHOLDER_RE = re.compile(r"<!--\s*image\s*-->")

    # MinerU 表格 HTML 检测（在 MinerU markdown 中表格是内联 HTML）
    _HTML_TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)

    def __init__(self, mineru_loader: MinerULoader) -> None:
        self._images = list(mineru_loader.images)
        self._tables = list(mineru_loader.tables)
        self._img_index = 0
        self._tbl_index = 0

    # ---- main API ---------------------------------------------------------

    def enhance(self, docling_markdown: str) -> str:
        """用 MinerU 数据增强 Docling 的 Markdown。

        Returns:
            增强后的 Markdown 字符串。
        """
        self._img_index = 0
        self._tbl_index = 0

        result = docling_markdown

        # Step 1: 替换图片占位符
        result = self._replace_image_placeholders(result)

        # Step 2: 注入 HTML 表格（附加在对应章节末尾）
        result = self._inject_html_tables(result)

        return result

    def enhance_with_tables(
        self, docling_markdown: str,
    ) -> tuple[str, list[MinerUTable]]:
        """增强 Markdown 并返回提取的表格列表。

        Returns:
            (enhanced_markdown, list_of_tables_with_position_info)
        """
        self._img_index = 0
        self._tbl_index = 0

        result = self._replace_image_placeholders(docling_markdown)

        # 返回未注入的表格列表（调用方自行决定如何关联）
        return result, list(self._tables)

    # ---- image replacement ------------------------------------------------

    def _replace_image_placeholders(self, text: str) -> str:
        """将 <!-- image --> 替换为 MinerU 的 ![](images/xxx.jpg)。

        按顺序一一对应：第 N 个占位符 → 第 N 个 MinerU 图片。
        """
        images = self._images

        def _replacer(m: re.Match) -> str:
            if self._img_index < len(images):
                img_path = images[self._img_index].img_path
                self._img_index += 1
                return f"![图片]({img_path})"
            return m.group(0)  # 超出则保留原占位符

        return self._IMAGE_PLACEHOLDER_RE.sub(_replacer, text)

    # ---- table injection --------------------------------------------------

    def _inject_html_tables(self, text: str) -> str:
        """在文档末尾追加 MinerU 的 HTML 表格数据。

        表格以 HTML 注释块形式嵌入，下游解析器可选择处理。
        """
        if not self._tables:
            return text

        parts = [text, "", "<!-- === MinerU HTML Tables === -->"]

        for i, tbl in enumerate(self._tables):
            flags = []
            if tbl.has_rowspan:
                flags.append("rowspan")
            if tbl.has_colspan:
                flags.append("colspan")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            caption = f" <!-- {tbl.caption} -->" if tbl.caption else ""
            parts.append(
                f"<!-- table_{i:03d}{flag_str}{caption} -->\n"
                f"{tbl.table_body}"
            )

        return "\n".join(parts)

    # ---- utility ----------------------------------------------------------

    @staticmethod
    def extract_html_tables_from_enhanced(
        enhanced_md: str,
    ) -> list[dict]:
        """从增强后的 Markdown 中提取 HTML 表格元数据。

        Returns:
            list of dicts with keys: index, flags, caption, table_html
        """
        tables: list[dict] = []
        pattern = re.compile(
            r"<!-- table_(\d{3})(.*?) -->\s*(<table>.*?</table>)",
            re.DOTALL,
        )
        for m in pattern.finditer(enhanced_md):
            idx = int(m.group(1))
            flags_str = m.group(2).strip()
            html = m.group(3)
            flags = [f for f in ["rowspan", "colspan"] if f in flags_str]
            tables.append({
                "index": idx,
                "flags": flags,
                "html": html,
            })
        return tables
