"""BCM-RAG Reasoning Engine — Forward/Backward Chain, Path Query, Conflict Detection.

Operates on the state machine graph + rule knowledge graph.
Uses NetworkX for traversal; exports results to structured JSON + Neo4j Cypher.

Modes:
  1. Forward Chaining  — "KeyLost 会影响什么？"
  2. Backward Chaining — "进入 Driving 需要什么条件？"
  3. Path Query        — "从 Inactive 到 Driving 的完整路径？"
  4. Conflict Detection — "哪些规则之间存在冲突？"
  5. Reachability       — "哪些状态不可达？哪些存在死锁？"
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ImpactNode:
    entity: str
    entity_type: str  # state | signal | function | fault | module
    module: str
    depth: int
    via: str  # relationship path
    effect: str = ""


@dataclass
class ConditionNode:
    type: str  # AND | OR | LEAF
    description: str = ""
    signal: str = ""
    value: str = ""
    source_module: str = ""
    children: list["ConditionNode"] = field(default_factory=list)


@dataclass
class ImpactReport:
    trigger: str
    trigger_type: str
    impacted: list[ImpactNode] = field(default_factory=list)
    total_depth: int = 0
    total_impacted: int = 0

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger,
            "trigger_type": self.trigger_type,
            "total_depth": self.total_depth,
            "total_impacted": self.total_impacted,
            "impacted": [
                {
                    "entity": i.entity,
                    "type": i.entity_type,
                    "module": i.module,
                    "depth": i.depth,
                    "via": i.via,
                    "effect": i.effect,
                }
                for i in self.impacted
            ],
        }


@dataclass
class ConditionTree:
    target: str
    module: str
    tree: Optional[ConditionNode] = None
    total_paths: int = 0
    alternative_paths: int = 0

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "module": self.module,
            "total_paths": self.total_paths,
            "alternative_paths": self.alternative_paths,
            "condition_tree": self._node_to_dict(self.tree) if self.tree else None,
        }

    def _node_to_dict(self, node: ConditionNode) -> dict:
        return {
            "type": node.type,
            "description": node.description,
            "signal": node.signal,
            "value": node.value,
            "source_module": node.source_module,
            "children": [self._node_to_dict(c) for c in node.children],
        }


# ---------------------------------------------------------------------------
# Reasoning Engine
# ---------------------------------------------------------------------------

class ReasoningEngine:
    """Graph-based reasoning on state machines and rules.

    Usage:
        engine = ReasoningEngine()
        engine.load_state_machine("output/content_analysis/state_machine_VMM.json")
        engine.load_rules("output/content_analysis/rules.json")

        # Forward chain
        report = engine.forward_chain("PEPS_KeyStatus", entity_type="signal", max_depth=5)

        # Backward chain
        tree = engine.backward_chain("Driving", module="VMM")

        # Path query
        paths = engine.path_query("Inactive", "Driving", module="VMM")

        # Conflict detection
        conflicts = engine.detect_conflicts(module="VMM")

        # Reachability
        issues = engine.reachability_analysis(module="VMM")
    """

    def __init__(self):
        self.state_machine: dict = {}
        self.rules: list[dict] = []
        self.state_graph: nx.DiGraph = nx.DiGraph()
        self.kg_graph: nx.DiGraph = nx.DiGraph()  # Full knowledge graph
        self._loaded = False

    # ---- Load ----------------------------------------------------------------

    def load_state_machine(self, path: str | Path) -> "ReasoningEngine":
        """Load a state machine JSON."""
        with open(path, "r", encoding="utf-8") as f:
            self.state_machine = json.load(f)

        # Build state graph
        self.state_graph = nx.DiGraph()
        for name, state in self.state_machine.get("states", {}).items():
            self.state_graph.add_node(name, **state)

        for trans in self.state_machine.get("transitions", []):
            self.state_graph.add_edge(
                trans["source"], trans["target"],
                guard=trans.get("guard", ""),
                effect=trans.get("effect", ""),
                source_rules=trans.get("source_rules", []),
                source_section=trans.get("source_section", ""),
            )

        module = self.state_machine.get("module", "Unknown")
        print(f"ReasoningEngine: loaded state machine '{module}' "
              f"({self.state_graph.number_of_nodes()} states, "
              f"{self.state_graph.number_of_edges()} transitions)")
        return self

    def load_rules(self, path: str | Path) -> "ReasoningEngine":
        """Load rules JSON for cross-rule reasoning."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.rules = data.get("rules", [])
        print(f"ReasoningEngine: loaded {len(self.rules)} rules")
        return self

    def load_kg(self, path: str | Path) -> "ReasoningEngine":
        """Load full knowledge graph for signal-level reasoning."""
        with open(path, "r", encoding="utf-8") as f:
            kg = json.load(f)

        for e in kg.get("entities", []):
            self.kg_graph.add_node(e["entity_id"], **e)

        for r in kg.get("relationships", []):
            self.kg_graph.add_edge(
                r["source_id"], r["target_id"],
                rel_type=r["rel_type"],
                weight=r.get("weight", 0.0),
            )

        print(f"ReasoningEngine: loaded KG ({self.kg_graph.number_of_nodes()} nodes, "
              f"{self.kg_graph.number_of_edges()} edges)")
        self._loaded = True
        return self

    # ---- Readiness Properties (replace _loaded checks) --------------------

    @property
    def state_ready(self) -> bool:
        """状态图是否已加载并可执行图算法。

        替代 _loaded 检查。load_state_machine() 之后即为 True。
        """
        return (
            hasattr(self, "state_graph")
            and self.state_graph.number_of_nodes() > 0
        )

    @property
    def kg_ready(self) -> bool:
        """知识图谱是否已加载并可执行图遍历。

        替代 _loaded 检查。load_kg() 之后即为 True。
        """
        return (
            hasattr(self, "kg_graph")
            and self.kg_graph.number_of_nodes() > 0
        )

    # ---- 1. Forward Chaining -------------------------------------------------

    def forward_chain(
        self,
        entity: str,
        entity_type: str = "signal",
        max_depth: int = 5,
        module: str = "",
    ) -> ImpactReport:
        """Forward impact analysis: what does this entity affect?

        Traces downstream effects through:
        1. Signal → consumed by modules → affects functions/states
        2. State → transitions to other states → affects dependent functions
        3. Fault → reactions → signal changes → affected modules

        Args:
            entity: Entity name to start from
            entity_type: signal | state | fault | function
            max_depth: Maximum traversal depth
            module: Optional module filter

        Returns:
            ImpactReport with all affected entities
        """
        impacted: list[ImpactNode] = []
        seen: set = set()

        # Determine starting nodes
        start_nodes = []

        if entity_type == "state":
            # State impact: follow outgoing transitions, find dependent functions
            if entity in self.state_graph:
                start_nodes.append((entity, entity_type, "", 0))

            # Also search for rules guarded by this state
            entity_lower = entity.lower()
            for rule in self.rules:
                pre_text = " ".join(rule.get("preconditions", [])).lower()
                cond_text = (rule.get("condition_expr", "") or "").lower()
                if entity_lower in pre_text or entity_lower in cond_text:
                    next_frontier = []
                    rid = rule.get("rule_id", "")
                    if rid not in seen:
                        seen.add(rid)
                        start_nodes.append((rid, "rule",
                                           f"guarded by state {entity}", 1))

        elif entity_type == "signal":
            # Signal impact: find modules consuming this signal
            # Search in rules for conditions referencing this signal
            start_nodes.append((entity, entity_type, "", 0))
            # Also find in KG
            for src, tgt, data in self.kg_graph.edges(data=True):
                src_ent = self.kg_graph.nodes.get(src, {})
                tgt_ent = self.kg_graph.nodes.get(tgt, {})
                src_name = src_ent.get("name", "")
                if entity.lower() in src_name.lower():
                    start_nodes.append((tgt_ent.get("name", tgt),
                                       tgt_ent.get("entity_type", "unknown"),
                                       data.get("rel_type", ""), 1))

        elif entity_type == "fault":
            # Fault impact: find fault_reaction rules
            start_nodes.append((entity, entity_type, "", 0))

        else:
            start_nodes.append((entity, entity_type, "", 0))

        # BFS traversal
        frontier = start_nodes.copy()
        for node, etype, via, depth in frontier:
            seen.add(node)

        while frontier:
            next_frontier = []
            for node, etype, via, depth in frontier:
                if depth > max_depth:
                    continue

                # Record impact
                if depth > 0:
                    impacted.append(ImpactNode(
                        entity=node,
                        entity_type=etype,
                        module=self._infer_module(node),
                        depth=depth,
                        via=via,
                    ))

                if etype == "state":
                    # Follow outgoing state transitions
                    for _, tgt, data in self.state_graph.out_edges(node, data=True):
                        if tgt not in seen:
                            seen.add(tgt)
                            next_frontier.append((tgt, "state",
                                                 f"transition: {data.get('guard', '')[:60]}",
                                                 depth + 1))

                    # Find rules guarded by this state
                    for rule in self.rules:
                        pre = rule.get("preconditions", [])
                        if any(node.lower() in p.lower() for p in pre):
                            rid = rule.get("rule_id", "")
                            if rid not in seen:
                                seen.add(rid)
                                next_frontier.append((rid, "rule",
                                                     f"guarded by {node}",
                                                     depth + 1))

                elif etype == "signal":
                    # Find rules that use this signal
                    # Also check for Chinese translations of the signal name
                    search_terms = [node]
                    # KeyLost → also search for 钥匙, 失效, key
                    if "key" in node.lower() or "钥匙" in node.lower():
                        search_terms.extend(["钥匙", "key", "PEPS_Key"])
                    if "lost" in node.lower() or "失效" in node.lower():
                        search_terms.extend(["失效", "丢失", "invalid", "not found"])

                    for rule in self.rules:
                        cond = (rule.get("condition_expr", "") or "").lower()
                        action = (rule.get("action", "") or "").lower()
                        full_text = cond + " " + action

                        match = any(term.lower() in full_text for term in search_terms)
                        if match:
                            rid = rule.get("rule_id", "")
                            if rid not in seen:
                                seen.add(rid)
                                next_frontier.append((rid, "rule",
                                                     f"signal {node}",
                                                     depth + 1))

                                # Check if this rule triggers a state change
                                target_state = rule.get("action_target_state", "")
                                if target_state and target_state not in seen:
                                    seen.add(target_state)
                                    next_frontier.append((target_state, "state",
                                                         f"rule {rid}",
                                                         depth + 1))

                elif etype == "rule":
                    # Rule impact: what does this rule affect?
                    target_state = self._get_rule_property(node, "action_target_state")
                    action_signals = self._get_rule_property(node, "action_signals")

                    if target_state and target_state not in seen:
                        seen.add(target_state)
                        next_frontier.append((target_state, "state",
                                             f"rule {node}",
                                             depth + 1))

                    for sig in (action_signals or []):
                        if sig not in seen:
                            seen.add(sig)
                            next_frontier.append((sig, "signal",
                                                 f"rule {node}",
                                                 depth + 1))

            frontier = next_frontier

        report = ImpactReport(
            trigger=entity,
            trigger_type=entity_type,
            impacted=impacted,
            total_depth=max(r.depth for r in impacted) if impacted else 0,
            total_impacted=len(impacted),
        )
        return report

    # ---- 2. Backward Chaining ------------------------------------------------

    def backward_chain(
        self,
        target_state: str,
        module: str = "VMM",
        max_depth: int = 5,
        _visited: set | None = None,
    ) -> ConditionTree:
        """Backward condition analysis: what's needed to reach a target state?

        Recursively traces all incoming transitions, building a condition tree.
        """
        # Guard against infinite recursion from cyclic state graphs
        if _visited is None:
            _visited = set()
        if target_state in _visited or max_depth <= 0:
            return ConditionTree(
                target=target_state, module=module,
                tree=ConditionNode(type="LEAF",
                                  description=f"Recursion limit or cycle at {target_state}")
            )
        _visited.add(target_state)

        tree = ConditionTree(target=target_state, module=module)

        # Find all incoming transitions
        incoming = []
        for trans in self.state_machine.get("transitions", []):
            if trans.get("target") == target_state:
                incoming.append(trans)

        if not incoming:
            # Check if it's an initial state
            state_info = self.state_machine.get("states", {}).get(target_state, {})
            if state_info.get("is_initial"):
                tree.tree = ConditionNode(type="LEAF", description="Initial State: No preconditions")
            else:
                tree.tree = ConditionNode(type="LEAF",
                                         description=f"No incoming transitions found — {target_state} may be unreachable")
            return tree

        # Build OR node for multiple incoming paths
        branches = []
        for trans in incoming:
            source = trans.get("source", "")
            guard = trans.get("guard", "")
            effect = trans.get("effect", "")
            section = trans.get("source_section", "")

            # Extract signals and states from guard
            signals = self._extract_signals_from_text(guard)
            states = self._extract_states_from_text(guard)

            # Build AND node for this transition's conditions
            children = []

            # Guard conditions
            for sig in signals:
                children.append(ConditionNode(
                    type="LEAF",
                    description=f"Signal condition",
                    signal=sig,
                    source_module=self._infer_module(sig),
                ))

            for st in states:
                children.append(ConditionNode(
                    type="LEAF",
                    description=f"State precondition",
                    signal=st,
                    source_module=module,
                ))

            # Recursively get source state conditions
            if source and source != target_state:
                source_tree = self.backward_chain(source, module, max_depth - 1, _visited.copy())
                if source_tree.tree and source_tree.tree.type != "LEAF":
                    children.append(source_tree.tree)
                elif source_tree.tree:
                    children.append(ConditionNode(
                        type="LEAF",
                        description=f"Must be in state: {source}",
                    ))

            if not children:
                children.append(ConditionNode(type="LEAF", description=f"Trigger: {guard[:100]}"))

            branch = ConditionNode(
                type="AND",
                description=f"Path via {source} → {target_state} [{section}]",
                children=children,
            )
            branches.append(branch)

        if len(branches) == 1:
            tree.tree = branches[0]
            tree.total_paths = 1
        else:
            tree.tree = ConditionNode(type="OR", children=branches)
            tree.total_paths = len(branches)

        tree.alternative_paths = len(branches) - 1
        return tree

    # ---- 3. Path Query -------------------------------------------------------

    def path_query(
        self,
        source_state: str,
        target_state: str,
        module: str = "VMM",
        max_hops: int = 6,
    ) -> dict:
        """Find all paths between two states, with transition details."""
        paths = []

        try:
            all_paths = list(nx.all_simple_paths(
                self.state_graph, source_state, target_state, cutoff=max_hops,
            ))
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            all_paths = []

        for path in all_paths:
            transitions = []
            conditions = []

            for i in range(len(path) - 1):
                src = path[i]
                tgt = path[i + 1]
                edge_data = self.state_graph.get_edge_data(src, tgt) or {}

                transitions.append({
                    "from": src,
                    "to": tgt,
                    "guard": edge_data.get("guard", ""),
                    "effect": edge_data.get("effect", ""),
                    "source_section": edge_data.get("source_section", ""),
                })

                # Extract conditions from guard
                guard = edge_data.get("guard", "")
                conditions.extend(self._extract_signals_from_text(guard))

            paths.append({
                "sequence": path,
                "hops": len(path) - 1,
                "transitions": transitions,
                "total_conditions": list(set(conditions)),
            })

        # Sort by shortest first
        paths.sort(key=lambda p: p["hops"])

        return {
            "source": source_state,
            "target": target_state,
            "module": module,
            "total_paths": len(paths),
            "shortest_hops": paths[0]["hops"] if paths else -1,
            "paths": paths,
        }

    # ---- 4. Conflict Detection -----------------------------------------------

    def detect_conflicts(self, module: str = "") -> list[dict]:
        """Detect rule conflicts.

        Types:
        1. Action conflict: same condition → different actions
        2. Priority conflict: overlapping conditions with inconsistent priority
        3. Redundant rules: same condition → same action
        """
        conflicts = []

        rules = self.rules
        if module:
            rules = [r for r in rules if r.get("module") == module]

        for i, r1 in enumerate(rules):
            for r2 in rules[i + 1:]:
                # Skip different modules
                if r1.get("module") != r2.get("module"):
                    continue

                conflict = self._check_rule_pair(r1, r2)
                if conflict:
                    conflicts.append(conflict)

        return conflicts

    def _check_rule_pair(self, r1: dict, r2: dict) -> dict | None:
        """Check two rules for conflicts."""
        cond1 = (r1.get("condition_expr", "") or "").lower()
        cond2 = (r2.get("condition_expr", "") or "").lower()
        action1 = (r1.get("action", "") or "").lower()
        action2 = (r2.get("action", "") or "").lower()

        # Check condition overlap (simplified: shared signal/state references)
        sigs1 = set(self._extract_signals_from_text(cond1))
        sigs2 = set(self._extract_signals_from_text(cond2))
        states1 = set(self._extract_states_from_text(cond1))
        states2 = set(self._extract_states_from_text(cond2))

        shared_sigs = sigs1 & sigs2
        shared_states = states1 & states2

        # Filter out source state names from shared states — same source having
        # multiple exit paths is normal state machine behavior, not a conflict.
        real_shared_states = set()
        for st in shared_states:
            # st comes from condition_states extraction, format varies
            # Check if it's just the source state name (normalized)
            st_clean = st.lower().replace("处于", "").replace("状态", "").strip()
            pre1 = " ".join(r1.get("preconditions", [])).lower()
            pre2 = " ".join(r2.get("preconditions", [])).lower()
            # This is only the source state if BOTH rules have it as "处于X状态" in pre
            is_source_only = (
                st_clean in pre1 and st_clean in pre2
                and ("处于" in pre1 or "处于" in pre2)
            )
            if not is_source_only:
                real_shared_states.add(st)

        # For transition_guard rules, same source state with different guards
        # is normal. Only flag conflicts if they share actual trigger signals.
        both_are_transitions = (
            r1.get("rule_type") == "transition_guard"
            and r2.get("rule_type") == "transition_guard"
        )
        if both_are_transitions and not shared_sigs and len(real_shared_states) <= 1:
            return None  # Normal: same source, different exits

        if not shared_sigs and not real_shared_states:
            return None  # No condition overlap beyond source state

        # Same action → potentially redundant
        if action1 == action2:
            return {
                "type": "redundant",
                "rule_a": r1.get("rule_id"),
                "rule_b": r2.get("rule_id"),
                "module": r1.get("module"),
                "shared_signals": list(shared_sigs),
                "shared_states": list(real_shared_states),
                "detail": f"Same action with overlapping conditions",
            }

        # Different actions with overlapping conditions → conflict
        target1 = r1.get("action_target_state", "")
        target2 = r2.get("action_target_state", "")
        if target1 and target2 and target1 != target2:
            return {
                "type": "action_conflict",
                "rule_a": r1.get("rule_id"),
                "rule_b": r2.get("rule_id"),
                "module": r1.get("module"),
                "target_a": target1,
                "target_b": target2,
                "shared_signals": list(shared_sigs),
                "shared_states": list(real_shared_states),
                "detail": f"Overlapping conditions lead to different targets: {target1} vs {target2}",
            }

        # Different actions generally
        action_a = r1.get("action", "")[:100]
        action_b = r2.get("action", "")[:100]
        if action_a != action_b:
            return {
                "type": "potential_conflict",
                "rule_a": r1.get("rule_id"),
                "rule_b": r2.get("rule_id"),
                "module": r1.get("module"),
                "shared_signals": list(shared_sigs),
                "shared_states": list(real_shared_states),
                "detail": f"Overlapping conditions with different actions",
            }

        return None

    # ---- 5. Reachability Analysis --------------------------------------------

    def reachability_analysis(self, module: str = "VMM") -> list[dict]:
        """Analyze state machine for reachability and deadlock issues."""
        issues = []

        if not self.state_graph.nodes:
            return [{"type": "empty", "detail": "No state machine loaded"}]

        states = self.state_machine.get("states", {})

        for node in self.state_graph.nodes:
            state_info = states.get(node, {})

            # Unreachable: no incoming edges, not initial
            in_deg = self.state_graph.in_degree(node)
            if in_deg == 0 and not state_info.get("is_initial"):
                issues.append({
                    "type": "unreachable",
                    "state": node,
                    "detail": f"No incoming transitions and not initial",
                    "recommendation": f"Add transition to {node} or mark as initial",
                })

            # Deadlock: no outgoing edges, not terminal
            out_deg = self.state_graph.out_degree(node)
            if out_deg == 0 and not state_info.get("is_terminal"):
                issues.append({
                    "type": "deadlock",
                    "state": node,
                    "detail": f"No outgoing transitions and not terminal",
                    "recommendation": f"Add transition from {node} to another state or mark as terminal",
                })

        # Livelock: cycles without guard conditions
        try:
            cycles = list(nx.simple_cycles(self.state_graph))
            for cycle in cycles:
                if len(cycle) <= len(self.state_graph.nodes):
                    # Check if all edges in cycle lack guards
                    all_unguarded = True
                    for i in range(len(cycle)):
                        src = cycle[i]
                        tgt = cycle[(i + 1) % len(cycle)]
                        edge = self.state_graph.get_edge_data(src, tgt) or {}
                        if edge.get("guard", "").strip():
                            all_unguarded = False
                            break
                    if all_unguarded:
                        issues.append({
                            "type": "livelock_risk",
                            "cycle": cycle,
                            "detail": f"Cycle without guard conditions: potential infinite loop",
                            "recommendation": "Add guard conditions to break the cycle",
                        })
        except Exception:
            pass  # nx.simple_cycles can fail on large graphs

        # Strongly connected components
        sccs = list(nx.strongly_connected_components(self.state_graph))
        for scc in sccs:
            if len(scc) > 1:
                issues.append({
                    "type": "strongly_connected",
                    "states": list(scc),
                    "detail": f"Strongly connected component: all states in {list(scc)} are mutually reachable",
                })

        return issues

    # ---- Helpers -------------------------------------------------------------

    def _extract_signals_from_text(self, text: str) -> list[str]:
        """Extract CAN/ signal names from text."""
        if not text:
            return []
        # Match ALLCAPS signal names
        signals = re.findall(r"\b([A-Z][A-Za-z0-9_]{4,}(?:Sts|Mode|Req|St)?)\b", text)
        # Also match hex values with signal names
        signal_pats = re.findall(r"(\w+)\s*=\s*(?:0x[\dA-Fa-f]+|\w+)", text)
        for s in signal_pats:
            if isinstance(s, tuple):
                signals.append(s[0] if s else "")
            elif isinstance(s, str) and len(s) > 1:
                signals.append(s)
        return list(set(signals))[:20]

    def _extract_states_from_text(self, text: str) -> list[str]:
        """Extract state references from text."""
        if not text:
            return []
        states = re.findall(r"(\w+)(?:状态|模式)", text)
        return list(set(states))[:10]

    def _infer_module(self, entity: str) -> str:
        """Try to infer which module an entity belongs to."""
        for rule in self.rules:
            if entity in str(rule):
                return rule.get("module", "Unknown")
        return "Unknown"

    def _get_rule_property(self, rule_id: str, prop: str):
        """Get a property from a rule by its ID."""
        for rule in self.rules:
            if rule.get("rule_id") == rule_id:
                return rule.get(prop)
        return None

    # ---- Save Results --------------------------------------------------------

    def save_report(self, report: ImpactReport, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return path

    def save_condition_tree(self, tree: ConditionTree, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# CLI Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    engine = ReasoningEngine()
    engine.load_state_machine("output/content_analysis/state_machine_VMM.json")
    engine.load_rules("output/content_analysis/rules.json")

    module = "VMM"

    print("=" * 60)
    print("1. FORWARD CHAINING: KeyLost impact")
    print("=" * 60)
    report = engine.forward_chain("KeyLost", entity_type="signal", max_depth=3)
    print(f"  Trigger: {report.trigger} ({report.trigger_type})")
    print(f"  Total impacted: {report.total_impacted}")
    for imp in report.impacted[:10]:
        print(f"    [{imp.depth}] {imp.entity_type}: {imp.entity} via {imp.via[:60]}")

    print()
    print("=" * 60)
    print("2. BACKWARD CHAINING: Driving entry conditions")
    print("=" * 60)
    tree = engine.backward_chain("Driving", module=module)
    print(f"  Target: {tree.target}")
    print(f"  Total paths: {tree.total_paths}")
    print(f"  Tree type: {tree.tree.type if tree.tree else 'EMPTY'}")
    if tree.tree and tree.tree.children:
        for child in tree.tree.children[:3]:
            print(f"    [{child.type}] {child.description[:80]}")

    print()
    print("=" * 60)
    print("3. PATH QUERY: Inactive → Driving")
    print("=" * 60)
    paths = engine.path_query("Inactive", "Driving", module=module)
    print(f"  Total paths: {paths['total_paths']}")
    print(f"  Shortest: {paths['shortest_hops']} hops")
    for path in paths["paths"][:2]:
        print(f"    Sequence: {' → '.join(path['sequence'])} ({path['hops']} hops)")

    print()
    print("=" * 60)
    print("4. CONFLICT DETECTION")
    print("=" * 60)
    conflicts = engine.detect_conflicts(module=module)
    print(f"  Conflicts found: {len(conflicts)}")
    for c in conflicts[:5]:
        print(f"    [{c['type']}] {c.get('detail', '')[:80]}")

    print()
    print("=" * 60)
    print("5. REACHABILITY ANALYSIS")
    print("=" * 60)
    issues = engine.reachability_analysis(module=module)
    print(f"  Issues found: {len(issues)}")
    for issue in issues:
        print(f"    [{issue['type']}] {issue['detail'][:100]}")
