"""BCM-RAG Hybrid Parser — HTML 表格注入器。

将 MinerU 的 HTML 表格（含 rowspan/colspan）注入到 DocumentTree 的对应节点中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from document_tree.models import DocumentTree, NodeType, TreeNode
from document_tree.tree_parser import HeadingInfo
from hybrid_parser.merger import MinerUTable


@dataclass
class TableInjection:
    """一次表格注入的结果。"""
    node_id: str                # 目标 TreeNode ID
    section_number: str         # 章节号
    table_index: int            # MinerU 表格序号
    table_html: str             # HTML 表格内容
    has_rowspan: bool
    has_colspan: bool
    row_count: int
    col_count: int


class TableInjector:
    """将 MinerU HTML 表格注入 DocumentTree。

    策略:
    1. 从 Docling Markdown 中提取每个章节的表格数量
    2. 按顺序将 MinerU 表格与 Docling 表格一一对应
    3. 将 HTML table_body 注入对应 TreeNode 的 markdown 字段
    """

    _TABLE_LINE_RE = re.compile(r"^\|.+\|$")
    _SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+")

    def inject(
        self,
        tree: DocumentTree,
        headings: list[HeadingInfo],
        mineru_tables: list[MinerUTable],
    ) -> list[TableInjection]:
        """将 MinerU 表格注入 DocumentTree。

        Args:
            tree: Docling 构建的 DocumentTree。
            headings: TreeParser 解析的 HeadingInfo 列表。
            mineru_tables: MinerU 提取的 HTML 表格列表。

        Returns:
            注入结果列表。
        """
        # 1. 统计每个章节的 Docling Markdown 表格数量
        section_tables = self._count_tables_by_section(headings)

        # 2. 为每个章节中的表格分配 MinerU 表格
        injections: list[TableInjection] = []
        tbl_idx = 0

        for heading in headings:
            sn = heading.section_number or ""
            tbl_count = section_tables.get(sn, 0)
            if tbl_count == 0:
                continue

            # 找到这个章节在 tree 中的节点
            node_id = self._find_node_by_section(tree, sn)
            if not node_id:
                continue

            node = tree.node_map.get(node_id)
            if not node:
                continue

            # 注入这个章节的所有表格
            for _ in range(tbl_count):
                if tbl_idx >= len(mineru_tables):
                    break

                mt = mineru_tables[tbl_idx]
                rows, cols = self._parse_table_dimensions(mt.table_body)

                injections.append(TableInjection(
                    node_id=node_id,
                    section_number=sn,
                    table_index=tbl_idx,
                    table_html=mt.table_body,
                    has_rowspan=mt.has_rowspan,
                    has_colspan=mt.has_colspan,
                    row_count=rows,
                    col_count=cols,
                ))

                # 注入到 node 的 markdown 字段
                if mt.has_rowspan or mt.has_colspan:
                    node.markdown = (node.markdown or "") + "\n" + mt.table_body

                tbl_idx += 1

        return injections

    # ---- internal ---------------------------------------------------------

    def _count_tables_by_section(
        self, headings: list[HeadingInfo],
    ) -> dict[str, int]:
        """统计每个章节中 Docling Markdown 表格的数量。"""
        counts: dict[str, int] = {}

        for i, heading in enumerate(headings):
            sn = heading.section_number or ""
            text = heading.raw_text
            if not text:
                continue

            # 统计 pipe table 数量（连续 |...| 行算一个表格）
            lines = text.splitlines()
            in_table = False
            tbl_count = 0
            for line in lines:
                s = line.strip()
                if s.startswith("|") and s.endswith("|"):
                    if not in_table:
                        tbl_count += 1
                        in_table = True
                else:
                    in_table = False

            if tbl_count > 0:
                counts[sn] = tbl_count

        return counts

    @staticmethod
    def _find_node_by_section(
        tree: DocumentTree, section_number: str,
    ) -> str | None:
        """在 tree 中查找 section_number 对应的节点 ID。"""
        # 直接查找 path_index
        node_id = tree.path_index.get(section_number)
        if node_id:
            return node_id

        # 遍历 node_map 查找
        for nid, node in tree.node_map.items():
            if node.section_number == section_number:
                return nid

        return None

    @staticmethod
    def _parse_table_dimensions(html: str) -> tuple[int, int]:
        """解析 HTML 表格的行列数。"""
        try:
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table")
            if not table:
                return 0, 0
            rows = table.find_all("tr")
            max_cols = 0
            for row in rows:
                cells = row.find_all(["td", "th"])
                col_count = sum(
                    int(c.get("colspan", 1)) for c in cells
                )
                max_cols = max(max_cols, col_count)
            return len(rows), max_cols
        except Exception:
            return 0, 0
