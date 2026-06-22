"""BCM-RAG Retrieval — Full retrieval pipeline.

Graph retrieval + dense/BM25 retrieval + merge + rerank + compression + LLM answer.

Usage:
    from retrieval import RetrievalPipeline

    pipeline = RetrievalPipeline()
    pipeline.load()

    # Without LLM
    result = pipeline.search("GlobalClose 的触发条件是什么？")
    print(result["evidence"])

    # With LLM
    pipeline.configure_llm(provider="ark")
    result = pipeline.search("GlobalClose 的触发条件是什么？", enable_llm=True)
    print(result["answer"])

    # With structured evidence (Improvement #3)
    result = pipeline.search(
        "GlobalClose 的触发条件是什么？",
        use_structured_evidence=True,
        enable_llm=True,
    )

    # With query rewriting (Improvement #2)
    result = pipeline.search(
        "GlobalClose 的触发条件是什么？",
        enable_query_rewrite=True,
        enable_llm=True,
    )

    # With LLM fusion (Improvement #4)
    result = pipeline.search(
        "GlobalClose 的触发条件是什么？",
        enable_llm_fusion=True,
        quality="accurate",
    )

    # With LLM compression (Improvement #1)
    result = pipeline.search(
        "GlobalClose 的触发条件是什么？",
        use_structured_evidence=True,
        use_llm_compress=True,
        enable_llm=True,
    )
"""

from retrieval.pipeline import RetrievalPipeline
from retrieval.graph_retriever import GraphRetriever
from retrieval.vector_retriever import KeywordRetriever
from retrieval.dense_retriever import DenseRetriever
from retrieval.embedder import EmbeddingGenerator, build_embeddings
from retrieval.llm_answer import LLMAnswerGenerator

# Improvement modules
from retrieval.evidence_builder import (
    EvidenceBuilder,
    StructuredEvidence,
    DependencyChain,
    StateTransition,
)
from retrieval.context_compressor import ContextCompressor
from retrieval.query_rewriter import QueryRewriter
from retrieval.llm_fusion import LLMFusion

__all__ = [
    # Core pipeline
    "RetrievalPipeline",
    "GraphRetriever",
    "KeywordRetriever",
    "DenseRetriever",
    "EmbeddingGenerator",
    "build_embeddings",
    "LLMAnswerGenerator",
    # Improvement #1: LLM compression
    "ContextCompressor",
    # Improvement #2: Query rewriting
    "QueryRewriter",
    # Improvement #3: Structured evidence
    "EvidenceBuilder",
    "StructuredEvidence",
    "DependencyChain",
    "StateTransition",
    # Improvement #4: LLM fusion
    "LLMFusion",
]
