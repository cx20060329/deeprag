"""DeepRAG Content Analysis — Entity Extractor (full coverage).

Extracts all entity types and relationship types from parsed documents.
Uses DomainConfig for domain-specific patterns — works with any domain.

Default patterns are BCM automotive (backward compatible).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from content_analysis.models import Entity, EntityType, Relationship, RelType, SectionTree
from content_analysis.table_analyzer import TableAnalyzer, TableClass

if TYPE_CHECKING:
    from domain.config import DomainConfig


# ---------------------------------------------------------------------------
# Default BCM patterns (backward compatible)
# These are used when no DomainConfig is provided.
# ---------------------------------------------------------------------------

_BCM_PATTERNS: dict[str, str] = {
    "signal": (
        r"\b([A-Z][A-Za-z0-9_]{3,}(?:Sts|Mode|Status|SW|Cmd|Req|Relay|Signal|Msg|"
        r"Active|Enable|Disable|Request|State|Value|Cnt|Cntrl)?)\b"
    ),
    "state": (
        r"\b(Inactive|Active|Driving|Convenience|Abandoned|"
        r"Disarmed|Armed|PreArmed|Alarm|Wakeup|Sleep|Standby|"
        r"OFF|ACC|ON|Idle|Run|Crank|Charging|Discharging)\b"
    ),
    "state_cn": r"(\w{2,20})\s*(?:状态|模式)\b",
    "parameter": r"\b(Cfg|cfg|Cal|cal|NVM_|nvm_)[A-Za-z0-9_]+\b",
    "fault": r"故障|失效|丢失|超时|短路|断路|DTC|报警|异常|Fault|Error|Failure",
    "can_id": r"\b(0x[0-9A-Fa-f]{3,8})\b",
    "can_message": (
        r"\b(?:CAN\s*(?:报文|消息|信号|Message|Frame|ID)\s*[:：]?\s*)?"
        r"((?:BCM|VCU|PEPS|ESC|TCM|ABS|EMS|IC|GW|BMS)_[A-Za-z0-9_]{3,})\b"
    ),
    "pin": (
        r"\b(PIN\s*\d{1,3}|Pin\s*\d{1,3}|pin\s*\d{1,3}|"
        r"KL30|KL15|KL31|KL87|GND|VBAT|"
        r"(?:HSD|LSD|H-Bridge|Relay)\s*\d*)\b"
    ),
    "function_title": r"(?:功能描述|功能说明|激活逻辑|关闭逻辑|使能条件|关闭条件|控制逻辑)",
    "function_text": (
        r"(GlobalClose|AutoLock|CrashUnlock|FollowMeHome|"
        r"WelcomeLight|ComingHome|LeavingHome|CorneringLight|"
        r"AutoFold|AutoFoldBack|GlobalOpen|KeyReminder|"
        r"[一-鿿]{2,15}(?:功能|控制|管理|保护|检测|诊断))"
    ),
    # Relationship patterns
    "transition": r"迁移到\s*(\w+)\s*状态",
    "output_signal": r"发送\s*(?:CAN)?\s*信号\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)\s*:?\s*(\w+)?",
    "output_generic": r"(?:输出|驱动|拉高|拉低|置位|Set|Reset|Toggle)\s*(?:信号|PIN)?\s*(\w{3,30})",
    "trigger": (
        r"(?:当|一旦|若|如果)\s*"
        r"(\w{3,30})\s*(?:==?|≠|不等于?|大于|小于|变为?|切换到?|设置为?)\s*"
        r"(\w{0,20})\s*(?:时|后|之际|时，|时。|则|,)\s*(?:触发|激活|唤醒|启动|进入|退出|执行)"
    ),
    "trigger_edge": r"(\w+)\s*(?:的)?\s*(?:上升沿|下降沿|边沿|变化)\s*(?:触发|激活)\s*(\w+)",
    "trigger_kw": r"(?:触发条件|触发源|唤醒源|激活条件)[：:]\s*(\w{3,30})",
    "depends": (
        r"(\w{3,30})\s*(?:依赖于|依赖|取决于|取决于信号|的前提是|的前置条件是|必要条件)"
        r"\s*(\w{3,30})"
    ),
    "depends_state": (
        r"(?:需要|要求|前提)\s*(\w+)\s*(?:处于|为|=)\s*(\w+)\s*(?:状态|模式)?"
        r"\s*(?:才能|方可|可以|允许)"
    ),
    "controls": (
        r"(\w{2,30})\s*(?:控制|管控(?:逻辑)?|管理)\s*(\w{2,30})"
        r"\s*(?:的)?\s*(?:输出|功能|状态|电源|继电器)"
    ),
    "controls_enable": r"(\w{2,30})\s*(?:使能|启用|禁用|关闭|打开)\s*(\w{2,30})",
    "requires": r"(?:需要|要求|必须有|必须存在|必要条件)[：:]?\s*(\w{3,30})\s*(?:信号|状态|报文|配置)?",
    "configures": (
        r"(\w{3,30})\s*(?:配置|标定|设置|参数)\s*(?:了|为|成)?\s*(\w{2,30})"
        r"\s*(?:的)?\s*(?:功能|参数|值|选项|阈值|时间)"
    ),
    "configures_via": r"(?:通过|使用|利用|经由)\s*(\w{3,30})\s*(?:配置|标定|设置)\s*(\w{2,30})",
    "reports": r"(\w{2,30})\s*(?:上报|报告|反馈|通知)\s*(\w{2,30})\s*(?:故障|状态|事件|信号|DTC|报警)?",
    "reports_dtc": r"(?:DTC|诊断(?:码)?|故障码)[：:]?\s*(\w{3,20})\s*(?:上报|报告|反馈)",
    "references": r"(?:参见|参考|详见|参照|见|参阅)\s*(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:节|章|页|段|部分)?",
    "references_section": r"(?:如|按照|根据|参考)\s*(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:节|章)\s*(?:所述|的定义|的规定|的描述)",
    "cross_module": (
        r"(VMM|ExteriorLight|InteriorLight|Window|Lock|TheftProtection|Wiper|RemoteControl)"
        r"\s*(?:模块|的)\s*(\w{3,30})"
    ),
}

# BCM-specific classification defaults
_BCM_STATE_NAMES = [
    "Inactive", "Active", "Driving", "Convenience", "Abandoned",
    "Disarmed", "Armed", "OFF", "ACC", "ON", "Idle", "Sleep",
]
_BCM_FUNCTION_KEYWORDS = ["Close", "Open", "Lock", "Unlock", "Fold", "Follow"]
_BCM_POWER_TERMINALS = ["KL30", "KL15", "KL31", "KL87", "GND", "VBAT"]


class EntityExtractor:
    """Extract entities and relationships from content_list.

    Covers all 8 entity types and all 10 relationship types.
    Uses TableAnalyzer for structured table extraction.

    Supports DomainConfig for domain-specific patterns. Falls back to
    BCM defaults if no config is provided (backward compatible).

    Usage:
        # With DomainConfig (recommended)
        extractor = EntityExtractor(domain=domain_config)

        # Backward compatible (BCM defaults)
        extractor = EntityExtractor()
    """

    def __init__(self, domain: "DomainConfig | None" = None):
        self.table_analyzer = TableAnalyzer()
        self._domain = domain
        self._compile_patterns(domain)

    def _compile_patterns(self, domain: "DomainConfig | None") -> None:
        """Compile regex patterns from DomainConfig or BCM defaults."""
        # Use DomainConfig patterns if provided, else BCM defaults
        src = domain.extraction if domain else None

        def _re(pat: str, flags: int = 0) -> re.Pattern | None:
            return re.compile(pat, flags) if pat else None

        def _from_src(key: str, flags: int = 0) -> re.Pattern | None:
            if src:
                val = getattr(src, f"{key}_pattern", "")
                return _re(val, flags) if val else None
            return _re(_BCM_PATTERNS.get(key, ""), flags)

        # Entity patterns
        self._signal_re = _from_src("signal")
        self._state_re = _from_src("state")
        self._state_cn_re = _from_src("state_cn")
        self._param_re = _from_src("parameter")
        self._fault_re = _from_src("fault")
        self._can_id_re = _from_src("can_id")
        self._can_msg_re = _from_src("can_message")
        self._pin_re = _from_src("pin", re.IGNORECASE if not src else 0)
        self._function_title_re = _from_src("function_title")
        self._function_text_re = _from_src("function_text")

        # Relationship patterns
        self._transition_re = _from_src("transition")
        self._output_signal_re = _from_src("output_signal")
        self._output_generic_re = _from_src("output_generic")
        self._trigger_re = _from_src("trigger")
        self._trigger_edge_re = _from_src("trigger_edge")
        self._trigger_kw_re = _from_src("trigger_kw")
        self._depends_re = _from_src("depends")
        self._depends_state_re = _from_src("depends_state")
        self._controls_re = _from_src("controls")
        self._controls_enable_re = _from_src("controls_enable")
        self._requires_re = _from_src("requires")
        self._configures_re = _from_src("configures")
        self._configures_via_re = _from_src("configures_via")
        self._reports_re = _from_src("reports")
        self._reports_dtc_re = _from_src("reports_dtc")
        self._references_re = _from_src("references")
        self._references_section_re = _from_src("references_section")
        self._cross_module_re = _from_src("cross_module")

        # Classification heuristics
        if src:
            self._state_names = src.state_names
            self._function_keywords = src.function_keywords
            self._power_terminals = src.power_terminal_names
        else:
            self._state_names = _BCM_STATE_NAMES
            self._function_keywords = _BCM_FUNCTION_KEYWORDS
            self._power_terminals = _BCM_POWER_TERMINALS

    def extract(
        self, content_list: list[dict], tree: SectionTree,
    ) -> tuple[list[Entity], list[Relationship]]:
        entities: list[Entity] = []
        relationships: list[Relationship] = []

        current_section_id = "root"
        current_module = ""

        for idx, item in enumerate(content_list):
            # Track section context
            if item.get("type") == "title":
                title_text = self._extract_title_text(item)
                current_module = self._resolve_module(idx, item, tree)
                for node in tree.nodes.values():
                    if node.title == title_text:
                        current_section_id = node.section_id
                        break

            # Dispatch by item type
            text = self._get_item_text(item)

            if item.get("type") == "title":
                ents = self._extract_from_title(idx, item, current_section_id, tree)
                entities.extend(ents)

            elif item.get("type") == "table":
                ents, rels = self._extract_from_table(idx, item, current_section_id, tree)
                entities.extend(ents)
                relationships.extend(rels)

            elif item.get("type") in ("paragraph", "list"):
                ents, rels = self._extract_from_text(
                    idx, item, current_section_id, current_module, tree,
                )
                entities.extend(ents)
                relationships.extend(rels)

        # Post-processing: extract HARDWARE_PIN entities from signal properties
        self._extract_pin_entities(entities, relationships, tree)

        # Deduplicate
        entities = self._dedup_entities(entities)
        relationships = self._dedup_relationships(relationships)

        return entities, relationships

    # ---- title extraction ------------------------------------------------

    def _extract_from_title(
        self, idx: int, item: dict, section_id: str, tree: SectionTree,
    ) -> list[Entity]:
        entities = []
        title_text = self._extract_title_text(item)
        node = tree.nodes.get(section_id)
        module = self._get_module(node, tree) if node else ""

        # Module entity from chapter titles (level 1)
        if node and node.level == 1 and module:
            eid = f"module_{module}"
            entities.append(Entity(
                entity_id=eid, entity_type=EntityType.MODULE,
                name=module, module=module,
                section_path=node.number,
                source_item_index=idx,
                properties={"title": title_text},
            ))

        # State entity from titles containing state names
        for m in self._state_re.finditer(title_text):
            state_name = m.group(0)
            eid = f"state_{module}_{state_name}"
            entities.append(Entity(
                entity_id=eid, entity_type=EntityType.STATE,
                name=state_name, module=module,
                section_path=node.number if node else "",
                source_item_index=idx,
            ))

        # State from Chinese patterns like "XX状态", "XX模式"
        for m in self._state_cn_re.finditer(title_text):
            state_name = m.group(1)
            if len(state_name) >= 2:
                eid = f"state_{module}_{state_name}"
                entities.append(Entity(
                    entity_id=eid, entity_type=EntityType.STATE,
                    name=state_name, module=module,
                    section_path=node.number if node else "",
                    source_item_index=idx,
                ))

        # Function entity: title contains function keywords
        if self._function_title_re.search(title_text):
            func_name = title_text[:60]
            eid = f"func_{module}_{self._slugify(func_name)}"
            entities.append(Entity(
                entity_id=eid, entity_type=EntityType.FUNCTION,
                name=func_name, module=module,
                section_path=node.number if node else "",
                source_item_index=idx,
                properties={"section_title": title_text},
            ))

        # Function names in title (Latin-named functions)
        for m in self._function_text_re.finditer(title_text):
            func_name = m.group(0)
            if any(ord(c) < 128 for c in func_name):  # Has ASCII chars
                eid = f"func_{module}_{self._slugify(func_name)}"
                entities.append(Entity(
                    entity_id=eid, entity_type=EntityType.FUNCTION,
                    name=func_name, module=module,
                    section_path=node.number if node else "",
                    source_item_index=idx,
                    properties={"section_title": title_text},
                ))

        return entities

    # ---- table extraction (schema-aware + regex fallback) ---------------

    def _extract_from_table(
        self, idx: int, item: dict, section_id: str, tree: SectionTree,
    ) -> tuple[list[Entity], list[Relationship]]:
        entities = []
        relationships = []
        node = tree.nodes.get(section_id)
        module = self._get_module(node, tree) if node else ""
        section_num = node.number if node else ""

        # Phase 1: Schema-aware table analysis (with section context)
        analysis = self.table_analyzer.analyze(
            idx, item, module, section_num,
            section_title=node.title if node else "",
        )

        if analysis.table_class != TableClass.UNKNOWN:
            entities.extend(analysis.entities)
            relationships.extend(analysis.relationships)
            # Still do regex extraction for supplementary signals/params in cells
            self._supplement_regex_from_table(
                idx, item, module, section_num, entities, relationships,
            )
            return entities, relationships

        # Phase 2: Fallback to regex-based extraction for unknown tables
        return self._extract_from_table_regex(idx, item, section_id, tree)

    def _supplement_regex_from_table(
        self, idx: int, item: dict, module: str, section_num: str,
        entities: list[Entity], relationships: list[Relationship],
    ) -> None:
        """Supplementary: catch signals/params in all cells using regex."""
        html = item.get("content", {}).get("html", "")
        if not html:
            return

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        all_text = soup.get_text()

        # Catch signals missed by column mapping
        for m in self._signal_re.finditer(all_text):
            sig_name = m.group(1)
            if len(sig_name) >= 4 and sig_name not in ("NULL", "True", "False"):
                self._add_entity(entities,
                    entity_type=EntityType.SIGNAL,
                    name=sig_name, module=module,
                    section_path=section_num, idx=idx,
                )

        # Catch CAN IDs
        for m in self._can_id_re.finditer(all_text):
            self._add_entity(entities,
                entity_type=EntityType.CAN_MESSAGE,
                name=m.group(0), module=module,
                section_path=section_num, idx=idx,
            )

    def _extract_from_table_regex(
        self, idx: int, item: dict, section_id: str, tree: SectionTree,
    ) -> tuple[list[Entity], list[Relationship]]:
        """Legacy regex-based table extraction for unclassified tables."""
        entities = []
        relationships = []
        node = tree.nodes.get(section_id)
        module = self._get_module(node, tree) if node else ""
        section_num = node.number if node else ""

        html = item.get("content", {}).get("html", "")
        if not html:
            return entities, relationships

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        all_text = soup.get_text()

        # Extract whatever we can with regex
        for m in self._signal_re.finditer(all_text):
            sig_name = m.group(1)
            if len(sig_name) >= 4 and sig_name not in ("NULL", "True", "False"):
                self._add_entity(entities, EntityType.SIGNAL,
                    name=sig_name, module=module, section_path=section_num, idx=idx)

        for m in self._can_id_re.finditer(all_text):
            self._add_entity(entities, EntityType.CAN_MESSAGE,
                name=m.group(0), module=module, section_path=section_num, idx=idx)

        for m in self._can_msg_re.finditer(all_text):
            self._add_entity(entities, EntityType.CAN_MESSAGE,
                name=m.group(1), module=module, section_path=section_num, idx=idx)

        return entities, relationships

    # ---- text extraction (all 10 relationship types) ----------------------

    def _extract_from_text(
        self, idx: int, item: dict, section_id: str,
        current_module: str, tree: SectionTree,
    ) -> tuple[list[Entity], list[Relationship]]:
        entities: list[Entity] = []
        relationships: list[Relationship] = []
        node = tree.nodes.get(section_id)
        module = self._get_module(node, tree) if node else current_module
        section_num = node.number if node else ""

        text = self._get_item_text(item)
        if not text:
            return entities, relationships

        # === Entity Extraction ===

        # Signal names
        for m in self._signal_re.finditer(text):
            sig_name = m.group(1)
            if len(sig_name) >= 4 and sig_name not in ("NULL", "True", "False"):
                self._add_entity(entities,
                    entity_type=EntityType.SIGNAL,
                    name=sig_name, module=module,
                    section_path=section_num, idx=idx,
                )

        # Parameters
        for m in self._param_re.finditer(text):
            self._add_entity(entities,
                entity_type=EntityType.PARAMETER,
                name=m.group(0), module=module,
                section_path=section_num, idx=idx,
            )

        # States (English named)
        for m in self._state_re.finditer(text):
            self._add_entity(entities,
                entity_type=EntityType.STATE,
                name=m.group(0), module=module,
                section_path=section_num, idx=idx,
            )

        # States (Chinese named)
        for m in self._state_cn_re.finditer(text):
            state_name = m.group(1)
            if len(state_name) >= 2:
                self._add_entity(entities,
                    entity_type=EntityType.STATE,
                    name=state_name, module=module,
                    section_path=section_num, idx=idx,
                )

        # CAN messages
        for m in self._can_msg_re.finditer(text):
            self._add_entity(entities,
                entity_type=EntityType.CAN_MESSAGE,
                name=m.group(1), module=module,
                section_path=section_num, idx=idx,
            )

        # CAN IDs
        for m in self._can_id_re.finditer(text):
            self._add_entity(entities,
                entity_type=EntityType.CAN_MESSAGE,
                name=m.group(0), module=module,
                section_path=section_num, idx=idx,
            )

        # Hardware PINs
        for m in self._pin_re.finditer(text):
            self._add_entity(entities,
                entity_type=EntityType.HARDWARE_PIN,
                name=m.group(0), module=module,
                section_path=section_num, idx=idx,
            )

        # Functions mentioned in text
        for m in self._function_text_re.finditer(text):
            func_name = m.group(0)
            if len(func_name) >= 3:
                self._add_entity(entities,
                    entity_type=EntityType.FUNCTION,
                    name=func_name, module=module,
                    section_path=section_num, idx=idx,
                )

        # Fault entities
        if self._fault_re.search(text):
            fault_name = text[:80].strip()
            self._add_entity(entities,
                entity_type=EntityType.FAULT,
                name=fault_name, module=module,
                section_path=section_num, idx=idx,
            )

        # === Relationship Extraction (all 10 types) ===

        # 1. TRANSITION_TO: "迁移到X状态"
        for m in self._transition_re.finditer(text):
            target = m.group(1)
            source_state = self._find_source_state(item, section_id, tree)
            if source_state:
                self._ensure_entity(entities, module, source_state, EntityType.STATE, section_num, idx)
                self._ensure_entity(entities, module, target, EntityType.STATE, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"state_{module}_{source_state}",
                    target_id=f"state_{module}_{target}",
                    rel_type=RelType.TRANSITION_TO,
                ))

        # 2. OUTPUTS: "发送CAN信号XXX=0xY:ZZZ"
        for m in _OUTPUTself._signal_re.finditer(text):
            sig_name, value, state_name = m.group(1), m.group(2), m.group(3)
            if not self._has_entity(entities, sig_name, EntityType.SIGNAL):
                self._ensure_entity(entities, module, sig_name, EntityType.SIGNAL, section_num, idx)
            if state_name:
                self._ensure_entity(entities, module, state_name, EntityType.STATE, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"state_{module}_{state_name}",
                    target_id=f"signal_{module}_{sig_name}",
                    rel_type=RelType.OUTPUTS,
                    properties={"value": value},
                ))

        # 3. TRIGGERED_BY: conditions that trigger state changes or functions
        for m in self._trigger_re.finditer(text):
            trigger_sig = m.group(1)
            trigger_val = m.group(2)
            # Find what's being triggered: look for function/state after the trigger
            triggered = self._find_triggered_target(text[m.end():])
            self._ensure_entity(entities, module, trigger_sig, EntityType.SIGNAL, section_num, idx)
            if triggered:
                target_type = self._classify_entity_type(triggered, entities)
                self._ensure_entity(entities, module, triggered, target_type, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"{target_type.value}_{module}_{triggered}",
                    target_id=f"signal_{module}_{trigger_sig}",
                    rel_type=RelType.TRIGGERED_BY,
                    properties={"value": trigger_val} if trigger_val else {},
                ))

        # Edge-triggered: "X的上升沿触发Y"
        for m in self._trigger_edge_re.finditer(text):
            trigger_sig = m.group(1)
            triggered_func = m.group(2)
            self._ensure_entity(entities, module, trigger_sig, EntityType.SIGNAL, section_num, idx)
            self._ensure_entity(entities, module, triggered_func, EntityType.FUNCTION, section_num, idx)
            relationships.append(Relationship(
                source_id=f"function_{module}_{triggered_func}",
                target_id=f"signal_{module}_{trigger_sig}",
                rel_type=RelType.TRIGGERED_BY,
            ))

        # Trigger keyword patterns
        for m in self._trigger_kw_re.finditer(text):
            trigger_name = m.group(1)
            self._ensure_entity(entities, module, trigger_name, EntityType.SIGNAL, section_num, idx)
            # The trigger relates to the current section's function
            func = self._get_section_function(node, module) if node else None
            if func:
                self._ensure_entity(entities, module, func, EntityType.FUNCTION, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"function_{module}_{func}",
                    target_id=f"signal_{module}_{trigger_name}",
                    rel_type=RelType.TRIGGERED_BY,
                ))

        # 4. DEPENDS_ON: dependency relationships
        for m in self._depends_re.finditer(text):
            dep_source = m.group(1)
            dep_target = m.group(2)
            source_type = self._classify_entity_type(dep_source, entities)
            target_type = self._classify_entity_type(dep_target, entities)
            self._ensure_entity(entities, module, dep_source, source_type, section_num, idx)
            self._ensure_entity(entities, module, dep_target, target_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"{source_type.value}_{module}_{dep_source}",
                target_id=f"{target_type.value}_{module}_{dep_target}",
                rel_type=RelType.DEPENDS_ON,
            ))

        # State-based dependency: "需要X处于Y状态"
        for m in _DEPENDSself._state_re.finditer(text):
            dep_signal = m.group(1)
            dep_state = m.group(2)
            self._ensure_entity(entities, module, dep_signal, EntityType.SIGNAL, section_num, idx)
            self._ensure_entity(entities, module, dep_state, EntityType.STATE, section_num, idx)
            relationships.append(Relationship(
                source_id=f"signal_{module}_{dep_signal}",
                target_id=f"state_{module}_{dep_state}",
                rel_type=RelType.DEPENDS_ON,
            ))

        # 5. CONTROLS: one entity controls another
        for m in self._controls_re.finditer(text):
            controller = m.group(1)
            controlled = m.group(2)
            ctrl_type = self._classify_entity_type(controller, entities)
            ctrld_type = self._classify_entity_type(controlled, entities)
            self._ensure_entity(entities, module, controller, ctrl_type, section_num, idx)
            self._ensure_entity(entities, module, controlled, ctrld_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"{ctrl_type.value}_{module}_{controller}",
                target_id=f"{ctrld_type.value}_{module}_{controlled}",
                rel_type=RelType.CONTROLS,
            ))

        # Enable/disable patterns
        for m in self._controls_enable_re.finditer(text):
            enabler = m.group(1)
            enabled = m.group(2)
            en_type = self._classify_entity_type(enabler, entities)
            ed_type = self._classify_entity_type(enabled, entities)
            self._ensure_entity(entities, module, enabler, en_type, section_num, idx)
            self._ensure_entity(entities, module, enabled, ed_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"{en_type.value}_{module}_{enabler}",
                target_id=f"{ed_type.value}_{module}_{enabled}",
                rel_type=RelType.CONTROLS,
            ))

        # 6. REQUIRES: prerequisite relationships
        for m in self._requires_re.finditer(text):
            required = m.group(1)
            req_type = self._classify_entity_type(required, entities)
            self._ensure_entity(entities, module, required, req_type, section_num, idx)
            # The current section's function requires this
            func = self._get_section_function(node, module) if node else None
            if func:
                self._ensure_entity(entities, module, func, EntityType.FUNCTION, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"function_{module}_{func}",
                    target_id=f"{req_type.value}_{module}_{required}",
                    rel_type=RelType.REQUIRES,
                ))

        # 7. CONFIGURES: configuration relationships
        for m in self._configures_re.finditer(text):
            param = m.group(1)
            target = m.group(2)
            param_type = self._classify_entity_type(param, entities)
            target_type = self._classify_entity_type(target, entities)
            self._ensure_entity(entities, module, param, param_type, section_num, idx)
            self._ensure_entity(entities, module, target, target_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"{param_type.value}_{module}_{param}",
                target_id=f"{target_type.value}_{module}_{target}",
                rel_type=RelType.CONFIGURES,
            ))

        # Via: "通过X配置Y"
        for m in self._configures_via_re.finditer(text):
            configurator = m.group(1)
            configured = m.group(2)
            cfg_type = self._classify_entity_type(configurator, entities)
            cfgd_type = self._classify_entity_type(configured, entities)
            self._ensure_entity(entities, module, configurator, cfg_type, section_num, idx)
            self._ensure_entity(entities, module, configured, cfgd_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"{cfg_type.value}_{module}_{configurator}",
                target_id=f"{cfgd_type.value}_{module}_{configured}",
                rel_type=RelType.CONFIGURES,
            ))

        # 8. REPORTS: diagnostic/fault reporting
        for m in self._reports_re.finditer(text):
            reporter = m.group(1)
            reported = m.group(2)
            rep_type = self._classify_entity_type(reporter, entities)
            rptd_type = self._classify_entity_type(reported, entities) if reported else EntityType.FAULT
            self._ensure_entity(entities, module, reporter, rep_type, section_num, idx)
            if reported:
                self._ensure_entity(entities, module, reported, rptd_type, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"{rep_type.value}_{module}_{reporter}",
                    target_id=f"{rptd_type.value}_{module}_{reported}",
                    rel_type=RelType.REPORTS,
                ))

        for m in self._reports_dtc_re.finditer(text):
            dtc = m.group(1)
            self._ensure_entity(entities, module, dtc, EntityType.FAULT, section_num, idx)
            func = self._get_section_function(node, module) if node else None
            if func:
                self._ensure_entity(entities, module, func, EntityType.FUNCTION, section_num, idx)
                relationships.append(Relationship(
                    source_id=f"function_{module}_{func}",
                    target_id=f"fault_{module}_{dtc}",
                    rel_type=RelType.REPORTS,
                ))

        # 9. REFERENCES: cross-references to other sections
        for m in self._references_re.finditer(text):
            ref_section = m.group(1)
            # Create a reference to the referenced section
            ref_entity_id = f"section_{ref_section.replace('.', '_')}"
            relationships.append(Relationship(
                source_id=f"section_{section_num.replace('.', '_')}" if section_num else "root",
                target_id=ref_entity_id,
                rel_type=RelType.REFERENCES,
                properties={"ref_text": m.group(0)},
            ))

        for m in self._references_section_re.finditer(text):
            ref_section = m.group(1)
            ref_entity_id = f"section_{ref_section.replace('.', '_')}"
            relationships.append(Relationship(
                source_id=f"section_{section_num.replace('.', '_')}" if section_num else "root",
                target_id=ref_entity_id,
                rel_type=RelType.REFERENCES,
                properties={"ref_text": m.group(0)},
            ))

        # 10. Cross-module references
        for m in self._cross_module_re.finditer(text):
            ref_module = m.group(1)
            ref_entity = m.group(2)
            ref_type = self._classify_entity_type(ref_entity, entities)
            self._ensure_entity(entities, ref_module, ref_entity, ref_type, section_num, idx)
            relationships.append(Relationship(
                source_id=f"module_{module}" if module else "root",
                target_id=f"{ref_type.value}_{ref_module}_{ref_entity}",
                rel_type=RelType.REFERENCES,
            ))

        return entities, relationships

    # ---- post-processing -------------------------------------------------

    def _extract_pin_entities(
        self, entities: list[Entity], relationships: list[Relationship],
        tree: SectionTree,
    ) -> None:
        """Post-process: extract HARDWARE_PIN entities from signal.pin properties.

        Many signal_def tables map PIN locations (A2-01, B1-09) as signal
        properties. This method promotes them to independent HARDWARE_PIN entities
        with proper OUTPUTS relationships.
        """
        new_pins: list[Entity] = []
        new_rels: list[Relationship] = []

        for signal in entities:
            if signal.entity_type != EntityType.SIGNAL:
                continue
            pin = signal.properties.get("pin", "").strip()
            if not pin or len(pin) < 2:
                continue

            # Create HARDWARE_PIN entity
            pin_id = f"hardware_pin_{signal.module}_{TableAnalyzer._safe_id(pin)}"
            if not any(e.entity_id == pin_id for e in entities + new_pins):
                new_pins.append(Entity(
                    entity_id=pin_id,
                    entity_type=EntityType.HARDWARE_PIN,
                    name=pin,
                    module=signal.module,
                    section_path=signal.section_path,
                    source_item_index=signal.source_item_index,
                    properties={"signal_name": signal.name},
                ))

            # Create signal → PIN relationship
            new_rels.append(Relationship(
                source_id=signal.entity_id,
                target_id=pin_id,
                rel_type=RelType.OUTPUTS,
                properties={"relation": "signal_assigned_to_pin"},
            ))

        entities.extend(new_pins)
        relationships.extend(new_rels)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _get_item_text(item: dict) -> str:
        """Extract plain text from any content_list item."""
        content = item.get("content", {})
        item_type = item.get("type", "")

        if item_type == "title":
            parts = []
            for tc in content.get("title_content", []):
                if tc.get("type") == "text":
                    parts.append(tc.get("content", ""))
            return "".join(parts)

        if item_type in ("paragraph", "list"):
            return EntityExtractor._extract_text_from_content(content)

        return ""

    @staticmethod
    def _extract_title_text(item: dict) -> str:
        content = item.get("content", {})
        parts = []
        for tc in content.get("title_content", []):
            if tc.get("type") == "text":
                parts.append(tc.get("content", ""))
        return "".join(parts).strip()

    @staticmethod
    def _extract_text_from_content(content: dict) -> str:
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
    def _find_source_state(item: dict, section_id: str, tree: SectionTree) -> str:
        node = tree.nodes.get(section_id)
        if node:
            for m in self._state_re.finditer(node.title):
                return m.group(0)
            if node.parent_id and node.parent_id in tree.nodes:
                parent = tree.nodes[node.parent_id]
                for m in self._state_re.finditer(parent.title):
                    return m.group(0)
        return ""

    @staticmethod
    def _get_module(node, tree) -> str:
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

    def _resolve_module(self, idx: int, item: dict, tree: SectionTree) -> str:
        """Resolve module from the section tree for a given item index."""
        for node in tree.nodes.values():
            start, end = node.item_range
            if start <= idx <= end:
                return self._get_module(node, tree)
        return ""

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"\s+", "_", text[:30].strip())

    @staticmethod
    def _get_section_function(node, module: str) -> str:
        """Extract function name from section node title."""
        if node:
            for m in self._function_text_re.finditer(node.title):
                return m.group(0)
            # If title contains "功能描述", use the full title as function name
            if self._function_title_re.search(node.title):
                return node.title[:30]
        return ""

    @staticmethod
    def _find_triggered_target(text_after: str) -> str:
        """Find what is triggered after a trigger condition."""
        m = re.search(r"(?:触发|激活|唤醒|启动|进入|执行)\s*(\w{3,30})", text_after)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _classify_entity_type(name: str, existing: list[Entity]) -> EntityType:
        """Guess entity type based on name patterns and existing entities."""
        if not name:
            return EntityType.SIGNAL
        # Check existing entities first
        for e in existing:
            if e.name == name:
                return e.entity_type
        # Heuristic classification
        if name.startswith(("Cfg", "cfg", "Cal", "cal", "NVM_", "nvm_")):
            return EntityType.PARAMETER
        if name in self._state_names:
            return EntityType.STATE
        if any(fn in name for fn in self._function_keywords):
            return EntityType.FUNCTION
        if name.startswith("0x"):
            return EntityType.CAN_MESSAGE
        if name.upper() in self._power_terminals:
            return EntityType.HARDWARE_PIN
        if re.match(r"^[A-Z][A-Za-z0-9_]{3,}$", name):
            return EntityType.SIGNAL
        return EntityType.FUNCTION

    @staticmethod
    def _add_entity(
        entities: list[Entity], entity_type: EntityType,
        name: str, module: str, section_path: str, idx: int,
        properties: dict | None = None,
    ) -> None:
        """Add entity if not already in list (by name+type+module)."""
        eid = f"{entity_type.value}_{module}_{name}"
        for e in entities:
            if e.entity_id == eid:
                return
        entities.append(Entity(
            entity_id=eid, entity_type=entity_type,
            name=name, module=module,
            section_path=section_path,
            source_item_index=idx,
            properties=properties or {},
        ))

    @staticmethod
    def _has_entity(entities: list[Entity], name: str, etype: EntityType) -> bool:
        """Check if entity exists in list."""
        for e in entities:
            if e.name == name and e.entity_type == etype:
                return True
        return False

    @staticmethod
    def _ensure_entity(
        entities: list[Entity], module: str, name: str,
        etype: EntityType, section_num: str, idx: int,
    ) -> str:
        """Ensure entity exists, return its entity_id."""
        eid = f"{etype.value}_{module}_{name}"
        for e in entities:
            if e.entity_id == eid:
                return eid
        entities.append(Entity(
            entity_id=eid, entity_type=etype,
            name=name, module=module,
            section_path=section_num,
            source_item_index=idx,
        ))
        return eid

    @staticmethod
    def _dedup_entities(entities: list[Entity]) -> list[Entity]:
        seen: dict[tuple, Entity] = {}
        for e in entities:
            key = (e.name, e.entity_type.value, e.module)
            if key not in seen:
                seen[key] = e
            else:
                seen[key].properties.update(e.properties)
        return list(seen.values())

    @staticmethod
    def _dedup_relationships(relationships: list[Relationship]) -> list[Relationship]:
        seen: set[tuple] = set()
        unique = []
        for r in relationships:
            key = (r.source_id, r.target_id, r.rel_type.value)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique
