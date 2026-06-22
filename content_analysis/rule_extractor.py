"""BCM-RAG Rule Extraction — Extracts structured rules from MinerU content_list.

Handles:
  1. State transition rules (前置条件/触发条件/执行输出 pattern)
  2. Voltage range rules (Enter/Exit thresholds)
  3. Signal coding rules (0x0=Inactive, 0x1=Active)
  4. Output control rules (激活逻辑/关闭逻辑 pattern)
  5. Configuration rules (parameter tables)
  6. Fault detection rules (检测/反应/恢复 pattern)

Output: Structured Rule JSON following the BCM Rule Schema.
"""

from __future__ import annotations

import json
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    rule_id: str
    rule_type: str  # entry_condition | exit_condition | transition_guard | activation_rule | deactivation_rule | fault_detection | fault_reaction | signal_value | voltage_rule | config_rule
    module: str
    condition_expr: str
    action: str
    action_type: str = ""  # state_transition | signal_output | function_call | timer_start | alarm | inhibit | enable
    priority: int = 0
    is_blocking: bool = True
    timeout_ms: int = 0
    exception: str = ""
    source_section: str = ""
    source_text: str = ""
    confidence: float = 0.0
    extraction_method: str = "regex"
    condition_signals: list[str] = field(default_factory=list)
    condition_states: list[str] = field(default_factory=list)
    action_signals: list[str] = field(default_factory=list)
    action_target_state: str = ""
    preconditions: list[str] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)
    source_page: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [], 0, 0.0, False) or k in ("rule_id", "rule_type", "module", "action")}


# ---------------------------------------------------------------------------
# Content List Helpers
# ---------------------------------------------------------------------------

def _extract_title_text(content: dict) -> str:
    parts = content.get("title_content", [])
    return "".join(p.get("content", "") for p in parts if p.get("type") == "text")


def _extract_para_text(content: dict) -> str:
    parts = content.get("paragraph_content", [])
    return "".join(p.get("content", "") for p in parts if p.get("type") == "text")


def _extract_list_items(content: dict) -> list[str]:
    items = content.get("list_items", [])
    result = []
    for li in items:
        parts = li.get("item_content", [])
        text = "".join(p.get("content", "") for p in parts if p.get("type") == "text")
        if text.strip():
            result.append(text.strip())
    return result


def _extract_table_rows(content: dict) -> list[list[str]]:
    """Extract table rows from MinerU table content (HTML-based)."""
    html = content.get("html", "")
    if not html:
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for tr in soup.find_all("tr"):
            cells = []
            for cell in tr.find_all(["td", "th"]):
                text = cell.get_text(strip=True)
                cells.append(text)
            if any(c for c in cells):
                rows.append(cells)
        return rows
    except ImportError:
        # Fallback: simple regex-based extraction
        rows = []
        tr_matches = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        for tr_html in tr_matches:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.DOTALL)
            clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if any(c for c in clean_cells):
                rows.append(clean_cells)
        return rows


# ---------------------------------------------------------------------------
# Rule ID Generator
# ---------------------------------------------------------------------------

def _make_rule_id(module: str, rule_type: str, text: str) -> str:
    """Generate a unique rule ID."""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    short_type = rule_type.replace("_rule", "").replace("_condition", "").replace("_", "-")
    short_type = short_type[:10]
    return f"RULE_{module}_{short_type}_{h}"


# ---------------------------------------------------------------------------
# Extractor 1: State Transition Rules
# ---------------------------------------------------------------------------

class StateTransitionExtractor:
    """Extract state transition rules from the structured content_list.

    The BCM document follows this pattern:

        Title: "X.X.X.X 迁移到TargetState状态"
        Paragraph: "前置条件（&&）："
        List: [conditions...]
        Paragraph: "触发条件：" or "触发条件（||）："
        List: [triggers...]
        Paragraph: "执行输出："
        List: [actions...]
        Paragraph: "注：" (optional)
        List: [notes...]
    """

    def extract(self, items: list[dict]) -> list[Rule]:
        rules = []
        current_section = ""
        current_module = "Unknown"
        section_state_map = {}  # section_number → {state: name, module: name}
        section_module_cache = {}  # cache section_num → module

        i = 0
        while i < len(items):
            item = items[i]
            content = item.get("content", {})
            typ = item.get("type", "")

            if typ == "title":
                title_text = _extract_title_text(content)

                # Track section number
                sec_match = re.match(r"(\d+(?:\.\d+)*)\s", title_text)
                if sec_match:
                    current_section = sec_match.group(1)
                    # Inherit module from parent section
                    current_module = self._resolve_module(
                        current_section, section_module_cache
                    )

                # Infer module from top-level section number
                top_match = re.match(r"(\d+)\s", title_text)
                if top_match:
                    top_num = int(top_match.group(1))
                    mapped = self._section_to_module(top_num)
                    section_module_cache[current_section] = mapped
                    current_module = mapped

                # Build section info
                if current_section not in section_state_map:
                    section_state_map[current_section] = {
                        "module": current_module,
                        "title": title_text,
                    }
                else:
                    section_state_map[current_section]["module"] = current_module
                    section_state_map[current_section]["title"] = title_text

                # Infer source state from section title (e.g., "Abandoned模式")
                state_mode = re.search(r"(\w+)模式", title_text)
                if state_mode:
                    section_state_map[current_section]["state"] = state_mode.group(1)

                # Detect transition target: "迁移到X状态"
                trans_match = re.search(r"迁移到(\w+)状态", title_text)
                if trans_match:
                    section_state_map[current_section]["target_state"] = (
                        trans_match.group(1)
                    )

            # Detect transition blocks
            if typ == "paragraph":
                para_text = _extract_para_text(content)

                if "前置条件" in para_text and "&&" in para_text:
                    section_info = section_state_map.get(current_section, {})
                    # Use the module from the map, not from current_module
                    rule_module = section_info.get("module", current_module)
                    if rule_module == "Unknown" and current_module != "Unknown":
                        rule_module = current_module

                    rule = self._parse_transition_block(
                        items, i, current_section, section_state_map, rule_module
                    )
                    if rule:
                        rules.append(rule)

            i += 1

        return rules

    def _resolve_module(self, section: str, cache: dict) -> str:
        """Resolve module from section hierarchy with caching."""
        if section in cache:
            return cache[section]
        # Try parent sections
        parts = section.split(".")
        for n in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:n])
            if parent in cache:
                cache[section] = cache[parent]
                return cache[parent]
        # Try top-level number
        try:
            top = int(parts[0])
            mod = self._section_to_module(top)
            cache[section] = mod
            return mod
        except (ValueError, IndexError):
            return "Unknown"

    def _parse_transition_block(
        self,
        items: list[dict],
        start_idx: int,
        section: str,
        state_map: dict,
        rule_module: str = "Unknown",
    ) -> Rule | None:
        """Parse a single transition block and return a Rule."""
        block = {
            "preconditions": [],
            "trigger_conditions": [],
            "actions": [],
            "notes": [],
        }

        current_field = "preconditions"
        j = start_idx + 1

        while j < len(items) and j < start_idx + 12:
            item = items[j]
            typ = item.get("type", "")
            content = item.get("content", {})

            if typ == "list":
                items_text = _extract_list_items(content)
                block[current_field].extend(items_text)
            elif typ == "paragraph":
                para = _extract_para_text(content)
                if "执行输出" in para:
                    current_field = "actions"
                elif "触发条件" in para:
                    current_field = "trigger_conditions"
                elif "注" in para and len(para) < 10:
                    current_field = "notes"
                elif "前置条件" in para:
                    current_field = "preconditions"
                elif "如果" in para or "则" in para:
                    # Conditional action paragraph: treat as action if we're in actions,
                    # otherwise as extra trigger/precondition note
                    if current_field == "actions" or "执行" in para:
                        current_field = "actions"
                        block[current_field].append(para)
                # else: skip intermediate paragraphs, continue scanning
            elif typ == "title":
                break
            j += 1

        if not block["preconditions"] and not block["actions"]:
            return None

        # Determine module and state context
        section_info = state_map.get(section, {})
        module = rule_module if rule_module != "Unknown" else section_info.get("module", "Unknown")
        target_state = section_info.get("target_state", "")

        # Infer source state: look at parent section
        source_state = ""
        parent_section = ".".join(section.split(".")[:-1]) if "." in section else ""
        for sec_key, info in state_map.items():
            if sec_key.startswith(parent_section) and "state" in info:
                source_state = info["state"]
                break

        # Build condition expression
        all_conditions = block["preconditions"] + block["trigger_conditions"]
        condition_expr = " AND ".join(all_conditions) if all_conditions else ""

        # Build action
        action = "; ".join(block["actions"]) if block["actions"] else f"ENTER {target_state}"
        if target_state and not any("迁移" in a for a in block["actions"]):
            action = f"ENTER {target_state}; " + action

        # Determine rule type
        if target_state and module != "Unknown":
            rule_type = "transition_guard"
            action_type = "state_transition"
        elif "使能" in action or "enable" in action.lower():
            rule_type = "activation_rule"
            action_type = "function_call"
        elif "关闭" in action or "off" in action.lower():
            rule_type = "deactivation_rule"
            action_type = "function_call"
        else:
            rule_type = "activation_rule"
            action_type = "signal_output"

        # Extract signals from conditions and actions
        condition_signals = re.findall(r"\b([A-Z][A-Za-z0-9_]{3,}(?:Sts|St|Mode|Req)?)\b", condition_expr)
        action_signals = re.findall(r"\b([A-Z][A-Za-z0-9_]{3,}(?:Sts|St|Mode|Req)?)\b", action)

        # Extract states
        condition_states = re.findall(r"(\w+)状态", condition_expr)
        condition_states += re.findall(r"(\w+)模式", condition_expr)

        # Build rule
        source_text = "; ".join(
            block["preconditions"] + block["trigger_conditions"] + block["actions"]
        )

        rule = Rule(
            rule_id=_make_rule_id(module, rule_type, source_text),
            rule_type=rule_type,
            module=module,
            condition_expr=condition_expr[:500],
            action=action[:500],
            action_type=action_type,
            source_section=section,
            source_text=source_text[:1000],
            confidence=0.80,  # Regex extraction from structured doc
            extraction_method="regex_structured",
            condition_signals=list(set(condition_signals))[:10],
            condition_states=list(set(condition_states))[:10],
            action_signals=list(set(action_signals))[:10],
            action_target_state=target_state,
            preconditions=block["preconditions"],
            trigger_conditions=block["trigger_conditions"],
        )

        if block["notes"]:
            rule.exception = "; ".join(block["notes"])

        return rule

    @staticmethod
    def _section_to_module(section_num: int) -> str:
        """Map top-level section number to module name."""
        MODULE_MAP = {
            1: "Overview",
            2: "VMM",
            3: "ExteriorLight",
            4: "InteriorLight",
            5: "Window",
            6: "Lock",
            7: "Wiper",
            8: "Wiper",
            9: "RemoteControl",
            10: "ATWS",
            11: "Network",
        }
        return MODULE_MAP.get(section_num, f"Module_{section_num}")


# ---------------------------------------------------------------------------
# Extractor 2: Table-Based Rules (Voltage, Config, Signal Coding)
# ---------------------------------------------------------------------------

class TableRuleExtractor:
    """Extract rules from table content in MinerU output.

    Handles:
    - Voltage range tables → voltage_rule
    - Config parameter tables → config_rule
    - Signal coding tables → signal_value rules
    """

    def extract(self, items: list[dict], section_module_map: dict) -> list[Rule]:
        rules = []
        current_section = ""
        current_module = "Unknown"
        section_module_cache = {}

        for i, item in enumerate(items):
            content = item.get("content", {})
            typ = item.get("type", "")

            if typ == "title":
                title_text = _extract_title_text(content)
                sec_match = re.match(r"(\d+(?:\.\d+)*)\s", title_text)
                if sec_match:
                    current_section = sec_match.group(1)
                    # Resolve module from section hierarchy
                    parts = current_section.split(".")
                    for n in range(len(parts), 0, -1):
                        parent = ".".join(parts[:n])
                        if parent in section_module_cache:
                            current_module = section_module_cache[parent]
                            break
                    else:
                        try:
                            current_module = StateTransitionExtractor._section_to_module(int(parts[0]))
                        except (ValueError, IndexError):
                            current_module = "Unknown"
                # Cache top-level module
                top_match = re.match(r"(\d+)\s", title_text)
                if top_match:
                    top_num = int(top_match.group(1))
                    mapped = StateTransitionExtractor._section_to_module(top_num)
                    section_module_cache[current_section] = mapped
                    current_module = mapped

            if typ == "table":
                rows = _extract_table_rows(content)
                if not rows or len(rows) < 2:
                    continue

                header = rows[0]

                # Detect table type
                header_text = " ".join(header).lower()

                if any(w in header_text for w in ("电压", "voltage", "voltage range")):
                    rules.extend(
                        self._parse_voltage_table(rows, current_section, current_module)
                    )
                elif any(w in header_text for w in ("参数", "parameter", "配置", "config", "nvm")):
                    rules.extend(
                        self._parse_config_table(rows, current_section, current_module)
                    )
                elif any(w in header_text for w in ("信号", "signal", "报文", "message", "can")):
                    rules.extend(
                        self._parse_signal_table(rows, current_section, current_module)
                    )
                elif any(w in header_text for w in ("故障", "fault", "dtc", "诊断")):
                    rules.extend(
                        self._parse_fault_table(rows, current_section, current_module)
                    )

        return rules

    def _parse_voltage_table(
        self, rows: list[list[str]], section: str, module: str
    ) -> list[Rule]:
        """Parse voltage range table → voltage_rule."""
        rules = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            voltage_text = row[0] if len(row) > 0 else ""
            status_text = row[1] if len(row) > 1 else ""
            func_text = row[2] if len(row) > 2 else ""

            # Parse "9V-16V（Enter: ↑9V, ↓16V）"
            v_match = re.match(
                r"([\d.]+)\s*V?\s*[-–]\s*([\d.]+)\s*V?", voltage_text
            )
            if not v_match:
                # Try ">18V" or "18V以上"
                v_match_gt = re.match(r"[>＞]\s*([\d.]+)\s*V?", voltage_text)
                if v_match_gt:
                    v_min = float(v_match_gt.group(1))
                    v_max = 99.0
                else:
                    continue
            else:
                v_min = float(v_match.group(1))
                v_max = float(v_match.group(2))

            # Parse enter/exit thresholds
            enter_match = re.search(r"Enter[:\s]*[↑]([\d.]+)\s*V?", voltage_text, re.IGNORECASE)
            exit_match = re.search(r"[↓]([\d.]+)\s*V?", voltage_text)

            enter_v = float(enter_match.group(1)) if enter_match else v_min
            exit_v = float(exit_match.group(1)) if exit_match else v_max

            # Normalize status name
            status_name = status_text.strip()
            status_name = re.sub(r"\s+", " ", status_name)

            rule = Rule(
                rule_id=_make_rule_id(module, "voltage_rule", f"{v_min}-{v_max} {status_name}"),
                rule_type="voltage_rule",
                module=module,
                condition_expr=f"Voltage >= {enter_v} AND Voltage <= {exit_v}",
                action=f"ENTER VoltageMode: {status_name}",
                action_type="state_transition",
                source_section=section,
                source_text=f"Voltage: {voltage_text} | Status: {status_text} | Function: {func_text}",
                confidence=0.92,
                extraction_method="regex_table",
                condition_signals=["SystemVoltage"],
                action_target_state=status_name,
            )
            rules.append(rule)

        return rules

    def _parse_config_table(
        self, rows: list[list[str]], section: str, module: str
    ) -> list[Rule]:
        """Parse config parameter table → config_rule."""
        rules = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            param_name = row[0].strip() if len(row) > 0 else ""
            description = row[1].strip() if len(row) > 1 else ""
            coding = row[3].strip() if len(row) > 3 else ""

            if not param_name or len(param_name) < 3:
                continue

            # Parse coding values: "00=not use, 01=不上can, 10=上can, 11=上can（怀挡）"
            coding_values = {}
            if "=" in coding:
                parts = re.split(r"[,，]\s*", coding)
                for part in parts:
                    kv = re.match(r"(\w+)\s*[=＝]\s*(.+)", part.strip())
                    if kv:
                        coding_values[kv.group(1)] = kv.group(2).strip()

            if coding_values:
                for val, meaning in coding_values.items():
                    rule = Rule(
                        rule_id=_make_rule_id(
                            module, "config_rule", f"{param_name}={val}"
                        ),
                        rule_type="config_rule",
                        module=module,
                        condition_expr=f"{param_name} = {val}",
                        action=f"Config: {meaning}",
                        action_type="function_call",
                        source_section=section,
                        source_text=f"{param_name}: {description} | {val} = {meaning}",
                        confidence=0.90,
                        extraction_method="regex_table",
                    )
                    rules.append(rule)

        return rules

    def _parse_signal_table(
        self, rows: list[list[str]], section: str, module: str
    ) -> list[Rule]:
        """Parse signal definition table → signal_value rules."""
        rules = []

        # Determine column mapping
        if not rows:
            return rules
        header = rows[0]
        header_lower = [h.lower() for h in header]

        # Find signal name column
        name_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("name", "名称", "信号"))),
            0,
        )
        # Find coding/values column
        coding_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("coding", "编码", "value", "值", "信号值"))),
            -1,
        )
        # Find description column
        desc_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("description", "描述", "remark", "备注", "说明"))),
            -1,
        )

        for row in rows[1:]:
            if len(row) <= name_col:
                continue
            signal_name = row[name_col].strip()
            if not signal_name or len(signal_name) < 2:
                continue

            # Parse coding values
            coding_text = row[coding_col].strip() if coding_col >= 0 and coding_col < len(row) else ""
            desc_text = row[desc_col].strip() if desc_col >= 0 and desc_col < len(row) else ""

            if "=" in coding_text and "x" in coding_text:
                # Pattern: "0x0=Inactive, 0x1=Active, 0x2=Invalid"
                parts = re.split(r"[,，]\s*", coding_text)
                for part in parts:
                    kv = re.match(r"(0x[\dA-Fa-f]+)\s*[=＝]\s*(.+)", part.strip())
                    if kv:
                        rule = Rule(
                            rule_id=_make_rule_id(
                                module, "signal_value", f"{signal_name}={kv.group(1)}"
                            ),
                            rule_type="signal_value",
                            module=module,
                            condition_expr=f"{signal_name} = {kv.group(1)}",
                            action=f"Signal Value: {kv.group(2)}",
                            action_type="signal_output",
                            source_section=section,
                            source_text=f"{signal_name}: {kv.group(1)} = {kv.group(2)}",
                            confidence=0.93,
                            extraction_method="regex_table",
                            condition_signals=[signal_name],
                        )
                        rules.append(rule)
            elif "x" in coding_text:
                # Just has a hex value, not multiple
                rule = Rule(
                    rule_id=_make_rule_id(module, "signal_value", f"{signal_name}_{coding_text}"),
                    rule_type="signal_value",
                    module=module,
                    condition_expr=f"{signal_name} exists",
                    action=f"{signal_name} = {coding_text} ({desc_text})",
                    action_type="signal_output",
                    source_section=section,
                    source_text=f"{signal_name}: {coding_text} | {desc_text}",
                    confidence=0.85,
                    extraction_method="regex_table",
                    condition_signals=[signal_name],
                )
                rules.append(rule)

        return rules

    def _parse_fault_table(
        self, rows: list[list[str]], section: str, module: str
    ) -> list[Rule]:
        """Parse fault/DTC table → fault_detection / fault_reaction rules."""
        rules = []

        # Determine column mapping
        if not rows:
            return rules
        header = rows[0]
        header_lower = [h.lower() for h in header]

        dtc_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("dtc", "故障码", "code"))),
            0,
        )
        detection_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("detection", "检测", "监测", "故障描述"))),
            -1,
        )
        reaction_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("reaction", "反应", "响应", "行为"))),
            -1,
        )
        recovery_col = next(
            (i for i, h in enumerate(header_lower) if any(w in h for w in ("recovery", "恢复", "解除"))),
            -1,
        )

        for row in rows[1:]:
            dtc = row[dtc_col].strip() if len(row) > dtc_col else ""
            detection = row[detection_col].strip() if detection_col >= 0 and detection_col < len(row) else ""
            reaction = row[reaction_col].strip() if reaction_col >= 0 and reaction_col < len(row) else ""
            recovery = row[recovery_col].strip() if recovery_col >= 0 and recovery_col < len(row) else ""

            if not detection and not reaction:
                continue

            source_text = f"DTC: {dtc} | Detection: {detection} | Reaction: {reaction} | Recovery: {recovery}"

            if detection:
                rules.append(
                    Rule(
                        rule_id=_make_rule_id(module, "fault_detection", source_text),
                        rule_type="fault_detection",
                        module=module,
                        condition_expr=detection[:500],
                        action=f"SET DTC: {dtc}" if dtc else "Record Fault",
                        action_type="signal_output",
                        source_section=section,
                        source_text=source_text[:1000],
                        confidence=0.88,
                        extraction_method="regex_table",
                    )
                )

            if reaction:
                rules.append(
                    Rule(
                        rule_id=_make_rule_id(module, "fault_reaction", source_text),
                        rule_type="fault_reaction",
                        module=module,
                        condition_expr=f"DTC {dtc} is SET" if dtc else detection[:200],
                        action=reaction[:500],
                        action_type="function_call",
                        source_section=section,
                        source_text=source_text[:1000],
                        confidence=0.88,
                        extraction_method="regex_table",
                        exception=recovery if recovery else "",
                    )
                )

        return rules


# ---------------------------------------------------------------------------
# Main Rule Extraction Pipeline
# ---------------------------------------------------------------------------

class RuleExtractionPipeline:
    """Combined rule extraction: transitions + tables → structured Rule JSON."""

    def __init__(self):
        self.transition_extractor = StateTransitionExtractor()
        self.table_extractor = TableRuleExtractor()

    def extract_all(
        self,
        content_list_path: str | Path = "output/bcm_mineru/content_list.json",
    ) -> dict:
        """Extract all rules from MinerU content_list.

        Returns:
            {
                "rules": [...],
                "stats": {
                    "total": int,
                    "by_type": {...},
                    "by_module": {...},
                    "by_method": {...},
                }
            }
        """
        content_list_path = Path(content_list_path)
        with open(content_list_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Unwrap nested list
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], list):
            items = data[0]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError(f"Unexpected content_list structure: {type(data)}")

        print(f"RuleExtractionPipeline: processing {len(items)} items...")

        # Phase 1: State transition rules
        transition_rules = self.transition_extractor.extract(items)
        print(f"  Phase 1 (Transitions): {len(transition_rules)} rules")

        # Phase 2: Table-based rules
        table_rules = self.table_extractor.extract(items, {})
        print(f"  Phase 2 (Tables):      {len(table_rules)} rules")

        # Merge & deduplicate
        all_rules = transition_rules + table_rules
        unique_rules = self._deduplicate(all_rules)
        duplicates = len(all_rules) - len(unique_rules)
        print(f"  Dedup:                 {duplicates} duplicates removed")
        print(f"  Total:                 {len(unique_rules)} unique rules")

        # Stats
        stats = self._compute_stats(unique_rules)

        return {
            "rules": [r.to_dict() for r in unique_rules],
            "stats": stats,
        }

    def _deduplicate(self, rules: list[Rule]) -> list[Rule]:
        """Remove duplicate rules by rule_id."""
        seen = set()
        unique = []
        for r in rules:
            if r.rule_id not in seen:
                seen.add(r.rule_id)
                unique.append(r)
        return unique

    def _compute_stats(self, rules: list[Rule]) -> dict:
        """Compute extraction statistics."""
        by_type = defaultdict(int)
        by_module = defaultdict(int)
        by_method = defaultdict(int)

        for r in rules:
            by_type[r.rule_type] += 1
            by_module[r.module] += 1
            by_method[r.extraction_method] += 1

        return {
            "total": len(rules),
            "by_type": dict(by_type),
            "by_module": dict(by_module),
            "by_method": dict(by_method),
        }

    def save(
        self,
        rules_data: dict,
        output_path: str | Path = "output/content_analysis/rules.json",
    ) -> Path:
        """Save rules to JSON."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rules_data, f, ensure_ascii=False, indent=2)
        print(f"Rules saved: {output_path}")
        return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    input_path = sys.argv[1] if len(sys.argv) > 1 else "output/bcm_mineru/content_list.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/content_analysis/rules.json"

    pipeline = RuleExtractionPipeline()
    rules_data = pipeline.extract_all(input_path)
    pipeline.save(rules_data, output_path)

    print()
    print("=== Rule Extraction Summary ===")
    stats = rules_data["stats"]
    print(f"Total rules: {stats['total']}")
    print(f"By type:")
    for t, c in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
        print(f"  {t:25s}: {c:3d}")
    print(f"By module:")
    for m, c in sorted(stats["by_module"].items(), key=lambda x: -x[1])[:10]:
        print(f"  {m:25s}: {c:3d}")
    print(f"By method:")
    for m, c in sorted(stats["by_method"].items(), key=lambda x: -x[1]):
        print(f"  {m:25s}: {c:3d}")
