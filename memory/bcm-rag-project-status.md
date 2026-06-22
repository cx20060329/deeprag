---
name: bcm-rag-project-status
description: Current project completion status — ~80% done, all 9 retrieval stages working, embeddings + LLM + API done
metadata:
  type: project
---

BCM-RAG project at ~80% completion.

Completed (2026-06-17 updated):
- Parser: Dual-backend Docling+MinerU with auto-fallback (95%)
- Document Tree: 482 nodes, page refs, table ownership (90%)
- Entity Extraction: 1717 entities, 1686 relationships (9/10 types) (90%)
- KG export: Cypher + JSON, NetworkX in-memory graph (70%)
- Chunking: 162 semantic chunks, image object storage (90%)
- Vector Store: BGE-small-zh-v1.5 embeddings (512-dim) + BM25 + DenseRetriever (85%)
- Retrieval pipeline: ALL 9 stages (85%) — Dense+BM25 RRF fusion + LLM Answer
- LLM Integration: OpenAI-compatible, Ark/Zhipu/DeepSeek support (80%)
- Reranking: Jaccard semantic + rule-based + Dense (65%)
- Context compression: dedup + top-5 evidence package (60%)
- API: FastAPI with 6 endpoints, SSE streaming (80%)

Key metrics (updated):
- 7/7 retrieval tests match correct module (100% accuracy, with dense+BM25 fusion)
- Query latency: 55-120ms (embeddings pre-loaded)
- 1802 graph nodes, 1645 edges
- 162 chunks with 512-dim BGE embeddings
- 5334 BM25 vocabulary terms

Remaining:
- TRANSITION_TO = 0 (table format limitation)
- 36 tables unclassified (regex fallback)
- No runtime Neo4j/Qdrant (all in-memory)
- No Cross-Encoder Rerank
- Limited test coverage

Key files: PROGRESS.md, CLAUDE.md, retrieval/pipeline.py, api/__init__.py
Related: [[bcm-rag-architecture]]
