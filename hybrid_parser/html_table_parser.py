"""BCM-RAG Hybrid Parser — HTML 表格解析器。

将 MinerU 的 HTML 表格（含 rowspan/colspan）解析为完整的二维数组。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape as html_unescape


@dataclass
class HtmlTableCell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False


@dataclass
class HtmlTable:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    has_rowspan: bool = False
    has_colspan: bool = False
    source_html: str = ""


class HtmlTableParser:
    """将 MinerU HTML <table> 解析为二维数组，正确处理 rowspan/colspan。"""

    _TD_RE = re.compile(
        r"<(td|th)([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE,
    )
    _TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
    _TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.DOTALL)
    _TAG_RE = re.compile(r"<[^>]+>")
    _WS_RE = re.compile(r"\s+")

    def parse(self, html: str) -> HtmlTable:
        m = self._TABLE_RE.search(html)
        if not m:
            return HtmlTable(source_html=html)
        body = m.group(1)

        # 第一遍：收集所有单元格（考虑 rowspan 占位）
        all_cells: list[HtmlTableCell] = []
        num_rows = 0
        max_col = 0

        # 追踪每行已被 rowspan 占用的列
        occupied: dict[int, set[int]] = {}  # row → {col, ...}

        for tr_m in self._TR_RE.finditer(body):
            row_idx = num_rows
            num_rows += 1

            # 找到该行第一个未被占用的列
            col_idx = 0
            occ = occupied.get(row_idx, set())
            while col_idx in occ:
                col_idx += 1

            tr_content = tr_m.group(1)
            for td_m in self._TD_RE.finditer(tr_content):
                tag_name = td_m.group(1).lower()
                attrs = td_m.group(2)
                raw_text = td_m.group(3)
                text = self._clean_text(raw_text)
                is_header = (tag_name == "th")
                rs = self._parse_span(attrs, "rowspan")
                cs = self._parse_span(attrs, "colspan")

                all_cells.append(HtmlTableCell(
                    text=text, row=row_idx, col=col_idx,
                    rowspan=rs, colspan=cs, is_header=is_header,
                ))

                # 标记 rowspan 占用的列
                for dr in range(rs):
                    r = row_idx + dr
                    if r not in occupied:
                        occupied[r] = set()
                    for dc in range(cs):
                        occupied[r].add(col_idx + dc)

                col_idx += cs

            max_col = max(max_col, col_idx)

        num_cols = max_col

        # 第二遍：构建完整网格
        grid: list[list[str]] = [
            [""] * num_cols for _ in range(num_rows)
        ]
        is_header_row = [False] * num_rows

        for cell in all_cells:
            for dr in range(cell.rowspan):
                for dc in range(cell.colspan):
                    r = cell.row + dr
                    c = cell.col + dc
                    if 0 <= r < num_rows and 0 <= c < num_cols:
                        grid[r][c] = cell.text
            if cell.is_header:
                is_header_row[cell.row] = True

        # 表头：第一个含 <th> 的行，否则第一行
        try:
            header_row_idx = is_header_row.index(True)
        except ValueError:
            header_row_idx = 0

        headers = grid[header_row_idx] if num_rows > 0 else []
        data_rows = (
            grid[header_row_idx + 1:]
            if num_rows > header_row_idx + 1
            else []
        )

        return HtmlTable(
            headers=headers,
            rows=data_rows,
            has_rowspan=any(c.rowspan > 1 for c in all_cells),
            has_colspan=any(c.colspan > 1 for c in all_cells),
            source_html=html,
        )

    @staticmethod
    def _parse_span(attrs: str, attr_name: str) -> int:
        m = re.search(rf'{attr_name}\s*=\s*"(\d+)"', attrs, re.IGNORECASE)
        return int(m.group(1)) if m else 1

    @staticmethod
    def _clean_text(raw: str) -> str:
        text = html_unescape(raw)
        text = HtmlTableParser._TAG_RE.sub(" ", text)
        text = HtmlTableParser._WS_RE.sub(" ", text).strip()
        return text
