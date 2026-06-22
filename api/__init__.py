"""BCM-RAG API — FastAPI application.

REST API for the BCM-RAG retrieval system.

Usage:
    python -m api.main
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from retrieval import RetrievalPipeline

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

pipeline: RetrievalPipeline | None = None

OUTPUT_DIR = Path(os.getenv("BCM_OUTPUT_DIR", "output/content_analysis"))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query in Chinese or English")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
    enable_llm: bool = Field(default=False, description="Whether to call LLM for answer")
    module_filter: str = Field(default="", description="Optional module filter")


class ChunkResult(BaseModel):
    chunk_id: str
    chunk_type: str
    module: str
    section_path: str
    section_title: str
    text_preview: str = Field(default="", description="First 300 chars of chunk text")
    score: float
    sources: list[str] = Field(default_factory=list)
    has_table: bool = False
    has_image: bool = False


class SearchResponse(BaseModel):
    query: str
    intent: dict
    merged: list[ChunkResult]
    evidence: str
    answer: str | None = None
    usage: dict | None = None
    model: str = ""
    retrieval_time_ms: float = 0.0


class HealthResponse(BaseModel):
    status: str
    loaded: bool
    stats: dict


class LLMConfigRequest(BaseModel):
    api_key: str = Field(default="", description="API key (defaults to env var)")
    base_url: str = Field(default="", description="API base URL")
    model: str = Field(default="", description="Model name")
    provider: str = Field(default="", description="Provider: ark, zhipu, deepseek")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load pipeline on startup, cleanup on shutdown."""
    global pipeline
    print("=" * 50)
    print("Loading BCM-RAG Pipeline...")
    print("=" * 50)
    pipeline = (
        RetrievalPipeline()
        .load(
            kg_path=OUTPUT_DIR / "knowledge_graph.json",
            chunks_path=OUTPUT_DIR / "chunks.json",
            tree_path=OUTPUT_DIR / "section_tree.json",
            points_path=OUTPUT_DIR / "vector_points.json",
            use_dense=True,
        )
    )
    print("Pipeline ready.")
    yield
    pipeline = None
    print("Pipeline unloaded.")


app = FastAPI(
    title="BCM-RAG API",
    description="Body Control Module RAG Retrieval System",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check with pipeline stats."""
    if not pipeline or not pipeline.is_loaded:
        return HealthResponse(status="loading", loaded=False, stats={})

    stats = {
        "graph_nodes": pipeline.graph.stats["nodes"],
        "graph_edges": pipeline.graph.stats["edges"],
        "chunks": pipeline.vector.stats["chunks"],
        "vocabulary": pipeline.vector.stats["vocabulary"],
        "dense_available": pipeline.dense is not None and pipeline.dense.is_loaded,
        "llm_configured": pipeline.llm is not None,
    }
    if pipeline.dense and pipeline.dense.is_loaded:
        stats.update(pipeline.dense.stats)

    return HealthResponse(status="ok", loaded=True, stats=stats)


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """Execute full retrieval pipeline.

    Returns merged results, compressed evidence, and optional LLM answer.
    """
    global pipeline
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    import time
    t0 = time.time()

    result = pipeline.search(
        query=req.query,
        top_k=req.top_k,
        enable_llm=req.enable_llm,
    )

    elapsed = (time.time() - t0) * 1000

    # Format merged results
    merged = []
    for r in result.get("merged", [])[:req.top_k]:
        chunk = r.get("chunk", {})
        text = chunk.get("text", "")
        merged.append(ChunkResult(
            chunk_id=chunk.get("chunk_id", ""),
            chunk_type=chunk.get("chunk_type", ""),
            module=chunk.get("module", ""),
            section_path=chunk.get("section_path", ""),
            section_title=chunk.get("section_title", ""),
            text_preview=text[:300] if text else "",
            score=round(r.get("score", 0), 4),
            sources=r.get("sources", []),
            has_table=chunk.get("has_table", False),
            has_image=chunk.get("has_image", False),
        ))

    return SearchResponse(
        query=result["query"],
        intent=result["intent"],
        merged=merged,
        evidence=result["evidence"],
        answer=result.get("answer"),
        usage=result.get("usage"),
        model=result.get("model", ""),
        retrieval_time_ms=round(elapsed, 2),
    )


@app.post("/search/stream")
async def search_stream(req: SearchRequest):
    """Execute retrieval and stream LLM answer tokens (SSE)."""
    global pipeline
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    if not pipeline.llm:
        raise HTTPException(status_code=400, detail="LLM not configured. Call /llm/configure first.")

    # Run retrieval (stages 1-8)
    result = pipeline.search(query=req.query, top_k=req.top_k, enable_llm=False)
    evidence = result["evidence"]

    async def generate():
        try:
            for token in pipeline.llm.answer_stream(
                evidence, req.query, result["intent"],
            ):
                yield f"data: {token}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/llm/configure")
async def configure_llm(req: LLMConfigRequest):
    """Configure LLM backend.

    Example:
        {"provider": "ark"}  # uses env ARK_API_KEY
        {"provider": "zhipu", "model": "glm-4-flash"}
        {"api_key": "sk-...", "base_url": "https://...", "model": "..."}
    """
    global pipeline
    if not pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    try:
        pipeline.configure_llm(
            api_key=req.api_key or None,
            base_url=req.base_url or None,
            model=req.model or None,
            provider=req.provider,
        )
        return {
            "status": "ok",
            "model": pipeline.llm.model if pipeline.llm else "none",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/modules")
async def list_modules():
    """List all modules in the knowledge graph."""
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    modules = []
    for eid, entity in pipeline.graph.entity_index.items():
        if entity.get("entity_type") == "module":
            modules.append({
                "name": entity.get("name", ""),
                "section_path": entity.get("section_path", ""),
                "entity_id": eid,
            })
    modules.sort(key=lambda x: x["name"])
    return {"count": len(modules), "modules": modules}


@app.get("/entities/search")
async def search_entities(
    q: str = Query(..., description="Search query"),
    entity_type: str = Query(default="", description="Entity type filter"),
):
    """Search entities in the knowledge graph."""
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    results = pipeline.graph.search_entities(q, entity_type=entity_type)
    return {
        "query": q,
        "count": len(results),
        "entities": results[:50],
    }
