"""DeepRAG Content Analysis — KG Relationship Enricher.

基于已提取的结构化数据（状态机、规则库、chunks）补全 KG 关系。
不需要重新运行全管道，只消费已有 JSON 产出。

新增关系类型:
  - State → guarded_by → Signal   (状态转移条件中引用的信号)
  - Signal → controls → Function  (规则条件/动作中的信号-功能控制链)
  - Fault → detected_by → Rule    (故障检测规则)
  - Function → triggers → State   (规则动作中的功能-状态触发)
  - Signal → consumed_by → Module (跨模块信号消费)

Usage:
    python content_analysis/kg_enricher.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path


class KGEnricher:
    """Enrich the knowledge graph with cross-referenced relationships.

    Supports DomainConfig for domain-specific state names.
    """

    def __init__(
        self,
        kg_path: str | None = None,
        sm_dir: str | None = None,
        rules_path: str | None = None,
        chunks_path: str | None = None,
        domain=None,
    ):
        from config import CONTENT_ANALYSIS_DIR, OUTPUT_ROOT
        self.kg_path = Path(kg_path) if kg_path else OUTPUT_ROOT.parent / "final" / "knowledge_graph.json"
        self.sm_dir = Path(sm_dir) if sm_dir else CONTENT_ANALYSIS_DIR
        self.rules_path = Path(rules_path) if rules_path else CONTENT_ANALYSIS_DIR / "rules.json"
        self.chunks_path = Path(chunks_path) if chunks_path else OUTPUT_ROOT.parent / "final" / "chunks.json"

        if domain is not None and domain.dag.state_names:
            self._state_names = domain.dag.state_names
        else:
            self._state_names = []

    def enrich(self) -> dict:
        """执行富化，返回新增关系的统计。"""
        # 加载 KG
        with open(self.kg_path, encoding="utf-8") as f:
            kg = json.load(f)

        entities = kg.get("entities", [])
        relationships = kg.get("relationships", [])

        # 构建索引
        entity_index = {e["entity_id"]: e for e in entities}
        name_index = defaultdict(list)
        for e in entities:
            name_index[e.get("name", "").lower()].append(e)

        new_rels = []
        stats = defaultdict(int)

        # ---- 1. State → guarded_by → Signal (从状态机提取) ----
        new_rels.extend(self._extract_guard_signals(
            entity_index, name_index, stats,
        ))

        # ---- 2. Signal → controls → Function (从规则提取) ----
        new_rels.extend(self._extract_rule_controls(
            entity_index, name_index, stats,
        ))

        # ---- 3. Fault → detected_by → Rule (从规则提取) ----
        new_rels.extend(self._extract_fault_rules(
            entity_index, name_index, stats,
        ))

        # ---- 4. Function → triggers → State (从规则动作提取) ----
        new_rels.extend(self._extract_function_state_triggers(
            entity_index, name_index, stats,
        ))

        # ---- 去重并合并 ----
        existing_keys = {
            (r["source_id"], r["target_id"], r.get("rel_type", ""))
            for r in relationships
        }
        for nr in new_rels:
            key = (nr["source_id"], nr["target_id"], nr.get("rel_type", ""))
            if key not in existing_keys:
                relationships.append(nr)
                existing_keys.add(key)
                stats["total_added"] += 1

        # 保存
        kg["relationships"] = relationships
        with open(self.kg_path, "w", encoding="utf-8") as f:
            json.dump(kg, f, ensure_ascii=False, indent=2)

        return dict(stats)

    # ------------------------------------------------------------------
    # 1. State → guarded_by → Signal
    # ------------------------------------------------------------------

    def _extract_guard_signals(self, entity_index, name_index, stats):
        """从状态机 JSON 中提取 guard 条件中引用的信号。"""
        new_rels = []
        signal_pattern = re.compile(r"[A-Z][A-Za-z0-9_]{3,}")

        for sm_path in self.sm_dir.glob("state_machine_*.json"):
            with open(sm_path, encoding="utf-8") as f:
                sm = json.load(f)

            module = sm.get("module", "")
            for t in sm.get("transitions", []):
                guard = t.get("guard", t.get("condition", ""))
                source = t.get("source", "")
                target = t.get("target", "")

                # 提取 guard 中的信号名
                signals_in_guard = signal_pattern.findall(guard)
                for sig_name in signals_in_guard:
                    # 查找信号实体
                    sig_entities = name_index.get(sig_name.lower(), [])
                    if not sig_entities:
                        # 模糊匹配
                        for name, ents in name_index.items():
                            if sig_name.lower() in name:
                                sig_entities.extend(ents)
                                break

                    for sig_ent in sig_entities[:1]:
                        # 源状态 → guarded_by → 信号
                        source_id = self._find_state_id(
                            entity_index, name_index, source, module
                        )
                        if source_id:
                            new_rels.append({
                                "source_id": source_id,
                                "target_id": sig_ent["entity_id"],
                                "rel_type": "guarded_by",
                                "weight": 0.8,
                                "properties": {"guard": guard[:100]},
                            })
                            stats["guarded_by"] += 1

                        # 目标状态同样 guarded_by
                        target_id = self._find_state_id(
                            entity_index, name_index, target, module
                        )
                        if target_id:
                            new_rels.append({
                                "source_id": target_id,
                                "target_id": sig_ent["entity_id"],
                                "rel_type": "guarded_by",
                                "weight": 0.7,
                                "properties": {"guard": guard[:100]},
                            })
                            stats["guarded_by"] += 1

        return new_rels

    # ------------------------------------------------------------------
    # 2. Signal → controls → Function (从规则提取)
    # ------------------------------------------------------------------

    def _extract_rule_controls(self, entity_index, name_index, stats):
        """从规则库中提取信号→功能的控制关系。"""
        new_rels = []
        signal_pattern = re.compile(r"[A-Z][A-Za-z0-9_]{3,}")

        if not self.rules_path.exists():
            return new_rels

        with open(self.rules_path, encoding="utf-8") as f:
            rules_data = json.load(f)

        for rule in rules_data.get("rules", []):
            condition = str(rule.get("condition_expr", rule.get("condition", "")))
            action = str(rule.get("action", rule.get("action_text", "")))
            rule_module = rule.get("module", "")

            # 从 condition 中提取信号
            cond_signals = signal_pattern.findall(condition)
            # 从 action 中提取功能名
            action_signals = signal_pattern.findall(action)

            for sig_name in cond_signals[:3]:
                sig_ents = name_index.get(sig_name.lower(), [])
                if not sig_ents:
                    for name, ents in name_index.items():
                        if sig_name.lower() in name:
                            sig_ents.extend(ents)
                            break

                for sig_ent in sig_ents[:1]:
                    # 信号 → controls → 规则(功能)
                    rule_id = rule.get("rule_id", "")
                    rule_entity_id = f"function_{rule_module}_{rule_id}"

                    new_rels.append({
                        "source_id": sig_ent["entity_id"],
                        "target_id": rule_entity_id,
                        "rel_type": "controls",
                        "weight": 0.6,
                        "properties": {"via": "rule_condition"},
                    })
                    stats["signal_controls_function"] += 1

        return new_rels

    # ------------------------------------------------------------------
    # 3. Fault → detected_by → Rule
    # ------------------------------------------------------------------

    def _extract_fault_rules(self, entity_index, name_index, stats):
        """从规则库中提取故障检测关系。"""
        new_rels = []
        fault_keywords = ["故障", "失效", "丢失", "超时", "短路", "断路", "异常", "fault", "error"]

        if not self.rules_path.exists():
            return new_rels

        with open(self.rules_path, encoding="utf-8") as f:
            rules_data = json.load(f)

        for rule in rules_data.get("rules", []):
            condition = str(rule.get("condition_expr", rule.get("condition", ""))).lower()
            action = str(rule.get("action", rule.get("action_text", ""))).lower()
            combined = condition + " " + action

            # 检测故障相关规则
            for kw in fault_keywords:
                if kw in combined:
                    rule_id = rule.get("rule_id", "")
                    rule_module = rule.get("module", "")
                    rule_entity_id = f"function_{rule_module}_{rule_id}"

                    # 查找或创建故障实体
                    fault_ents = [
                        e_item
                        for ents in name_index.values()
                        for e_item in ents
                        if e_item.get("entity_type") == "fault"
                        and kw in e_item.get("name", "").lower()
                    ]
                    if fault_ents:
                        for fault_ent in fault_ents[:1]:
                            new_rels.append({
                                "source_id": fault_ent["entity_id"],
                                "target_id": rule_entity_id,
                                "rel_type": "detected_by",
                                "weight": 0.9,
                            })
                            stats["fault_detected_by"] += 1
                    break

        return new_rels

    # ------------------------------------------------------------------
    # 4. Function → triggers → State
    # ------------------------------------------------------------------

    def _extract_function_state_triggers(self, entity_index, name_index, stats):
        """从规则动作中提取功能→状态的触发关系。"""
        new_rels = []
        state_names = set(self._state_names) if self._state_names else {"abandoned", "inactive", "convenience", "driving", "active", "standby", "sleep", "off", "on"}

        if not self.rules_path.exists():
            return new_rels

        with open(self.rules_path, encoding="utf-8") as f:
            rules_data = json.load(f)

        for rule in rules_data.get("rules", []):
            action = str(rule.get("action", rule.get("action_text", "")))
            rule_module = rule.get("module", "")

            for state_name in state_names:
                if state_name in action.lower():
                    rule_id = rule.get("rule_id", "")
                    rule_entity_id = f"function_{rule_module}_{rule_id}"

                    state_ents = name_index.get(state_name, [])
                    if not state_ents:
                        state_ents = [
                            e for ents in name_index.values()
                            for e in ents
                            if e.get("entity_type") == "state"
                            and state_name in e.get("name", "").lower()
                        ]

                    for state_ent in state_ents[:1]:
                        new_rels.append({
                            "source_id": rule_entity_id,
                            "target_id": state_ent["entity_id"],
                            "rel_type": "triggers",
                            "weight": 0.7,
                            "properties": {"via": "rule_action"},
                        })
                        stats["function_triggers_state"] += 1
                    break

        return new_rels

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_state_id(self, entity_index, name_index, state_name, module):
        """查找状态实体 ID。"""
        key = f"state_{module}_{state_name}"
        if key in entity_index:
            return key

        ents = name_index.get(state_name.lower(), [])
        for e in ents:
            if e.get("entity_type") == "state":
                return e["entity_id"]
        return None


if __name__ == "__main__":
    enricher = KGEnricher()
    stats = enricher.enrich()

    print("KG 关系富化完成:")
    print(f"  State→guarded_by→Signal:     {stats.get('guarded_by', 0)}")
    print(f"  Signal→controls→Function:    {stats.get('signal_controls_function', 0)}")
    print(f"  Fault→detected_by→Rule:      {stats.get('fault_detected_by', 0)}")
    print(f"  Function→triggers→State:     {stats.get('function_triggers_state', 0)}")
    print(f"  ---")
    print(f"  总计新增关系:                 {stats.get('total_added', 0)}")
