"""BCM-RAG Hybrid Parser — 图片-章节关联器。

将 MinerU 的图片引用按顺序关联到 Docling 的章节位置。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class LinkedImage:
    """一张已关联到章节的图片。"""
    img_path: str               # MinerU 图片路径
    section_number: str         # 所在章节号，如 "3.3.2"
    section_title: str          # 章节标题
    line_number: int            # 在增强 Markdown 中的行号
    order: int                  # 全局顺序（第几张图片）


class ImageLinker:
    """将 MinerU 图片与 Docling 章节关联。

    策略: 按顺序匹配 — 第 N 个 <!-- image --> 占位符位于第 M 个章节，
    对应第 N 个 MinerU 图片。
    """

    _IMAGE_RE = re.compile(r"!\[图片\]\(([^)]+)\)")
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+")

    def link(
        self,
        enhanced_markdown: str,
        docling_headings: list,  # list of HeadingInfo
    ) -> list[LinkedImage]:
        """将增强 Markdown 中的图片关联到最近的标题。

        Args:
            enhanced_markdown: 经过 HybridDocumentMerger.enhance() 后的文本。
            docling_headings: TreeParser 解析出的 HeadingInfo 列表。

        Returns:
            LinkedImage 列表，按文档顺序排列。
        """
        lines = enhanced_markdown.splitlines()

        # 构建行号 → 章节号的映射
        line_to_section: dict[int, tuple[str, str]] = {}
        for h in docling_headings:
            sn = h.section_number or ""
            title = h.title
            for ln in range(h.line_number, h.content_end + 1):
                line_to_section[ln] = (sn, title)

        # 找到所有 MinerU 图片并关联最近的章节
        result: list[LinkedImage] = []
        order = 0

        for i, line in enumerate(lines, 1):
            m = self._IMAGE_RE.search(line)
            if not m:
                continue

            img_path = m.group(1)
            # 找到这一行所属的章节
            sn, title = line_to_section.get(i, ("", ""))

            # 如果当前行没有章节信息，向前搜索最近的标题
            if not sn:
                for j in range(i - 1, 0, -1):
                    if j in line_to_section:
                        sn, title = line_to_section[j]
                        break

            result.append(LinkedImage(
                img_path=img_path,
                section_number=sn,
                section_title=title,
                line_number=i,
                order=order,
            ))
            order += 1

        return result

    def get_images_by_section(
        self, linked_images: list[LinkedImage],
    ) -> dict[str, list[LinkedImage]]:
        """按章节分组图片。

        Returns:
            section_number → [LinkedImage, ...]
        """
        result: dict[str, list[LinkedImage]] = {}
        for img in linked_images:
            sn = img.section_number or "_unknown"
            result.setdefault(sn, []).append(img)
        return result
