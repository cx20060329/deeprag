"""DeepRAG State Machine Builder — Converts rules into state transition graphs.

Phase 2b: Takes extracted Rule objects and builds a NetworkX state machine graph,
then exports to Neo4j Cypher.

Supports DomainConfig for domain-specific state definitions.
Falls back to BCM defaults if no config is provided.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import networkx as nx

if TYPE_CHECKING:
    from domain.config import DomainConfig


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class StateNode:
    name: str
    module: str
    is_initial: bool = False
    is_terminal: bool = False
    is_composite: bool = False
    parent_state: str = ""
    description: str = ""
    section_path: str = ""
    entry_actions: list[str] = field(default_factory=list)
    exit_actions: list[str] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    power_mode: str = ""  # OFF/Crank/ON/ACC


@dataclass
class Transition:
    id: str
    source: str
    target: str
    module: str
    trigger: str = ""
    guard: str = ""
    effect: str = ""
    priority: int = 1
    is_automatic: bool = False
    time_constraint: str = ""
    source_rules: list[str] = field(default_factory=list)
    source_section: str = ""
    confidence: float = 0.0


@dataclass
class StateMachine:
    module: str
    states: dict[str, StateNode] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    def add_state(self, state: StateNode):
        self.states[state.name] = state
        self.graph.add_node(state.name, **state.__dict__)

    def add_transition(self, trans: Transition):
        self.transitions.append(trans)
        self.graph.add_edge(
            trans.source, trans.target,
            id=trans.id,
            trigger=trans.trigger,
            guard=trans.guard,
            effect=trans.effect,
            priority=trans.priority,
            is_automatic=trans.is_automatic,
            time_constraint=trans.time_constraint,
            source_rules=trans.source_rules,
            source_section=trans.source_section,
        )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class StateMachineBuilder:
    """Build state machines from extracted rules.

    Usage:
        from config import CONTENT_ANALYSIS_DIR
        builder = StateMachineBuilder()
        builder.load_rules(CONTENT_ANALYSIS_DIR / "rules.json")
        sm = builder.build("VMM")
        print(sm.summary())
    """

    # Section → state name inference patterns (generic)
    STATE_PATTERNS = [
        (r"(\w+)模式", "mode_state"),
        (r"迁移到(\w+)状态", "target"),
        (r"处于(\w+)状态", "source"),
    ]

    # BCM default states (used when no DomainConfig provided)
    _BCM_MODULE_STATES: dict[str, dict[str, dict]] = {
        "VMM": {
            "Abandoned": {"is_terminal": True, "power_mode": "OFF"},
            "Inactive": {"is_initial": True, "power_mode": "OFF"},
            "Convenience": {"power_mode": "Crank/ON"},
            "Driving": {"power_mode": "ON"},
        },
        "Window": {
            "Stopped": {"is_initial": True}, "Rising": {}, "Falling": {}, "AntiPinch": {},
        },
        "Lock": {
            "Unlocked": {"is_initial": True}, "Locked": {}, "AutoLocked": {}, "CrashUnlocked": {},
        },
        "ExteriorLight": {
            "Off": {"is_initial": True}, "PositionLight": {}, "LowBeam": {}, "HighBeam": {}, "AutoLight": {},
        },
        "InteriorLight": {
            "Off": {"is_initial": True}, "On": {}, "Dimmed": {},
        },
        "Wiper": {
            "Off": {"is_initial": True}, "Intermittent": {}, "LowSpeed": {}, "HighSpeed": {},
        },
        "RemoteControl": {
            "Disarmed": {"is_initial": True}, "Armed": {}, "Alarm": {},
        },
        "TheftProtection": {
            "Disarmed": {"is_initial": True}, "PreArmed": {}, "Armed": {}, "Alarm": {},
        },
    }

    def _get_module_states(self, module: str) -> dict[str, dict]:
        """获取指定模块的已知状态定义。"""
        return dict(self._module_states.get(module, {}))

    def __init__(self, domain: "DomainConfig | None" = None):
        self.rules: list[dict] = []
        self._state_machines: dict[str, StateMachine] = {}

        # Use DomainConfig states if provided, else BCM defaults
        if domain is not None and domain.state_machine is not None:
            self._module_states = domain.state_machine.module_states
            self._section_to_module = domain.state_machine.section_to_module_map or {}
        else:
            self._module_states = self._BCM_MODULE_STATES
            self._section_to_module = {}

    # ---- Load ----------------------------------------------------------------

    def load_rules(self, rules_path: str | Path) -> "StateMachineBuilder":
        """Load rules from JSON file."""
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.rules = data.get("rules", [])
        print(f"StateMachineBuilder: loaded {len(self.rules)} rules")
        return self

    # ---- Build ---------------------------------------------------------------

    def build(self, module: str = "VMM") -> StateMachine:
        """Build a state machine for a specific module."""
        sm = StateMachine(module=module)

        # Phase 1: Extract states from rules
        states_found = self._extract_states(module)
        for name, props in states_found.items():
            state = StateNode(name=name, module=module, **props)
            sm.add_state(state)

        # Phase 2: Build transitions from transition_guard rules
        module_rules = [r for r in self.rules if r.get("module") == module]
        for rule in module_rules:
            if rule.get("rule_type") == "transition_guard":
                trans = self._rule_to_transition(rule)
                if trans:
                    sm.add_transition(trans)

        # Phase 3: Also extract transitions from activation rules
        # that reference VMM state changes
        for rule in module_rules:
            if rule.get("rule_type") in ("activation_rule", "deactivation_rule"):
                trans = self._activation_to_transition(rule, sm)
                if trans:
                    sm.add_transition(trans)

        # Phase 4: Validate
        issues = self.validate(sm)
        if issues:
            print(f"  ⚠ {len(issues)} validation issues found")

        self._state_machines[module] = sm
        return sm

    def build_all(self) -> dict[str, StateMachine]:
        """Build state machines for all modules that have rules.

        不限于 transition_guard 类型——只要有 activation_rule 或
        deactivation_rule 的模块都可以推断状态机。
        """
        modules = set(r.get("module", "") for r in self.rules
                      if r.get("module") and r.get("rule_type") in (
                          "transition_guard", "activation_rule",
                          "deactivation_rule", "signal_value"))
        for mod in modules:
            if mod and mod != "Unknown":
                try:
                    self.build(mod)
                except Exception as e:
                    print(f"  ✗ {mod}: {e}")
        return self._state_machines

    # ---- State Extraction ----------------------------------------------------

    def _extract_states(self, module: str) -> dict[str, dict]:
        """Extract all states for a module from rules.

        Only extracts states that are part of the state machine:
        - Known states from VMM_STATES
        - States explicitly mentioned as transition source/target
        - NOT conditions like '电源', 'AVP激活', etc.
        """
        states = {}

        # 从已知状态定义开始（VMM + 非VMM模块）
        known = self._get_module_states(module)
        for name, props in known.items():
            states[name] = dict(props)

        # 从规则中提取状态
        for rule in self.rules:
            if rule.get("module") != module:
                continue

            rt = rule.get("rule_type", "")

            # transition_guard: "处于X状态" → 源状态
            for pre in rule.get("preconditions", []):
                m = re.search(r"处于(\w+)状态", pre)
                if m:
                    name = m.group(1)
                    name = name[0].upper() + name[1:] if name else name
                    if rt == "transition_guard" and name not in states:
                        states[name] = {}

            # action_target_state → 目标状态
            target = rule.get("action_target_state", "")
            if target:
                target = target[0].upper() + target[1:] if target else target
                if rt == "transition_guard" and target not in states:
                    states[target] = {}

            # activation_rule / deactivation_rule: 从条件/动作文本推断状态
            if rt in ("activation_rule", "deactivation_rule"):
                cond = str(rule.get("condition_expr", rule.get("condition", "")))
                action = str(rule.get("action", rule.get("action_text", "")))
                combined = cond + " " + action
                for known_name in known:
                    if known_name.lower() in combined.lower() and known_name not in states:
                        states[known_name] = dict(known[known_name])

        return states

    # ---- Transition Building -------------------------------------------------

    def _rule_to_transition(self, rule: dict) -> Transition | None:
        """Convert a transition_guard rule to a Transition."""
        # Determine source state from preconditions
        source = ""
        for pre in rule.get("preconditions", []):
            m = re.search(r"处于(\w+)状态", pre)
            if m:
                source = m.group(1)
                break

        target = rule.get("action_target_state", "")
        # Normalize case: "convenience" → "Convenience"
        target = target[0].upper() + target[1:] if target else ""
        source = source[0].upper() + source[1:] if source else source
        if not source or not target:
            return None

        # Merge preconditions (excluding source) + triggers as guard
        guards = [
            p for p in rule.get("preconditions", [])
            if "处于" not in p
        ]
        triggers = rule.get("trigger_conditions", [])
        guard = " AND ".join(guards + triggers) if (guards or triggers) else ""

        # Build effect from action
        effect = rule.get("action", "")

        return Transition(
            id=f"trans_{source}_to_{target}",
            source=source,
            target=target,
            module=rule.get("module", ""),
            guard=guard,
            effect=effect[:500],
            priority=1,
            source_rules=[rule.get("rule_id", "")],
            source_section=rule.get("source_section", ""),
            confidence=rule.get("confidence", 0.0),
        )

    def _activation_to_transition(
        self, rule: dict, sm: StateMachine,
    ) -> Transition | None:
        """Convert an activation/deactivation rule that implies a state transition."""
        # Check if this rule activates/deactivates something based on VMM state
        pre = rule.get("preconditions", [])
        if not pre:
            return None

        # Look for state references in preconditions
        state_refs = []
        for p in pre:
            for state_name in sm.states:
                if state_name.lower() in p.lower():
                    state_refs.append(state_name)

        if not state_refs:
            return None

        # This rule is "guarded by" these states — record as guarded activation
        # but not as a state transition (unless action changes state)
        action = rule.get("action", "")
        if "迁移到" in action or "进入" in action or "ENTER" in action:
            target_match = re.search(r"(?:迁移到|进入|ENTER)\s*(\w+)(?:状态|模式)?", action)
            if target_match and state_refs:
                target = target_match.group(1)
                for src in state_refs:
                    if src != target and target in sm.states:
                        return Transition(
                            id=f"trans_{src}_to_{target}_via_{rule.get('rule_id','')[:20]}",
                            source=src,
                            target=target,
                            module=rule.get("module", ""),
                            guard=" AND ".join(pre),
                            effect=action[:300],
                            priority=0,
                            source_rules=[rule.get("rule_id", "")],
                            source_section=rule.get("source_section", ""),
                            confidence=rule.get("confidence", 0.0),
                        )
        return None

    # ---- Validation ----------------------------------------------------------

    def validate(self, sm: StateMachine) -> list[dict]:
        """Validate state machine completeness."""
        issues = []

        if not sm.graph.nodes:
            issues.append({"type": "empty", "detail": "No states in machine"})
            return issues

        # Unreachable states (no incoming transitions, not initial)
        for node in sm.graph.nodes:
            in_degree = sm.graph.in_degree(node)
            is_initial = sm.states.get(node, StateNode(name=node, module=sm.module)).is_initial
            if in_degree == 0 and not is_initial:
                issues.append({
                    "type": "unreachable",
                    "state": node,
                    "detail": f"State '{node}' has no incoming transitions and is not initial",
                })

        # Deadlock states (no outgoing transitions, not terminal)
        for node in sm.graph.nodes:
            out_degree = sm.graph.out_degree(node)
            is_terminal = sm.states.get(node, StateNode(name=node, module=sm.module)).is_terminal
            if out_degree == 0 and not is_terminal:
                issues.append({
                    "type": "deadlock",
                    "state": node,
                    "detail": f"State '{node}' has no outgoing transitions and is not terminal",
                })

        # Duplicate transitions (same source+target, different guards = possibly redundant)
        edge_pairs = defaultdict(list)
        for trans in sm.transitions:
            key = (trans.source, trans.target)
            edge_pairs[key].append(trans)
        for (src, tgt), trans_list in edge_pairs.items():
            if len(trans_list) > 1:
                issues.append({
                    "type": "duplicate",
                    "source": src,
                    "target": tgt,
                    "detail": f"Multiple transitions from '{src}' to '{tgt}': {len(trans_list)}",
                    "transition_ids": [t.id for t in trans_list],
                })

        return issues

    # ---- Export --------------------------------------------------------------

    def to_cypher(self, sm: StateMachine) -> str:
        """Export state machine to Neo4j Cypher."""
        lines = [
            f"// State Machine: {sm.module}",
            f"// Generated by StateMachineBuilder",
            f"// States: {len(sm.states)}, Transitions: {len(sm.transitions)}",
            "",
            "// === State Nodes ===",
        ]

        for name, state in sm.states.items():
            props = []
            if state.is_initial:
                props.append("is_initial: true")
            if state.is_terminal:
                props.append("is_terminal: true")
            if state.power_mode:
                props.append(f'power_mode: "{state.power_mode}"')
            if state.description:
                desc = state.description.replace('"', "'")[:200]
                props.append(f'description: "{desc}"')
            if state.section_path:
                props.append(f'section_path: "{state.section_path}"')

            props_str = ", ".join(props)
            lines.append(
                f"MERGE (s_{name}:State {{name: '{name}', module: '{sm.module}'}}) "
                f"SET s_{name} += {{{props_str}}};"
            )

        lines.append("")
        lines.append("// === Transition Edges ===")

        for trans in sm.transitions:
            lines.append(
                f"MATCH (src:State {{name: '{trans.source}', module: '{sm.module}'}}), "
                f"(tgt:State {{name: '{trans.target}', module: '{sm.module}'}})"
            )
            guard = trans.guard.replace('"', "'")[:400] if trans.guard else ""
            effect = trans.effect.replace('"', "'")[:400] if trans.effect else ""
            section = trans.source_section

            lines.append(
                f"MERGE (src)-[:TRANSITION_TO {{"
                f"trigger: \"{guard}\", "
                f"effect: \"{effect}\", "
                f"source_section: \"{section}\", "
                f"source_rules: {json.dumps(trans.source_rules)}"
                f"}}]->(tgt);"
            )
            lines.append("")

        return "\n".join(lines)

    def to_json(self, sm: StateMachine) -> dict:
        """Export state machine to JSON."""
        return {
            "module": sm.module,
            "states": {
                name: {
                    "name": name,
                    "is_initial": s.is_initial,
                    "is_terminal": s.is_terminal,
                    "power_mode": s.power_mode,
                    "description": s.description,
                    "section_path": s.section_path,
                }
                for name, s in sm.states.items()
            },
            "transitions": [
                {
                    "id": t.id,
                    "source": t.source,
                    "target": t.target,
                    "guard": t.guard,
                    "effect": t.effect,
                    "priority": t.priority,
                    "source_rules": t.source_rules,
                    "source_section": t.source_section,
                }
                for t in sm.transitions
            ],
        }

    def save(
        self,
        sm: StateMachine,
        output_dir: str | Path | None = None,
    ) -> dict[str, Path]:
        """Save state machine as both Cypher and JSON."""
        if output_dir is None:
            from config import CONTENT_ANALYSIS_DIR
            output_dir = CONTENT_ANALYSIS_DIR
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        cypher_path = output_dir / f"state_machine_{sm.module}.cypher"
        json_path = output_dir / f"state_machine_{sm.module}.json"

        cypher_path.write_text(self.to_cypher(sm), encoding="utf-8")
        json_path.write_text(
            json.dumps(self.to_json(sm), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"State machine saved: {cypher_path}, {json_path}")
        return {"cypher": cypher_path, "json": json_path}

    # ---- Summary -------------------------------------------------------------

    def summary(self, sm: StateMachine) -> str:
        """Generate a human-readable summary."""
        lines = [
            f"State Machine: {sm.module}",
            f"  States: {len(sm.states)}",
            f"  Transitions: {len(sm.transitions)}",
            "",
            "  States:",
        ]

        for name, state in sm.states.items():
            flags = []
            if state.is_initial:
                flags.append("initial")
            if state.is_terminal:
                flags.append("terminal")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"    {name}{flag_str}")

        lines.append("")
        lines.append("  Transitions:")
        for t in sm.transitions:
            trigger_preview = t.guard[:80] + "..." if len(t.guard) > 80 else t.guard
            lines.append(f"    {t.source} → {t.target}: {trigger_preview}")

        issues = self.validate(sm)
        if issues:
            lines.append("")
            lines.append(f"  ⚠ Issues ({len(issues)}):")
            for issue in issues:
                lines.append(f"    [{issue['type']}] {issue['detail'][:100]}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from config import CONTENT_ANALYSIS_DIR

    rules_path = sys.argv[1] if len(sys.argv) > 1 else str(CONTENT_ANALYSIS_DIR / "rules.json")
    output_dir = sys.argv[2] if len(sys.argv) > 2 else str(CONTENT_ANALYSIS_DIR)

    builder = StateMachineBuilder()
    builder.load_rules(rules_path)

    # Build for each module that has transition rules
    machines = builder.build_all()

    for module, sm in machines.items():
        print()
        print(builder.summary(sm))
        builder.save(sm, output_dir)
