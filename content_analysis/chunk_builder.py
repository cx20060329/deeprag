"""DeepRAG Content Analysis — Context-Aware Chunk Builder.

Rules:
  1. Images → object storage (storage/images/module/img_hash.jpg)
  2. VLM description merged into adjacent text chunk (NOT standalone image chunks)
  3. Tables remain standalone chunks with full breadcrumb context
  4. Min chunk: 80 tokens. Smaller chunks get merged with neighbors.
  5. Each chunk tracks referenced images (image_refs) with storage paths.

Supports DomainConfig for domain-specific chunk type patterns.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from content_analysis.models import (
    Entity, EntityType, SectionTree, SectionNode,
    TextChunk, ChunkList,
)

if TYPE_CHECKING:
    from domain.config import DomainConfig

_MIN_TOKENS = 40           # 降低合并阈值，保留更多独立小chunk
_MAX_TOKENS = 1500          # 大chunk上限，超过则拆分

# BCM default patterns (used when no DomainConfig provided)
_BCM_CHUNK_TYPE_PATTERNS = {
    "function_requirement": r"基本功能要求|功能定义.*描述|功能列表|功能需求规格",
    "division_table": r"设计职责.*分工|责任分工表|工作任务.*CH事业部.*供应商|R&A|S&A",
    "signal_table": r"信号名称|CAN\s*ID|信号位置|Signal Name|PIN脚",
    "state_transition": r"前置条件|触发条件|执行输出|迁移到.*状态",
    "state_machine": r"状态表|状态图|转移表|模式定义|State Table|State Machine",
    "function_desc": r"功能描述|激活逻辑|关闭逻辑|使能条件|关闭条件",
    "config_block": r"配置参数|NVM参数|常数参数|Parameter Name|默认值",
    "fault_handling": r"故障诊断|故障检测|故障处理|故障反应|故障恢复|故障码|DTC\s*码|失效模式|故障注入|故障模拟",
    "output_control": r"输出控制|Output Control|PWM|占空比|优先级",
}

_BCM_KEY_TERM_PATTERNS = [
    r'(以太网|Ethernet|SOMEIP|DoIP|CAN\s*FD|CANFD|LIN|FlexRay|AutoSAR|AUTOSAR|OSEK|UDS|OBD)',
    r'(Bootloader|刷写|烧写|调试工具|编译器|测试盒|休眠唤醒|网络管理|路由功能|诊断路由|诊断功能|信息安全|功能安全)',
    r'(R&A|S&A|CH事业部|供应商|埃泰克|负责|协助|验收|评审)',
    r'(ASIL\s*[A-D]|ISO\s*26262|GB\s*\d+|Q/BAIC|企标)',
    r'(EP1|EP2|PPV|PPAP|SOP|ESO|OTS|DV|PV)',
]

_BCM_MODULE_ABBREV = {
    "VMM": "VMM", "ExteriorLight": "ExtLight", "InteriorLight": "IntLight",
    "Window": "Window", "Lock": "Lock", "TheftProtection": "ATWS",
    "Wiper": "Wiper", "RemoteControl": "Remote", "_TOC": "TOC",
}


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if '一' <= ch <= '鿿')
    other = len(text) - cjk
    return max(1, int(cjk * 1.5 + other * 0.3))


class ChunkBuilder:
    """Build chunks: images → object storage, VLM desc → merged into text chunks.

    Supports DomainConfig for domain-specific chunk patterns.
    Falls back to BCM defaults if no config is provided.
    """

    def __init__(self, storage_dir: str | None = None, domain: "DomainConfig | None" = None):
        if storage_dir is None:
            from config import STORAGE_DIR
            storage_dir = str(STORAGE_DIR)
        self.storage_dir = Path(storage_dir)
        self.images_storage = self.storage_dir / "images"
        self.images_storage.mkdir(parents=True, exist_ok=True)

        # Compile chunk type patterns from DomainConfig or BCM defaults
        if domain is not None:
            self._chunk_type_patterns = {
                k: re.compile(v, re.IGNORECASE)
                for k, v in domain.chunking.chunk_type_patterns.items()
            }
        else:
            self._chunk_type_patterns = {
                k: re.compile(v, re.IGNORECASE)
                for k, v in _BCM_CHUNK_TYPE_PATTERNS.items()
            }

        # Key term patterns
        if domain is not None:
            self._key_term_patterns = [
                re.compile(p) for p in domain.chunking.key_term_patterns
            ]
        else:
            self._key_term_patterns = [
                re.compile(p) for p in _BCM_KEY_TERM_PATTERNS
            ]

        # Module abbreviation map
        if domain is not None:
            self._module_abbrev_map = domain.chunking.module_abbrev_map
        else:
            self._module_abbrev_map = _BCM_MODULE_ABBREV

    def build(
        self,
        content_list: list[dict],
        tree: SectionTree,
        entities: list[Entity],
        images_dir: str = "",
        vlm_results: list[dict] | None = None,
    ) -> ChunkList:
        chunks = ChunkList()

        # Entity lookup by section_path
        section_entities: dict[str, list[Entity]] = {}
        for e in entities:
            section_entities.setdefault(e.section_path, []).append(e)

        # Build item→section lookup
        item_section: dict[int, tuple[str, SectionNode | None]] = {}
        current_sid = "root"
        for idx, item in enumerate(content_list):
            if item.get("type") == "title":
                title_text = self._extract_title_text(item)
                for nid, node in tree.nodes.items():
                    if node.title == title_text:
                        current_sid = nid
                        break
            item_section[idx] = (current_sid, tree.nodes.get(current_sid))

        # VLM lookup by image path
        vlm_lookup: dict[str, dict] = {}
        if vlm_results:
            for vr in vlm_results:
                vlm_lookup[vr.get("image_path", "")] = vr

        # ---- PASS 1: build groups, merging images into adjacent text ----
        groups: list[dict] = []
        current_group: dict | None = None

        for idx, item in enumerate(content_list):
            item_type = item.get("type", "")

            if item_type == "title":
                title_text = self._extract_title_text(item)
                level = item.get("content", {}).get("level", 1)
                if level <= 3:               # h1/h2/h3 都创建新分组，不再只限 h1/h2
                    if current_group and current_group["indices"]:
                        groups.append(current_group)
                    current_group = {
                        "type": "text", "indices": [idx],
                        "title": title_text, "title_level": level,
                        "section_id": item_section.get(idx, ("root", None))[0],
                        "image_refs": [],
                    }
                else:
                    if current_group is None:
                        current_group = {
                            "type": "text", "indices": [], "title": title_text,
                            "title_level": level,
                            "section_id": item_section.get(idx, ("root", None))[0],
                            "image_refs": [],
                        }
                    current_group["indices"].append(idx)
                    if not current_group["title"] or current_group["title_level"] > level:
                        current_group["title"] = title_text
                        current_group["title_level"] = level

            elif item_type == "table":
                if current_group and current_group["indices"]:
                    groups.append(current_group)
                    current_group = None
                groups.append({
                    "type": "table", "indices": [idx], "title": "",
                    "section_id": item_section.get(idx, ("root", None))[0],
                })

            elif item_type == "image":
                img_path = item.get("content", {}).get("image_source", {}).get("path", "")
                # Store image to object storage
                storage_path = self._store_image(img_path, item_section, idx, tree)
                vlm = vlm_lookup.get(img_path, {})
                description = vlm.get("summary", "") or vlm.get("text_content", "")
                image_type = vlm.get("image_type", "unknown")

                # Build image markdown reference
                img_md = f"[IMAGE: {storage_path}]"
                if description:
                    img_md += f"\n[描述: {description[:500]}]"
                if image_type and image_type != "unknown":
                    img_md += f"\n[类型: {image_type}]"

                # Merge into current text group (or start one)
                if current_group is None:
                    sid = item_section.get(idx, ("root", None))
                    current_group = {
                        "type": "text", "indices": [], "title": "",
                        "title_level": 99,
                        "section_id": sid[0],
                        "image_refs": [],
                    }
                current_group["indices"].append(idx)
                current_group.setdefault("image_refs", []).append({
                    "storage_path": storage_path,
                    "description": description[:500],
                    "image_type": image_type,
                    "vlm_states": vlm.get("states", []),
                    "vlm_signals": vlm.get("signals", []),
                    "vlm_transitions": vlm.get("transitions", []),
                })

            elif item_type in ("paragraph", "list"):
                text = self._get_item_text(item)
                if not text.strip():
                    continue
                if current_group is None:
                    sid = item_section.get(idx, ("root", None))
                    current_group = {
                        "type": "text", "indices": [], "title": "",
                        "title_level": 99,
                        "section_id": sid[0],
                        "image_refs": [],
                    }
                current_group["indices"].append(idx)

        if current_group and current_group["indices"]:
            groups.append(current_group)

        # ---- PASS 2: merge small groups ----
        merged = self._merge_small_groups(groups, content_list)

        # ---- PASS 3: build chunks ----
        for g in merged:
            if g["type"] == "table":
                tbl_chunk = self._build_table_chunk(
                    g["indices"][0], content_list, item_section, tree, section_entities,
                )
                if tbl_chunk:
                    chunks.text_chunks.append(tbl_chunk)
            else:
                txt_chunks = self._build_text_chunks(
                    g, content_list, item_section, tree, section_entities,
                )
                for tc in txt_chunks:
                    if tc:
                        chunks.text_chunks.append(tc)

        self._assign_chunk_ids(chunks)
        return chunks

    # ---- image object storage ---------------------------------------------

    def _store_image(
        self, img_path: str, item_section: dict, idx: int, tree: SectionTree,
    ) -> str:
        """Copy image to object storage with organized path: module/section/img_hash.ext"""
        if not img_path or not os.path.exists(img_path):
            return img_path

        sid, node = item_section.get(idx, ("root", None))
        module = self._get_module(node, tree) if node else "unknown"
        if not module:
            module = "unknown"
        section_num = node.number if node else "0"

        # Use original filename hash to avoid duplicates
        import hashlib
        fname = Path(img_path).name
        name_hash = hashlib.md5(fname.encode()).hexdigest()[:8]
        ext = Path(img_path).suffix

        # Organized path: module/section/filename
        rel_dir = f"{module}/{section_num.replace('.', '_')}"
        storage_dir = self.images_storage / rel_dir
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / f"{name_hash}{ext}"

        if not storage_path.exists():
            shutil.copy2(img_path, storage_path)

        return str(storage_path)

    # ---- merge small groups ------------------------------------------------

    def _merge_small_groups(self, groups: list[dict], content_list: list[dict]) -> list[dict]:
        if len(groups) <= 1:
            return groups

        tokens = []
        for g in groups:
            if g["type"] == "table":
                tokens.append(9999)
            else:
                text = self._group_text(g, content_list)
                tokens.append(estimate_tokens(text))

        merged = []
        i = 0
        while i < len(groups):
            g = groups[i]
            t = tokens[i]
            if g["type"] == "table" or t >= _MIN_TOKENS:
                merged.append(g)
                i += 1
                continue

            best_target = i
            if merged and merged[-1]["type"] == "text":
                prev_text = self._group_text(merged[-1], content_list)
                if estimate_tokens(prev_text) < 300:
                    best_target = -1
            if best_target >= 0 and i + 1 < len(groups) and groups[i + 1]["type"] == "text":
                nxt_text = self._group_text(groups[i + 1], content_list)
                if estimate_tokens(nxt_text) < 300:
                    best_target = i + 1

            if best_target == -1:
                prev = merged.pop()
                prev["indices"].extend(g["indices"])
                prev.setdefault("image_refs", []).extend(g.get("image_refs", []))
                if g["title"] and not prev["title"]:
                    prev["title"] = g["title"]
                merged.append(prev)
                i += 1
            elif best_target > i:
                nxt = groups[best_target]
                g["indices"].extend(nxt["indices"])
                g.setdefault("image_refs", []).extend(nxt.get("image_refs", []))
                if nxt["title"] and not g["title"]:
                    g["title"] = nxt["title"]
                merged.append(g)
                i = best_target + 1
            else:
                merged.append(g)
                i += 1

        # Force-merge <30 tokens
        final = []
        for g in merged:
            if g["type"] == "table":
                final.append(g)
                continue
            text = self._group_text(g, content_list)
            t = estimate_tokens(text)
            if t < 30 and final and final[-1]["type"] == "text":
                prev = final.pop()
                prev["indices"].extend(g["indices"])
                prev.setdefault("image_refs", []).extend(g.get("image_refs", []))
                if g["title"] and not prev["title"]:
                    prev["title"] = g["title"]
                final.append(prev)
            else:
                final.append(g)

        return final

    # ---- group text -------------------------------------------------------

    def _group_text(self, group: dict, content_list: list[dict]) -> str:
        texts = []
        for idx in group["indices"]:
            item = content_list[idx]
            if item.get("type") == "title":
                t = self._extract_title_text(item)
                lv = item.get("content", {}).get("level", 1)
                prefix = "#" * min(lv, 3)
                if t.strip():
                    texts.append(f"{prefix} {t.strip()}")
            elif item.get("type") == "image":
                # Placeholder only — actual path+description added in _build_text_chunk
                texts.append("[图片]")
            elif item.get("type") in ("paragraph", "list", "table"):
                pass  # handled separately
            else:
                t = self._get_item_text(item)
                if t.strip():
                    texts.append(t.strip())
        return "\n".join(texts)

    # ---- table chunk ------------------------------------------------------

    def _build_table_chunk(
        self, idx: int, content_list: list[dict],
        item_section: dict, tree: SectionTree, section_entities: dict,
    ) -> TextChunk | None:
        item = content_list[idx]
        html = item.get("content", {}).get("html", "")
        if not html:
            return None

        sid, node = item_section.get(idx, ("root", None))
        module = self._get_module(node, tree) if node else ""
        section_num = node.number if node else ""

        from html.parser import HTMLParser

        class _SimpleTableParser(HTMLParser):
            """Lightweight HTML table parser — replaces hybrid_parser dependency."""
            def __init__(self):
                super().__init__()
                self.headers: list[str] = []
                self.rows: list[list[str]] = []
                self._current_row: list[str] = []
                self._in_cell = False
                self._in_header = False
                self._cell_text = ""

            def handle_starttag(self, tag, attrs):
                if tag in ("th", "td"):
                    self._in_cell = True
                    self._in_header = (tag == "th")
                    self._cell_text = ""

            def handle_endtag(self, tag):
                if tag in ("th", "td"):
                    self._in_cell = False
                    self._current_row.append(self._cell_text.strip())
                elif tag == "tr":
                    if self._current_row:
                        if self._in_header and not self.headers:
                            self.headers = self._current_row
                        else:
                            self.rows.append(self._current_row)
                    self._current_row = []
                    self._in_header = False

            def handle_data(self, data):
                if self._in_cell:
                    self._cell_text += data

        parser = _SimpleTableParser()
        parser.feed(html)
        lines = []
        if parser.headers:
            lines.append(" | ".join(parser.headers))
        for row in parser.rows[:40]:
            lines.append(" | ".join(row))
        table_text = "\n".join(lines)

        chunk_type = self._classify_chunk_type(table_text, node.title if node else "")
        breadcrumb = self._breadcrumb(node, tree)
        before = self._adjacent_text(content_list, idx, -5, -1)
        after = self._adjacent_text(content_list, idx, 1, 5)
        ents = section_entities.get(section_num, [])

        key_terms = self._extract_key_terms(table_text, chunk_type)
        emb_parts = [
            f"[{module}] [{section_num}] [TABLE] [{chunk_type}]",
            f"路径: {breadcrumb}",
            f"表格: {node.title if node else ''}",
        ]
        if key_terms:
            emb_parts.insert(2, f"关键术语: {key_terms}")
        if before:
            emb_parts.append(f"上文: {before[:300]}")
        emb_parts.append(table_text[:2000])
        if after:
            emb_parts.append(f"下文: {after[:300]}")

        return TextChunk(
            chunk_id="",
            chunk_type=chunk_type,
            text=table_text,
            embedding_text="\n".join(emb_parts),
            module=module,
            section_path=section_num,
            section_title=node.title if node else "",
            entities=[e.entity_id for e in ents],
            signals=[e.name for e in ents if e.entity_type == EntityType.SIGNAL],
            states=[e.name for e in ents if e.entity_type == EntityType.STATE],
            parameters=[e.name for e in ents if e.entity_type == EntityType.PARAMETER],
            has_table=True,
            source_indices=[idx],
            token_count=estimate_tokens(table_text),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    # ---- text chunks (with merged images, auto-split large) ----------------

    def _build_text_chunks(
        self, group: dict, content_list: list[dict],
        item_section: dict, tree: SectionTree, section_entities: dict,
    ) -> list[TextChunk]:
        if not group["indices"]:
            return []

        first_idx = group["indices"][0]
        last_idx = group["indices"][-1]
        sid, node = item_section.get(first_idx, ("root", None))
        module = self._get_module(node, tree) if node else ""
        section_num = node.number if node else ""

        text = self._group_text(group, content_list)
        image_refs = group.get("image_refs", [])

        # Always add image storage paths + descriptions (object storage refs)
        for ref in image_refs:
            storage = ref.get("storage_path", "")
            desc = ref.get("description", "")
            if storage:
                text += f"\n[图片: {storage}]"
            if desc:
                text += f"\n[描述: {desc[:500]}]"

        # Clean up [图片] placeholder (actual image refs are added above)
        text = text.replace("[图片]\n", "").replace("[图片]", "")

        if not text.strip():
            return []

        token_count = estimate_tokens(text)
        if token_count <= _MAX_TOKENS:
            segments = [text]
        else:
            segments = self._split_large_text(text)

        results = []
        breadcrumb = self._breadcrumb(node, tree)
        before = self._adjacent_text(content_list, first_idx, -5, -1)
        after = self._adjacent_text(content_list, last_idx, 1, 5)
        ents = section_entities.get(section_num, [])

        for seg_i, seg_text in enumerate(segments):
            if not seg_text.strip():
                continue

            chunk_type = self._classify_chunk_type(seg_text, node.title if node else "")

            # ── 关键术语提取：把chunk中的技术术语注入embedding文本首行 ──
            # 解决"以太网"在大表中被语义平均化的问题
            key_terms = self._extract_key_terms(seg_text, chunk_type)

            emb_parts = [
                f"[{module}] [{section_num}] [{chunk_type}]",
                f"路径: {breadcrumb}",
            ]
            if key_terms:
                emb_parts.insert(1, f"关键术语: {key_terms}")  # 紧跟在类型后面，最大化embedding权重
            if group["title"]:
                emb_parts.append(f"章节: {group['title']}")
            if len(segments) > 1:
                emb_parts.append(f"分段: {seg_i+1}/{len(segments)}")
            if seg_i == 0 and before:
                emb_parts.append(f"上文: {before[:200]}")
            emb_parts.append(seg_text[:3000])
            if seg_i == len(segments) - 1 and after:
                emb_parts.append(f"下文: {after[:200]}")

            seg_title = (group.get("title") or node.title if node else "") or ""
            if len(segments) > 1:
                seg_title = f"{seg_title} (part {seg_i+1})"

            results.append(TextChunk(
                chunk_id="",
                chunk_type=chunk_type,
                text=seg_text,
                embedding_text="\n".join(emb_parts),
                module=module,
                section_path=section_num,
                section_title=seg_title,
                entities=[e.entity_id for e in ents],
                signals=[e.name for e in ents if e.entity_type == EntityType.SIGNAL],
                states=[e.name for e in ents if e.entity_type == EntityType.STATE],
                parameters=[e.name for e in ents if e.entity_type == EntityType.PARAMETER],
                has_image=len(image_refs) > 0 and seg_i == 0,
                image_refs=image_refs if seg_i == 0 else [],
                source_indices=group["indices"],
                token_count=estimate_tokens(seg_text),
                created_at=datetime.now(timezone.utc).isoformat(),
            ))

        return results

    @staticmethod
    def _split_large_text(text: str) -> list[str]:
        """按段落边界拆分大文本，优先在 ## 标题处断开。"""
        parts = re.split(r"(\n(?=##\s))", text)
        current = ""
        segments = []
        for part in parts:
            candidate = current + part
            if estimate_tokens(candidate) > _MAX_TOKENS and current.strip():
                segments.append(current.strip())
                current = part
            else:
                current = candidate
        if current.strip():
            if estimate_tokens(current) > _MAX_TOKENS * 1.5:
                sub_parts = current.split("\n\n")
                sub_current = ""
                for sp in sub_parts:
                    sc = sub_current + ("\n\n" if sub_current else "") + sp
                    if estimate_tokens(sc) > _MAX_TOKENS and sub_current.strip():
                        segments.append(sub_current.strip())
                        sub_current = sp
                    else:
                        sub_current = sc
                if sub_current.strip():
                    segments.append(sub_current.strip())
            else:
                segments.append(current.strip())
        return segments if segments else [text.strip()]


    # ---- context helpers --------------------------------------------------

    def _adjacent_text(self, content_list: list[dict], idx: int,
                       start_offset: int, end_offset: int) -> str:
        texts = []
        for j in range(idx + start_offset, idx + end_offset + 1):
            if 0 <= j < len(content_list):
                item = content_list[j]
                if item.get("type") in ("paragraph", "list"):
                    t = self._get_item_text(item)
                    if t.strip():
                        texts.append(t.strip()[:200])
                elif item.get("type") == "title":
                    t = self._extract_title_text(item)
                    if t.strip():
                        texts.append(f"[{t.strip()[:100]}]")
        return " ".join(texts)

    def _extract_key_terms(self, text: str, chunk_type: str = "") -> str:
        """从chunk文本中提取高价值技术术语，用于增强embedding关键词权重。

        解决"以太网"在572-token大表中被语义平均化的问题：
        将提取到的术语放在embedding_text第二行（紧接类型），embedding模型
        对文本前部的词给予更高权重，确保这些术语不会被后续大量文本稀释。
        """
        found = set()
        for pattern in self._key_term_patterns:
            for m in pattern.finditer(text):
                term = m.group(0).strip()
                if len(term) >= 2:
                    found.add(term)
        # 限制数量：太多术语反而会分散权重
        return " | ".join(sorted(found)[:20]) if found else ""

    @staticmethod
    def _breadcrumb(node: SectionNode | None, tree: SectionTree) -> str:
        if node is None:
            return ""
        parts = []
        current = node
        for _ in range(10):
            parts.append(current.title[:60] if current.title else current.number)
            if current.parent_id and current.parent_id in tree.nodes:
                current = tree.nodes[current.parent_id]
            else:
                break
        parts.reverse()
        return " > ".join(parts)

    # ---- helpers ----------------------------------------------------------

    def _assign_chunk_ids(self, chunks: ChunkList) -> None:
        from collections import defaultdict
        counters: dict[str, int] = defaultdict(int)
        for c in chunks.text_chunks:
            counters[c.module] += 1
            c.chunk_id = f"chunk_{self._abbrev(c.module)}_{counters[c.module]:03d}"

    def _abbrev(self, module: str) -> str:
        return self._module_abbrev_map.get(module, module[:6])

    @staticmethod
    def _get_item_text(item: dict) -> str:
        content = item.get("content", {})
        parts = []
        for pc in content.get("paragraph_content", []):
            if pc.get("type") == "text":
                parts.append(pc.get("content", ""))
        for li in content.get("list_items", []):
            for ic in li.get("item_content", []):
                if ic.get("type") == "text":
                    parts.append(ic.get("content", ""))
        return "".join(parts)

    @staticmethod
    def _extract_title_text(item: dict) -> str:
        content = item.get("content", {})
        parts = []
        for tc in content.get("title_content", []):
            if tc.get("type") == "text":
                parts.append(tc.get("content", ""))
        return "".join(parts).strip()

    @staticmethod
    def _classify_chunk_type(text: str, title: str) -> str:
        combined = title + " " + text[:500]
        for ctype, pattern in self._chunk_type_patterns.items():
            if pattern.search(combined):
                return ctype
        return "general_text"

    # ── 通用模块名推导 ──
    # 从文档章节标题自动推导模块标识，替代硬编码的章节号→模块名映射。
    # 兼容所有文档类型：BCM、信息安全SOR、座椅控制器SOR、RFQ 等。

    def _get_module(self, node, tree) -> str:
        """从章节标题推导模块名。

        向上遍历父节点，取第一个有意义的顶级章节标题作为模块标识。
        如果所有祖先都没有标题，返回空字符串。
        """
        from content_analysis.section_tree import derive_module_name

        current = node
        for _ in range(10):
            if current.title and current.title.strip():
                mod = derive_module_name(current.title, current.number or "")
                if mod:
                    return mod
            if current.parent_id and current.parent_id in tree.nodes:
                current = tree.nodes[current.parent_id]
            else:
                break
        return ""
