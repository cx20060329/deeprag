"""Tests for retrieval.llm_fusion — LLM Comparative Fusion.

Tests the LLMFusion class (Improvement #4).
All LLM calls are mocked to avoid external dependencies.
"""

import pytest


class FakeLLMGenerator:
    """Mock LLMAnswerGenerator for testing fusion."""

    def __init__(self, mock_response: str = ""):
        self.mock_response = mock_response
        self.model = "test-model"
        self._client = None

    def answer(self, evidence, query, intent=None, system_prompt=None):
        return {
            "answer": self.mock_response,
            "model": self.model,
            "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            "evidence_length": len(evidence),
        }


def make_candidate(chunk_id, module, section, text, score, chunk_type="general_text"):
    """Helper to create a candidate dict."""
    return {
        "chunk": {
            "chunk_id": chunk_id,
            "chunk_type": chunk_type,
            "module": module,
            "section_path": section,
            "text": text,
            "signals": [],
            "states": [],
        },
        "score": score,
        "sources": ["vector"],
    }


class TestLLMFusion:
    """Test the LLMFusion class."""

    @pytest.fixture
    def llm(self):
        return FakeLLMGenerator(
            '[{"id": "G_0", "rank": 1, "reason": "直接定义IGN1信号取值"},'
            '{"id": "V_0", "rank": 2, "reason": "补充信号的控制逻辑"},'
            '{"id": "V_1", "rank": 3, "reason": "包含相关故障检测规则"}]'
        )

    @pytest.fixture
    def fuser(self, llm):
        from retrieval.llm_fusion import LLMFusion
        return LLMFusion(llm)

    @pytest.fixture
    def graph_candidates(self):
        return [
            make_candidate("g1", "VMM", "2.2.1.1", "IGN1 signal definition: relay feedback", 0.9, "signal_table"),
        ]

    @pytest.fixture
    def vector_candidates(self):
        return [
            make_candidate("v1", "VMM", "2.2.1.2", "IGN1 control logic and activation conditions", 0.85),
            make_candidate("v2", "VMM", "2.2.3.1", "IGN1 fault detection rules", 0.80),
            make_candidate("v3", "Window", "3.1.1.1", "Window motor control", 0.70),
        ]

    def test_fuse_basic(self, fuser, graph_candidates, vector_candidates):
        """Test basic LLM fusion."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1"],
        }
        result = fuser.fuse(
            graph_candidates=graph_candidates,
            vector_candidates=vector_candidates,
            query="IGN1 signal?",
            intent=intent,
            top_k=5,
        )
        assert isinstance(result, list)
        assert len(result) > 0
        # Results should have fusion_reason
        for entry in result:
            assert "fusion_reason" in entry

    def test_fuse_empty_graph(self, fuser, vector_candidates):
        """Test fusion with empty graph candidates."""
        intent = {"question_type": "factual"}
        result = fuser.fuse(
            graph_candidates=[],
            vector_candidates=vector_candidates,
            query="test",
            intent=intent,
            top_k=3,
        )
        assert len(result) <= 3

    def test_fuse_empty_vector(self, fuser, graph_candidates):
        """Test fusion with empty vector candidates."""
        intent = {"question_type": "factual"}
        result = fuser.fuse(
            graph_candidates=graph_candidates,
            vector_candidates=[],
            query="test",
            intent=intent,
            top_k=3,
        )
        assert len(result) <= 1

    def test_fuse_empty_both(self, fuser):
        """Test fusion with both empty."""
        result = fuser.fuse([], [], "test", {"question_type": "factual"})
        assert result == []

    def test_build_candidate_summaries(self, fuser, vector_candidates):
        """Test building candidate summaries."""
        summaries = fuser._build_candidate_summaries(vector_candidates, "V")
        assert isinstance(summaries, list)
        assert len(summaries) == len(vector_candidates)
        for s in summaries:
            assert s["id"].startswith("V_")
            assert "summary" in s
            assert "module" in s

    def test_build_fusion_prompt(self, fuser, graph_candidates, vector_candidates):
        """Test building the fusion prompt."""
        graph_summaries = fuser._build_candidate_summaries(graph_candidates, "G")
        vector_summaries = fuser._build_candidate_summaries(vector_candidates, "V")
        prompt = fuser._build_fusion_prompt(
            graph_summaries=graph_summaries,
            vector_summaries=vector_summaries,
            query="test query",
            intent={"question_type": "factual", "modules": ["VMM"]},
        )
        assert isinstance(prompt, str)
        assert "test query" in prompt
        assert "VMM" in prompt

    def test_parse_fusion_response_valid(self, fuser):
        """Test parsing valid JSON fusion response."""
        response = '[{"id": "G_0", "rank": 1, "reason": "best match"}]'
        result = fuser._parse_fusion_response(response)
        assert len(result) == 1
        assert result[0]["id"] == "G_0"

    def test_parse_fusion_response_code_fence(self, fuser):
        """Test parsing JSON inside markdown code fence."""
        response = '```json\n[{"id": "G_0", "rank": 1, "reason": "best"}]\n```'
        result = fuser._parse_fusion_response(response)
        assert len(result) == 1

    def test_parse_fusion_response_invalid(self, fuser):
        """Test parsing invalid response."""
        result = fuser._parse_fusion_response("not json at all")
        assert result == []

    def test_parse_fusion_response_empty(self, fuser):
        """Test parsing empty response."""
        assert fuser._parse_fusion_response("") == []

    def test_fallback_fuse(self, fuser, graph_candidates, vector_candidates):
        """Test fallback fusion (interleaving)."""
        result = fuser._fallback_fuse(
            graph_candidates=graph_candidates,
            vector_candidates=vector_candidates,
            top_k=5,
        )
        assert len(result) > 0
        # Should interleave: G, V, V, V (1 graph + 3 vector)
        sources = [r.get("sources", []) for r in result]
        flat_sources = [s for sublist in sources for s in sublist]
        assert "llm_fusion_fallback" in flat_sources

    def test_llm_error_fallback(self, graph_candidates, vector_candidates):
        """Test that LLM errors trigger fallback fusion."""
        bad_llm = FakeLLMGenerator("")
        def raise_error(*args, **kwargs):
            raise RuntimeError("Connection error")
        bad_llm.answer = raise_error

        from retrieval.llm_fusion import LLMFusion
        fuser = LLMFusion(bad_llm)
        intent = {"question_type": "factual"}
        result = fuser.fuse(
            graph_candidates=graph_candidates,
            vector_candidates=vector_candidates,
            query="test",
            intent=intent,
            top_k=3,
        )
        assert len(result) > 0
        for entry in result:
            assert "llm_fusion_fallback" in entry.get("sources", [])

    def test_fusion_result_preserves_original_data(self, fuser, vector_candidates):
        """Test that fusion results preserve original chunk data."""
        intent = {"question_type": "factual"}
        # No graph candidates → returns vector candidates directly
        result = fuser.fuse(
            graph_candidates=[],
            vector_candidates=vector_candidates,
            query="test",
            intent=intent,
            top_k=3,
        )
        assert len(result) == min(3, len(vector_candidates))
        # Chunk data should be preserved
        for entry in result:
            assert "chunk" in entry
            assert "text" in entry["chunk"]
