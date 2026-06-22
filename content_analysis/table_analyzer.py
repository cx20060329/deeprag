"""BCM-RAG Content Analysis — Table-Structured Data Extractor.

Replaces generic regex-on-HTML with schema-aware table parsing.
Each BCM table type has a known column schema — we match headers,
extract typed records, and generate precise entities + relationships.

Table types detected:
  - signal_def:   信号名称 | 信号类型 | PIN位置 | 描述
  - can_signal:    CAN信号名称 | 描述 | 编码方式 | 注释
  - state_machine: 序号 | 模式/状态 | 状态说明 | 备注
  - config_param:  ParameterName | Description | Length | Coding | Comments
  - output_pin:    序号 | PIN脚定义 | 功能说明 | 类型 | 信号位置
  - fault_diag:    故障码 | 检测条件 | 反应 | 恢复条件
  - voltage_range: OperatingVoltage | In range | Status | SystemFunction
  - transition:    当前状态 | 事件/条件 | 目标状态

Each record produces:
  - Entities with typed properties (not just a generic dict)
  - Intra-table relationships (row→row within same table)
  - Cross-table links (signal name → referenced in state table)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from bs4 import BeautifulSoup

from content_analysis.models import Entity, EntityType, Relationship, RelType


# ---------------------------------------------------------------------------
# Table type classification
# ---------------------------------------------------------------------------

class TableClass(Enum):
    SIGNAL_DEF = "signal_def"         # 硬线信号定义
    CAN_SIGNAL = "can_signal"         # CAN信号定义
    STATE_MACHINE = "state_machine"   # 状态机/模式定义
    CONFIG_PARAM = "config_param"     # 配置参数/NVM参数
    OUTPUT_PIN = "output_pin"         # 输出控制/PIN定义
    FAULT_DIAG = "fault_diag"         # 故障诊断/DTC
    VOLTAGE_RANGE = "voltage_range"   # 电压范围
    TRANSITION = "transition"         # 状态迁移表
    FUNCTION_LIST = "function_list"   # 简单功能列表 (Function | Comments)
    UNKNOWN = "unknown"


# Header keyword → table class + column schema
# Each schema maps {column_index: (property_name, entity_type_hint)}
_TABLE_SCHEMAS: dict[TableClass, dict] = {
    TableClass.SIGNAL_DEF: {
        "headers": [
            ["信号名称", "signal name"],
            ["信号类型", "signal type", "开关类型", "switch type"],
            ["pin", "pin脚", "信号位置", "连接"],
            ["描述", "说明", "description", "功能说明", "注释", "comments"],
        ],
        "col_map": {
            "name": ["信号名称", "signal name", "名称"],
            "signal_type": ["信号类型", "signal type", "开关类型", "switch type", "类型"],
            "pin": ["pin", "pin脚", "信号位置", "连接", "位置"],
            "description": ["描述", "说明", "description", "功能说明", "注释", "comments"],
        },
        "entity_type": EntityType.SIGNAL,
    },
    TableClass.CAN_SIGNAL: {
        "headers": [
            ["can", "can信号", "can signal"],
            ["描述", "说明", "description"],
            ["编码", "coding", "编码方式"],
            ["注释", "comments"],
        ],
        "col_map": {
            "name": ["can", "can信号", "can signal", "信号名称", "signal name"],
            "description": ["描述", "说明", "description"],
            "coding": ["编码", "coding", "编码方式"],
            "comments": ["注释", "comments"],
        },
        "entity_type": EntityType.CAN_MESSAGE,
    },
    TableClass.STATE_MACHINE: {
        "headers": [
            ["状态", "state", "模式", "mode", "用户模式", "电源模式"],
            ["说明", "描述", "description", "状态说明", "功能"],
        ],
        "col_map": {
            "name": ["状态", "state", "模式", "mode", "名称"],
            "description": ["说明", "描述", "description", "状态说明"],
            "power_mode": ["备注", "电源模式", "power mode", "相关模式"],
        },
        "entity_type": EntityType.STATE,
    },
    TableClass.CONFIG_PARAM: {
        "headers": [
            ["parameter", "参数", "变量", "variable"],
            ["description", "描述", "说明"],
            ["length", "长度", "bits"],
            ["coding", "编码", "conversion", "转换"],
        ],
        "col_map": {
            "name": ["parameter", "参数", "变量", "variable", "名称", "name"],
            "description": ["description", "描述", "说明"],
            "length": ["length", "长度", "bits"],
            "coding": ["coding", "编码", "conversion", "转换"],
            "default": ["默认", "default", "默认值"],
            "comments": ["注释", "comments", "备注", "注"],
        },
        "entity_type": EntityType.PARAMETER,
    },
    TableClass.OUTPUT_PIN: {
        "headers": [
            ["pin", "pin脚", "输出", "信号名称", "name", "io type", "io", "名称"],
            ["功能", "function", "说明", "输出类型", "output type", "remarks", "备注", "功能说明"],
            ["类型", "type", "信号类型"],
        ],
        "col_map": {
            "name": ["pin", "pin脚", "输出", "信号名称", "name", "名称", "io"],
            "function": ["功能", "function", "说明", "功能说明", "output type", "remarks", "备注"],
            "signal_type": ["类型", "type", "信号类型"],
            "position": ["位置", "信号位置", "连接"],
            "description": ["描述", "说明", "备注", "remarks", "remake"],
        },
        "entity_type": EntityType.HARDWARE_PIN,
    },
    TableClass.FAULT_DIAG: {
        "headers": [
            ["故障", "fault", "dtc", "诊断"],
            ["检测", "detection", "条件"],
            ["反应", "reaction", "处理"],
        ],
        "col_map": {
            "name": ["故障", "fault", "dtc", "诊断码", "故障码", "故障名称"],
            "detection": ["检测", "detection", "条件", "检测条件"],
            "reaction": ["反应", "reaction", "处理", "系统反应"],
            "recovery": ["恢复", "recovery", "恢复条件"],
        },
        "entity_type": EntityType.FAULT,
    },
    TableClass.TRANSITION: {
        "headers": [
            ["当前", "current", "源状态", "source", "迁移", "转移", "当前状态", "现态"],
            ["事件", "event", "条件", "触发", "condition", "转移条件", "迁移条件", "触发条件"],
            ["目标状态", "target", "下一", "next", "新状态", "次态", "迁移到"],
        ],
        "col_map": {
            "source_state": ["当前", "current", "转移", "迁移", "源状态", "当前状态"],
            "trigger": ["事件", "event", "条件", "触发", "condition", "迁移条件"],
            "target_state": ["目标", "target", "下一", "next", "新状态", "目标状态"],
            "action": ["动作", "action", "输出", "执行"],
            "transition_text": ["转移", "迁移", "transition"],
        },
        "entity_type": EntityType.STATE,  # produces TRANSITION_TO relationships
    },
    TableClass.FUNCTION_LIST: {
        "headers": [
            ["function", "功能", "feature", "特性"],
            ["comments", "注释", "说明", "备注", "描述", "description"],
        ],
        "col_map": {
            "name": ["function", "功能", "feature", "特性", "名称"],
            "description": ["comments", "注释", "说明", "备注", "描述", "description"],
        },
        "entity_type": EntityType.FUNCTION,
    },
    TableClass.VOLTAGE_RANGE: {
        "headers": [
            ["voltage", "电压"],
            ["range", "范围"],
            ["status", "状态"],
            ["function", "功能"],
        ],
        "col_map": {
            "voltage": ["voltage", "电压"],
            "range": ["range", "范围"],
            "status": ["status", "状态"],
            "function": ["function", "功能"],
        },
        "entity_type": EntityType.PARAMETER,
    },
}


@dataclass
class TableRecord:
    """A single row extracted from a typed table."""
    table_class: TableClass
    row_index: int              # row number within the table
    fields: dict[str, str]      # mapped column → value
    raw_cells: list[str]        # original cell texts
    entity: Entity | None = None  # created entity (if applicable)


@dataclass
class TableAnalysis:
    """Result of analyzing one table."""
    table_class: TableClass
    table_index: int            # position in content_list
    num_rows: int
    records: list[TableRecord] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TableAnalyzer:
    """Schema-aware table parser for BCM functional specifications.

    Usage:
        analyzer = TableAnalyzer()
        for idx, item in enumerate(content_list):
            if item["type"] == "table":
                analysis = analyzer.analyze(idx, item, module, section_path)
                entities.extend(analysis.entities)
                relationships.extend(analysis.relationships)
    """

    # ---- Public API --------------------------------------------------------

    def analyze(
        self,
        table_index: int,
        table_item: dict,
        module: str = "",
        section_path: str = "",
        section_title: str = "",
    ) -> TableAnalysis:
        """Analyze a single table: classify → parse → extract.

        Args:
            table_index: Index of this table in content_list
            table_item: The content_list table item
            module: Module name for entity attribution
            section_path: Section path (e.g. "2.2.1")

        Returns:
            TableAnalysis with classified type, parsed records, entities, relationships.
        """
        html = table_item.get("content", {}).get("html", "")
        if not html:
            return TableAnalysis(
                table_class=TableClass.UNKNOWN,
                table_index=table_index, num_rows=0,
                warnings=["empty html"],
            )

        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")
        if len(rows) < 2:
            return TableAnalysis(
                table_class=TableClass.UNKNOWN,
                table_index=table_index, num_rows=len(rows),
                warnings=["too few rows"],
            )

        # Extract all rows as cell text lists
        all_rows: list[list[str]] = []
        for row in rows:
            cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            if cells:
                all_rows.append(cells)

        if not all_rows:
            return TableAnalysis(
                table_class=TableClass.UNKNOWN,
                table_index=table_index, num_rows=0,
            )

        # Classify table type from headers (first 1-2 rows)
        header_text = " ".join(all_rows[0]).lower()
        if len(all_rows) > 1:
            header_text += " " + " ".join(all_rows[1]).lower()

        table_class, col_map = self._classify(header_text)

        if table_class == TableClass.UNKNOWN:
            return TableAnalysis(
                table_class=TableClass.UNKNOWN,
                table_index=table_index, num_rows=len(all_rows),
                warnings=[f"unclassified: {all_rows[0][:5]}"],
            )

        # Parse rows into typed records using column mapping
        records = self._parse_rows(all_rows, col_map, table_class)

        # Generate entities and relationships
        entities, relationships = self._extract_from_records(
            records, table_class, table_index, module, section_path, section_title,
        )

        return TableAnalysis(
            table_class=table_class,
            table_index=table_index,
            num_rows=len(all_rows),
            records=records,
            entities=entities,
            relationships=relationships,
        )

    # ---- Classification ----------------------------------------------------

    def _classify(self, header_text: str) -> tuple[TableClass, dict]:
        """Match header text against known table schemas.

        Returns (table_class, col_map).
        """
        best_score = 0
        best_class = TableClass.UNKNOWN
        best_col_map: dict = {}

        for tclass, schema in _TABLE_SCHEMAS.items():
            score = 0
            for header_group in schema["headers"]:
                for keyword in header_group:
                    if keyword.lower() in header_text:
                        score += 1
                        break  # one match per group is enough
            if score > best_score:
                best_score = score
                best_class = tclass
                best_col_map = schema["col_map"]

        # Require at least 2 header groups matched
        if best_score < 2:
            return TableClass.UNKNOWN, {}

        return best_class, best_col_map

    # ---- Row parsing -------------------------------------------------------

    def _parse_rows(
        self, rows: list[list[str]], col_map: dict, table_class: TableClass,
    ) -> list[TableRecord]:
        """Parse all rows, mapping cell positions to named fields."""
        # Determine which row is the header (usually row 0)
        header_row_idx = 0
        col_indices = self._map_columns(rows[header_row_idx], col_map)

        # If header row didn't match well, try row 0+1 combined
        if len(col_indices) < 2 and len(rows) > 1:
            combined = [
                f"{rows[0][i] if i < len(rows[0]) else ''} {rows[1][i] if i < len(rows[1]) else ''}"
                for i in range(max(len(rows[0]), len(rows[1])))
            ]
            col_indices = self._map_columns(combined, col_map)
            header_row_idx = 1

        records: list[TableRecord] = []
        data_start = header_row_idx + 1

        for row_idx in range(data_start, len(rows)):
            cells = rows[row_idx]
            # Skip empty rows and separator rows
            if not cells or all(not c for c in cells):
                continue
            if all(c in ("—", "-", "—", "/") for c in cells):
                continue

            fields: dict[str, str] = {}
            for field_name, col_idx in col_indices.items():
                if col_idx < len(cells):
                    fields[field_name] = cells[col_idx].strip()

            # Only keep rows that have at least a name field
            name_val = fields.get("name", "").strip()
            if not name_val:
                # Try to derive name from first non-empty cell
                for cell in cells:
                    if cell.strip():
                        name_val = cell.strip()
                        break

            if name_val:
                fields["name"] = name_val
                records.append(TableRecord(
                    table_class=table_class,
                    row_index=row_idx,
                    fields=fields,
                    raw_cells=cells,
                ))

        # Post-process TRANSITION tables: split "Source -> Target" in source_state
        if table_class == TableClass.TRANSITION:
            self._split_transition_records(records)

        return records

    def _split_transition_records(self, records: list[TableRecord]) -> None:
        """Split 'Source -> Target' format in transition table records.

        BCM transition tables often have a single column containing both source
        and target states in format 'OFF -> ShortTermLighting'. This method
        splits them into separate source_state and target_state fields.
        """
        for rec in records:
            fld = rec.fields

            # Check transition_text field first (raw transition column)
            trans_text = fld.get("transition_text", "").strip()
            if not trans_text:
                # Fall back to source_state if it contains arrow
                trans_text = fld.get("source_state", "").strip()

            if not trans_text:
                continue

            # Try "->" separator
            match = re.split(r"\s*[-→>]+\s*", trans_text, maxsplit=1)
            if len(match) == 2 and match[0] and match[1]:
                src = match[0].strip()
                tgt = match[1].strip()
                # Only overwrite if fields are empty or match the combined value
                if not fld.get("source_state") or fld["source_state"] == trans_text:
                    fld["source_state"] = src
                fld["target_state"] = tgt
                # Keep original combined text for reference
                fld["transition_text"] = trans_text

    def _map_columns(self, header_cells: list[str], col_map: dict) -> dict[str, int]:
        """Map column names to field names based on header cell text.

        Returns {field_name: column_index}.
        """
        result: dict[str, int] = {}
        used_fields: set = set()

        for col_idx, cell_text in enumerate(header_cells):
            cell_lower = cell_text.lower().strip()
            if not cell_lower:
                continue

            for field_name, keywords in col_map.items():
                if field_name in used_fields:
                    continue
                for kw in keywords:
                    if kw in cell_lower:
                        result[field_name] = col_idx
                        used_fields.add(field_name)
                        break

        return result

    # ---- Entity & Relationship extraction ----------------------------------

    def _extract_from_records(
        self,
        records: list[TableRecord],
        table_class: TableClass,
        table_index: int,
        module: str,
        section_path: str,
        section_title: str = "",
    ) -> tuple[list[Entity], list[Relationship]]:
        """Generate typed entities and relationships from parsed records."""
        entities: list[Entity] = []
        relationships: list[Relationship] = []

        if not records:
            return entities, relationships

        etype = _TABLE_SCHEMAS.get(table_class, {}).get(
            "entity_type", EntityType.SIGNAL,
        )

        for rec in records:
            name = rec.fields.get("name", "")
            if not name:
                continue

            eid = f"{etype.value}_{module}_{self._safe_id(name)}"
            props = dict(rec.fields)
            props["table_class"] = table_class.value
            props["table_index"] = table_index
            props["row"] = rec.row_index

            entity = Entity(
                entity_id=eid,
                entity_type=etype,
                name=name,
                module=module,
                section_path=section_path,
                source_item_index=table_index,
                properties=props,
            )
            rec.entity = entity
            entities.append(entity)

        # Generate table-specific relationships
        if table_class == TableClass.SIGNAL_DEF:
            rels = self._rel_signal_def(records, module)
            relationships.extend(rels)

        elif table_class == TableClass.STATE_MACHINE:
            rels = self._rel_state_machine(records, module)
            relationships.extend(rels)

        elif table_class == TableClass.TRANSITION:
            rels = self._rel_transition(records, module, section_title)
            relationships.extend(rels)

        elif table_class == TableClass.OUTPUT_PIN:
            rels = self._rel_output_pin(records, module)
            relationships.extend(rels)

        elif table_class == TableClass.FAULT_DIAG:
            rels = self._rel_fault_diag(records, module)
            relationships.extend(rels)

        elif table_class == TableClass.CONFIG_PARAM:
            rels = self._rel_config_param(records, module)
            relationships.extend(rels)

        elif table_class == TableClass.FUNCTION_LIST:
            # Simple function list: no table-internal relationships
            pass

        return entities, relationships

    # ---- Relationship generators per table type ----------------------------

    def _rel_signal_def(self, records: list[TableRecord], module: str) -> list[Relationship]:
        """Signal table: signal → PIN, signal → signal_type."""
        rels: list[Relationship] = []
        for rec in records:
            sig_id = rec.entity.entity_id if rec.entity else ""
            if not sig_id:
                continue
            fld = rec.fields

            # Signal has PIN
            pin = fld.get("pin", "")
            if pin and len(pin) >= 2:
                pin_id = f"hardware_pin_{module}_{self._safe_id(pin)}"
                rels.append(Relationship(sig_id, pin_id, RelType.OUTPUTS,
                    properties={"relation": "assigned_to_pin"}, weight=0.8))

            # Signal has type → creates CONTROLS relationship
            sig_type = fld.get("signal_type", "")
            if sig_type:
                type_id = f"signal_{module}_{self._safe_id(sig_type)}"
                rels.append(Relationship(sig_id, type_id, RelType.CONTROLS,
                    properties={"relation": "signal_type"}, weight=0.8))

        return rels

    def _rel_state_machine(self, records: list[TableRecord], module: str) -> list[Relationship]:
        """State machine table: sequential states, power mode references."""
        rels: list[Relationship] = []
        prev_eid = ""

        for rec in records:
            eid = rec.entity.entity_id if rec.entity else ""
            if not eid:
                continue

            # Sequential states are related
            if prev_eid:
                rels.append(Relationship(prev_eid, eid, RelType.REFERENCES,
                    properties={"relation": "next_in_table"}))

            # Power mode reference
            power_mode = rec.fields.get("power_mode", "")
            if power_mode and len(power_mode) >= 2:
                pm_id = f"state_{module}_{self._safe_id(power_mode)}"
                rels.append(Relationship(eid, pm_id, RelType.DEPENDS_ON,
                    properties={"relation": "power_mode"}))

            prev_eid = eid

        return rels

    def _rel_transition(self, records: list[TableRecord], module: str, section_title: str = "") -> list[Relationship]:
        """State transition table: source —[trigger]→ target.

        Handles three formats found in BCM documents:
          1. Split columns: source_state | trigger | target_state  → TRANSITION_TO
          2. Combined column: 转移 (Source->Target) | trigger       → TRANSITION_TO (via splitting)
          3. Trigger-only: trigger text only                        → TRIGGERED_BY + inferred
        """
        rels: list[Relationship] = []

        # Infer parent state from section title (e.g. "2.3.1 Inactive状态" → "Inactive")
        import re as _re
        parent_state = ""
        if section_title:
            m = _re.search(r"(\w+)\s*(?:状态|模式|State|Mode)", section_title)
            if m:
                parent_state = m.group(1)

        for rec in records:
            fld = rec.fields
            source = fld.get("source_state", "").strip()
            target = fld.get("target_state", "").strip()
            trigger = fld.get("trigger", "").strip()
            trans_text = fld.get("transition_text", "").strip()

            # Format 1 & 2: source + target available (direct or via splitting)
            if source and target:
                src_id = f"state_{module}_{self._safe_id(source)}"
                tgt_id = f"state_{module}_{self._safe_id(target)}"
                props = {}
                if trigger:
                    props["trigger"] = trigger[:200]
                if trans_text:
                    props["transition_text"] = trans_text
                rels.append(Relationship(
                    src_id, tgt_id, RelType.TRANSITION_TO,
                    properties=props, weight=0.8,
                ))

                if trigger:
                    self._extract_trigger_signals(trigger, tgt_id, module, rels)

            # Format 3: trigger-only — infer source from section context
            elif trigger and not source and not target:
                # Extract signals from trigger text → TRIGGERED_BY
                self._extract_trigger_signals(trigger, None, module, rels)

                # Try to infer TRANSITION_TO from transition_text if available
                if trans_text and "->" in trans_text:
                    parts = _re.split(r"\s*[-→>]+\s*", trans_text, maxsplit=1)
                    if len(parts) == 2 and parts[0] and parts[1]:
                        src_id = f"state_{module}_{self._safe_id(parts[0].strip())}"
                        tgt_id = f"state_{module}_{self._safe_id(parts[1].strip())}"
                        rels.append(Relationship(
                            src_id, tgt_id, RelType.TRANSITION_TO,
                            properties={"trigger": trigger[:200], "transition_text": trans_text},
                            weight=0.7,
                        ))
                elif parent_state:
                    # Try to find target state in trigger text
                    target_match = _re.search(
                        r"(?:进入|迁移到|切换到|变为?|切换到?|进入)\s*(\w+)\s*(?:状态|模式)?",
                        trigger,
                    )
                    if target_match:
                        inferred_target = target_match.group(1)
                        src_id = f"state_{module}_{self._safe_id(parent_state)}"
                        tgt_id = f"state_{module}_{self._safe_id(inferred_target)}"
                        rels.append(Relationship(src_id, tgt_id, RelType.TRANSITION_TO,
                            properties={"trigger": trigger[:120]}, weight=0.6))
                    else:
                        # Try peer records for target inference
                        for rec2 in records:
                            t2 = rec2.fields.get("trigger", "")
                            alt_match = _re.search(
                                r"(?:进入|切换到|变为?)\s*(\w+)\s*(?:状态|模式)?", t2,
                            )
                            if alt_match:
                                inferred_target = alt_match.group(1)
                                if inferred_target != parent_state:
                                    src_id = f"state_{module}_{self._safe_id(parent_state)}"
                                    tgt_id = f"state_{module}_{self._safe_id(inferred_target)}"
                                    rels.append(Relationship(src_id, tgt_id, RelType.TRANSITION_TO,
                                        properties={"trigger": t2[:120]}, weight=0.6))
                                    break

            # Format 4: all fields in a single narrative cell (source but no target)
            elif source and not target:
                full_text = f"{source} {trigger}".strip()
                self._extract_trigger_signals(full_text, None, module, rels)

        return rels

    def _extract_trigger_signals(
        self, text: str, target_id: str | None, module: str,
        rels: list[Relationship],
    ) -> None:
        """Extract TRIGGERED_BY from trigger text mentioning signals."""
        from content_analysis.entity_extractor import _SIGNAL_RE
        seen_sigs: set = set()
        for m in _SIGNAL_RE.finditer(text):
            sig_name = m.group(1)
            if len(sig_name) >= 3 and sig_name not in seen_sigs:
                seen_sigs.add(sig_name)
                sig_id = f"signal_{module}_{self._safe_id(sig_name)}"
                if target_id:
                    rels.append(Relationship(target_id, sig_id, RelType.TRIGGERED_BY,
                        properties={"trigger_text": text[:120]}))
                else:
                    rels.append(Relationship(
                        f"state_{module}_unknown", sig_id, RelType.TRIGGERED_BY,
                        properties={"trigger_text": text[:120]},
                    ))

    def _rel_output_pin(self, records: list[TableRecord], module: str) -> list[Relationship]:
        """Output PIN table: pin → function."""
        rels: list[Relationship] = []
        for rec in records:
            eid = rec.entity.entity_id if rec.entity else ""
            if not eid:
                continue

            func = rec.fields.get("function", "")
            if func and len(func) >= 2:
                func_id = f"function_{module}_{self._safe_id(func)}"
                rels.append(Relationship(eid, func_id, RelType.CONTROLS,
                    properties={"relation": "pin_controls_function"}))

            # PIN signal type
            sig_type = rec.fields.get("signal_type", "")
            if sig_type in ("HSD", "LSD", "H-Bridge", "Relay"):
                rels.append(Relationship(
                    eid,
                    f"signal_{module}_{self._safe_id(sig_type)}",
                    RelType.REFERENCES,
                    properties={"relation": "driver_type"},
                ))

        return rels

    def _rel_fault_diag(self, records: list[TableRecord], module: str) -> list[Relationship]:
        """Fault table: fault → detection_condition, fault → reaction."""
        rels: list[Relationship] = []
        for rec in records:
            eid = rec.entity.entity_id if rec.entity else ""
            if not eid:
                continue
            fld = rec.fields

            # Fault reports to detection condition
            detection = fld.get("detection", "")
            if detection:
                det_id = f"signal_{module}_{self._safe_id(detection)}"
                rels.append(Relationship(eid, det_id, RelType.REPORTS,
                    properties={"relation": "detection_condition"}))

            # Fault recovery → DEPENDS_ON
            recovery = fld.get("recovery", "")
            if recovery:
                rec_id = f"function_{module}_{self._safe_id(recovery)}"
                rels.append(Relationship(eid, rec_id, RelType.DEPENDS_ON,
                    properties={"relation": "recovery_action"}))

        return rels

    def _rel_config_param(self, records: list[TableRecord], module: str) -> list[Relationship]:
        """Config parameter table: param → function it configures."""
        rels: list[Relationship] = []
        for rec in records:
            eid = rec.entity.entity_id if rec.entity else ""
            if not eid:
                continue

            desc = rec.fields.get("description", "") + " " + rec.fields.get("comments", "")
            # Look for function references in description
            from content_analysis.entity_extractor import _FUNCTION_TEXT_RE
            for m in _FUNCTION_TEXT_RE.finditer(desc):
                func_name = m.group(0)
                if len(func_name) >= 2:
                    func_id = f"function_{module}_{self._safe_id(func_name)}"
                    rels.append(Relationship(eid, func_id, RelType.CONFIGURES,
                        properties={"relation": "configures_function"}))

        return rels

    # ---- Helpers -----------------------------------------------------------

    @staticmethod
    def _safe_id(text: str) -> str:
        """Convert arbitrary text to a safe identifier fragment."""
        # Keep alphanumeric, underscore, Chinese chars; replace rest
        safe = re.sub(r"[^\w一-鿿]", "_", text.strip())
        safe = re.sub(r"_+", "_", safe)  # collapse underscores
        return safe[:50].strip("_")
