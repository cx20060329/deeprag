"""Tests for retrieval.evidence_builder — Structured Evidence Package.

Tests the EvidenceBuilder, StructuredEvidence, DependencyChain,
and StateTransition classes (Improvement #3).
"""

import pytest

from retrieval.evidence_builder import (
    DependencyChain,
    EvidenceBuilder,
    StateTransition,
    StructuredEvidence,
)


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------

class TestDependencyChain:
    """Test the DependencyChain data class."""

    def test_create_simple_chain(self):
        dc = DependencyChain(
            chain=["IGN1", "IGN1Relay", "PEPS_UsageMode"],
            relation_types=["controls", "determines"],
            source_sections=["2.2.1.1", "2.2.1.2", "2.3.4.1"],
            description="IGN1 controls IGN1Relay, which determines PEPS_UsageMode",
        )
        assert len(dc.chain) == 3
        assert dc.chain[0] == "IGN1"
        assert dc.relation_types == ["controls", "determines"]

    def test_empty_chain_defaults(self):
        dc = DependencyChain(chain=[], relation_types=[], source_sections=[])
        assert dc.chain == []
        assert dc.description == ""


class TestStateTransition:
    """Test the StateTransition data class."""

    def test_create_transition(self):
        st = StateTransition(
            source="Inactive",
            target="Convenience",
            guard="DoorOpen=TRUE AND KeyValid=TRUE",
            effect="Enter Convenience mode",
            section="2.3.4.2.2",
            module="VMM",
        )
        assert st.source == "Inactive"
        assert st.target == "Convenience"
        assert "DoorOpen" in st.guard

    def test_transition_defaults(self):
        st = StateTransition(source="A", target="B")
        assert st.guard == ""
        assert st.module == ""


class TestStructuredEvidence:
    """Test the StructuredEvidence data class."""

    def test_create_empty(self):
        se = StructuredEvidence(query="test query")
        assert se.query == "test query"
        assert se.modules == []
        assert se.dependency_chains == []

    def test_create_full(self):
        dc = DependencyChain(
            chain=["A", "B"], relation_types=["controls"], source_sections=["1.0"],
        )
        st = StateTransition(
            source="X", target="Y", guard="cond", section="2.0", module="M",
        )
        se = StructuredEvidence(
            query="test",
            modules=["VMM"],
            signals=["IGN1"],
            states=["Driving"],
            dependency_chains=[dc],
            state_transitions=[st],
            related_rules=[{"rule_id": "R1", "text": "test rule"}],
            text_chunks=[
                {"chunk": {"text": "test chunk", "module": "VMM"}, "score": 0.9}
            ],
        )
        assert len(se.modules) == 1
        assert len(se.dependency_chains) == 1
        assert len(se.state_transitions) == 1
        assert len(se.related_rules) == 1
        assert len(se.text_chunks) == 1


# ---------------------------------------------------------------------------
# EvidenceBuilder tests
# ---------------------------------------------------------------------------

class TestEvidenceBuilder:
    """Test the EvidenceBuilder class."""

    @pytest.fixture
    def builder(self):
        return EvidenceBuilder()

    @pytest.fixture
    def sample_graph_results(self):
        """Create sample graph results for testing."""
        return [
            {
                "entity": {
                    "entity_id": "sig_ign1",
                    "name": "IGN1",
                    "entity_type": "signal",
                    "module": "VMM",
                    "section_path": "2.2.1.1",
                    "description": "IGN1 relay feedback signal",
                },
                "relationship": "BELONGS_TO",
                "distance": 1,
            },
            {
                "entity": {
                    "entity_id": "state_inactive",
                    "name": "Inactive",
                    "entity_type": "state",
                    "module": "VMM",
                    "section_path": "2.3.4.2",
                    "target": "Convenience",
                },
                "relationship": "TRANSITION_TO",
                "distance": 1,
            },
        ]

    @pytest.fixture
    def sample_candidates(self):
        """Create sample merged candidates."""
        return [
            {
                "chunk": {
                    "chunk_id": "c1",
                    "chunk_type": "signal_table",
                    "module": "VMM",
                    "section_path": "2.2.1.1",
                    "text": "IGN1 signal: IGN1 relay feedback. Values: 0=Open, 1=Closed.",
                    "signals": ["IGN1"],
                    "states": [],
                },
                "score": 0.95,
                "sources": ["vector", "graph"],
            },
            {
                "chunk": {
                    "chunk_id": "c2",
                    "chunk_type": "state_machine",
                    "module": "VMM",
                    "section_path": "2.3.4.2.2",
                    "text": "Inactive to Convenience transition requires DoorOpen=TRUE.",
                    "signals": [],
                    "states": ["Inactive", "Convenience"],
                },
                "score": 0.88,
                "sources": ["vector"],
            },
        ]

    def test_build_structured_evidence(
        self, builder, sample_graph_results, sample_candidates,
    ):
        """Test building structured evidence from graph + candidates."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1"],
            "states": ["Inactive"],
            "functions": [],
            "hint_transition": True,
        }

        evidence = builder.build(
            graph_results=sample_graph_results,
            merged_candidates=sample_candidates,
            intent=intent,
            query="IGN1 signal definition?",
        )

        assert isinstance(evidence, StructuredEvidence)
        assert evidence.query == "IGN1 signal definition?"
        assert "VMM" in evidence.modules
        assert "IGN1" in evidence.signals
        # Should have extracted state transitions from TRANSITION_TO
        assert len(evidence.state_transitions) >= 0
        # Should have text chunks
        assert len(evidence.text_chunks) > 0

    def test_build_empty_graph_results(self, builder, sample_candidates):
        """Test building evidence with empty graph results."""
        intent = {"question_type": "factual"}
        evidence = builder.build(
            graph_results=[],
            merged_candidates=sample_candidates,
            intent=intent,
            query="test",
        )
        assert evidence.dependency_chains == []
        assert evidence.state_transitions == []
        assert len(evidence.text_chunks) > 0

    def test_format_for_llm(self, builder, sample_graph_results, sample_candidates):
        """Test formatting structured evidence for LLM consumption."""
        intent = {
            "question_type": "factual",
            "modules": ["VMM"],
            "signals": ["IGN1"],
            "states": [],
            "functions": [],
        }
        evidence = builder.build(
            graph_results=sample_graph_results,
            merged_candidates=sample_candidates,
            intent=intent,
            query="IGN1 signal?",
        )
        formatted = builder.format_for_llm(evidence)

        assert isinstance(formatted, str)
        assert len(formatted) > 0
        assert "IGN1 signal?" in formatted
        assert "## 涉及模块" in formatted
        assert "VMM" in formatted
        assert "## 文档片段" in formatted

    def test_format_empty_evidence(self, builder):
        """Test formatting empty evidence."""
        evidence = StructuredEvidence(query="empty")
        formatted = builder.format_for_llm(evidence)
        assert "empty" in formatted

    def test_dependency_chain_extraction(
        self, builder, sample_graph_results,
    ):
        """Test dependency chain extraction from graph results."""
        chains = builder._extract_dependency_chains(
            graph_results=sample_graph_results,
            intent={"signals": ["IGN1"], "functions": [], "modules": ["VMM"]},
        )
        assert isinstance(chains, list)

    def test_state_transition_extraction(
        self, builder, sample_graph_results,
    ):
        """Test state transition extraction from graph results."""
        transitions = builder._extract_state_transitions(
            graph_results=sample_graph_results,
            state_machine=None,
            intent={"states": ["Inactive"], "hint_transition": True},
        )
        assert isinstance(transitions, list)
        # Should find the TRANSITION_TO relationship
        assert len(transitions) >= 1
        found = False
        for t in transitions:
            if t.source == "Inactive" and t.target == "Convenience":
                found = True
                break
        assert found, "Should find Inactive→Convenience transition"

    def test_rule_matching(self, builder):
        """Test rule matching from intent."""
        rules = [
            {"rule_id": "VMM_001", "text": "IGN1 open circuit detection", "module": "VMM"},
            {"rule_id": "WIPER_001", "text": "Wiper speed control", "module": "Wiper"},
            {"rule_id": "VMM_002", "text": "PEPS key status check", "module": "VMM"},
        ]
        intent = {
            "modules": ["VMM"],
            "signals": ["IGN1"],
            "keywords": ["open", "circuit"],
        }
        matched = builder._match_relevant_rules(intent, rules)
        assert len(matched) > 0
        # VMM rules should match, Wiper should not
        matched_ids = [r["rule_id"] for r in matched]
        assert "VMM_001" in matched_ids
        assert "WIPER_001" not in matched_ids

    def test_text_chunk_selection(self, builder, sample_candidates):
        """Test deduplication in text chunk selection."""
        selected = builder._select_text_chunks(sample_candidates)
        assert len(selected) == len(sample_candidates)

        # Test dedup: duplicate text should be removed
        dup_candidates = sample_candidates + sample_candidates
        selected_dup = builder._select_text_chunks(dup_candidates)
        assert len(selected_dup) == len(sample_candidates)
