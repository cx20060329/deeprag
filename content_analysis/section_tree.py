"""BCM-RAG Content Analysis — Section Tree Builder.

Rebuilds document hierarchy from content_list.json title items.
Tracks page references and table ownership.
"""

from __future__ import annotations

import re
from content_analysis.models import SectionNode, SectionTree

# Chapter number → module name (for BCM document)
_CHAPTER_TO_MODULE: dict[str, str] = {
    "1": "_TOC",
    "2": "VMM",
    "3": "ExteriorLight",
    "4": "InteriorLight",
    "5": "Window",
    "6": "Lock",
    "7": "TheftProtection",
    "8": "Wiper",
    "9": "RemoteControl",
}

# Module name → abbreviation
_MODULE_ABBREV: dict[str, str] = {
    "VMM": "VMM", "ExteriorLight": "ExtLight", "InteriorLight": "IntLight",
    "Window": "Window", "Lock": "Lock", "TheftProtection": "ATWS",
    "Wiper": "Wiper", "RemoteControl": "Remote",
}


class SectionTreeBuilder:
    """Build section tree from content_list title items.

    Preserves page references and table ownership information.
    """

    def build(self, content_list: list[dict]) -> SectionTree:
        """Build section tree from content_list.

        Accepts both page-wrapped format [[page0], [page1], ...]
        and flat format [item, item, ...].

        Each title item has: type="title", content={level, title_content[]}
        Content between title A and next title at same/higher level belongs to A.
        """
        tree = SectionTree()
        root = SectionNode(
            section_id="root", title="Document Root", level=0,
            number="", parent_id=None,
        )
        tree.nodes["root"] = root

        # Detect format and flatten if page-wrapped
        is_page_wrapped = (
            len(content_list) > 0
            and isinstance(content_list[0], list)
        )

        if is_page_wrapped:
            # Build flat_index → page_number map BEFORE flattening
            flat_to_page: dict[int, int] = {}
            flat_idx = 0
            for page_no, page_items in enumerate(content_list):
                for _ in page_items:
                    flat_to_page[flat_idx] = page_no + 1  # 1-based pages
                    flat_idx += 1

            # Flatten for processing
            flat_list: list[dict] = []
            for page in content_list:
                flat_list.extend(page)
        else:
            flat_to_page = {}
            flat_list = content_list

        # First pass: find all titles with their indices
        title_items: list[tuple[int, dict]] = []
        for idx, item in enumerate(flat_list):
            if item.get("type") == "title":
                title_items.append((idx, item))

        if not title_items:
            return tree

        # Stack: (level, node_id)
        stack: list[tuple[int, str]] = [(0, "root")]

        for i, (idx, item) in enumerate(title_items):
            content = item.get("content", {})
            level = content.get("level", 1)
            title_text = self._extract_title_text(content)

            # Determine section number
            number = self._extract_section_number(title_text) or str(len(tree.nodes))

            # Pop stack to find parent
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_id = stack[-1][1] if stack else "root"

            # Generate section_id
            section_id = self._make_section_id(number, level)
            if section_id in tree.nodes:
                section_id = f"{section_id}_{idx}"

            # Content range (items between this title and the next)
            start = idx
            end = title_items[i + 1][0] - 1 if i + 1 < len(title_items) else len(flat_list) - 1

            # Page info: get page of the title item
            page = flat_to_page.get(idx, -1)
            page_range = (page, page)  # Will be expanded below

            # Table ownership: scan content range for table items
            table_indices: list[int] = []
            for j in range(start, end + 1):
                if flat_list[j].get("type") == "table":
                    table_indices.append(j)
                    tree.table_owner[j] = section_id

            node = SectionNode(
                section_id=section_id,
                title=title_text,
                level=level,
                number=number,
                parent_id=parent_id,
                item_range=(start, end),
                page=page,
                page_range=page_range,
                table_indices=table_indices,
                table_count=len(table_indices),
            )

            tree.nodes[section_id] = node
            tree.nodes[parent_id].children.append(section_id)
            tree.number_index[number] = section_id

            # Track page → section mapping
            if page > 0:
                if page not in tree.page_index:
                    tree.page_index[page] = []
                tree.page_index[page].append(section_id)

            stack.append((level, section_id))

        # Second pass: expand page_range for each node by scanning children
        self._expand_page_ranges(tree)

        return tree

    def _expand_page_ranges(self, tree: SectionTree) -> None:
        """For each node, compute page_range as min/max child pages."""
        # Bottom-up: compute page range from leaf sections
        def get_subtree_pages(node: SectionNode) -> set[int]:
            pages: set[int] = set()
            if node.page > 0:
                pages.add(node.page)
            for child_id in node.children:
                child = tree.nodes.get(child_id)
                if child:
                    pages.update(get_subtree_pages(child))
            if pages:
                node.page_range = (min(pages), max(pages))
            return pages

        root = tree.nodes.get("root")
        if root:
            get_subtree_pages(root)

    @staticmethod
    def _extract_title_text(content: dict) -> str:
        """Extract plain text from title_content list."""
        parts = []
        for tc in content.get("title_content", []):
            if tc.get("type") == "text":
                parts.append(tc.get("content", ""))
        return "".join(parts).strip()

    @staticmethod
    def _extract_section_number(title: str) -> str | None:
        """Extract leading section number like '3.3.4.2'."""
        m = re.match(r"(\d+(?:\.\d+)*)\s", title)
        return m.group(1) if m else None

    @staticmethod
    def _make_section_id(number: str, level: int) -> str:
        """Generate section_id from number and level."""
        prefixes = {1: "ch", 2: "sec", 3: "ss", 4: "sss", 5: "leaf"}
        prefix = prefixes.get(level, "node")
        clean = number.replace(".", "_")
        return f"{prefix}_{clean}"

    def get_module(self, node: SectionNode, tree: SectionTree) -> str:
        """Walk up to find chapter node, then map to module name."""
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
        return node.number.split(".")[0] if node.number else ""
