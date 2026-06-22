"""Regression tests for the Retrieval Pipeline.

Covers edge cases, bug regressions, and load/search integrity.
"""

import json
import pytest
from pathlib import Path
from retrieval import RetrievalPipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipeline():
    """Load pipeline once for all tests in this module."""
    p = RetrievalPipeline()
    p.load(use_dense=True)
    return p


# ---------------------------------------------------------------------------
# Bug Regression Tests
# ---------------------------------------------------------------------------

class TestBugRegressions:
    """Verify bugs fixed in the previous session don't regress."""

    def test_empty_query_returns_empty(self, pipeline):
        """Bug #2 fix: empty query should return empty results, not 5 random chunks."""
        result = pipeline.search("", top_k=5)
        assert len(result["merged"]) == 0, f"Expected 0 results for empty query, got {len(result['merged'])}"
        assert "查询为空" in result["evidence"]

    def test_whitespace_query_returns_empty(self, pipeline):
        """Bug #2 variant: whitespace-only query should also return empty."""
        result = pipeline.search("   \t  ", top_k=5)
        assert len(result["merged"]) == 0

    def test_ign1_keyword_matches_vmm(self, pipeline):
        """Bug #1 fix: IGN1 query should find VMM module via keyword extraction."""
        result = pipeline.search("IGN1继电器控制逻辑", top_k=3)
        # Verify IGN1 was extracted as a keyword
        keywords = result["intent"].get("keywords", [])
        has_ign1 = any("IGN1" in kw or "ign1" in kw.lower() for kw in keywords)
        assert has_ign1, f"IGN1 not found in keywords: {keywords}"
        # Verify graph results were found
        assert len(result["graph_results"]) > 0, "Expected graph results for IGN1"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary condition tests for the pipeline."""

    def test_pure_english_query(self, pipeline):
        """English queries should return results."""
        result = pipeline.search("How does window anti-pinch work?", top_k=3)
        assert len(result["merged"]) > 0, "English query should return results"

    def test_pure_chinese_query(self, pipeline):
        """Chinese queries are the primary use case."""
        result = pipeline.search("车窗", top_k=3)
        assert len(result["merged"]) > 0
        # Should find Window module
        modules = [r["chunk"].get("module") for r in result["merged"]]
        assert any(m == "Window" for m in modules), f"Expected Window in results: {modules}"

    def test_mixed_language_query(self, pipeline):
        """Mixed Chinese-English queries (common in automotive domain)."""
        result = pipeline.search("VMM的PEPS_UsageMode信号", top_k=3)
        assert len(result["merged"]) > 0

    def test_special_characters_query(self, pipeline):
        """Queries with special chars should not crash."""
        result = pipeline.search("VMM && Window || Lock", top_k=3)
        assert len(result["merged"]) >= 0  # May or may not have results

    def test_very_long_query(self, pipeline):
        """Very long queries should not cause memory issues."""
        long_q = "VMM " * 200
        result = pipeline.search(long_q, top_k=3)
        assert len(result["merged"]) >= 0

    def test_single_character_query(self, pipeline):
        """Single character query should not crash."""
        result = pipeline.search("V", top_k=3)
        assert len(result["merged"]) >= 0

    def test_numeric_query(self, pipeline):
        """Numeric queries (CAN IDs, hex values)."""
        result = pipeline.search("0x272", top_k=3)
        assert len(result["merged"]) >= 0

    def test_query_with_unicode_special(self, pipeline):
        """Queries with unicode special characters."""
        result = pipeline.search("电压＜9V", top_k=3)
        assert len(result["merged"]) >= 0


# ---------------------------------------------------------------------------
# Pipeline Integrity
# ---------------------------------------------------------------------------

class TestPipelineIntegrity:
    """Verify pipeline output structure and data consistency."""

    def test_search_returns_all_required_keys(self, pipeline):
        """Every search result must have the expected structure."""
        result = pipeline.search("VMM", top_k=3)
        required = ["query", "intent", "graph_results", "tree_sections",
                     "vector_results", "merged", "evidence", "answer", "usage"]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_merged_items_have_required_fields(self, pipeline):
        """Each merged result must have chunk, score, sources."""
        result = pipeline.search("VMM", top_k=3)
        assert len(result["merged"]) > 0
        for item in result["merged"]:
            assert "chunk" in item, "Missing 'chunk' in merged item"
            assert "score" in item, "Missing 'score' in merged item"
            assert "sources" in item, "Missing 'sources' in merged item"

    def test_chunk_has_text(self, pipeline):
        """Chunks must have text (not empty)."""
        result = pipeline.search("VMM电源管理", top_k=3)
        for item in result["merged"]:
            text = item["chunk"].get("text", "")
            assert len(text) > 0, f"Empty text in chunk: {item['chunk'].get('chunk_id')}"

    def test_evidence_not_empty(self, pipeline):
        """Evidence compression must produce output."""
        result = pipeline.search("车窗防夹", top_k=5)
        assert len(result["evidence"]) > 200, f"Evidence too short: {len(result['evidence'])} chars"

    def test_intent_has_query_type(self, pipeline):
        """Intent analysis must classify query type."""
        result = pipeline.search("如何进入Driving？", top_k=3)
        assert result["intent"]["question_type"] == "reasoning"

        result = pipeline.search("DTC故障码怎么读？", top_k=3)
        assert result["intent"]["question_type"] == "diagnostic"

        result = pipeline.search("车窗", top_k=3)
        assert result["intent"]["question_type"] == "factual"

    def test_top_k_respected(self, pipeline):
        """top_k parameter must be respected."""
        for k in [1, 3, 10]:
            result = pipeline.search("VMM", top_k=k)
            assert len(result["merged"]) <= k, f"Got {len(result['merged'])} results for top_k={k}"


# ---------------------------------------------------------------------------
# Retriever Component Tests
# ---------------------------------------------------------------------------

class TestKeywordRetriever:
    """BM25 keyword retriever tests."""

    def test_cjk_tokenization(self, pipeline):
        """CJK tokenizer should split Chinese text correctly."""
        from retrieval.vector_retriever import KeywordRetriever
        tokens = KeywordRetriever._tokenize("车窗防夹功能")
        # Should contain individual chars + bigrams + identifiable words
        assert len(tokens) > 0
        # Should contain Chinese characters
        assert any("窗" in t for t in tokens)

    def test_empty_text_tokenization(self, pipeline):
        """Empty text should tokenize to empty list."""
        from retrieval.vector_retriever import KeywordRetriever
        tokens = KeywordRetriever._tokenize("")
        assert tokens == []


class TestGraphRetriever:
    """Graph retriever tests."""

    def test_entity_search_substring(self, pipeline):
        """Entity search should find entities by substring."""
        matches = pipeline.graph.search_entities("IGN1")
        assert len(matches) > 0, "Should find IGN1 entities"

    def test_entity_search_empty(self, pipeline):
        """Empty search should return no entities (not crash)."""
        matches = pipeline.graph.search_entities("")
        assert len(matches) >= 0  # May return all or none

    def test_get_by_name(self, pipeline):
        """get_by_name should find exact matches."""
        entities = pipeline.graph.get_by_name("VMM", "module")
        assert len(entities) > 0
        assert entities[0]["name"] == "VMM"

    def test_expand_one_hop(self, pipeline):
        """1-hop expansion should find neighbors."""
        # Find a VMM module entity
        entities = pipeline.graph.get_by_name("VMM", "module")
        assert len(entities) > 0
        eid = entities[0]["entity_id"]
        neighbors = pipeline.graph.expand(eid, hops=1)
        assert len(neighbors) > 0, "VMM should have neighbor entities"


class TestDenseRetriever:
    """Dense retriever tests."""

    def test_dense_retriever_loaded(self, pipeline):
        """Dense retriever should be loaded when use_dense=True."""
        assert pipeline.dense is not None, "Dense retriever should be loaded"
        assert pipeline.dense.is_loaded, "Dense retriever should be loaded"

    def test_dense_search_returns_results(self, pipeline):
        """Dense search should return results."""
        results = pipeline.dense.search("车窗", top_k=5)
        assert len(results) > 0

    def test_dense_search_score_range(self, pipeline):
        """Dense scores should be in valid range."""
        results = pipeline.dense.search("VMM", top_k=10)
        for r in results:
            assert -1.0 <= r["score"] <= 1.0, f"Score out of range: {r['score']}"
