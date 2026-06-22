"""BCM-RAG Retrieval — Structured Evidence Builder (Stage 8 enhanced).

Builds structured evidence packages from retrieval results, explicitly injecting
dependency chains and state transition information from the knowledge graph,
rather than relying on plain text chunk truncation alone.

Improvement #3: Structured Evidence Package

The evidence package now contains:
  - Dependency chains (A→B→C) extracted from graph_results
  - State transitions (source→target: guard) extracted from state machines
  - Related rules matched by intent
  - Text chunks (the original evidence fragments)

This structured format helps the LLM understand the logical relationships
between entities, not just isolated text snippets.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class DependencyChain:
    """A chain of entities connected by relationships, extracted from the KG.

    Example:
        DependencyChain(
            chain=["IGN1", "IGN1Relay", "PEPS_UsageMode"],
            relation_types=["controls", "determines"],
            source_sections=["2.2.1.1", "2.2.1.2", "2.3.4.1"],
            description="IGN1 controls IGN1Relay, which determines PEPS_UsageMode",
        )
    """

    chain: list[str]
    relation_types: list[str]
    source_sections: list[str]
    description: str = ""


@dataclass
class StateTransition:
    """A state transition extracted from graph results and state machines.

    Example:
        StateTransition(
            source="Inactive",
            target="Convenience",
            guard="DoorOpen=TRUE AND KeyValid=TRUE",
            effect="Enter Convenience mode",
            section="2.3.4.2.2",
            module="VMM",
        )
    """

    source: str
    target: str
    guard: str = ""
    effect: str = ""
    section: str = ""
    module: str = ""


@dataclass
class StructuredEvidence:
    """Complete structured evidence package, replacing plain text chunks.

    Compared to the old evidence format (just "# 查询: XXX\n\n## 证据片段\n..."):
      - dependency_chains: explicit causal relationships
      - state_transitions: explicit state machine transitions
      - related_rules: matched rule entries
      - text_chunks: the original document fragments (deduped)
    """

    query: str
    modules: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    dependency_chains: list[DependencyChain] = field(default_factory=list)
    state_transitions: list[StateTransition] = field(default_factory=list)
    related_rules: list[dict] = field(default_factory=list)
    text_chunks: list[dict] = field(default_factory=list)


class EvidenceBuilder:
    """Build structured evidence from retrieval results.

    Extracts dependency chains and state transitions from graph_results,
    matches related rules from the rule database, and produces a
    StructuredEvidence object that can be formatted for LLM consumption.

    Usage:
        builder = EvidenceBuilder()
        evidence = builder.build(
            graph_results=graph_results,
            merged_candidates=merged,
            intent=intent,
            query=query,
            state_machine=state_machine,  # optional
            rules=rules,                   # optional
        )
        formatted = builder.format_for_llm(evidence)
    """

    # --- Public API ----------------------------------------------------------

    def build(
        self,
        graph_results: list[dict],
        merged_candidates: list[dict],
        intent: dict,
        query: str,
        state_machine: dict | None = None,
        rules: list[dict] | None = None,
    ) -> StructuredEvidence:
        """Build a structured evidence package from all retrieval results.

        Args:
            graph_results: Results from Stage 2 graph retrieval
            merged_candidates: Results from Stage 5 merge
            intent: Intent analysis dict from Stage 1
            query: Original user query
            state_machine: Optional state machine data for transition extraction
            rules: Optional rule database for matching relevant rules

        Returns:
            StructuredEvidence with dependency chains, state transitions,
            related rules, and text chunks.
        """
        evidence = StructuredEvidence(query=query)

        # Collect metadata from candidates
        for entry in merged_candidates[:10]:
            chunk = entry.get("chunk", {})
            mod = chunk.get("module", "")
            if mod and mod not in evidence.modules:
                evidence.modules.append(mod)
            for sig in chunk.get("signals", [])[:3]:
                if sig not in evidence.signals:
                    evidence.signals.append(sig)
            for st in chunk.get("states", [])[:3]:
                if st not in evidence.states:
                    evidence.states.append(st)

        # Extract dependency chains from graph results
        evidence.dependency_chains = self._extract_dependency_chains(
            graph_results, intent
        )

        # Extract state transitions from graph results + state machine
        evidence.state_transitions = self._extract_state_transitions(
            graph_results, state_machine, intent
        )

        # Match related rules
        evidence.related_rules = self._match_relevant_rules(intent, rules)

        # Include text chunks (deduped top candidates)
        evidence.text_chunks = self._select_text_chunks(merged_candidates)

        return evidence

    def format_for_llm(self, evidence: StructuredEvidence) -> str:
        """Format structured evidence for LLM consumption.

        Produces a markdown document with explicit sections for dependency
        chains, state transitions, rules, and text fragments.

        Args:
            evidence: The structured evidence package

        Returns:
            Markdown-formatted string suitable for LLM system/user prompt
        """
        parts = [
            f"# 查询: {evidence.query}",
            "",
        ]

        # Modules, signals, states summary
        if evidence.modules:
            parts.append(f"## 涉及模块\n{', '.join(evidence.modules)}")
            parts.append("")
        if evidence.signals:
            parts.append(f"## 相关信号\n{', '.join(evidence.signals[:10])}")
            parts.append("")
        if evidence.states:
            parts.append(f"## 相关状态\n{', '.join(evidence.states[:10])}")
            parts.append("")

        # Dependency chains (from graph)
        if evidence.dependency_chains:
            parts.append(f"## 依赖链 ({len(evidence.dependency_chains)} 条)")
            parts.append("")
            for i, dc in enumerate(evidence.dependency_chains, 1):
                parts.append(f"### 依赖链 {i}")
                parts.append(f"- **路径**: {' → '.join(dc.chain)}")
                parts.append(f"- **关系**: {' → '.join(dc.relation_types)}")
                if dc.source_sections:
                    parts.append(
                        f"- **来源**: {', '.join(dc.source_sections)}"
                    )
                if dc.description:
                    parts.append(f"- **说明**: {dc.description}")
                parts.append("")

        # State transitions (from graph + state machine)
        if evidence.state_transitions:
            parts.append(
                f"## 状态转移 ({len(evidence.state_transitions)} 条)"
            )
            parts.append("")
            for i, st in enumerate(evidence.state_transitions, 1):
                parts.append(f"### 转移 {i}: {st.source} → {st.target}")
                if st.guard:
                    parts.append(f"- **条件**: {st.guard}")
                if st.effect:
                    parts.append(f"- **效果**: {st.effect}")
                info_parts = []
                if st.module:
                    info_parts.append(f"模块: {st.module}")
                if st.section:
                    info_parts.append(f"章节: {st.section}")
                if info_parts:
                    parts.append(f"- **{' | '.join(info_parts)}**")
                parts.append("")

        # Related rules
        if evidence.related_rules:
            parts.append(
                f"## 相关规则 ({len(evidence.related_rules)} 条)"
            )
            parts.append("")
            for i, rule in enumerate(evidence.related_rules[:8], 1):
                rule_id = rule.get("rule_id", f"rule_{i}")
                rule_text = rule.get("text", "")[:200]
                rule_module = rule.get("module", "")
                parts.append(f"### 规则 {i}: {rule_id}")
                if rule_module:
                    parts.append(f"- **模块**: {rule_module}")
                parts.append(f"- **内容**: {rule_text}")
                parts.append("")

        # Text chunks (the original document fragments)
        n_chunks = min(5, len(evidence.text_chunks))
        if evidence.text_chunks:
            parts.append(f"## 文档片段 ({n_chunks} 条)")
            parts.append("")
            for i, entry in enumerate(evidence.text_chunks[:5], 1):
                chunk = entry.get("chunk", {})
                chunk_type = chunk.get("chunk_type", "?")
                section_path = chunk.get("section_path", "?")
                module = chunk.get("module", "?")
                score = entry.get("score", 0)
                text = chunk.get("text", "")[:600]

                parts.append(f"### 片段 {i} [{chunk_type}]")
                parts.append(
                    f"章节: {section_path} | 模块: {module} | 得分: {score:.3f}"
                )

                # Include image paths if present
                if chunk.get("has_image"):
                    img_paths = [
                        ref.get("storage_path", "")
                        for ref in chunk.get("image_refs", [])
                    ]
                    if img_paths:
                        parts.append(f"图片: {', '.join(img_paths[:2])}")

                parts.append("")
                parts.append(text)
                parts.append("")

        return "\n".join(parts)

    # --- Private: extraction logic -------------------------------------------

    def _extract_dependency_chains(
        self,
        graph_results: list[dict],
        intent: dict,
    ) -> list[DependencyChain]:
        """Extract dependency chains from graph retrieval results.

        Strategy:
          1. Identify BELONGS_TO, CONTROLS, DEPENDS_ON, TRIGGERED_BY,
             REQUIRES, REFERENCES relationships
          2. Group by entity, build a directed dependency graph
          3. Output chains up to depth 5
        """
        # Collect relationship edges from graph results
        edges: list[tuple[str, str, str]] = []  # (source, target, rel_type)

        for item in graph_results:
            entity = item.get("entity", {})
            rel_type = item.get("relationship", "")

            # Normalize relationship type
            rel_type_lower = rel_type.lower() if isinstance(rel_type, str) else ""

            entity_name = entity.get("name", "")
            entity_type = entity.get("entity_type", "")

            if not entity_name:
                continue

            # For BELONGS_TO: entity belongs to module → module depends on entity
            if "belongs_to" in rel_type_lower:
                module = entity.get("module", "")
                if module:
                    edges.append((entity_name, module, "belongs_to"))

            # For CONTROLS / OUTPUTS: entity controls something
            elif "controls" in rel_type_lower or "outputs" in rel_type_lower:
                target = entity.get("target", entity.get("related_to", ""))
                if target:
                    edges.append((entity_name, target, "controls"))

            # For DEPENDS_ON / REQUIRES: entity depends on something
            elif "depends_on" in rel_type_lower or "requires" in rel_type_lower:
                target = entity.get("target", entity.get("related_to", ""))
                if target:
                    edges.append((entity_name, target, "depends_on"))

            # For TRIGGERED_BY: something triggers entity
            elif "triggered_by" in rel_type_lower:
                source = entity.get("source", entity.get("related_to", ""))
                if source:
                    edges.append((source, entity_name, "triggers"))

            # For generic relationships with source/target
            else:
                source = entity.get("source", "")
                target = entity.get("target", "")
                if source and target and rel_type:
                    edges.append((source, target, rel_type))

        # Build adjacency list for chain construction
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for src, tgt, rel in edges:
            adjacency[src].append((tgt, rel))

        # Build chains by following edges from intent-matched entities
        intent_signals = set(intent.get("signals", []))
        intent_functions = set(intent.get("functions", []))
        intent_modules = set(intent.get("modules", []))
        start_entities = intent_signals | intent_functions | intent_modules

        chains: list[DependencyChain] = []
        seen_chains: set[str] = set()

        for start in list(start_entities)[:6]:
            # BFS chain building
            chain: list[str] = [start]
            rels: list[str] = []
            sections: list[str] = []

            # Follow edges up to depth 5
            current = start
            for _ in range(5):
                neighbors = adjacency.get(current, [])
                if not neighbors:
                    break
                # Take the first neighbor
                next_node, next_rel = neighbors[0]
                chain.append(next_node)
                rels.append(next_rel)
                current = next_node

            # Only keep chains with at least 2 nodes
            if len(chain) >= 2:
                chain_key = "→".join(chain)
                if chain_key not in seen_chains:
                    seen_chains.add(chain_key)
                    chains.append(
                        DependencyChain(
                            chain=chain,
                            relation_types=rels,
                            source_sections=sections,
                            description=" → ".join(
                                f"{a}--[{r}]-->{b}"
                                for a, r, b in zip(
                                    chain, rels, chain[1:]
                                )
                            ),
                        )
                    )

        return chains[:8]

    def _extract_state_transitions(
        self,
        graph_results: list[dict],
        state_machine: dict | None,
        intent: dict,
    ) -> list[StateTransition]:
        """Extract state transitions from graph results and state machine data.

        Strategy:
          1. Find state entities in graph_results
          2. Look up their incoming/outgoing transitions in the state machine
          3. Extract guard conditions and effects
        """
        transitions: list[StateTransition] = []
        seen: set[tuple[str, str]] = set()

        # Collect state names from graph results
        state_names: set[str] = set()
        for item in graph_results:
            entity = item.get("entity", {})
            if entity.get("entity_type") == "state":
                name = entity.get("name", "")
                if name:
                    state_names.add(name)

        # Also collect from intent
        for s in intent.get("states", []):
            state_names.add(s)

        # Extract transitions from state machine data
        if state_machine:
            sm_transitions = state_machine.get("transitions", [])
            if not sm_transitions:
                # Try nested structure: {"states": {"StateName": {"transitions": [...]}}}
                states_dict = state_machine.get("states", {})
                for state_name, state_data in states_dict.items():
                    for trans in state_data.get("transitions", []):
                        sm_transitions.append(
                            {
                                "source": state_name,
                                "target": trans.get("target", ""),
                                "guard": trans.get("guard", trans.get("condition", "")),
                                "effect": trans.get("effect", trans.get("action", "")),
                            }
                        )

            for trans in sm_transitions:
                source = str(trans.get("source", ""))
                target = str(trans.get("target", ""))
                if not source or not target:
                    continue

                # Only include if source or target matches our states of interest
                if source not in state_names and target not in state_names:
                    # But still include if the intent asks about transitions
                    if not intent.get("hint_transition"):
                        continue

                key = (source, target)
                if key in seen:
                    continue
                seen.add(key)

                transitions.append(
                    StateTransition(
                        source=source,
                        target=target,
                        guard=str(trans.get("guard", trans.get("condition", ""))),
                        effect=str(trans.get("effect", trans.get("action", ""))),
                        section=str(trans.get("section", "")),
                        module=str(
                            trans.get("module", state_machine.get("module", ""))
                        ),
                    )
                )

        # Also extract TRANSITION_TO relationships from graph_results
        for item in graph_results:
            rel_type = str(item.get("relationship", "")).lower()
            if "transition_to" in rel_type:
                entity = item.get("entity", {})
                source = entity.get("name", "")
                target = entity.get("target", entity.get("related_to", ""))
                if source and target:
                    key = (source, target)
                    if key not in seen:
                        seen.add(key)
                        transitions.append(
                            StateTransition(
                                source=source,
                                target=target,
                                guard=entity.get("guard", entity.get("condition", "")),
                                section=entity.get("section_path", ""),
                                module=entity.get("module", ""),
                            )
                        )

        return transitions[:10]

    def _match_relevant_rules(
        self,
        intent: dict,
        rules: list[dict] | None,
    ) -> list[dict]:
        """Match rules relevant to the query intent.

        Strategy:
          1. Filter rules by module (from intent modules)
          2. Filter rules by keyword (from intent keywords, signals, states)
          3. Rank by match count
        """
        if not rules:
            return []

        target_modules = set(m.lower() for m in intent.get("modules", []))
        target_signals = set(s.lower() for s in intent.get("signals", []))
        target_states = set(s.lower() for s in intent.get("states", []))
        target_functions = set(f.lower() for f in intent.get("functions", []))
        keywords = set(k.lower() for k in intent.get("keywords", []))

        scored: list[tuple[int, dict]] = []

        for rule in rules:
            score = 0
            rule_text = str(rule.get("text", ""))
            rule_module = str(rule.get("module", "")).lower()
            rule_id = str(rule.get("rule_id", "")).lower()

            combined = (rule_text + " " + rule_id + " " + rule_module).lower()

            # Module match
            if target_modules and rule_module in target_modules:
                score += 3

            # Signal match
            for sig in target_signals:
                if sig in combined:
                    score += 2

            # State match
            for st in target_states:
                if st in combined:
                    score += 2

            # Function match
            for func in target_functions:
                if func in combined:
                    score += 2

            # Keyword match
            for kw in keywords:
                if kw in combined:
                    score += 1

            if score > 0:
                scored.append((score, rule))

        # Sort by score descending, return top 8
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rule for _, rule in scored[:8]]

    def _select_text_chunks(
        self, merged_candidates: list[dict],
    ) -> list[dict]:
        """Select deduplicated text chunks from merged candidates.

        Deduplication is based on the first 200 characters of text.
        """
        seen: set[str] = set()
        selected: list[dict] = []

        for entry in merged_candidates:
            chunk = entry.get("chunk", {})
            text = chunk.get("text", "")
            sig = text[:200]
            if sig and sig not in seen:
                seen.add(sig)
                selected.append(entry)
                if len(selected) >= 5:
                    break

        return selected
