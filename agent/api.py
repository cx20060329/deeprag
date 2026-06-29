"""DeepRAG — Agent API facade.

Public API for DAG-based reasoning agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.config import DomainConfig


class AgentAPI:
    """Public API for the DAG reasoning agent.

    Usage:
        from retrieval.api import RetrievalAPI

        retrieval = RetrievalAPI(domain=domain, data_dir="output/PA2A")
        retrieval.load()

        agent = AgentAPI(retrieval_api=retrieval, domain=domain)
        response = agent.query("GlobalClose的触发条件是什么？")
    """

    def __init__(
        self,
        retrieval_api=None,
        domain: "DomainConfig | None" = None,
        provider: str = "",
        model: str | None = None,
        api_key: str | None = None,
        debug: bool = False,
    ):
        self._retrieval = retrieval_api
        self._domain = domain
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._debug = debug
        self._agent = None

    def query(self, question: str) -> dict:
        """Run the DAG agent on a question.

        Args:
            question: User question string.

        Returns:
            AgentResponse with answer, confidence, reasoning trace, etc.
        """
        self._ensure_agent()
        return self._agent.query(question)

    def query_stream(self, question: str):
        """Stream the agent response tokens.

        Args:
            question: User question string.

        Yields:
            Text chunks as the agent reasons.
        """
        self._ensure_agent()
        return self._agent.query_stream(question)

    @property
    def agent(self):
        """Access the underlying DagAgent (advanced use)."""
        self._ensure_agent()
        return self._agent

    def _ensure_agent(self):
        if self._agent is not None:
            return

        from agent.dag_agent import DagAgent

        pipeline = None
        if self._retrieval is not None:
            pipeline = self._retrieval.pipeline

        self._agent = DagAgent(
            retrieval_pipeline=pipeline,
            provider=self._provider,
            model=self._model,
            api_key=self._api_key,
            debug=self._debug,
            domain=self._domain,
        )
