"""Tests for retrieval.query_rewriter — HyDE Query Rewriting.

Tests the QueryRewriter class (Improvement #2).
All LLM calls are mocked to avoid external dependencies.
"""

import pytest


class FakeLLMGenerator:
    """Mock LLMAnswerGenerator for testing query rewriting."""

    def __init__(self, mock_response: str = ""):
        self.mock_response = mock_response
        self.model = "test-model"
        self._client = None

    def answer(self, evidence, query, intent=None, system_prompt=None):
        return {
            "answer": self.mock_response,
            "model": self.model,
            "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            "evidence_length": len(evidence),
        }


class TestQueryRewriter:
    """Test the QueryRewriter class."""

    @pytest.fixture
    def llm(self):
        return FakeLLMGenerator(
            "IGN1信号是车身控制模块中的点火继电器反馈信号。"
            "当IGN1继电器闭合时,该信号值为1(Closed),"
            "当IGN1继电器断开时,该信号值为0(Open)。"
            "该信号通过PEPS模块采集,用于判断车辆上电状态。"
        )

    @pytest.fixture
    def rewriter(self, llm):
        from retrieval.query_rewriter import QueryRewriter
        return QueryRewriter(llm)

    def test_rewrite_hyde_strategy(self, rewriter):
        """Test HyDE strategy generates hypothetical document."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1"],
            "states": [],
        }
        result = rewriter.rewrite(
            query="IGN1信号的定义是什么？",
            intent=intent,
            strategy="hyde",
        )
        assert result["original_query"] == "IGN1信号的定义是什么？"
        assert result["strategy"] == "hyde"
        assert result["hypothetical_doc"] is not None
        assert len(result["hypothetical_doc"]) > 0
        # Augmented query should be longer than original
        assert len(result["augmented_query"]) > len(result["original_query"])
        # Original query should be in augmented query
        assert "IGN1信号的定义是什么" in result["augmented_query"]

    def test_rewrite_query2doc_strategy(self, rewriter):
        """Test query2doc strategy."""
        intent = {"question_type": "factual"}
        result = rewriter.rewrite(
            query="GlobalClose的触发条件？",
            intent=intent,
            strategy="query2doc",
        )
        assert result["strategy"] == "query2doc"
        assert result["hypothetical_doc"] is not None

    def test_rewrite_keywords_strategy(self, rewriter):
        """Test keyword-only expansion (no LLM)."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1", "PEPS_UsageMode"],
            "states": ["Driving"],
            "functions": ["GlobalClose"],
        }
        result = rewriter.rewrite(
            query="test query",
            intent=intent,
            strategy="keywords",
        )
        assert result["strategy"] == "keywords"
        assert result["hypothetical_doc"] is None
        assert "VMM" in result["augmented_query"]
        assert "IGN1" in result["augmented_query"]

    def test_build_augmented_query_hyde(self, rewriter):
        """Test augmented query building for HyDE."""
        augmented = rewriter.build_augmented_query(
            original_query="original question",
            hypothetical_doc="hypothetical document text about the topic",
            strategy="hyde",
        )
        assert "original question" in augmented
        assert "hypothetical document" in augmented

    def test_build_augmented_query_empty_hypothetical(self, rewriter):
        """Test augmented query building with empty hypothetical doc."""
        augmented = rewriter.build_augmented_query(
            original_query="original",
            hypothetical_doc="",
            strategy="hyde",
        )
        assert augmented == "original"

    def test_expand_keywords(self, rewriter):
        """Test keyword expansion from intent."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM", "Window"],
            "signals": ["IGN1", "PEPS_UsageMode", "VehicleSpeed"],
            "states": ["Driving", "Inactive"],
            "functions": ["GlobalClose", "AutoLock"],
        }
        result = rewriter._expand_keywords("base query", intent)
        assert "base query" in result
        assert "VMM" in result
        assert "IGN1" in result

    def test_llm_error_fallback(self):
        """Test that LLM errors trigger keyword fallback."""
        # Create an LLM that always raises
        bad_llm = FakeLLMGenerator("")
        # Make answer raise an exception
        def raise_error(*args, **kwargs):
            raise RuntimeError("Connection error")
        bad_llm.answer = raise_error

        from retrieval.query_rewriter import QueryRewriter
        rewriter = QueryRewriter(bad_llm)
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1"],
        }
        result = rewriter.rewrite(
            query="test query",
            intent=intent,
            strategy="hyde",
        )
        # Should fall back to keywords
        assert result["strategy"] == "keywords"
        assert result["augmented_query"] != "test query"
