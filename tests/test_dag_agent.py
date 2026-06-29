"""Tests for agent.dag_agent — DAG-Mode Agent.

Tests DAG templates, executor, node executors, synthesizer,
and end-to-end DagAgent.
"""

import json
import time
from unittest.mock import MagicMock

import pytest


# ======================================================================
# Fake / Mock objects
# ======================================================================


class FakePipeline:
    """Mock RetrievalPipeline for testing."""

    def _analyze_intent(self, query):
        return {
            "modules": ["VMM"],
            "signals": ["IGN1", "PEPS_UsageMode"],
            "states": ["Inactive", "Driving"],
            "functions": ["GlobalClose"],
            "faults": ["KeyLost"],
            "keywords": ["IGN1", "信号", "定义"],
            "question_type": "factual",
            "hint_signal_def": True,
            "hint_transition": False,
        }

    def search(self, query, top_k=5, enable_llm=False, **kwargs):
        return {
            "merged": [
                {
                    "chunk": {
                        "chunk_id": "c1",
                        "chunk_type": "signal_table",
                        "module": "VMM",
                        "section_path": "2.2.1.1",
                        "section_title": "IGN1 Signal Definition",
                        "text": "IGN1 relay feedback signal. 0=Open, 1=Closed.",
                    },
                    "score": 0.95,
                },
            ],
        }


class FakeReasoningEngine:
    """Mock ReasoningEngine for testing."""

    def __init__(self):
        self._loaded = True
        self.state_graph = self._build_fake_state_graph()
        self.kg_graph = self._build_fake_kg_graph()

    def _build_fake_state_graph(self):
        import networkx as nx
        g = nx.DiGraph()
        g.add_node("Abandoned")
        g.add_node("Inactive")
        g.add_node("Convenience")
        g.add_node("Driving")
        return g

    def _build_fake_kg_graph(self):
        import networkx as nx
        g = nx.DiGraph()
        g.add_node("sig_ign1", name="IGN1", entity_type="signal", module="VMM")
        return g

    @property
    def state_ready(self):
        return self.state_graph.number_of_nodes() > 0

    @property
    def kg_ready(self):
        return self.kg_graph.number_of_nodes() > 0

    def forward_chain(self, entity, entity_type="signal", max_depth=5, module=""):
        from retrieval.reasoning_engine import ImpactReport, ImpactNode

        return ImpactReport(
            trigger=entity,
            trigger_type=entity_type,
            impacted=[
                ImpactNode(
                    entity="PEPS_UsageMode",
                    entity_type="signal",
                    module="VMM",
                    depth=1,
                    via="controls",
                    effect="Changes usage mode",
                ),
                ImpactNode(
                    entity="Driving",
                    entity_type="state",
                    module="VMM",
                    depth=2,
                    via="depends_on",
                    effect="Affects driving state",
                ),
            ],
            total_depth=2,
            total_impacted=2,
        )

    def path_query(self, source, target, max_hops=6, module=""):
        return {
            "source": source,
            "target": target,
            "module": "VMM",
            "total_paths": 2,
            "shortest_hops": 3,
            "paths": [
                {
                    "sequence": [source, "Convenience", target],
                    "hops": 3,
                    "transitions": [
                        {
                            "source": source,
                            "target": "Convenience",
                            "guard": "DoorOpen=TRUE AND KeyValid=TRUE",
                            "effect": "Enter Convenience",
                            "source_section": "2.3.4.2.2",
                        },
                        {
                            "source": "Convenience",
                            "target": target,
                            "guard": "BrakePressed=TRUE AND GearInDrive=TRUE",
                            "effect": "Enter Driving",
                            "source_section": "2.3.4.3.2",
                        },
                    ],
                    "total_conditions": [
                        "DoorOpen=TRUE",
                        "KeyValid=TRUE",
                        "BrakePressed=TRUE",
                        "GearInDrive=TRUE",
                    ],
                },
            ],
        }

    def backward_chain(self, target_state, module="VMM", max_depth=5, _visited=None):
        return None

    def detect_conflicts(self, module="VMM"):
        return [
            {
                "rule1": "VMM_001",
                "rule2": "VMM_002",
                "type": "potential_conflict",
                "detail": "Both rules trigger on same condition",
            },
        ]

    def reachability_analysis(self, module="VMM"):
        return [
            {
                "type": "unreachable",
                "state": "Abandoned",
                "detail": "No incoming transitions from initial state",
                "recommendation": "Check state machine completeness",
            },
        ]

    def load_state_machine(self, path):
        pass

    def load_rules(self, path):
        pass


class FakeLLMGenerator:
    """Mock LLMAnswerGenerator for testing."""

    def __init__(self, response="test answer"):
        self.response = response
        self.model = "test-model"

    def answer(self, evidence, query, intent=None, system_prompt=None):
        return {
            "answer": self.response,
            "model": self.model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "evidence_length": len(evidence),
        }


# ======================================================================
# Test DAG Templates
# ======================================================================


class TestDagTemplates:
    """Test the 6 predefined DAG templates."""

    def test_all_templates_defined(self):
        from agent.dag_agent import DAG_TEMPLATES

        assert len(DAG_TEMPLATES) == 6
        expected = [
            "factual_lookup",
            "state_transition",
            "impact_analysis",
            "path_finding",
            "diagnostic",
            "reachability_check",
        ]
        for name in expected:
            assert name in DAG_TEMPLATES

    def test_factual_lookup_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["factual_lookup"]
        assert "intent" in tmpl.nodes
        assert "chunks" in tmpl.nodes
        assert len(tmpl.edges) >= 1

    def test_state_transition_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["state_transition"]
        assert "intent" in tmpl.nodes
        assert "sm" in tmpl.nodes
        assert "rules" in tmpl.nodes
        assert "chunks" in tmpl.nodes
        assert len(tmpl.edges) >= 3

    def test_impact_analysis_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["impact_analysis"]
        assert "impact" in tmpl.nodes
        # impact node should exist
        impact_node = tmpl.nodes["impact"]
        assert impact_node["type"] == "impact_analysis"

    def test_path_finding_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["path_finding"]
        assert "path" in tmpl.nodes
        path_node = tmpl.nodes["path"]
        assert path_node["type"] == "path_finder"

    def test_diagnostic_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["diagnostic"]
        assert "conflicts" in tmpl.nodes
        assert tmpl.nodes["conflicts"]["type"] == "conflict_detection"

    def test_reachability_structure(self):
        from agent.dag_agent import DAG_TEMPLATES

        tmpl = DAG_TEMPLATES["reachability_check"]
        assert "reach" in tmpl.nodes
        assert tmpl.nodes["reach"]["type"] == "reachability"

    def test_all_templates_have_intent_node(self):
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            assert "intent" in tmpl.nodes, f"{name} missing intent node"

    def test_all_templates_have_chunks_node(self):
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            assert "chunks" in tmpl.nodes, f"{name} missing chunks node"


# ======================================================================
# Test Node Executors
# ======================================================================


class TestNodeExecutors:
    """Test individual node executor functions."""

    @pytest.fixture
    def pipeline(self):
        return FakePipeline()

    @pytest.fixture
    def engine(self):
        return FakeReasoningEngine()

    @pytest.fixture
    def sm(self):
        return {
            "module": "VMM",
            "transitions": [
                {
                    "source": "Inactive",
                    "target": "Convenience",
                    "guard": "DoorOpen=TRUE AND KeyValid=TRUE",
                    "effect": "Enter Convenience mode",
                    "source_section": "2.3.4.2.2",
                },
                {
                    "source": "Convenience",
                    "target": "Driving",
                    "guard": "BrakePressed=TRUE AND GearInDrive=TRUE",
                    "effect": "Enter Driving mode",
                    "source_section": "2.3.4.3.2",
                },
                {
                    "source": "Driving",
                    "target": "Inactive",
                    "guard": "IGN_OFF AND VehicleSpeed=0",
                    "effect": "Return to Inactive",
                    "source_section": "2.3.4.5.1",
                },
            ],
        }

    @pytest.fixture
    def rules(self):
        return {
            "rules": [
                {
                    "rule_id": "VMM_001",
                    "module": "VMM",
                    "condition_expr": "IGN1 == 1",
                    "action": "Set PEPS_UsageMode=Normal",
                    "rule_type": "activation",
                    "section": "2.2.1.1",
                },
                {
                    "rule_id": "VMM_002",
                    "module": "VMM",
                    "condition_expr": "KeyLost == TRUE",
                    "action": "Trigger KeyLost fault",
                    "rule_type": "fault_detection",
                    "section": "2.3.4.2",
                },
            ],
        }

    def test_intent_analysis(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_intent_analysis

        result = _exec_intent_analysis(
            pipeline, engine, sm, rules,
            {"query": "IGN1 signal?"}, {},
        )
        assert "modules" in result
        assert "VMM" in result["modules"]
        assert "IGN1" in result["signals"]

    def test_state_machine(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_state_machine

        result = _exec_state_machine(
            pipeline, engine, sm, rules,
            {"states": ["Inactive", "Driving"]}, {},
        )
        assert "transitions" in result
        assert len(result["transitions"]) >= 1

    def test_state_machine_empty(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_state_machine

        result = _exec_state_machine(
            pipeline, engine, sm, rules,
            {"states": []}, {},
        )
        assert "transitions" in result

    def test_rule_lookup(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_rule_lookup

        result = _exec_rule_lookup(
            pipeline, engine, sm, rules,
            {"keywords": "IGN1", "modules": ["VMM"]}, {},
        )
        assert "matched_rules" in result
        assert len(result["matched_rules"]) >= 1

    def test_rule_lookup_from_upstream(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_rule_lookup

        upstream = {
            "intent": {
                "keywords": ["IGN1", "signal"],
                "modules": ["VMM"],
            },
        }
        result = _exec_rule_lookup(
            pipeline, engine, sm, rules,
            {"keywords": "", "modules": []}, upstream,
        )
        assert len(result["matched_rules"]) >= 1

    def test_path_finder(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_path_finder

        result = _exec_path_finder(
            pipeline, engine, sm, rules,
            {"source": "Inactive", "target": "Driving"}, {},
        )
        assert "paths" in result
        assert result["source"] == "Inactive"
        assert result["target"] == "Driving"

    def test_path_finder_empty(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_path_finder

        result = _exec_path_finder(
            pipeline, engine, sm, rules,
            {"source": "", "target": ""}, {},
        )
        assert "error" in result or len(result.get("paths", [])) == 0

    def test_impact_analysis(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_impact_analysis

        result = _exec_impact_analysis(
            pipeline, engine, sm, rules,
            {"entity": "IGN1", "entity_type": "signal"}, {},
        )
        assert "impacted" in result
        assert len(result["impacted"]) >= 1

    def test_impact_analysis_from_upstream(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_impact_analysis

        upstream = {"intent": {"signals": ["IGN1"]}}
        result = _exec_impact_analysis(
            pipeline, engine, sm, rules,
            {"entity": "", "entity_type": "signal"}, upstream,
        )
        assert len(result["impacted"]) >= 1

    def test_conflict_detection(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_conflict_detection

        result = _exec_conflict_detection(
            pipeline, engine, sm, rules,
            {"module": "VMM"}, {},
        )
        assert "conflicts" in result
        assert len(result["conflicts"]) >= 1

    def test_reachability(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_reachability

        result = _exec_reachability(
            pipeline, engine, sm, rules,
            {"module": "VMM"}, {},
        )
        assert "issues" in result
        assert len(result["issues"]) >= 1

    def test_chunk_search(self, pipeline, engine, sm, rules):
        from agent.dag_agent import _exec_chunk_search

        result = _exec_chunk_search(
            pipeline, engine, sm, rules,
            {"query": "IGN1 signal", "top_k": 3}, {},
        )
        assert "chunks" in result
        assert len(result["chunks"]) >= 1


# ======================================================================
# Test Data Flow Engine
# ======================================================================


class TestDataFlow:
    """Test data flow resolution between DAG nodes."""

    def test_direct_field_access(self):
        from agent.dag_agent import _resolve_data_flow

        upstream = {"intent": {"modules": ["VMM"], "signals": ["IGN1"]}}
        result = _resolve_data_flow(
            upstream,
            "intent.modules → modules",
        )
        assert "modules" in result
        assert result["modules"] == ["VMM"]

    def test_array_expansion(self):
        from agent.dag_agent import _resolve_data_flow

        upstream = {
            "sm": {
                "transitions": [
                    {"guard": "cond1", "effect": "eff1"},
                    {"guard": "cond2", "effect": "eff2"},
                ],
            },
        }
        result = _resolve_data_flow(
            upstream,
            "sm.transitions[*].guard → keywords",
        )
        assert "keywords" in result
        assert "cond1" in result["keywords"]
        assert "cond2" in result["keywords"]

    def test_static_assignment(self):
        from agent.dag_agent import _resolve_data_flow

        upstream = {"intent": {"signals": ["IGN1"]}}
        result = _resolve_data_flow(
            upstream,
            "intent.signals → entity_type=signal",
        )
        assert "entity_type" in result
        assert result["entity_type"] == "signal"

    def test_multiple_rules(self):
        from agent.dag_agent import _resolve_data_flow

        upstream = {"intent": {"modules": ["VMM"], "signals": ["IGN1"]}}
        result = _resolve_data_flow(
            upstream,
            "intent.modules → modules; intent.signals → entity",
        )
        assert "modules" in result
        assert "entity" in result
        assert result["entity"] == ["IGN1"]

    def test_empty_rule(self):
        from agent.dag_agent import _resolve_data_flow

        result = _resolve_data_flow({}, "")
        assert result == {}

    def test_nonexistent_node(self):
        from agent.dag_agent import _resolve_data_flow

        result = _resolve_data_flow({}, "nonexistent.field → target")
        assert result == {}

    def test_merge_upstream_data(self):
        from agent.dag_agent import _merge_upstream_data

        edges = [
            {
                "from": "intent",
                "to": "rules",
                "data_flow": "intent.modules → modules; intent.signals → keywords",
            },
        ]
        upstream = {"intent": {"modules": ["VMM"], "signals": ["IGN1"]}}
        result = _merge_upstream_data("rules", edges, upstream)
        assert "modules" in result
        assert "keywords" in result


# ======================================================================
# Test DagExecutor
# ======================================================================


class TestDagExecutor:
    """Test the DAG execution engine."""

    @pytest.fixture
    def executor(self):
        from agent.dag_agent import DagExecutor
        return DagExecutor()

    @pytest.fixture
    def pipeline(self):
        return FakePipeline()

    @pytest.fixture
    def engine(self):
        return FakeReasoningEngine()

    @pytest.fixture
    def simple_dag_plan(self):
        return {
            "template": "factual_lookup",
            "reasoning": "Simple fact lookup",
            "nodes": {
                "intent": {
                    "enabled": True,
                    "type": "intent_analysis",
                    "params": {},
                },
                "chunks": {
                    "enabled": True,
                    "type": "chunk_search",
                    "params": {"top_k": 3},
                },
            },
            "edges": [
                {"from": "intent", "to": "chunks"},
            ],
        }

    @pytest.fixture
    def complex_dag_plan(self):
        return {
            "template": "state_transition",
            "reasoning": "State transition reasoning",
            "nodes": {
                "intent": {
                    "enabled": True,
                    "type": "intent_analysis",
                    "params": {},
                },
                "sm": {
                    "enabled": True,
                    "type": "state_machine",
                    "params": {"states": []},
                },
                "rules": {
                    "enabled": True,
                    "type": "rule_lookup",
                    "params": {"keywords": "", "modules": []},
                },
                "chunks": {
                    "enabled": True,
                    "type": "chunk_search",
                    "params": {"top_k": 3},
                },
            },
            "edges": [
                {"from": "intent", "to": "sm",
                 "data_flow": "intent.states → sm.states"},
                {"from": "intent", "to": "chunks"},
                {"from": "sm", "to": "rules",
                 "data_flow": "sm.transitions[*].guard → rules.keywords"},
            ],
        }

    @pytest.fixture
    def sm(self):
        return {
            "module": "VMM",
            "transitions": [
                {
                    "source": "Inactive", "target": "Convenience",
                    "guard": "DoorOpen=TRUE AND KeyValid=TRUE",
                    "effect": "Enter Convenience",
                    "source_section": "2.3.4.2.2",
                },
            ],
        }

    @pytest.fixture
    def rules(self):
        return {
            "rules": [
                {
                    "rule_id": "VMM_001", "module": "VMM",
                    "condition_expr": "IGN1 == 1",
                    "action": "Set PEPS_UsageMode=Normal",
                    "rule_type": "activation",
                },
            ],
        }

    def test_execute_simple_dag(
        self, executor, simple_dag_plan, pipeline, engine, sm, rules,
    ):
        """Test executing a simple 2-node DAG."""
        result = executor.execute(
            simple_dag_plan, pipeline, engine, sm, rules, "test query",
        )
        assert result.template == "factual_lookup"
        assert len(result.node_outputs) == 2
        assert "intent" in result.node_outputs
        assert "chunks" in result.node_outputs
        assert result.node_outputs["intent"].status == "success"
        assert result.node_outputs["chunks"].status == "success"
        # Execution order: intent first, then chunks
        assert len(result.execution_order) >= 2

    def test_execute_complex_dag(
        self, executor, complex_dag_plan, pipeline, engine, sm, rules,
    ):
        """Test executing a 4-node DAG with data flow."""
        result = executor.execute(
            complex_dag_plan, pipeline, engine, sm, rules,
            "How does Inactive transition to Driving?",
        )
        assert len(result.node_outputs) == 4
        # All nodes should succeed
        for nid, no in result.node_outputs.items():
            assert no.status == "success", f"{nid} failed: {no.error}"

    def test_execution_order_is_topological(
        self, executor, complex_dag_plan, pipeline, engine, sm, rules,
    ):
        """Test that execution follows topological order."""
        result = executor.execute(
            complex_dag_plan, pipeline, engine, sm, rules, "test",
        )
        # intent should be in level 0 (no dependencies)
        level0 = result.execution_order[0] if result.execution_order else []
        assert "intent" in level0, f"Expected intent in level 0, got {level0}"

    def test_disabled_node_skipped(self, executor, pipeline, engine, sm, rules):
        """Test that disabled nodes are skipped."""
        plan = {
            "template": "test",
            "nodes": {
                "intent": {"enabled": True, "type": "intent_analysis", "params": {}},
                "sm": {"enabled": False, "type": "state_machine", "params": {"states": []}},
            },
            "edges": [],
        }
        result = executor.execute(plan, pipeline, engine, sm, rules, "test")
        assert result.node_outputs["intent"].status == "success"
        # sm node should not be in outputs (was disabled and skipped)
        assert "sm" not in result.node_outputs or result.node_outputs["sm"].status != "success"

    def test_unknown_node_type(self, executor, pipeline, engine, sm, rules):
        """Test that unknown node types are handled gracefully."""
        plan = {
            "template": "test",
            "nodes": {
                "bad": {"enabled": True, "type": "unknown_type", "params": {}},
            },
            "edges": [],
        }
        result = executor.execute(plan, pipeline, engine, sm, rules, "test")
        assert result.node_outputs["bad"].status == "error"

    def test_total_duration_set(self, executor, simple_dag_plan, pipeline, engine, sm, rules):
        """Test that total_duration_ms is set."""
        result = executor.execute(
            simple_dag_plan, pipeline, engine, sm, rules, "test",
        )
        assert result.total_duration_ms > 0


# ======================================================================
# Test DagSynthesizer
# ======================================================================


class TestDagSynthesizer:
    """Test the DAG answer synthesizer."""

    @pytest.fixture
    def llm(self):
        return FakeLLMGenerator(
            "## 结论\nIGN1是点火继电器反馈信号。\n\n"
            "## 推理链\n意图分析 → 状态机 → 规则 → 文档\n\n"
            "## 详细分析\nIGN1信号值为0=Open, 1=Closed。\n\n"
            "## 置信度评估\nCONFIDENCE=0.85"
        )

    @pytest.fixture
    def synthesizer(self, llm):
        from agent.dag_agent import DagSynthesizer
        return DagSynthesizer(llm)

    def test_synthesize(self, synthesizer):
        from agent.dag_agent import DagResult, DagNodeOutput

        result = DagResult(
            question="test question",
            template="state_transition",
            dag_plan={
                "template": "state_transition",
                "reasoning": "test",
                "nodes": {},
                "edges": [],
            },
            node_outputs={
                "intent": DagNodeOutput(
                    node_id="intent",
                    node_type="intent_analysis",
                    status="success",
                    output={"modules": ["VMM"], "signals": ["IGN1"]},
                    duration_ms=10,
                ),
                "chunks": DagNodeOutput(
                    node_id="chunks",
                    node_type="chunk_search",
                    status="success",
                    output={"chunks": [
                        {"chunk_type": "signal_table", "section_path": "2.2.1.1",
                         "module": "VMM", "text": "IGN1 definition", "score": 0.95},
                    ]},
                    duration_ms=50,
                ),
            },
            execution_order=[["intent"], ["chunks"]],
        )

        synth = synthesizer.synthesize("test question", result.dag_plan, result)
        assert "answer" in synth
        assert len(synth["answer"]) > 0
        assert synth.get("confidence", 0) > 0

    def test_fallback_synthesize(self):
        from agent.dag_agent import DagSynthesizer, DagResult, DagNodeOutput

        synthesizer = DagSynthesizer(None)  # No LLM
        result = DagResult(
            question="test",
            template="factual_lookup",
            dag_plan={"template": "factual_lookup", "nodes": {}, "edges": []},
            node_outputs={
                "intent": DagNodeOutput(
                    node_id="intent", node_type="intent_analysis",
                    status="success",
                    output={"modules": ["VMM"]},
                ),
            },
            execution_order=[["intent"]],
        )
        synth = synthesizer._fallback_synthesize("test", result)
        assert "answer" in synth
        assert synth["model"] == "fallback"

    def test_extract_confidence(self, synthesizer):
        conf = synthesizer._extract_confidence("CONFIDENCE=0.75")
        assert conf == pytest.approx(0.75, abs=0.01)

    def test_extract_confidence_percentage(self, synthesizer):
        conf = synthesizer._extract_confidence("置信度: 85%")
        assert conf == pytest.approx(0.85, abs=0.01)


# ======================================================================
# Test DagAgent (end-to-end with mocks)
# ======================================================================


class TestDagAgent:
    """End-to-end tests for DagAgent with mocked subsystems."""

    def test_build_plan_from_template(self):
        from agent.dag_agent import DagAgent

        agent = DagAgent.__new__(DagAgent)
        plan = agent._build_plan_from_template("factual_lookup", "test query")
        assert plan["template"] == "factual_lookup"
        assert "nodes" in plan
        assert "edges" in plan
        assert plan["nodes"]["intent"]["enabled"] is True

    def test_build_plan_unknown_template(self):
        from agent.dag_agent import DagAgent

        agent = DagAgent.__new__(DagAgent)
        plan = agent._build_plan_from_template("nonexistent", "test")
        assert plan["template"] == "nonexistent"
        # Should fall back to factual_lookup template structure

    def test_select_template_fallback(self):
        from agent.dag_agent import DagAgent

        agent = DagAgent.__new__(DagAgent)
        plan = agent._select_template_fallback("KeyLost会影响什么功能？")
        # "影响" should trigger impact_analysis
        assert plan["template"] in ["impact_analysis", "factual_lookup"]

        plan2 = agent._select_template_fallback("从Inactive如何到达Driving？")
        # State names + path keywords
        assert plan2["template"] in ["path_finding", "state_transition"]

    def test_merge_with_template(self):
        from agent.dag_agent import DagAgent

        agent = DagAgent.__new__(DagAgent)
        llm_plan = {
            "template": "state_transition",
            "reasoning": "User asks about state transitions",
            "nodes": {
                "intent": {"enabled": True},
                "sm": {"enabled": True, "params": {"states": ["Driving"]}},
                "rules": {"enabled": True},
                "chunks": {"enabled": True},
            },
            "custom_edges": [],
        }
        merged = agent._merge_with_template(llm_plan)
        assert merged["template"] == "state_transition"
        assert merged["nodes"]["sm"]["enabled"] is True
        assert merged["nodes"]["sm"]["params"].get("states") == ["Driving"]

    def test_backward_compat_existing_agents(self):
        """Verify existing agents are not affected by dag_agent module."""
        from agent.core import BCMAgent
        from agent.agentic_rag_v2 import AgenticRAGv2
        from agent.agentic_rag import AgenticRAG

        # These should import without error
        assert BCMAgent is not None
        assert AgenticRAGv2 is not None
        assert AgenticRAG is not None


# ======================================================================
# Completeness Evaluation Tests
# ======================================================================


class TestCompletenessEval:
    """Tests for _exec_completeness_eval node executor."""

    def test_all_sufficient(self):
        """All dimensions above threshold → high score, is_sufficient=True."""
        from agent.dag_agent import _exec_completeness_eval

        upstream = {
            "intent": {
                "modules": ["VMM", "Window"],
                "signals": ["IGN1", "PEPS_UsageMode"],
                "states": ["Inactive", "Driving"],
                "faults": ["KeyLost"],
                "question_type": "reasoning",
            },
            "sm": {
                "transitions": [
                    {"source": "Inactive", "target": "Driving", "guard": "IGN1=1"}
                    for _ in range(3)
                ]
            },
            "rules": {
                "matched_rules": [{"rule_id": "r1"} for _ in range(5)]
            },
            "chunks": {
                "chunks": [{"chunk_id": "c1"} for _ in range(5)]
            },
        }
        params = {
            "query": "How does state transition work?",
            "dimensions": [
                "intent_coverage", "state_transitions",
                "matched_rules", "document_chunks",
            ],
        }
        result = _exec_completeness_eval(None, None, None, None, params, upstream)

        assert result["overall_score"] >= 0.7
        assert result["is_sufficient"] is True
        assert len(result["dimensions"]) == 4
        for dim in result["dimensions"]:
            assert dim["status"] == "sufficient", f"{dim['name']} should be sufficient"

    def test_all_missing(self):
        """Empty upstream → low score, is_sufficient=False."""
        from agent.dag_agent import _exec_completeness_eval

        params = {
            "query": "Some query",
            "dimensions": [
                "intent_coverage", "state_transitions",
                "matched_rules", "document_chunks",
            ],
        }
        result = _exec_completeness_eval(None, None, None, None, params, {})

        assert result["overall_score"] == 0.0
        assert result["is_sufficient"] is False
        for dim in result["dimensions"]:
            assert dim["status"] == "missing", f"{dim['name']} should be missing"
            assert dim["found"] == 0

    def test_partial_data(self):
        """Some dimensions sufficient, some missing → mixed scores."""
        from agent.dag_agent import _exec_completeness_eval

        upstream = {
            "intent": {"signals": ["IGN1"], "question_type": "factual"},
            "chunks": {"chunks": [{"chunk_id": "c1"}, {"chunk_id": "c2"}]},
        }
        params = {
            "query": "Partial data query",
            "dimensions": [
                "intent_coverage", "document_chunks", "state_transitions",
            ],
        }
        result = _exec_completeness_eval(None, None, None, None, params, upstream)

        assert 0.0 < result["overall_score"] < 0.5
        assert result["is_sufficient"] is False
        # intent_coverage should be insufficient (1 entity < 2 threshold)
        intent_dim = [d for d in result["dimensions"] if d["name"] == "intent_coverage"][0]
        assert intent_dim["status"] == "insufficient"
        # state_transitions should be missing
        sm_dim = [d for d in result["dimensions"] if d["name"] == "state_transitions"][0]
        assert sm_dim["status"] == "missing"

    def test_follow_up_queries_generated(self):
        """When gaps exist, gap_queries should be generated."""
        from agent.dag_agent import _exec_completeness_eval

        upstream = {
            "intent": {"states": ["Driving"], "question_type": "reasoning"},
        }
        params = {
            "query": "How to enter Driving mode?",
            "dimensions": [
                "intent_coverage", "state_transitions",
                "matched_rules", "document_chunks",
            ],
        }
        result = _exec_completeness_eval(None, None, None, None, params, upstream)

        # All dimensions except intent_coverage should be missing
        # so gap_queries should be non-empty
        assert len(result["gap_queries"]) > 0
        # Should have summary mentioning gaps
        assert "缺口" in result["summary"] or result["overall_score"] < 0.5

    def test_dimension_weights(self):
        """Weighted average calculation is correct with known inputs."""
        from agent.dag_agent import _exec_completeness_eval

        # transitions (weight 0.20): 5 found → score 1.0
        # rules (weight 0.20): 0 found → score 0.0
        # chunks (weight 0.15): 0 found → score 0.0
        # intent_coverage (weight 0.05): 5 entities → score 1.0
        # Expected: (0.20*1.0 + 0.20*0.0 + 0.15*0.0 + 0.05*1.0) / 0.60 = 0.25/0.60 ≈ 0.42
        upstream = {
            "intent": {
                "signals": ["S1", "S2", "S3"],
                "states": ["St1", "St2"],
                "question_type": "reasoning",
            },
            "sm": {"transitions": [{} for _ in range(5)]},
        }
        params = {
            "query": "Weight test",
            "dimensions": [
                "intent_coverage", "state_transitions",
                "matched_rules", "document_chunks",
            ],
        }
        result = _exec_completeness_eval(None, None, None, None, params, upstream)

        # Verify dimensions
        for dim in result["dimensions"]:
            if dim["name"] == "state_transitions":
                assert dim["score"] == 1.0
                assert dim["status"] == "sufficient"
            elif dim["name"] == "matched_rules":
                assert dim["score"] == 0.0
                assert dim["status"] == "missing"
            elif dim["name"] == "document_chunks":
                assert dim["score"] == 0.0
            elif dim["name"] == "intent_coverage":
                assert dim["score"] == 1.0

        # Weighted sum: 0.20*1.0 + 0.20*0.0 + 0.15*0.0 + 0.05*1.0 = 0.25
        # Total weight: 0.20 + 0.20 + 0.15 + 0.05 = 0.60
        # Expected: 0.25 / 0.60 ≈ 0.42
        assert 0.40 <= result["overall_score"] <= 0.44

    def test_output_keys(self):
        """Output dict has all expected keys for DagSynthesizer compatibility."""
        from agent.dag_agent import _exec_completeness_eval

        upstream = {
            "intent": {"signals": ["IGN1"], "question_type": "factual"},
        }
        params = {
            "query": "Test",
            "dimensions": ["intent_coverage", "document_chunks"],
        }
        result = _exec_completeness_eval(None, None, None, None, params, upstream)

        expected_keys = {
            "overall_score", "dimensions", "is_sufficient",
            "gap_queries", "summary", "llm_used", "report_type",
        }
        assert expected_keys.issubset(set(result.keys()))

        for dim in result["dimensions"]:
            dim_keys = {"name", "score", "threshold", "status", "detail", "gaps", "found"}
            assert dim_keys.issubset(set(dim.keys()))


class TestDagExecutorWithEval:
    """Integration tests: DagExecutor with completeness_eval node."""

    def test_execute_with_eval_node(self):
        """DAG with intent → chunks → eval executes all nodes."""
        from agent.dag_agent import DagExecutor, DagResult

        executor = DagExecutor()
        dag_plan = {
            "template": "factual_lookup",
            "nodes": {
                "intent": {"enabled": True, "type": "intent_analysis", "params": {}},
                "chunks": {"enabled": True, "type": "chunk_search", "params": {"top_k": 3}},
                "eval": {
                    "enabled": True,
                    "type": "completeness_eval",
                    "params": {"dimensions": ["intent_coverage", "document_chunks"]},
                },
            },
            "edges": [
                {"from": "intent", "to": "chunks"},
                {"from": "intent", "to": "eval"},
                {"from": "chunks", "to": "eval"},
            ],
        }

        from tests.test_dag_agent import FakePipeline, FakeReasoningEngine
        pipeline = FakePipeline()
        engine = FakeReasoningEngine()

        result = executor.execute(
            dag_plan=dag_plan,
            pipeline=pipeline,
            engine=engine,
            sm={"module": "VMM", "transitions": []},
            rules={"rules": []},
            query="What is IGN1?",
        )

        assert isinstance(result, DagResult)
        assert "intent" in result.node_outputs
        assert "chunks" in result.node_outputs
        assert "eval" in result.node_outputs
        assert result.node_outputs["eval"].status == "success"

        eval_output = result.node_outputs["eval"].output
        assert eval_output is not None
        assert "overall_score" in eval_output
        assert "is_sufficient" in eval_output
        assert "dimensions" in eval_output

    def test_eval_node_runs_last(self):
        """Eval node should be in the last topological level."""
        from agent.dag_agent import DagExecutor

        executor = DagExecutor()
        dag_plan = {
            "template": "state_transition",
            "nodes": {
                "intent": {"enabled": True, "type": "intent_analysis", "params": {}},
                "sm": {"enabled": True, "type": "state_machine", "params": {"states": []}},
                "rules": {"enabled": True, "type": "rule_lookup", "params": {"keywords": "", "modules": []}},
                "chunks": {"enabled": True, "type": "chunk_search", "params": {"top_k": 3}},
                "eval": {
                    "enabled": True,
                    "type": "completeness_eval",
                    "params": {"dimensions": ["intent_coverage", "state_transitions", "matched_rules", "document_chunks"]},
                },
            },
            "edges": [
                {"from": "intent", "to": "sm"},
                {"from": "intent", "to": "rules"},
                {"from": "intent", "to": "chunks"},
                {"from": "sm", "to": "rules"},
                {"from": "intent", "to": "eval"},
                {"from": "sm", "to": "eval"},
                {"from": "rules", "to": "eval"},
                {"from": "chunks", "to": "eval"},
            ],
        }

        from tests.test_dag_agent import FakePipeline, FakeReasoningEngine
        pipeline = FakePipeline()
        engine = FakeReasoningEngine()

        result = executor.execute(
            dag_plan=dag_plan,
            pipeline=pipeline,
            engine=engine,
            sm={"module": "VMM", "transitions": [
                {"source": "Inactive", "target": "Driving", "guard": "IGN1=1"}
            ]},
            rules={"rules": [{"rule_id": "r1", "module": "VMM", "condition": "test", "action": "test"}]},
            query="How to enter Driving?",
        )

        # eval should be in the last level
        assert len(result.execution_order) >= 3
        last_level = result.execution_order[-1]
        assert "eval" in last_level, f"eval should be in last level, got: {last_level}"

    def test_eval_node_with_empty_upstream(self):
        """Eval node handles empty upstream gracefully."""
        from agent.dag_agent import DagExecutor

        executor = DagExecutor()
        dag_plan = {
            "template": "factual_lookup",
            "nodes": {
                "intent": {"enabled": True, "type": "intent_analysis", "params": {}},
                "chunks": {"enabled": True, "type": "chunk_search", "params": {"top_k": 3}},
                "eval": {
                    "enabled": True,
                    "type": "completeness_eval",
                    "params": {"dimensions": ["intent_coverage", "document_chunks"]},
                },
            },
            "edges": [
                {"from": "intent", "to": "chunks"},
                {"from": "intent", "to": "eval"},
                {"from": "chunks", "to": "eval"},
            ],
        }

        from tests.test_dag_agent import FakePipeline, FakeReasoningEngine
        pipeline = FakePipeline()
        engine = FakeReasoningEngine()

        result = executor.execute(
            dag_plan=dag_plan,
            pipeline=pipeline,
            engine=engine,
            sm={},
            rules={},
            query="Test query",
        )

        eval_output = result.node_outputs["eval"]
        assert eval_output.status == "success"
        assert eval_output.output is not None
        assert eval_output.output["is_sufficient"] is not None


class TestDagSynthesizerWithEval:
    """Integration tests: DagSynthesizer with completeness eval output."""

    def test_format_eval_node_output(self):
        """_format_node_output handles completeness_eval type."""
        from agent.dag_agent import DagSynthesizer

        synthesizer = DagSynthesizer(None)
        parts = []
        data = {
            "overall_score": 0.75,
            "is_sufficient": True,
            "dimensions": [
                {"name": "state_transitions", "score": 0.8, "threshold": 2,
                 "status": "sufficient", "found": 4, "gaps": [], "detail": "OK"},
                {"name": "matched_rules", "score": 0.4, "threshold": 2,
                 "status": "insufficient", "found": 2, "gaps": ["需要更多规则"], "detail": "不足"},
            ],
            "gap_queries": ["Find more rules"],
            "summary": "信息基本充分",
        }
        synthesizer._format_node_output(parts, "completeness_eval", data)

        output = "\n".join(parts)
        assert "75%" in output or "0.75" in output
        assert "sufficient" in output.lower() or "充分" in output
        assert "gap_queries" in output.lower() or "跟进查询" in output

    def test_compute_dag_stats_includes_eval(self):
        """_compute_dag_stats includes completeness metrics."""
        from agent.dag_agent import DagSynthesizer, DagResult, DagNodeOutput

        synthesizer = DagSynthesizer(None)
        result = DagResult(
            question="Test",
            template="state_transition",
            dag_plan={},
            node_outputs={
                "intent": DagNodeOutput(
                    node_id="intent", node_type="intent_analysis",
                    status="success", output={"modules": ["VMM"]},
                ),
                "eval": DagNodeOutput(
                    node_id="eval", node_type="completeness_eval",
                    status="success", output={
                        "overall_score": 0.72,
                        "is_sufficient": True,
                        "dimensions": [
                            {"name": "d1", "score": 0.8, "status": "sufficient"},
                            {"name": "d2", "score": 0.4, "status": "insufficient"},
                        ],
                    },
                ),
            },
        )

        stats = synthesizer._compute_dag_stats(result)
        assert stats["completeness_score"] == 0.72
        assert stats["completeness_sufficient"] is True
        assert stats["completeness_gaps"] == 1  # one insufficient dimension


class TestDagTemplatesEval:
    """Verify eval node is present in all templates."""

    def test_all_templates_have_eval_node(self):
        """All 6 templates include 'eval' in their nodes dict."""
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            assert "eval" in tmpl.nodes, (
                f"Template '{name}' is missing 'eval' node"
            )

    def test_eval_node_edges_exist(self):
        """Each template has at least 2 edges with to='eval'."""
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            eval_edges = [e for e in tmpl.edges if e.get("to") == "eval"]
            assert len(eval_edges) >= 2, (
                f"Template '{name}' has only {len(eval_edges)} eval edges"
            )

    def test_eval_node_type_correct(self):
        """Each template's eval node has type 'completeness_eval'."""
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            eval_node = tmpl.nodes["eval"]
            assert eval_node["type"] == "completeness_eval", (
                f"Template '{name}' eval node type is '{eval_node['type']}'"
            )
            assert "dimensions" in eval_node["params"], (
                f"Template '{name}' eval node missing 'dimensions' in params"
            )

    def test_eval_node_is_required(self):
        """All eval nodes should be required=True to ensure they always run."""
        from agent.dag_agent import DAG_TEMPLATES

        for name, tmpl in DAG_TEMPLATES.items():
            eval_node = tmpl.nodes["eval"]
            assert eval_node.get("required") is True, (
                f"Template '{name}' eval node should be required=True"
            )
