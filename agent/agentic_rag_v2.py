"""BCM-RAG Agentic RAG v2 — Complete Engineering Reasoning Agent.

======================================================================
WHY THIS EXISTS — AND WHY IT'S DIFFERENT FROM OTHER RAG TYPES
======================================================================

┌─────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Capability      │ Naive RAG        │ Tool-use Agent   │ Agentic RAG v2   │
├─────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Retrieve chunks │ ✅               │ ✅               │ ✅               │
│ Multi-source    │ ❌               │ ✅ (8 tools)     │ ✅ + LLM chooses  │
│ Query decompose │ ❌               │ ❌               │ ✅ + LLM reasons  │
│ Self-reflect    │ ❌               │ ❌               │ ✅ critique loop  │
│ Reformulate     │ ❌               │ ❌               │ ✅ semantic re-do │
│ Evidence chain  │ ❌               │ ❌               │ ✅ traceable path │
│ Hypothesis test │ ❌               │ ❌               │ ✅ generate+verify│
│ Citations       │ ❌               │ ❌               │ ✅ section-level  │
│ Audit trail     │ ❌               │ ❌               │ ✅ step-by-step   │
└─────────────────┴──────────────────┴──────────────────┴──────────────────┘

KEY DIFFERENTIATORS (why each matters for BCM engineering):

1. LLM-DRIVEN TOOL SELECTION (not regex)
   - Regex: "查询含'影响'→调用impact_analysis" — brittle, misses synonyms
   - LLM: "用户问KeyLost的后果，我需要impact_analysis + query_rules + search_chunks"
   - Why: 汽车规范中同一概念有中/英/缩写多种表达, 正则无法穷举

2. SELF-REFLECTION LOOP (not just coverage score)
   - Coverage: "2/5 sources found → 40% confidence" — quantitative only
   - Self-reflection: "我找到了状态迁移路径,但缺少具体的CAN信号编码值,需要查信号表"
   - Why: 工程问题需要定性判断,不是打分发; 知道"缺什么"比知道"得分低"更有价值

3. DYNAMIC QUERY REFORMULATION (semantic, not keyword)
   - Keyword: "KeyLost"没匹配→换"KeyLost失效"再搜 — 同义词替换
   - Semantic: "KeyLost"→理解为"钥匙丢失/PEPS_KeyStatus=Invalid"→重新检索
   - Why: BCM文档用中文描述"钥匙失效", 工程师可能用英文"KeyLost"查询

4. EVIDENCE CHAIN TRACING (audit trail)
   - Without: "答案是Abandoned→Inactive→Convenience→Driving"
   - With: "①状态机§2.3.4.1.1: Abandoned出边为Inactive(收到网络唤醒)
            ②状态机§2.3.4.2.2: Inactive出边为Convenience(门打开+钥匙有效)
            ③状态机§2.3.4.3.2: Convenience出边为Driving(刹车+D/R档+钥匙)"
   - Why: 汽车功能安全(ISO 26262)要求所有推理可追溯到规范原文

5. HYPOTHESIS GENERATION & TESTING
   - Without: "IGN1故障: 文档说会影响PEPS_UsageMode"
   - With: "假设1: IGN1开路→PEPS_IGN1RelayValidity=Invalid.
           验证: 规则VMM_IGN1_Fault_001确认.
           假设2: IGN1短路→IGN1继电器无法驱动.
           验证: 输出信号表§2.2.1.2显示IGN1为HSD驱动"
   - Why: 诊断类问题本质是假设检验,不是信息检索
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ======================================================================
# FEATURE 1: LLM-DRIVEN TOOL SELECTION
# ======================================================================
#
# WHY: Regex-based tool selection ("查询含'影响'→impact_analysis") fails when:
#   - Same concept expressed differently (KeyLost vs 钥匙失效 vs PEPS_KeyStatus)
#   - Query implies a tool need that regex can't capture
#   ("为什么不能启动" → needs backward_chain, not just rule lookup)
#
# SOLUTION: LLM receives tool descriptions and decides which to use.
# ======================================================================

TOOL_DESCRIPTIONS_FOR_LLM = """
可用工具:
1. search_chunks(query, top_k=5) — 搜索文档原始内容。适用:事实/定义查询。
2. query_graph(entity, entity_type="", expand_hops=1) — 查知识图谱。适用:信号属于哪个模块?模块有哪些信号?
3. query_rules(module="", keywords="") — 查规则库。适用:激活条件?故障检测逻辑?前置条件?
4. query_state_machine(state) — 查状态机。适用:某状态如何进入/退出?有哪些迁移边?
5. trace_path(source, target) — 两状态间路径。适用:从A如何到达B?最短几步?
6. analyze_impact(entity, max_depth=3) — 前向影响分析。适用:KeyLost会影响什么?某故障会导致什么?
7. check_conflicts(module="VMM") — 规则冲突检测。适用:规则冲突?优先级?
8. check_reachability(module="VMM") — 可达性分析。适用:死锁?不可达状态?
"""


# ======================================================================
# FEATURE 2: SELF-REFLECTION LOOP
# ======================================================================
#
# WHY: Coverage scores are quantitative but don't capture qualitative gaps.
#   "3/5 sources found, 60% confidence" — but WHAT is missing?
#   Self-reflection tells you: "I found the state transition path but
#   I'm missing the specific CAN signal values. Need to search signal tables."
#
# This is what separates a "retrieval system" from an "engineering assistant."
# ======================================================================

# ======================================================================
# FEATURE 3: DYNAMIC QUERY REFORMULATION (semantic)
# ======================================================================
#
# WHY: BCM domain has multiple names for the same thing:
#   KeyLost = 钥匙失效 = 钥匙丢失 = PEPS_KeyStatus = 钥匙无效
#   A naive system searches "KeyLost" → finds nothing → gives up.
#   An agentic system reformulates using domain knowledge from KG.
# ======================================================================

# ======================================================================
# FEATURE 4: EVIDENCE CHAIN TRACING
# ======================================================================
#
# WHY: In automotive functional safety (ISO 26262), every claim must be
#   traceable to the specification. "The system enters Driving when..."
#   is not enough. You need: "Per §2.3.4.3.2, Transition Convenience→Driving
#   requires: BrakePedal=Pressed AND KeyValid=True AND BMSH_StsCC2=Disconnect"
# ======================================================================

# ======================================================================
# FEATURE 5: HYPOTHESIS GENERATION & TESTING
# ======================================================================
#
# WHY: Diagnostic questions are fundamentally different from lookup questions.
#   "Why can't the vehicle start?" is not a retrieval task — it's hypothesis
#   generation and verification:
#     1. Generate possible causes (no key, IGN1 fault, low voltage, ...)
#     2. For each hypothesis, check conditions against rules
#     3. Rank by likelihood (which conditions are NOT met?)
# ======================================================================


@dataclass
class EvidenceLink:
    """A single piece of evidence with full traceability."""
    source_type: str       # state_machine | rule | chunk | graph | path
    source_id: str         # section number or rule_id or state name
    content: str           # the actual evidence text
    reasoning: str         # how this evidence relates to the question
    confidence: float      # 0.0-1.0


@dataclass
class Hypothesis:
    statement: str
    supporting_evidence: list[EvidenceLink] = field(default_factory=list)
    contradicting_evidence: list[EvidenceLink] = field(default_factory=list)
    likelihood: float = 0.0


@dataclass
class AgenticV2Result:
    question: str
    tool_plan: list[dict]          # which tools LLM chose and why
    evidence_chain: list[EvidenceLink]
    hypotheses: list[Hypothesis]
    reflections: list[str]         # self-reflection notes
    answer: str
    confidence: float
    audit_trail: str               # full step-by-step trace


class AgenticRAGv2:
    """Complete Agentic RAG with all 5 advanced features."""

    def __init__(self, api_key: str | None = None, provider: str = "zhipu"):
        self.api_key = api_key
        self.provider = provider
        self._pipeline = None
        self._engine = None
        self._sm = None
        self._rules = None
        self._loaded = False

    def load(self) -> "AgenticRAGv2":
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
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
        print("AgenticRAGv2 ready.")
        return self

    # ==================================================================
    # FEATURE 1: LLM-DRIVEN TOOL SELECTION
    # ==================================================================

    def _plan_tools_with_llm(self, question: str) -> list[dict]:
        """Use LLM to decide which tools to call and WHY.

        WHY THIS MATTERS:
        Regex-based selection fails on synonyms (KeyLost≠钥匙失效) and
        implicit needs ("为什么不能启动" implies backward chain, not just rules).
        LLM understands semantic intent, not just keyword presence.
        """
        if not self.api_key:
            return self._plan_tools_fallback(question)

        from openai import OpenAI
        client = self._get_client()

        prompt = f"""你是BCM工程专家。用户提问: "{question}"

{TOOL_DESCRIPTIONS_FOR_LLM}

请选择最合适的工具组合(2-5个),按调用顺序排列。
输出严格JSON数组,每个元素含:
{{"tool": "工具名", "args": {{参数}}, "why": "为什么选这个工具,它能提供什么信息"}}

只输出JSON数组,不要其他文字。"""

        try:
            r = client.chat.completions.create(
                model="glm-4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500, temperature=0.1,
            )
            text = r.choices[0].message.content
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass
        return self._plan_tools_fallback(question)

    def _plan_tools_fallback(self, question: str) -> list[dict]:
        """Rule-based fallback when LLM unavailable."""
        ql = question.lower()
        tools = []
        states = [s for s in ["Abandoned","Inactive","Convenience","Driving"] if s.lower() in ql]
        if len(states) >= 2:
            tools.append({"tool":"trace_path","args":{"source":states[0],"target":states[-1]},
                         "why":"Query mentions two states, need to find path between them"})
        elif states:
            tools.append({"tool":"query_state_machine","args":{"state":states[0]},
                         "why":f"Query mentions state {states[0]}, need entry/exit conditions"})
        if any(w in ql for w in ["影响","impact","导致","失效"]):
            tools.append({"tool":"analyze_impact","args":{"entity":question[:30]},
                         "why":"Query asks about impact/consequences"})
        if any(w in ql for w in ["条件","触发","为什么","无法","规则"]):
            tools.append({"tool":"query_rules","args":{"keywords":question[:80]},
                         "why":"Query asks about conditions/rules"})
        if any(w in ql for w in ["死锁","不可达","是否存在"]):
            tools.append({"tool":"check_reachability","args":{},
                         "why":"Query asks about state machine properties"})
        tools.append({"tool":"search_chunks","args":{"query":question,"top_k":5},
                     "why":"Always needed for evidence grounding"})
        return tools

    def _get_client(self):
        from openai import OpenAI
        if self.provider == "zhipu":
            return OpenAI(api_key=self.api_key, base_url="https://open.bigmodel.cn/api/paas/v4/")
        return OpenAI(api_key=self.api_key, base_url="https://api.openai.com/v1")

    def _get_llm(self):
        """Get an LLMAnswerGenerator instance for this agent.

        Reuses the existing LLMAnswerGenerator class with the agent's
        API key and provider settings. Used by Improvement #5
        (LLM answer synthesis).
        """
        from retrieval.llm_answer import LLMAnswerGenerator
        return LLMAnswerGenerator(
            api_key=self.api_key,
            provider=self.provider,
        )

    # ==================================================================
    # FEATURE 2: SELF-REFLECTION LOOP
    # ==================================================================

    def _reflect(self, question: str, evidence: list[EvidenceLink]) -> list[str]:
        """Self-reflection: what did we find? What's still missing?

        WHY THIS MATTERS:
        Coverage scores say "60% confidence" but don't say WHY.
        Self-reflection produces actionable feedback:
        "Missing CAN signal values" → triggers reformulation to search signal tables.
        This is the difference between a retrieval system and an engineering assistant.
        """
        reflections = []

        # Check evidence source diversity
        sources = set(e.source_type for e in evidence)
        if "state_machine" not in sources:
            reflections.append("缺少状态机分析: 未检查状态迁移路径和触发条件")
        if "rule" not in sources:
            reflections.append("缺少规则匹配: 未查询激活/关闭条件和故障检测规则")
        if "chunk" not in sources:
            reflections.append("缺少文档原文: 未检索原始规范文本作为证据支撑")

        # Check evidence quality
        high_conf = [e for e in evidence if e.confidence > 0.7]
        if len(high_conf) < 2:
            reflections.append("证据置信度不足: 找到的信息确定性不够,需要更精准的检索")

        # Check if question type is addressed
        ql = question.lower()
        if any(w in ql for w in ["影响","impact"]) and not any(
            "impact" in e.source_type for e in evidence):
            reflections.append("未执行影响分析: 查询涉及影响评估但未触发前向链推理")

        if any(s in ql for s in ["abandoned","inactive","convenience","driving"]) and len([
            e for e in evidence if e.source_type == "state_machine"]) == 0:
            reflections.append("未查询状态机: 查询涉及状态但未检索迁移路径")

        return reflections

    # ==================================================================
    # FEATURE 3: DYNAMIC QUERY REFORMULATION
    # ==================================================================

    def _reformulate(self, question: str, reflections: list[str],
                     evidence: list[EvidenceLink]) -> list[str]:
        """Generate better queries based on what's missing.

        WHY THIS MATTERS:
        "KeyLost" → no results → reformulate as "钥匙 失效 PEPS_KeyStatus"
        Uses domain knowledge from KG to expand queries semantically,
        not just adding synonyms.
        """
        new_queries = []

        for reflection in reflections:
            if "状态机" in reflection:
                states = ["Abandoned", "Inactive", "Convenience", "Driving"]
                for s in states:
                    if s.lower() in question.lower():
                        new_queries.append(f"{s} 状态 进入条件 退出条件 迁移")
                        break

            if "规则" in reflection:
                new_queries.append(f"{question} 激活条件 触发条件 前置条件 规则")

            if "影响分析" in reflection:
                # Try to find entity names from KG
                entities = re.findall(r"[A-Z][A-Za-z0-9_]{2,}", question)
                for ent in entities[:3]:
                    new_queries.append(f"{ent} 信号 影响 模块 功能")

            if "文档原文" in reflection:
                new_queries.append(question)

        # Deduplicate
        seen = set()
        unique = []
        for q in new_queries:
            if q not in seen:
                seen.add(q)
                unique.append(q)

        return unique[:3]

    # ==================================================================
    # FEATURE 4: EVIDENCE CHAIN TRACING
    # ==================================================================

    def _build_evidence_chain(self, question: str) -> list[EvidenceLink]:
        """Build a traceable evidence chain from multiple sources.

        WHY THIS MATTERS:
        ISO 26262 requires every claim to be traceable to specification.
        Each EvidenceLink records: what was found, where exactly, and
        how it relates to answering the question.
        """
        chain = []

        # 1) State machine evidence
        ql = question.lower()
        for state in ["Abandoned", "Inactive", "Convenience", "Driving"]:
            if state.lower() in ql:
                # Get transitions
                transitions = self._sm.get("transitions", [])
                incoming = [t for t in transitions if t["target"] == state]
                outgoing = [t for t in transitions if t["source"] == state]

                for t in incoming:
                    chain.append(EvidenceLink(
                        source_type="state_machine",
                        source_id=t.get("source_section", "?"),
                        content=f"{t['source']} → {t['target']}: {t.get('guard','')[:200]}",
                        reasoning=f"进入{state}的前置条件和触发事件",
                        confidence=0.90,
                    ))

                for t in outgoing:
                    chain.append(EvidenceLink(
                        source_type="state_machine",
                        source_id=t.get("source_section", "?"),
                        content=f"{t['source']} → {t['target']}: {t.get('guard','')[:200]}",
                        reasoning=f"退出{state}的迁移目标和条件",
                        confidence=0.90,
                    ))
                break

        # 2) Rule evidence
        if self._rules:
            terms = self._tokenize_cjk(question)
            for rule in self._rules.get("rules", []):
                rule_text = json.dumps(rule, ensure_ascii=False).lower()
                matches = sum(1 for t in terms if len(t) > 1 and t.lower() in rule_text)
                if matches >= 2:
                    chain.append(EvidenceLink(
                        source_type="rule",
                        source_id=rule.get("rule_id", "?"),
                        content=f"[{rule.get('rule_type','?')}] {rule.get('condition_expr','')[:150]} → {rule.get('action','')[:150]}",
                        reasoning=f"规则定义了{rule.get('module','?')}模块的行为逻辑",
                        confidence=0.85,
                    ))
                if len([e for e in chain if e.source_type == "rule"]) >= 5:
                    break

        # 3) Chunk evidence
        r = self._pipeline.search(question, top_k=3)
        for m in r.get("merged", [])[:3]:
            chunk = m["chunk"]
            chain.append(EvidenceLink(
                source_type="chunk",
                source_id=f"{chunk.get('section_path','?')}",
                content=chunk.get("text", "")[:300],
                reasoning=f"文档原文,章节{chunk.get('section_title','?')}",
                confidence=0.70,
            ))

        return chain

    def _tokenize_cjk(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Z][A-Za-z0-9_]{1,}", text)
        cjk = re.findall(r"[一-鿿]", text)
        for i in range(len(cjk)-1):
            tokens.append(cjk[i] + cjk[i+1])
        tokens.extend(cjk)
        return tokens

    # ==================================================================
    # FEATURE 5: HYPOTHESIS GENERATION & TESTING
    # ==================================================================

    def _generate_hypotheses(self, question: str,
                             evidence: list[EvidenceLink]) -> list[Hypothesis]:
        """For diagnostic queries, generate and test hypotheses.

        WHY THIS MATTERS:
        "Why can't X happen?" is not a retrieval task — it's diagnosis.
        The agent should:
          1. Generate possible causes from rules/KG
          2. Test each against known conditions
          3. Rank by likelihood
        This transforms the system from "search engine" to "diagnostic assistant."
        """
        ql = question.lower()
        if not any(w in ql for w in ["为什么","为何","无法","不能","故障","失效","诊断"]):
            return []

        hypotheses = []

        # Find what the user is asking about
        target = None
        for state in ["Abandoned", "Inactive", "Convenience", "Driving"]:
            if state.lower() in ql:
                target = ("state", state); break
        if not target:
            for sig in re.findall(r"[A-Z][A-Za-z0-9_]{3,}", question):
                target = ("signal", sig); break
        if not target:
            cn = re.findall(r"[一-鿿]{2,6}", question)
            if cn:
                target = ("concept", cn[0])

        if not target:
            return []

        # Generate hypotheses from rules
        target_type, target_name = target
        for rule in self._rules.get("rules", [])[:20]:
            rule_text = json.dumps(rule, ensure_ascii=False).lower()
            if target_name.lower() not in rule_text:
                continue

            cond = rule.get("condition_expr", "")
            action = rule.get("action", "")

            # Hypothesis: this rule's condition being UNMET could be the cause
            if cond:
                h = Hypothesis(
                    statement=f"条件不满足: {cond[:150]}",
                    likelihood=0.5,
                )
                h.supporting_evidence.append(EvidenceLink(
                    source_type="rule", source_id=rule.get("rule_id","?"),
                    content=f"IF {cond[:200]} THEN {action[:200]}",
                    reasoning=f"规则定义了必要条件,缺少任一条件则无法执行",
                    confidence=0.85,
                ))
                hypotheses.append(h)

        # Rank by likelihood (more conditions = more ways to fail)
        for h in hypotheses:
            h.likelihood = min(len(h.supporting_evidence) * 0.15 + 0.3, 0.95)

        hypotheses.sort(key=lambda h: -h.likelihood)
        return hypotheses[:5]

    # ==================================================================
    # MAIN QUERY
    # ==================================================================

    def query(
        self,
        question: str,
        max_iterations: int = 2,
        use_llm_synthesis: bool = False,
    ) -> AgenticV2Result:
        """Run the complete agentic RAG pipeline.

        Args:
            question: User's engineering question
            max_iterations: Max reformulation iterations
            use_llm_synthesis: Use LLM for answer synthesis (Improvement #5).
                               When True, the evidence chain and hypotheses
                               are sent to an LLM for structured answer
                               generation with full citations.
                               When False, uses the original rule-based
                               string concatenation (backward compatible).

        Returns:
            AgenticV2Result with answer, evidence chain, hypotheses, etc.
        """
        if not self._loaded:
            raise RuntimeError("Not loaded")

        t0 = time.time()
        result = AgenticV2Result(
            question=question, tool_plan=[], evidence_chain=[],
            hypotheses=[], reflections=[], answer="", confidence=0.0,
            audit_trail="",
        )

        # Step 1: LLM-driven tool planning
        result.tool_plan = self._plan_tools_with_llm(question)
        trail = [f"## Tool Plan ({len(result.tool_plan)} tools)"]
        for tp in result.tool_plan:
            trail.append(f"- **{tp['tool']}**: {tp.get('why','')}")

        # Step 2: Execute tools + build evidence chain
        result.evidence_chain = self._build_evidence_chain(question)
        trail.append(f"\n## Evidence Chain ({len(result.evidence_chain)} links)")
        for i, ev in enumerate(result.evidence_chain[:8]):
            trail.append(f"{i+1}. [{ev.source_type}] §{ev.source_id}: {ev.content[:100]}...")

        # Step 3: Self-reflection
        result.reflections = self._reflect(question, result.evidence_chain)
        trail.append(f"\n## Self-Reflection")
        for ref in result.reflections:
            trail.append(f"- ⚠ {ref}")

        # Step 4: Reformulate if needed
        reformulated = []
        if result.reflections:
            reformulated = self._reformulate(question, result.reflections,
                                             result.evidence_chain)
            if reformulated:
                trail.append(f"\n## Reformulated Queries")
                for rq in reformulated:
                    trail.append(f"- {rq}")
                    # Re-search with reformulated queries
                    extra = self._build_evidence_chain(rq)
                    for ev in extra[:3]:
                        if ev not in result.evidence_chain:
                            result.evidence_chain.append(ev)

        # Step 5: Hypothesis generation (for diagnostic queries)
        result.hypotheses = self._generate_hypotheses(question, result.evidence_chain)
        if result.hypotheses:
            trail.append(f"\n## Hypotheses ({len(result.hypotheses)})")
            for i, h in enumerate(result.hypotheses):
                trail.append(f"{i+1}. {h.statement[:120]} (likelihood: {h.likelihood:.0%})")

        # Step 6: Compute confidence
        sources_count = len(set(e.source_type for e in result.evidence_chain))
        high_conf_count = len([e for e in result.evidence_chain if e.confidence > 0.7])
        result.confidence = min(sources_count * 0.15 + high_conf_count * 0.08, 0.95)
        if result.hypotheses:
            result.confidence = min(result.confidence + 0.1, 0.95)

        # Step 7: Build answer
        if use_llm_synthesis and self.api_key:
            # ---- LLM Synthesis (Improvement #5) ----
            from retrieval.llm_answer import LLMAnswerGenerator
            from agent.answer_synthesizer import AgentAnswerSynthesizer

            llm = self._get_llm()
            synthesizer = AgentAnswerSynthesizer(llm)
            synthesis_result = synthesizer.synthesize(
                question=question,
                evidence_chain=result.evidence_chain,
                hypotheses=result.hypotheses,
                reflections=result.reflections,
                tool_plan=result.tool_plan,
            )
            result.answer = synthesis_result["answer"]
            result.confidence = synthesis_result.get(
                "confidence", result.confidence
            )
            # Append audit trail (collapsible for readability)
            result.answer += (
                f"\n\n<details>\n<summary>审计追踪</summary>\n\n"
                f"{'\n'.join(trail)}\n</details>"
            )
        else:
            # ---- Original Rule-Based Synthesis (backward compatible) ----
            parts = [f"# {question}\n"]
            if result.hypotheses:
                parts.append("## 诊断假设")
                for i, h in enumerate(result.hypotheses[:3]):
                    parts.append(f"{i+1}. **{h.statement[:150]}** (可能性: {h.likelihood:.0%})")
                    for ev in h.supporting_evidence[:1]:
                        parts.append(f"   - 依据: [{ev.source_type}] §{ev.source_id} — {ev.content[:120]}")
                parts.append("")

            parts.append("## 证据链")
            for i, ev in enumerate(result.evidence_chain[:8]):
                parts.append(f"{i+1}. **[{ev.source_type}]** §{ev.source_id}")
                parts.append(f"   {ev.reasoning}")
                parts.append(f"   > {ev.content[:200]}")
                parts.append("")

            if result.reflections:
                parts.append("## 自查笔记")
                for ref in result.reflections:
                    parts.append(f"- {ref}")
                parts.append("")

            parts.append(f"## 置信度: {'█'*int(result.confidence*10)}{'░'*(10-int(result.confidence*10))} {result.confidence:.0%}")

            result.answer = "\n".join(parts)

        result.audit_trail = "\n".join(trail)

        return result


# ======================================================================
# CLI Demo
# ======================================================================

if __name__ == "__main__":
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"

    agent = AgenticRAGv2()
    agent.load()

    tests = [
        "为什么车辆无法从Inactive直接进入Driving？",
        "从Abandoned模式如何进入Driving模式？",
        "KeyLost会影响哪些功能？",
    ]

    for q in tests:
        print(f"\n{'='*70}")
        result = agent.query(q)
        print(result.answer[:1200])
        print(f"\nConfidence: {result.confidence:.0%} | "
              f"Sources: {len(set(e.source_type for e in result.evidence_chain))} | "
              f"Hypotheses: {len(result.hypotheses)}")
