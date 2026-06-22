"""BCM-RAG Agent — LLM Answer Synthesizer for Agentic RAG v2.

Improvement #5: Agent v2 + LLM Synthesis

The AgenticRAGv2 builds rich evidence chains (EvidenceLink) and hypotheses
(Hypothesis), but its Step 7 answer synthesis is pure rule-based string
concatenation. This module adds LLM-driven synthesis that:

  1. Consumes EvidenceLink list with full traceability
  2. Consumes Hypothesis list with supporting/contradicting evidence
  3. Generates structured answers with:
     - Conclusion (1-2 sentences)
     - Detailed analysis with evidence citations
     - Confidence assessment with gap analysis
     - Full audit trail (collapsible)

Key difference from pipeline LLMAnswerGenerator:
  - LLMAnswerGenerator: receives evidence text → simple QA
  - AgentAnswerSynthesizer: receives EvidenceLink + Hypothesis →
    generates answer with full evidence citations and confidence evaluation

Usage:
    from retrieval.llm_answer import LLMAnswerGenerator
    from agent.answer_synthesizer import AgentAnswerSynthesizer

    llm = LLMAnswerGenerator(provider="zhipu")
    synthesizer = AgentAnswerSynthesizer(llm)

    result = synthesizer.synthesize(
        question="为什么车辆无法从Inactive进入Driving？",
        evidence_chain=evidence_chain,   # list[EvidenceLink]
        hypotheses=hypotheses,           # list[Hypothesis]
        reflections=reflections,         # list[str]
        tool_plan=tool_plan,             # list[dict]
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retrieval.llm_answer import LLMAnswerGenerator


class AgentAnswerSynthesizer:
    """LLM-driven answer synthesizer for Agentic RAG v2.

    Consumes the agent's evidence chain, hypotheses, and reflection
    notes to produce a comprehensive, cited, structured answer.
    """

    # Maximum evidence content per link in the prompt
    MAX_EVIDENCE_CONTENT_LEN = 300
    # Maximum total evidence chain links in the prompt
    MAX_EVIDENCE_LINKS = 12

    def __init__(self, llm_generator: "LLMAnswerGenerator"):
        """Initialize with an existing LLMAnswerGenerator instance.

        Args:
            llm_generator: Reuses the pipeline's LLM client.
        """
        self.llm = llm_generator

    # --- Public API ----------------------------------------------------------

    def synthesize(
        self,
        question: str,
        evidence_chain: list,
        hypotheses: list,
        reflections: list[str],
        tool_plan: list[dict],
    ) -> dict:
        """Synthesize a final answer from agent's evidence and hypotheses.

        Args:
            question: User's original question
            evidence_chain: list of EvidenceLink objects
            hypotheses: list of Hypothesis objects
            reflections: Self-reflection notes
            tool_plan: Tool planning records

        Returns:
            {
                "answer": str,          # LLM-generated structured answer
                "confidence": float,    # LLM-assessed confidence (0-1)
                "citations": list[str], # Referenced source IDs
                "model": str,
                "usage": dict,
            }
        """
        # Build the synthesis prompt
        system_prompt = self._build_synthesis_system_prompt()
        user_prompt = self._build_synthesis_prompt(
            question=question,
            evidence_chain=evidence_chain,
            hypotheses=hypotheses,
            reflections=reflections,
        )

        try:
            result = self.llm.answer(
                evidence=user_prompt,
                query=question,
                system_prompt=system_prompt,
            )
            answer_text = result.get("answer", "")

            if answer_text.startswith("[LLM Error]"):
                return self._fallback_synthesize(
                    question, evidence_chain, hypotheses, reflections
                )

            # Extract citations from the answer
            citations = self._extract_citations(
                answer_text, evidence_chain
            )

            # Extract LLM-assessed confidence
            confidence = self._extract_confidence(answer_text)

            return {
                "answer": answer_text,
                "confidence": confidence,
                "citations": citations,
                "model": result.get("model", ""),
                "usage": result.get("usage", {}),
            }

        except Exception:
            return self._fallback_synthesize(
                question, evidence_chain, hypotheses, reflections
            )

    # --- Prompt builders -----------------------------------------------------

    def _build_synthesis_system_prompt(self) -> str:
        """Build the system prompt for agent answer synthesis."""
        return """你是汽车BCM（车身控制模块）工程专家Agent。

你的任务是根据证据链和假设检验结果，生成结构化的工程回答。

规则：
1. 每个结论必须引用具体的证据（格式：[来源类型] §章节号）
2. 对于状态转换问题，描述完整的状态链和触发条件
3. 对于信号问题，说明信号来源、用途和相关模块
4. 对于故障诊断问题，列出检测条件、故障反应和恢复方式
5. 如果证据不足，明确说明信息缺口
6. 使用中文回答，技术术语保留英文原名
7. 在回答末尾评估置信度（基于证据覆盖面和信息缺口）
8. 使用结构化格式，必要时使用列表或表格"""

    def _build_synthesis_prompt(
        self,
        question: str,
        evidence_chain: list,
        hypotheses: list,
        reflections: list[str],
    ) -> str:
        """Build the full synthesis prompt with all agent context."""
        parts = [
            "# 用户问题",
            question,
            "",
        ]

        # Evidence chain section
        if evidence_chain:
            parts.append(
                f"# 证据链 ({len(evidence_chain)} 条证据)"
            )
            parts.append("")
            parts.append(
                self._format_evidence_for_prompt(evidence_chain)
            )
            parts.append("")

        # Hypotheses section
        if hypotheses:
            parts.append(
                f"# 诊断假设 ({len(hypotheses)} 个)"
            )
            parts.append("")
            parts.append(
                self._format_hypotheses_for_prompt(hypotheses)
            )
            parts.append("")

        # Self-reflection section
        if reflections:
            parts.append("# 自查笔记")
            parts.append("以下是检索过程中的自我反思，标注了已知的信息缺口：")
            parts.append("")
            for ref in reflections:
                parts.append(f"- {ref}")
            parts.append("")

        # Answer requirements
        parts.append("# 请回答")
        parts.append("基于上述证据链和假设检验结果，生成结构化的工程回答：")
        parts.append("")
        parts.append("## 结论")
        parts.append("[1-2句话直接回答用户问题]")
        parts.append("")
        parts.append("## 详细分析")
        parts.append("[基于证据链展开分析，每段引用证据编号，如 [SM] §2.3.4.1.1]")
        parts.append("")
        parts.append("## 证据引用")
        parts.append("[列出所有引用的证据，包括来源类型、章节号和置信度]")
        parts.append("")
        parts.append("## 置信度评估")
        parts.append("- 证据覆盖面: [评估各来源覆盖情况]")
        parts.append("- 信息缺口: [列出已知缺失的信息]")
        parts.append("- 综合置信度: [百分比，如 85%]")
        parts.append("")
        parts.append("## 置信度数值")
        parts.append("[在最后一行单独输出: CONFIDENCE=X.XX]")

        return "\n".join(parts)

    # --- Formatters ----------------------------------------------------------

    def _format_evidence_for_prompt(self, evidence_chain: list) -> str:
        """Format EvidenceLink list for LLM consumption.

        Groups evidence by source_type for clarity:
          - state_machine: State transition evidence
          - rule: Rule match evidence
          - chunk: Document fragment evidence
          - graph: Graph relationship evidence
          - path: Path analysis evidence
        """
        # Group by source type
        grouped: dict[str, list] = {}
        for ev in evidence_chain:
            st = getattr(ev, "source_type", "unknown")
            if st not in grouped:
                grouped[st] = []
            if len(grouped[st]) < self.MAX_EVIDENCE_LINKS // len(
                grouped
            ):
                grouped[st].append(ev)

        source_labels = {
            "state_machine": "状态机证据",
            "rule": "规则证据",
            "chunk": "文档片段证据",
            "graph": "图谱关系证据",
            "path": "路径分析证据",
            "impact": "影响分析证据",
            "conflict": "冲突检测证据",
            "reachability": "可达性分析证据",
        }

        lines: list[str] = []
        global_idx = 0

        for source_type, ev_list in grouped.items():
            label = source_labels.get(source_type, source_type)
            lines.append(f"### {label}")
            lines.append("")

            for ev in ev_list:
                global_idx += 1
                source_id = getattr(ev, "source_id", "?")
                content = getattr(ev, "content", "")[
                    : self.MAX_EVIDENCE_CONTENT_LEN
                ]
                reasoning = getattr(ev, "reasoning", "")
                confidence = getattr(ev, "confidence", 0.0)

                lines.append(
                    f"**[{global_idx}] [{source_type.upper()}] "
                    f"§{source_id}** (置信度: {confidence:.0%})"
                )
                if reasoning:
                    lines.append(f"  推理: {reasoning}")
                if content:
                    lines.append(f"  内容: {content}")
                lines.append("")

        return "\n".join(lines)

    def _format_hypotheses_for_prompt(self, hypotheses: list) -> str:
        """Format Hypothesis list for LLM consumption.

        Each hypothesis includes:
          - Statement
          - Supporting evidence (up to 2 links)
          - Contradicting evidence (up to 1 link)
          - Likelihood score
        """
        lines: list[str] = []

        for i, h in enumerate(hypotheses, 1):
            statement = getattr(h, "statement", "")[:200]
            likelihood = getattr(h, "likelihood", 0.0)

            lines.append(f"### 假设 {i}: {statement}")
            lines.append(f"可能性: {likelihood:.0%}")
            lines.append("")

            # Supporting evidence
            supporting = getattr(h, "supporting_evidence", [])
            if supporting:
                lines.append("**支持证据:**")
                for ev in supporting[:2]:
                    source_type = getattr(ev, "source_type", "?")
                    source_id = getattr(ev, "source_id", "?")
                    content = getattr(ev, "content", "")[:150]
                    lines.append(
                        f"  - [{source_type}] §{source_id}: {content}"
                    )
                lines.append("")

            # Contradicting evidence
            contradicting = getattr(h, "contradicting_evidence", [])
            if contradicting:
                lines.append("**反对证据:**")
                for ev in contradicting[:1]:
                    source_type = getattr(ev, "source_type", "?")
                    source_id = getattr(ev, "source_id", "?")
                    content = getattr(ev, "content", "")[:150]
                    lines.append(
                        f"  - [{source_type}] §{source_id}: {content}"
                    )
                lines.append("")

        return "\n".join(lines)

    # --- Post-processing -----------------------------------------------------

    def _extract_citations(
        self, answer: str, evidence_chain: list,
    ) -> list[str]:
        """Extract cited source IDs from the answer text.

        Looks for patterns like:
          - [SM] §2.3.4.1.1
          - [RULE] rule_VMM_001
          - [CHUNK] §2.2.1.2
        """
        citations: list[str] = []
        seen: set[str] = set()

        for ev in evidence_chain:
            source_type = getattr(ev, "source_type", "")
            source_id = getattr(ev, "source_id", "")

            # Check if source_id appears in answer
            if source_id and source_id in answer:
                citation = f"[{source_type.upper()}] §{source_id}"
                if citation not in seen:
                    seen.add(citation)
                    citations.append(citation)

        return citations

    def _extract_confidence(self, answer: str) -> float:
        """Extract LLM-assessed confidence from the answer.

        Looks for "CONFIDENCE=X.XX" pattern at the end of the answer.
        Falls back to heuristic estimation if not found.
        """
        import re

        # Try explicit confidence marker
        match = re.search(
            r"CONFIDENCE\s*=\s*([0-9.]+)", answer, re.IGNORECASE
        )
        if match:
            try:
                val = float(match.group(1))
                # Normalize: if > 1, assume it's a percentage
                if val > 1:
                    val = val / 100.0
                return max(0.0, min(1.0, val))
            except ValueError:
                pass

        # Try percentage pattern
        match = re.search(r"(\d+)%", answer)
        if match:
            try:
                return float(match.group(1)) / 100.0
            except ValueError:
                pass

        # Heuristic: count citations and evidence mentions
        citation_count = len(re.findall(r"§\d", answer))
        gap_indicators = len(
            re.findall(
                r"无法确定|证据不足|信息缺失|缺少|未找到",
                answer,
            )
        )

        base = min(citation_count * 0.1, 0.7)
        penalty = gap_indicators * 0.1

        return max(0.1, min(0.95, base - penalty))

    # --- Fallback ------------------------------------------------------------

    def _fallback_synthesize(
        self,
        question: str,
        evidence_chain: list,
        hypotheses: list,
        reflections: list[str],
    ) -> dict:
        """Fallback synthesis when LLM is unavailable.

        Produces a structured answer using pure rule-based formatting,
        matching the original AgenticRAGv2 Step 7 behavior.
        """
        parts = [f"# {question}\n"]

        # Hypotheses section
        if hypotheses:
            parts.append("## 诊断假设")
            for i, h in enumerate(hypotheses[:3]):
                statement = getattr(h, "statement", "")[:150]
                likelihood = getattr(h, "likelihood", 0.0)
                parts.append(
                    f"{i+1}. **{statement}** (可能性: {likelihood:.0%})"
                )
                supporting = getattr(h, "supporting_evidence", [])
                for ev in supporting[:1]:
                    st = getattr(ev, "source_type", "?")
                    sid = getattr(ev, "source_id", "?")
                    content = getattr(ev, "content", "")[:120]
                    parts.append(
                        f"   - 依据: [{st}] §{sid} — {content}"
                    )
            parts.append("")

        # Evidence chain section
        parts.append("## 证据链")
        for i, ev in enumerate(evidence_chain[:8]):
            st = getattr(ev, "source_type", "?")
            sid = getattr(ev, "source_id", "?")
            reasoning = getattr(ev, "reasoning", "")
            content = getattr(ev, "content", "")[:200]
            parts.append(f"{i+1}. **[{st}]** §{sid}")
            parts.append(f"   {reasoning}")
            parts.append(f"   > {content}")
            parts.append("")

        # Reflection notes
        if reflections:
            parts.append("## 自查笔记")
            for ref in reflections:
                parts.append(f"- {ref}")
            parts.append("")

        # Confidence
        n_links = len(evidence_chain)
        high_conf = sum(
            1
            for e in evidence_chain
            if getattr(e, "confidence", 0) > 0.7
        )
        conf = min(n_links * 0.08 + high_conf * 0.05, 0.95)
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        parts.append(
            f"## 置信度: {bar} {conf:.0%} (回退模式: LLM不可用)"
        )

        return {
            "answer": "\n".join(parts),
            "confidence": conf,
            "citations": [],
            "model": "fallback",
            "usage": {},
        }
