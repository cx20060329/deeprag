"""BCM-RAG Agentic RAG — Self-verifying, iterative retrieval agent.

Key differences from naive RAG:
  1. Query Decomposition: complex question → sub-questions
  2. Iterative Retrieval: reformulate if first pass insufficient
  3. Self-Verification: check if retrieved info actually answers the question
  4. Multi-source Fusion: merge chunk retrieval + graph + rules + state machine
  5. Confidence Scoring: know when to say "I don't know"

Pattern:
  User Query
    → Decompose (if complex)
    → Retrieve (per sub-question)
    → Verify (check coverage)
    → (if insufficient: reformulate → retrieve → verify)
    → Synthesize
    → Score confidence
    → Answer

Usage:
    from agent.agentic_rag import AgenticRAG
    rag = AgenticRAG()
    rag.load()
    result = rag.query("为什么车辆无法进入Driving？")
    print(result.answer, result.confidence, result.iterations)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class RetrievalStep:
    query: str
    source: str  # chunk_search | graph | rules | state_machine
    results_count: int
    top_sections: list[str] = field(default_factory=list)
    coverage_score: float = 0.0  # How well does this answer the original question?
    notes: str = ""


@dataclass
class AgenticResult:
    question: str
    sub_questions: list[str] = field(default_factory=list)
    steps: list[RetrievalStep] = field(default_factory=list)
    answer: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    iterations: int = 0
    sources: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agentic RAG Engine
# ---------------------------------------------------------------------------

class AgenticRAG:
    """Self-verifying, iterative retrieval agent for BCM engineering queries.

    The agent follows a Plan→Retrieve→Verify→(Reformulate)→Answer loop.
    """

    def __init__(self):
        self._pipeline = None
        self._engine = None
        self._sm = None
        self._rules = None
        self._loaded = False

        # Verification keywords per question type
        self._verification_checks = {
            "state_path": ["→", "迁移", "transition", "状态", "hops"],
            "impact": ["影响", "impact", "depth", "downstream", "affected"],
            "condition": ["条件", "前置", "触发", "guard", "必须", "需要"],
            "rule": ["规则", "rule", "激活", "关闭", "IF", "THEN"],
            "reachability": ["可达", "死锁", "不可达", "连通"],
        }

    # ---- Load ----

    def load(self) -> "AgenticRAG":
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

        print("Loading Agentic RAG...")
        from retrieval import RetrievalPipeline
        self._pipeline = RetrievalPipeline()
        self._pipeline.load(use_dense=True)

        from retrieval.reasoning_engine import ReasoningEngine
        self._engine = ReasoningEngine()
        sm_path = Path("output/content_analysis/state_machine_VMM.json")
        rules_path = Path("output/content_analysis/rules.json")
        if sm_path.exists():
            self._engine.load_state_machine(sm_path)
            self._sm = json.loads(sm_path.read_text(encoding="utf-8"))
        if rules_path.exists():
            self._engine.load_rules(rules_path)
            self._rules = json.loads(rules_path.read_text(encoding="utf-8"))

        self._loaded = True
        print("Agentic RAG ready.")
        return self

    # ---- Main Query Entry ----

    def query(self, question: str, max_iterations: int = 3) -> AgenticResult:
        """Answer a question with iterative, self-verifying retrieval."""
        if not self._loaded:
            raise RuntimeError("Not loaded. Call .load() first.")

        result = AgenticResult(question=question)

        # Step 1: Decompose complex questions
        result.sub_questions = self._decompose(question)
        if not result.sub_questions:
            result.sub_questions = [question]

        # Step 2: Retrieve + Verify loop
        seen_step_keys = set()
        for iteration in range(max_iterations):
            new_steps_this_round = 0
            for sub_q in result.sub_questions:
                steps = self._retrieve(sub_q, question)
                for step in steps:
                    # Deduplicate: skip if same source+query already seen
                    step_key = f"{step.source}:{step.query}"
                    if step_key in seen_step_keys:
                        continue
                    seen_step_keys.add(step_key)
                    result.steps.append(step)
                    new_steps_this_round += 1

            # Verify: do we have enough to answer?
            coverage = self._verify(question, result.steps)
            result.iterations = iteration + 1

            if coverage >= 0.7 or new_steps_this_round == 0:
                break  # Good enough or no new steps found

            # Reformulate: generate new sub-questions for missing info
            missing = self._detect_gaps(question, result.steps)
            if missing:
                result.sub_questions = missing
                result.missing_info.extend(missing)
            else:
                break

        # Step 3: Synthesize answer
        result.answer = self._synthesize(question, result.steps)
        result.confidence = self._compute_confidence(question, result)
        result.sources = list(set(s.notes for s in result.steps if s.notes))

        return result

    # ---- Decomposition ----

    def _decompose(self, question: str) -> list[str]:
        """Break complex questions into sub-questions."""
        ql = question.lower()
        subs = []

        # Pattern: "A如何影响B" → ["A是什么？", "B依赖什么？", "A→B的路径"]
        if any(w in ql for w in ["影响", "impact", "导致", "连锁"]):
            entities = re.findall(r"[A-Z][A-Za-z0-9_]{2,}|[一-鿿]{2,6}", question)
            if len(entities) >= 2:
                subs.append(f"{entities[0]} 的定义和作用")
                subs.append(f"{entities[0]} 关联的模块和信号")
                subs.append(f"{entities[0]} 失效后的影响链")

        # Pattern: "如何从A进入B" → path finding
        states_mentioned = [s for s in ["Abandoned", "Inactive", "Convenience", "Driving"]
                           if s.lower() in ql]
        if len(states_mentioned) >= 2:
            subs.append(f"从 {states_mentioned[0]} 到 {states_mentioned[-1]} 的迁移路径")

        # Pattern: "为什么不能X" → conditions + constraints
        if any(w in ql for w in ["为什么", "为何", "不能", "无法"]):
            target_state = None
            for s in ["Abandoned", "Inactive", "Convenience", "Driving"]:
                if s.lower() in ql:
                    target_state = s
                    break
            if target_state:
                subs.append(f"进入 {target_state} 的前置条件")
                subs.append(f"阻止进入 {target_state} 的约束条件")
            else:
                subs.append(question)

        # Pattern: "X需要什么条件" → conditions
        if any(w in ql for w in ["需要", "满足", "条件", "前置"]):
            target_state = None
            for s in ["Abandoned", "Inactive", "Convenience", "Driving"]:
                if s.lower() in ql:
                    target_state = s; break
            if target_state:
                subs.append(f"进入 {target_state} 的前置条件和触发条件")
                subs.append(f"{target_state} 状态相关的规则")
            else:
                subs.append(question)

        if not subs:
            subs.append(question)

        return subs[:4]

    # ---- Retrieval ----

    def _retrieve(self, sub_query: str, original_question: str) -> list[RetrievalStep]:
        """Execute multi-source retrieval for a sub-query.

        Uses BOTH the sub-query keywords AND the original question's intent
        to decide which retrieval sources to activate.
        """
        steps = []
        ql = sub_query.lower()
        oql = original_question.lower()

        # 1) Always do chunk search
        r = self._pipeline.search(sub_query, top_k=5)
        merged = r.get("merged", [])
        sections = [m["chunk"].get("section_path", "") for m in merged[:3]]
        steps.append(RetrievalStep(
            query=sub_query, source="chunk_search",
            results_count=len(merged), top_sections=sections,
            coverage_score=self._quick_coverage(sub_query, merged),
        ))

        # Check both sub-query AND original question for intent keywords
        combined_ql = ql + " " + oql

        # 2) State machine if states mentioned in EITHER query
        sm_states = ["abandoned", "inactive", "convenience", "driving"]
        if any(s in combined_ql for s in sm_states):
            for state in sm_states:
                if state in ql:
                    state_title = state.title()
                    incoming = [t for t in self._sm.get("transitions", [])
                               if t["target"] == state_title]
                    outgoing = [t for t in self._sm.get("transitions", [])
                               if t["source"] == state_title]
                    if incoming or outgoing:
                        steps.append(RetrievalStep(
                            query=f"state:{state_title}", source="state_machine",
                            results_count=len(incoming) + len(outgoing),
                            notes=f"Entering {state_title}: {len(incoming)} paths, Exiting: {len(outgoing)} paths",
                            coverage_score=0.8 if incoming and outgoing else 0.5,
                        ))
                    break

        # 3) Path query if two states mentioned in EITHER query
        mentioned = [s for s in ["Abandoned", "Inactive", "Convenience", "Driving"]
                    if s.lower() in combined_ql]
        if len(mentioned) >= 2:
            try:
                paths = self._engine.path_query(mentioned[0], mentioned[-1])
                if paths["paths"]:
                    steps.append(RetrievalStep(
                        query=f"path:{mentioned[0]}→{mentioned[-1]}", source="path_finder",
                        results_count=paths["total_paths"],
                        notes=f"Shortest: {paths['shortest_hops']} hops via {'→'.join(paths['paths'][0]['sequence'])}",
                        coverage_score=0.9,
                    ))
            except Exception:
                pass

        # 4) Rule lookup for conditions — check BOTH queries
        if any(w in combined_ql for w in ["条件", "触发", "激活", "规则", "前置", "为什么", "如何进入", "需要满足"]):
            matched = []
            # Tokenize Chinese+English text properly
            search_terms = self._tokenize_query(sub_query) + self._tokenize_query(original_question)
            search_terms = list(set(t for t in search_terms if len(t) > 1))[:15]
            for rule in self._rules.get("rules", []):
                rule_text = json.dumps(rule, ensure_ascii=False).lower()
                kw_matches = sum(1 for kw in search_terms if kw.lower() in rule_text)
                if kw_matches >= 1:
                    matched.append(rule)
            if matched:
                # Show rule details
                rule_summary = "; ".join(
                    f"{r['module']}/{r['rule_type']}: {r.get('action','')[:60]}"
                    for r in matched[:3]
                )
                steps.append(RetrievalStep(
                    query=f"rules:{sub_query[:50]}", source="rule_engine",
                    results_count=len(matched),
                    notes=f"Matched {len(matched)} rules: {rule_summary}",
                    coverage_score=min(len(matched) / 8, 0.9),
                ))

        # 6) Forward chain for impact queries on ANY entity (not just states)
        if any(w in combined_ql for w in ["影响", "impact", "导致", "后果", "连锁", "失效", "会影响"]):
            # Try to find entity names using proper tokenization
            entities = re.findall(r"[A-Z][A-Za-z0-9_]{2,}", original_question)
            cn_bigrams = re.findall(r"[一-鿿]{2,6}", original_question)
            entities.extend(cn_bigrams)
            for entity in entities[:5]:
                if entity.lower() in ("从", "如何", "为什么", "什么", "怎么", "哪些"):
                    continue
                try:
                    report = self._engine.forward_chain(entity, max_depth=3)
                    if report.total_impacted > 0:
                        impacted_summary = "; ".join(
                            f"{i.entity_type}:{i.entity}" for i in report.impacted[:5]
                        )
                        steps.append(RetrievalStep(
                            query=f"impact:{entity}", source="impact_analysis",
                            results_count=report.total_impacted,
                            notes=f"{entity} impacts {report.total_impacted} entities: {impacted_summary}",
                            coverage_score=0.8,
                        ))
                        break  # Found a good entity
                except Exception:
                    continue

        # 5) Reachability for deadlock/unreachable queries — check BOTH
        if any(w in combined_ql for w in ["死锁", "不可达", "活锁", "存在", "所有状态", "连通", "VMM状态机"]):
            try:
                issues = self._engine.reachability_analysis("VMM")
                steps.append(RetrievalStep(
                    query=f"reachability:VMM", source="reachability_check",
                    results_count=len(issues),
                    notes=f"{'No issues' if not issues else f'{len(issues)} issues found: ' + '; '.join(i['type'] for i in issues[:3])}",
                    coverage_score=0.9 if not issues else 0.7,
                ))
            except Exception:
                pass

        return steps

    def _quick_coverage(self, query: str, merged: list[dict]) -> float:
        """Quick estimate of how well chunks cover the query."""
        if not merged:
            return 0.0
        query_terms = set(re.findall(r"\w{2,}", query.lower()))
        if not query_terms:
            return 0.3
        all_text = " ".join(m["chunk"].get("text", "") for m in merged[:3]).lower()
        term_hits = sum(1 for t in query_terms if t in all_text)
        return min(term_hits / len(query_terms), 1.0)

    # ---- Verification ----

    def _verify(self, question: str, steps: list[RetrievalStep]) -> float:
        """Verify if retrieved information sufficiently answers the question."""
        if not steps:
            return 0.0

        # Check: do we have results from enough sources?
        sources_used = set(s.source for s in steps)
        source_score = min(len(sources_used) / 3, 1.0)  # 3+ sources = full score

        # Check: average coverage
        coverage_scores = [s.coverage_score for s in steps]
        avg_coverage = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0

        # Check: any high-quality source (state machine, rules, path finder)?
        has_deep_reasoning = any(
            s.source in ("state_machine", "rule_engine", "path_finder") and s.results_count > 0
            for s in steps
        )
        reasoning_bonus = 0.2 if has_deep_reasoning else 0.0

        # Determine question type and check key signals
        ql = question.lower()
        expected_signals = []
        if any(w in ql for w in ["路径", "如何进入", "如何退出", "迁移"]):
            expected_signals = ["→", "hops", "path", "sequence"]
        elif any(w in ql for w in ["影响", "impact", "导致"]):
            expected_signals = ["impact", "depth", "affected"]
        elif any(w in ql for w in ["条件", "触发", "为什么"]):
            expected_signals = ["condition", "guard", "前置"]

        signal_score = 0.0
        if expected_signals:
            all_notes = " ".join(s.notes.lower() for s in steps)
            hits = sum(1 for sig in expected_signals if sig in all_notes)
            signal_score = hits / len(expected_signals)

        return min(source_score * 0.3 + avg_coverage * 0.3 + signal_score * 0.2 + reasoning_bonus, 1.0)

    def _detect_gaps(self, question: str, steps: list[RetrievalStep]) -> list[str]:
        """Detect what information is still missing and generate follow-up queries."""
        ql = question.lower()
        gaps = []
        sources_found = set(s.source for s in steps)

        # Missing state machine?
        if any(s in ql for s in ["abandoned", "inactive", "convenience", "driving"]):
            if "state_machine" not in sources_found:
                for state in ["Abandoned", "Inactive", "Convenience", "Driving"]:
                    if state.lower() in ql:
                        gaps.append(f"{state} 状态的进入和退出条件")
                        break

        # Missing rules?
        if any(w in ql for w in ["为什么", "条件", "触发", "无法", "需要满足", "规则"]):
            if "rule_engine" not in sources_found:
                gaps.append(question)

        # Missing path?
        mentioned = [s for s in ["Abandoned", "Inactive", "Convenience", "Driving"] if s.lower() in ql]
        if len(mentioned) >= 2:
            if "path_finder" not in sources_found:
                gaps.append(f"{mentioned[0]} 到 {mentioned[-1]} 的迁移路径")

        # Missing reachability?
        if any(w in ql for w in ["死锁", "不可达", "活锁", "存在"]):
            if "reachability_check" not in sources_found:
                gaps.append("VMM状态机可达性分析")

        return gaps[:2]

    # ---- Synthesis ----

    def _synthesize(self, question: str, steps: list[RetrievalStep]) -> str:
        """Synthesize a structured answer from all retrieval steps."""
        parts = [f"# {question}\n"]

        # Group steps by source
        by_source = defaultdict(list)
        for s in steps:
            by_source[s.source].append(s)

        source_names = {
            "chunk_search": "文档检索",
            "state_machine": "状态机分析",
            "path_finder": "路径查找",
            "rule_engine": "规则匹配",
        }

        for source, source_steps in by_source.items():
            name = source_names.get(source, source)
            parts.append(f"## {name}")
            for s in source_steps:
                if s.notes:
                    parts.append(f"- {s.notes}")
                if s.top_sections:
                    parts.append(f"  相关章节: {', '.join(s.top_sections[:3])}")
                parts.append("")

        # Overall assessment
        coverage = self._verify(question, steps)
        if coverage >= 0.7:
            parts.append(f"## 置信度: {'█' * int(coverage * 10)}{'░' * (10 - int(coverage * 10))} {coverage:.0%}")
            parts.append("信息充足，可以回答。")
        elif coverage >= 0.4:
            parts.append(f"## 置信度: {'█' * int(coverage * 10)}{'░' * (10 - int(coverage * 10))} {coverage:.0%}")
            parts.append("部分信息可用，建议进一步确认。")
        else:
            parts.append(f"## 置信度: {coverage:.0%} — 信息不足")
            parts.append("⚠️ 根据现有文档无法完全确定，建议查阅原始规范。")

        return "\n".join(parts)

    def _tokenize_query(self, text: str) -> list[str]:
        """Tokenize Chinese+English text into searchable terms."""
        tokens = []
        # English identifiers
        tokens.extend(re.findall(r"[A-Z][A-Za-z0-9_]{1,}", text))
        # Chinese bigrams
        cjk = re.findall(r"[一-鿿]", text)
        for i in range(len(cjk) - 1):
            tokens.append(cjk[i] + cjk[i + 1])
        # Chinese single characters
        tokens.extend(cjk)
        # Whole English words
        tokens.extend(re.findall(r"[a-z0-9_]{2,}", text.lower()))
        return tokens

    def _compute_confidence(self, question: str, result: AgenticResult) -> float:
        """Compute overall answer confidence."""
        if not result.steps:
            return 0.0

        # Source diversity
        sources = set(s.source for s in result.steps)
        source_div = min(len(sources) / 3, 1.0)  # 3+ sources = full diversity score

        # Coverage
        coverage = self._verify(question, result.steps)

        # Iteration benefit (more iterations = harder question, lower confidence)
        iter_penalty = max(0, (result.iterations - 1) * 0.1)

        return min(source_div * 0.3 + coverage * 0.7 - iter_penalty, 1.0)


# ---------------------------------------------------------------------------
# CLI Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"

    rag = AgenticRAG()
    rag.load()

    test_queries = [
        "从Abandoned模式如何进入Driving模式？",
        "KeyLost会影响哪些功能？",
        "为什么车辆无法从Inactive直接进入Driving？",
        "VMM状态机是否存在死锁？",
        "进入Driving需要同时满足哪些条件？",
    ]

    for q in test_queries:
        print(f"\n{'='*60}")
        result = rag.query(q)
        print(f"Q: {q}")
        print(f"Sub-questions: {result.sub_questions}")
        print(f"Iterations: {result.iterations}")
        print(f"Confidence: {result.confidence:.0%}")
        print(f"Sources: {len(set(s.source for s in result.steps))} types")
        print(result.answer[:500])
        print(f"{'='*60}")
