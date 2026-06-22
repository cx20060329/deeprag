"""Tests for retrieval.context_compressor — LLM Context Compression.

Tests the ContextCompressor class (Improvement #1).
All LLM calls are mocked to avoid external dependencies.
"""

from unittest.mock import MagicMock, patch

import pytest


class FakeLLMGenerator:
    """Mock LLMAnswerGenerator for testing."""

    def __init__(self, mock_response: str = "compressed summary"):
        self.mock_response = mock_response
        self.model = "test-model"
        self._client = None

    def answer(self, evidence, query, intent=None, system_prompt=None):
        return {
            "answer": self.mock_response,
            "model": self.model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "evidence_length": len(evidence),
        }


# Avoid importing ContextCompressor at module level (it depends on retrieval.llm_answer)
# We'll import inside each test after ensuring the path is set up


class TestContextCompressor:
    """Test the ContextCompressor class."""

    @pytest.fixture
    def llm(self):
        return FakeLLMGenerator("compressed result with dependency chains")

    @pytest.fixture
    def compressor(self, llm):
        from retrieval.context_compressor import ContextCompressor
        return ContextCompressor(llm)

    @pytest.fixture
    def sample_candidates(self):
        return [
            {
                "chunk": {
                    "chunk_id": "c1",
                    "chunk_type": "signal_table",
                    "module": "VMM",
                    "section_path": "2.2.1.1",
                    "text": "IGN1 signal: relay feedback. Values: 0=Open, 1=Closed.",
                    "signals": ["IGN1"],
                    "states": [],
                },
                "score": 0.95,
                "sources": ["vector"],
            },
            {
                "chunk": {
                    "chunk_id": "c2",
                    "chunk_type": "state_machine",
                    "module": "VMM",
                    "section_path": "2.3.4.2.2",
                    "text": "Inactive→Convenience: DoorOpen=TRUE AND KeyValid=TRUE.",
                    "signals": [],
                    "states": ["Inactive", "Convenience"],
                },
                "score": 0.88,
                "sources": ["vector", "graph"],
            },
        ]

    def test_compress_basic(self, compressor, sample_candidates):
        """Test basic LLM compression."""
        intent = {"question_type": "factual"}
        result = compressor.compress(
            candidates=sample_candidates,
            query="IGN1 signal?",
            intent=intent,
        )
        assert isinstance(result, str)
        assert "compressed" in result.lower()

    def test_compress_empty_candidates(self, compressor):
        """Test compression with empty candidates."""
        intent = {"question_type": "factual"}
        result = compressor.compress(
            candidates=[],
            query="test",
            intent=intent,
        )
        assert "未找到" in result

    def test_compress_with_graph_results(self, compressor, sample_candidates):
        """Test compression with graph context included."""
        graph_results = [
            {
                "entity": {
                    "name": "IGN1",
                    "entity_type": "signal",
                    "module": "VMM",
                },
                "relationship": "BELONGS_TO",
            }
        ]
        intent = {"question_type": "factual"}
        result = compressor.compress(
            candidates=sample_candidates,
            query="IGN1 signal?",
            intent=intent,
            graph_results=graph_results,
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compress_structured(self, compressor):
        """Test compression of already-structured evidence."""
        structured = """## 查询: test
## 依赖链
1. A → B (controls)
## 状态转移
1. X → Y: guard condition
## 文档片段
test content
"""
        intent = {"question_type": "factual"}
        result = compressor.compress_structured(
            structured_evidence=structured,
            query="test",
            intent=intent,
        )
        assert isinstance(result, str)

    def test_fallback_compress(self, compressor, sample_candidates):
        """Test fallback compression when LLM is unavailable."""
        intent = {"question_type": "factual"}
        result = compressor._fallback_compress(
            candidates=sample_candidates,
            query="test query",
            intent=intent,
        )
        assert isinstance(result, str)
        assert "test query" in result
        assert "## 证据片段" in result

    def test_build_input_text(self, compressor, sample_candidates):
        """Test building input text from candidates."""
        text = compressor._build_input_text(sample_candidates)
        assert isinstance(text, str)
        assert "IGN1" in text

    def test_extract_graph_context(self, compressor):
        """Test extracting graph context from graph results."""
        graph_results = [
            {
                "entity": {
                    "name": "IGN1",
                    "entity_type": "signal",
                    "module": "VMM",
                },
                "relationship": "controls",
            },
            {
                "entity": {
                    "name": "IGN1Relay",
                    "entity_type": "signal",
                    "module": "VMM",
                },
                "relationship": "belongs_to",
            },
        ]
        context = compressor._extract_graph_context(graph_results)
        assert isinstance(context, str)
        assert "IGN1" in context

    def test_extract_graph_context_empty(self, compressor):
        """Test extracting graph context from empty results."""
        assert compressor._extract_graph_context([]) == ""

    def test_build_compression_prompt(self, compressor):
        """Test building the compression prompt."""
        prompt = compressor._build_compression_prompt(
            chunks_text="chunk1 text\nchunk2 text",
            query="test query",
            graph_context="IGN1 --[controls]--> IGN1Relay",
        )
        assert isinstance(prompt, str)
        assert "test query" in prompt
        assert "IGN1Relay" in prompt
        assert "依赖链" in prompt

    def test_llm_error_fallback(self, sample_candidates):
        """Test that LLM errors trigger fallback compression."""
        bad_llm = FakeLLMGenerator("[LLM Error] Connection timeout")
        from retrieval.context_compressor import ContextCompressor
        compressor = ContextCompressor(bad_llm)
        intent = {"question_type": "factual"}
        result = compressor.compress(
            candidates=sample_candidates,
            query="test",
            intent=intent,
        )
        # Should fall back to simple compression
        assert isinstance(result, str)
        assert "test" in result
