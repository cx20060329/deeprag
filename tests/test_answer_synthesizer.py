"""Tests for agent.answer_synthesizer — Agent LLM Answer Synthesis.

Tests the AgentAnswerSynthesizer class (Improvement #5).
All LLM calls are mocked to avoid external dependencies.
"""

from dataclasses import dataclass, field

import pytest


# Re-create minimal EvidenceLink and Hypothesis for testing
@dataclass
class TestEvidenceLink:
    source_type: str = "chunk"
    source_id: str = "2.2.1.1"
    content: str = "Test evidence content"
    reasoning: str = "Test reasoning"
    confidence: float = 0.85


@dataclass
class TestHypothesis:
    statement: str = "Test hypothesis"
    supporting_evidence: list = field(default_factory=list)
    contradicting_evidence: list = field(default_factory=list)
    likelihood: float = 0.7


class FakeLLMGenerator:
    """Mock LLMAnswerGenerator for testing synthesis."""

    def __init__(self, mock_response: str = ""):
        self.mock_response = mock_response
        self.model = "test-model"
        self._client = None

    def answer(self, evidence, query, intent=None, system_prompt=None):
        return {
            "answer": self.mock_response,
            "model": self.model,
            "usage": {
                "prompt_tokens": 300,
                "completion_tokens": 200,
                "total_tokens": 500,
            },
            "evidence_length": len(evidence),
        }


SYNTHETIC_ANSWER = """## 结论
IGN1信号的定义为点火继电器反馈信号。

## 详细分析
根据 [SM] §2.3.4.1.1 的状态机证据，IGN1是车身控制模块中的关键信号。
当IGN1继电器闭合时，该信号值为1(Closed)，当IGN1继电器断开时，该信号值为0(Open)。
该信号通过PEPS模块采集，用于判断车辆上电状态。

根据 [RULE] §rule_VMM_001 的规则证据，IGN1开路故障检测条件为IGN1=0持续超过500ms。

## 证据引用
- [1] [STATE_MACHINE] §2.3.4.1.1: IGN1状态定义 (置信度: 90%)
- [2] [RULE] §rule_VMM_001: IGN1开路检测规则 (置信度: 85%)
- [3] [CHUNK] §2.2.1.1: IGN1信号表定义 (置信度: 70%)

## 置信度评估
- 证据覆盖面: 3/3 来源 (状态机/规则/文档片段)
- 信息缺口: 缺少IGN1与其他模块的交互细节
- 综合置信度: 85%

CONFIDENCE=0.85
"""


class TestAgentAnswerSynthesizer:
    """Test the AgentAnswerSynthesizer class."""

    @pytest.fixture
    def llm(self):
        return FakeLLMGenerator(SYNTHETIC_ANSWER)

    @pytest.fixture
    def synthesizer(self, llm):
        from agent.answer_synthesizer import AgentAnswerSynthesizer
        return AgentAnswerSynthesizer(llm)

    @pytest.fixture
    def evidence_chain(self):
        return [
            TestEvidenceLink(
                source_type="state_machine",
                source_id="2.3.4.1.1",
                content="IGN1 is a relay feedback signal in VMM module.",
                reasoning="Defines IGN1 signal purpose",
                confidence=0.90,
            ),
            TestEvidenceLink(
                source_type="rule",
                source_id="rule_VMM_001",
                content="IGN1 open circuit detection: IGN1=0 for >500ms",
                reasoning="Shows IGN1 fault detection logic",
                confidence=0.85,
            ),
            TestEvidenceLink(
                source_type="chunk",
                source_id="2.2.1.1",
                content="IGN1 signal table: values 0=Open, 1=Closed",
                reasoning="Signal definition from document",
                confidence=0.70,
            ),
        ]

    @pytest.fixture
    def hypotheses(self):
        return [
            TestHypothesis(
                statement="IGN1 is a relay feedback signal",
                supporting_evidence=[
                    TestEvidenceLink(
                        source_type="state_machine",
                        source_id="2.3.4.1.1",
                        content="IGN1 state definition",
                        confidence=0.90,
                    ),
                ],
                likelihood=0.85,
            ),
        ]

    @pytest.fixture
    def reflections(self):
        return [
            "缺少IGN1与其他模块的CAN信号交互细节",
            "未找到IGN1的PIN脚定义",
        ]

    def test_synthesize_basic(self, synthesizer, evidence_chain, hypotheses, reflections):
        """Test basic LLM synthesis."""
        result = synthesizer.synthesize(
            question="IGN1信号的定义是什么？",
            evidence_chain=evidence_chain,
            hypotheses=hypotheses,
            reflections=reflections,
            tool_plan=[{"tool": "search_chunks", "why": "Find IGN1 definition"}],
        )
        assert "answer" in result
        assert "confidence" in result
        assert "citations" in result
        assert len(result["answer"]) > 0

    def test_synthesize_extracts_confidence(self, synthesizer):
        """Test confidence extraction from answer."""
        conf = synthesizer._extract_confidence(SYNTHETIC_ANSWER)
        assert conf == pytest.approx(0.85, abs=0.01)

    def test_synthesize_extracts_confidence_percentage(self, synthesizer):
        """Test confidence extraction from percentage format."""
        conf = synthesizer._extract_confidence("综合置信度: 75%")
        assert conf == pytest.approx(0.75, abs=0.01)

    def test_synthesize_extracts_confidence_heuristic(self, synthesizer):
        """Test heuristic confidence when no explicit marker."""
        answer = "根据 §2.3.4.1.1 和 §2.2.1.1 证据显示IGN1信号定义明确。"
        conf = synthesizer._extract_confidence(answer)
        assert 0.0 <= conf <= 1.0

    def test_build_synthesis_prompt(
        self, synthesizer, evidence_chain, hypotheses, reflections,
    ):
        """Test building the synthesis prompt."""
        prompt = synthesizer._build_synthesis_prompt(
            question="test question",
            evidence_chain=evidence_chain,
            hypotheses=hypotheses,
            reflections=reflections,
        )
        assert "test question" in prompt
        assert "证据链" in prompt
        assert "诊断假设" in prompt
        assert "自查笔记" in prompt

    def test_format_evidence_for_prompt(self, synthesizer, evidence_chain):
        """Test formatting evidence for prompt."""
        formatted = synthesizer._format_evidence_for_prompt(evidence_chain)
        assert "STATE_MACHINE" in formatted
        assert "RULE" in formatted
        assert "CHUNK" in formatted
        assert "2.3.4.1.1" in formatted

    def test_format_hypotheses_for_prompt(self, synthesizer, hypotheses):
        """Test formatting hypotheses for prompt."""
        formatted = synthesizer._format_hypotheses_for_prompt(hypotheses)
        assert "假设 1" in formatted
        assert "支持证据" in formatted
        assert "85%" in formatted

    def test_extract_citations(self, synthesizer, evidence_chain):
        """Test citation extraction from answer."""
        answer = "See [STATE_MACHINE] §2.3.4.1.1 and [RULE] §rule_VMM_001"
        citations = synthesizer._extract_citations(answer, evidence_chain)
        assert len(citations) > 0

    def test_fallback_synthesize(
        self, synthesizer, evidence_chain, hypotheses, reflections,
    ):
        """Test fallback synthesis when LLM is unavailable."""
        result = synthesizer._fallback_synthesize(
            question="test question",
            evidence_chain=evidence_chain,
            hypotheses=hypotheses,
            reflections=reflections,
        )
        assert "answer" in result
        assert result["model"] == "fallback"
        assert "证据链" in result["answer"]

    def test_llm_error_triggers_fallback(self, evidence_chain, hypotheses, reflections):
        """Test that LLM errors trigger fallback."""
        bad_llm = FakeLLMGenerator("[LLM Error] Connection timeout")
        from agent.answer_synthesizer import AgentAnswerSynthesizer
        synthesizer = AgentAnswerSynthesizer(bad_llm)
        result = synthesizer.synthesize(
            question="test",
            evidence_chain=evidence_chain,
            hypotheses=hypotheses,
            reflections=reflections,
            tool_plan=[],
        )
        assert result["model"] == "fallback"
