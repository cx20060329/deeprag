"""BCM-RAG Agent — Tool-use Agent on top of the RAG engineering system.

The agent uses LLM function-calling to select and invoke tools:
  - search_chunks: hybrid retrieval
  - query_graph: KG entity/relationship lookup
  - query_rules: rule engine lookup
  - query_state_machine: state transition paths
  - trace_path: shortest path between states
  - analyze_impact: forward chaining
  - check_conflicts: rule conflict detection
  - check_reachability: deadlock/unreachable analysis

Architecture:
  User Query → Agent Planner → Tool Selection → Execution → Synthesis → Answer
                   ↑                                          │
                   └────────── (iterate if needed) ←──────────┘

Usage:
    from agent import BCMAgent
    agent = BCMAgent()
    agent.load()
    answer = agent.ask("从Abandoned如何进入Driving？")
"""

from agent.core import BCMAgent

__all__ = ["BCMAgent"]
