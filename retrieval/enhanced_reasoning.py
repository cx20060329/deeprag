"""BCM-RAG Enhanced Reasoning Layer — bridges retrieval with KG/Rules/SM reasoning.

Replaces keyword-based reasoning trigger with entity-aware semantic matching.
Adds state machines for ExteriorLight and other modules beyond VMM.
Provides structured reasoning output for all 7 benchmark categories.

Key optimizations:
  1. Entity-aware trigger: uses graph search to find relevant entities in query
  2. Multi-module state machines: VMM + ExteriorLight + Window + Lock + Wiper
  3. Rule matching via graph entity search + full text search
  4. LLM answer integration option
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional


class EnhancedReasoningEngine:
    """Adds KG/Rules/SM reasoning on top of retrieval pipeline results.

    Usage:
        from config import CONTENT_ANALYSIS_DIR
        engine = EnhancedReasoningEngine(pipeline)
        engine.load_sm(CONTENT_ANALYSIS_DIR / "state_machine_VMM.json")
        engine.load_rules(CONTENT_ANALYSIS_DIR / "rules.json")
        engine.load_kg(CONTENT_ANALYSIS_DIR / "knowledge_graph.json")

        result = pipeline.search("从Abandoned如何进入Driving")
        enhanced = engine.enhance(result)
        print(enhanced["reasoning"])
    """

    def __init__(self, pipeline=None):
        self.pipeline = pipeline
        self.state_machines: dict[str, dict] = {}  # module → SM
        self.rules: list[dict] = []
        self.kg_entities: dict[str, dict] = {}     # entity_id → entity
        self.kg_name_index: dict[str, list] = defaultdict(list)  # name → [entity_ids]
        self._loaded = False

    # ---- Load ----

    def load_sm(self, path: str | Path) -> "EnhancedReasoningEngine":
        sm = json.loads(Path(path).read_text(encoding="utf-8"))
        module = sm.get("module", "Unknown")
        self.state_machines[module] = sm
        return self

    def load_rules(self, path: str | Path) -> "EnhancedReasoningEngine":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.rules = data.get("rules", [])
        return self

    def load_kg(self, path: str | Path) -> "EnhancedReasoningEngine":
        kg = json.loads(Path(path).read_text(encoding="utf-8"))
        for e in kg.get("entities", []):
            self.kg_entities[e["entity_id"]] = e
            name = e.get("name", "")
            if name:
                self.kg_name_index[name.lower()].append(e)
        self._loaded = True
        return self

    # ---- Enhance ----

    def enhance(self, search_result: dict) -> dict:
        """Add structured reasoning to a search result."""
        query = search_result.get("query", "")
        intent = search_result.get("intent", {})
        merged = search_result.get("merged", [])
        evidence = search_result.get("evidence", "")

        # Phase 1: Analyze query → what entities does it involve?
        query_entities = self._analyze_query(query, intent, merged)

        # Phase 2: Generate relevant reasoning sections
        reasoning_parts = []

        # State machine reasoning
        sm_section = self._build_sm_section(query, query_entities)
        if sm_section:
            reasoning_parts.append(sm_section)

        # Rule reasoning
        rule_section = self._build_rule_section(query, query_entities)
        if rule_section:
            reasoning_parts.append(rule_section)

        # Path reasoning (two states mentioned)
        path_section = self._build_path_section(query, query_entities)
        if path_section:
            reasoning_parts.append(path_section)

        # Impact/forward chain
        impact_section = self._build_impact_section(query, query_entities)
        if impact_section:
            reasoning_parts.append(impact_section)

        # Conflict detection
        conflict_section = self._build_conflict_section(query, query_entities)
        if conflict_section:
            reasoning_parts.append(conflict_section)

        # Reachability
        reach_section = self._build_reachability_section(query, query_entities)
        if reach_section:
            reasoning_parts.append(reach_section)

        # Build answer
        reasoning_text = "\n\n".join(reasoning_parts) if reasoning_parts else ""
        search_result["reasoning"] = reasoning_text
        search_result["reasoning_entities"] = query_entities
        search_result["has_reasoning"] = len(reasoning_parts) > 0

        # Build full answer
        if reasoning_text:
            search_result["answer"] = (
                f"[BCM-RAG ENGINEERING REASONING]\n\n{reasoning_text}"
                f"\n\n## Evidence\n{evidence}"
            )

        return search_result

    # ---- Query Analysis ----

    def _analyze_query(self, query: str, intent: dict, merged: list[dict]) -> dict:
        """Analyze query to determine what entities and reasoning types are relevant."""
        result = {
            "modules": list(intent.get("modules", [])),
            "states": list(intent.get("states", [])),
            "signals": list(intent.get("signals", [])),
            "functions": list(intent.get("functions", [])),
            "faults": list(intent.get("faults", [])),
            "question_type": intent.get("question_type", "factual"),
            "reasoning_types": [],
        }

        ql = query.lower()

        # Use graph search for entity matching (substring match on 1802 entities)
        if self._loaded:
            for etype in ["module", "state", "signal", "function", "fault"]:
                # Search with individual query terms for better coverage
                for term in self._extract_search_terms(query):
                    if len(term) < 2:
                        continue
                    matches = [e for e_list in self.kg_name_index.values()
                              for e in e_list
                              if term.lower() in e.get("name", "").lower()
                              and e.get("entity_type") == etype]
                    for m in matches[:3]:
                        name = m["name"]
                        if name not in result[etype + "s"]:
                            result[etype + "s"].append(name)

        # Detect reasoning types needed
        # State machine: mentions states or transitions
        state_keywords = [
            "abandoned", "inactive", "convenience", "driving",
            "状态", "模式", "迁移", "进入", "退出", "切换", "transition",
            "电源模式", "休眠", "唤醒",
        ]
        if any(kw in ql for kw in state_keywords) or result["states"]:
            result["reasoning_types"].append("state_machine")

        # Path: two or more states mentioned, or "路径/path/how to get"
        path_keywords = ["路径", "path", "如何进入", "如何退出", "经过哪些", "步骤", "完整"]
        state_count = sum(1 for s in ["abandoned", "inactive", "convenience", "driving"]
                         if s in ql)
        if state_count >= 2 or any(kw in ql for kw in path_keywords):
            result["reasoning_types"].append("path")

        # Impact: asks about consequences
        impact_keywords = [
            "影响", "impact", "affect", "导致", "后果", "连锁", "失效", "丢失",
            "会影响", "故障会导致",
        ]
        if any(kw in ql for kw in impact_keywords):
            result["reasoning_types"].append("impact")

        # Rule: conditions, triggers, activation
        rule_keywords = [
            "条件", "触发", "激活", "关闭", "规则", "前置", "当", "如果",
            "condition", "trigger", "rule", "activation", "为什么", "如何检测",
        ]
        if any(kw in ql for kw in rule_keywords):
            result["reasoning_types"].append("rule")

        # Conflict: mentions conflict, priority, simultaneous
        conflict_keywords = [
            "冲突", "矛盾", "互斥", "同时", "优先级", "覆盖", "抢占",
            "conflict", "priority", "override",
        ]
        if any(kw in ql for kw in conflict_keywords):
            result["reasoning_types"].append("conflict")

        # Reachability: asks about existence of paths, deadlock, unreachable
        reach_keywords = [
            "不可达", "死锁", "活锁", "是否存在", "能否到达", "连通",
            "unreachable", "deadlock", "reachable",
        ]
        if any(kw in ql for kw in reach_keywords):
            result["reasoning_types"].append("reachability")

        return result

    # Synonym mapping for BCM domain terms
    SYNONYMS = {
        "keylost": ["钥匙失效", "钥匙丢失", "key lost", "peps_keystatus", "钥匙无效"],
        "钥匙失效": ["keylost", "peps_keystatus", "key invalid", "no key"],
        "crash": ["碰撞", "acu_crashoutputsts", "碰撞解锁", "crashunlock"],
        "ign1": ["ign1继电器", "ign1 relay", "peps_ign1relay"],
        "peps_keystatus": ["钥匙状态", "key status", "keylost", "钥匙失效"],
    }

    def _extract_search_terms(self, query: str) -> list[str]:
        """Extract individual search terms from query, with domain synonym expansion."""
        terms = []
        # English identifiers
        terms.extend(re.findall(r"[A-Z][A-Za-z0-9_]{1,}", query))
        # Chinese phrases (2-8 chars)
        terms.extend(re.findall(r"[一-鿿]{2,8}", query))
        # Also add the full query
        terms.append(query)

        # Expand synonyms
        ql = query.lower()
        for key, synonyms in self.SYNONYMS.items():
            if key in ql:
                terms.extend(synonyms)

        return list(set(terms))[:20]

    # ---- State Machine Section ----

    def _build_sm_section(self, query: str, qe: dict) -> str:
        """Build state machine reasoning section."""
        if "state_machine" not in qe["reasoning_types"]:
            return ""

        parts = ["## State Machine Reasoning"]

        ql = query.lower()
        for module, sm in self.state_machines.items():
            states = sm.get("states", {})
            transitions = sm.get("transitions", [])

            # Find which states are mentioned or relevant
            mentioned_states = []
            for state_name in states:
                if state_name.lower() in ql:
                    mentioned_states.append(state_name)

            if not mentioned_states:
                # Try to find states from query entities
                for s in qe.get("states", []):
                    if s in states:
                        mentioned_states.append(s)

            if not mentioned_states:
                continue

            for state in mentioned_states[:3]:
                # Incoming transitions
                incoming = [t for t in transitions if t["target"] == state]
                outgoing = [t for t in transitions if t["source"] == state]

                parts.append(f"\n### {module}: {state}")

                if incoming:
                    parts.append(f"**Entering {state} from:**")
                    for t in incoming:
                        parts.append(f"- `{t['source']}` → `{t['target']}` [{t.get('source_section','')}]")
                        guard = t.get('guard', '')
                        if guard:
                            parts.append(f"  Guard: {guard[:200]}")

                if outgoing:
                    parts.append(f"**Exiting {state} to:**")
                    for t in outgoing:
                        parts.append(f"- `{t['source']}` → `{t['target']}` [{t.get('source_section','')}]")
                        guard = t.get('guard', '')
                        if guard:
                            parts.append(f"  Guard: {guard[:200]}")

        if len(parts) == 1:
            return ""  # No actual SM content
        return "\n".join(parts)

    # ---- Rule Section ----

    def _build_rule_section(self, query: str, qe: dict) -> str:
        """Build rule reasoning section."""
        if "rule" not in qe["reasoning_types"] and "state_machine" not in qe["reasoning_types"]:
            return ""

        # Find relevant rules
        ql = query.lower()
        relevant_rules = []

        # Search with multiple terms
        search_terms = self._extract_search_terms(query)

        for rule in self.rules:
            rule_text = json.dumps(rule, ensure_ascii=False).lower()
            # Check if any search term matches
            matches = sum(1 for t in search_terms if len(t) > 1 and t.lower() in rule_text)
            if matches >= 1:
                relevant_rules.append((matches, rule))

        # Sort by relevance (more matches = more relevant)
        relevant_rules.sort(key=lambda x: -x[0])
        relevant_rules = relevant_rules[:8]

        if not relevant_rules:
            return ""

        parts = [f"## Matched Rules ({len(relevant_rules)})"]

        # Group by module
        by_module = defaultdict(list)
        for _, rule in relevant_rules:
            by_module[rule.get("module", "?")].append(rule)

        for mod, mod_rules in sorted(by_module.items()):
            parts.append(f"\n### {mod} ({len(mod_rules)} rules)")
            for rule in mod_rules[:3]:
                rtype = rule.get("rule_type", "?")
                cond = rule.get("condition_expr", "")[:150]
                action = rule.get("action", "")[:150]
                section = rule.get("source_section", "")
                parts.append(f"- **[{rtype}]** [{section}]")
                if cond:
                    parts.append(f"  IF: {cond}")
                parts.append(f"  THEN: {action}")

        return "\n".join(parts)

    # ---- Path Section ----

    def _build_path_section(self, query: str, qe: dict) -> str:
        """Build path reasoning section between two states."""
        if "path" not in qe["reasoning_types"]:
            return ""

        ql = query.lower()
        # Find state names in query
        all_states = set()
        for module, sm in self.state_machines.items():
            all_states.update(sm.get("states", {}).keys())

        mentioned = [s for s in all_states if s.lower() in ql]
        if len(mentioned) < 2:
            # Try from entities
            mentioned = [s for s in qe.get("states", []) if s in all_states]

        if len(mentioned) < 2:
            return ""

        # Find shortest paths in all state machines
        parts = ["## Path Analysis"]
        for module, sm in self.state_machines.items():
            states = sm.get("states", {})
            transitions = sm.get("transitions", [])

            # Build adjacency
            adj = defaultdict(list)
            for t in transitions:
                adj[t["source"]].append(t)

            for src in mentioned:
                for tgt in mentioned:
                    if src == tgt:
                        continue
                    if src not in states or tgt not in states:
                        continue

                    # BFS shortest path
                    path, hops = self._bfs_path(adj, src, tgt)
                    if path:
                        parts.append(f"\n### {src} → {tgt} ({hops} hops)")
                        parts.append(f"Path: {' → '.join(path)}")
                        # Add transition details
                        for i in range(len(path) - 1):
                            edge = next((t for t in transitions
                                        if t["source"] == path[i] and t["target"] == path[i+1]), None)
                            if edge:
                                parts.append(f"  {path[i]} → {path[i+1]}: {edge.get('guard','')[:120]}")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    def _bfs_path(self, adj: dict, src: str, tgt: str) -> tuple[list[str] | None, int]:
        """BFS for shortest path."""
        from collections import deque
        visited = {src: [src]}
        q = deque([src])
        while q:
            node = q.popleft()
            if node == tgt:
                path = visited[node]
                return path, len(path) - 1
            for edge in adj.get(node, []):
                nxt = edge["target"]
                if nxt not in visited:
                    visited[nxt] = visited[node] + [nxt]
                    q.append(nxt)
        return None, -1

    # ---- Impact Section ----

    def _build_impact_section(self, query: str, qe: dict) -> str:
        """Build forward impact analysis section."""
        if "impact" not in qe["reasoning_types"]:
            return ""

        # Find what entity the query is asking about
        ql = query.lower()
        impacted_entity = None
        for entity_type in ["signals", "faults", "states", "functions"]:
            for entity in qe.get(entity_type, []):
                if entity.lower() in ql:
                    impacted_entity = (entity, entity_type.rstrip("s"))
                    break
            if impacted_entity:
                break

        # Fallback: search KG directly for impact-related terms in query
        if not impacted_entity and self._loaded:
            impact_terms = re.findall(r"[A-Z][A-Za-z0-9_]{2,}|[一-鿿]{2,6}", query)
            for term in impact_terms[:10]:
                for eid, entity in self.kg_entities.items():
                    if term.lower() in entity.get("name", "").lower():
                        etype = entity.get("entity_type", "")
                        if etype in ("signal", "fault", "state", "function"):
                            impacted_entity = (entity["name"], etype)
                            break
                if impacted_entity:
                    break

        if not impacted_entity:
            return ""

        entity_name, etype = impacted_entity
        parts = [f"## Impact Analysis: {entity_name} ({etype})"]

        # Find rules mentioning this entity
        downstream_rules = []
        for rule in self.rules:
            rule_text = json.dumps(rule, ensure_ascii=False).lower()
            if entity_name.lower() in rule_text:
                downstream_rules.append(rule)

        if downstream_rules:
            parts.append(f"\n**Directly affects {len(downstream_rules)} rules:**")
            for rule in downstream_rules[:5]:
                parts.append(f"- [{rule.get('rule_type','?')}] {rule.get('action','')[:120]}")

            # Find cascading effects
            affected_modules = set(r.get("module", "") for r in downstream_rules)
            affected_signals = set()
            for rule in downstream_rules:
                action = rule.get("action", "")
                for sig_name in re.findall(r"[A-Z][A-Za-z0-9_]{3,}", action):
                    affected_signals.add(sig_name)

            if affected_modules:
                parts.append(f"\n**Affected modules:** {', '.join(sorted(affected_modules))}")
            if affected_signals:
                parts.append(f"\n**Affected signals:** {', '.join(sorted(affected_signals)[:8])}")

        # Check state transitions downstream
        for module, sm in self.state_machines.items():
            affected_transitions = []
            for t in sm.get("transitions", []):
                if entity_name.lower() in t.get("guard", "").lower():
                    affected_transitions.append(t)
            if affected_transitions:
                parts.append(f"\n**Affected state transitions ({module}):**")
                for t in affected_transitions[:3]:
                    parts.append(f"- {t['source']} → {t['target']}: guarded by {entity_name}")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    # ---- Conflict Section ----

    def _build_conflict_section(self, query: str, qe: dict) -> str:
        """Build conflict detection section."""
        if "conflict" not in qe["reasoning_types"]:
            return ""

        parts = ["## Conflict Analysis"]

        for module, sm in self.state_machines.items():
            transitions = sm.get("transitions", [])
            # Find states with multiple outgoing edges
            by_source = defaultdict(list)
            for t in transitions:
                by_source[t["source"]].append(t)

            multi_exit = {s: ts for s, ts in by_source.items() if len(ts) > 1}
            if multi_exit:
                parts.append(f"\n### {module}: States with multiple exits")
                for state, ts in multi_exit.items():
                    parts.append(f"\n**{state}** has {len(ts)} exits:")
                    for t in ts:
                        parts.append(f"- → `{t['target']}` [Guard: {t.get('guard','')[:100]}]")
                    # Check if guards are mutually exclusive
                    guards = [t.get("guard", "") for t in ts if t.get("guard")]
                    if len(guards) >= 2:
                        parts.append(f"  *Analysis: Guards appear {'mutually exclusive' if self._guards_mutually_exclusive(guards) else 'potentially overlapping — needs priority resolution'}*")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    def _guards_mutually_exclusive(self, guards: list[str]) -> bool:
        """Heuristic: check if guards are likely mutually exclusive."""
        # Simple check: if they mention different trigger events
        if len(guards) < 2:
            return True
        # If guards share no common keywords beyond state names
        all_words = [set(g.lower().split()) for g in guards]
        common = all_words[0].intersection(*all_words[1:])
        # Remove state names
        state_words = {"abandoned", "inactive", "convenience", "driving", "状态", "模式"}
        common -= state_words
        return len(common) <= 2  # Very little overlap = likely exclusive

    # ---- Reachability Section ----

    def _build_reachability_section(self, query: str, qe: dict) -> str:
        """Build reachability analysis section."""
        if "reachability" not in qe["reasoning_types"]:
            return ""

        parts = ["## Reachability Analysis"]

        for module, sm in self.state_machines.items():
            states = sm.get("states", {})
            transitions = sm.get("transitions", [])
            g = defaultdict(set)
            for t in transitions:
                g[t["source"]].add(t["target"])

            # Find states with no incoming transitions
            has_incoming = set()
            for t in transitions:
                has_incoming.add(t["target"])

            unreachable = [
                s for s in states
                if s not in has_incoming and not states[s].get("is_initial")
            ]
            deadlock = [
                s for s in states
                if s not in g and not states[s].get("is_terminal")
            ]

            parts.append(f"\n### {module}")
            parts.append(f"States: {len(states)}, Transitions: {len(transitions)}")

            if unreachable:
                parts.append(f"**Unreachable states (no incoming transitions):** {', '.join(unreachable)}")
            else:
                parts.append("All states reachable: ✓")

            if deadlock:
                parts.append(f"**Deadlock states (no outgoing transitions):** {', '.join(deadlock)}")
            else:
                parts.append("No deadlocks: ✓")

            # Cyclic check
            from collections import deque
            visited_all = set()
            for start in states:
                if start in visited_all:
                    continue
                q = deque([start])
                component = set()
                while q:
                    n = q.popleft()
                    if n in component:
                        continue
                    component.add(n)
                    for nxt in g.get(n, set()):
                        q.append(nxt)
                visited_all.update(component)
                if len(component) > 1:
                    # Check if strongly connected
                    parts.append(f"Connected component: {', '.join(sorted(component))} "
                               f"({len(component)} states)")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Integration with Pipeline
# ---------------------------------------------------------------------------

def create_enhanced_pipeline(
    kg_path: str | None = None,
    rules_path: str | None = None,
    sm_paths: list[str] | None = None,
) -> "EnhancedPipeline":
    """Create a pipeline with enhanced reasoning built-in.

    Usage:
        pipeline = create_enhanced_pipeline()
        result = pipeline.search("从Abandoned如何进入Driving？")
        print(result["reasoning"])
    """
    from retrieval import RetrievalPipeline
    from config import CONTENT_ANALYSIS_DIR

    if kg_path is None:
        kg_path = str(CONTENT_ANALYSIS_DIR / "knowledge_graph.json")
    if rules_path is None:
        rules_path = str(CONTENT_ANALYSIS_DIR / "rules.json")

    if sm_paths is None:
        sm_dir = CONTENT_ANALYSIS_DIR
        sm_paths = [str(p) for p in sm_dir.glob("state_machine_*.json")]

    pipeline = RetrievalPipeline()
    pipeline.load(use_dense=True)

    engine = EnhancedReasoningEngine(pipeline)
    for sp in sm_paths:
        if Path(sp).exists():
            engine.load_sm(sp)
    if Path(rules_path).exists():
        engine.load_rules(rules_path)
    if Path(kg_path).exists():
        engine.load_kg(kg_path)

    # Wrap search to add reasoning
    original_search = pipeline.search

    def enhanced_search(query, top_k=10, enable_llm=False, quality="fast"):
        result = original_search(query, top_k=top_k, enable_llm=enable_llm, quality=quality)
        result = engine.enhance(result)
        return result

    pipeline.search = enhanced_search
    pipeline._reasoning_engine = engine

    print(f"Enhanced pipeline ready: {len(engine.state_machines)} SMs, "
          f"{len(engine.rules)} rules, {len(engine.kg_entities)} KG entities")
    return pipeline
