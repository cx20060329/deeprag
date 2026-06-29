"""DeepRAG API — FastAPI application with API key authentication.

REST API for the DeepRAG retrieval system.
Supports domain-configurable document RAG.

Usage:
    # Set API keys via env var (comma-separated):
    DEEPRAG_API_KEYS=sk-deeprag-xxxx,sk-deeprag-yyyy

    python -m api.main
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Client usage:
    curl -H "Authorization: Bearer sk-deeprag-xxxx" \\
         -H "Content-Type: application/json" \\
         -d '{"query":"What is GlobalClose?"}' \\
         http://localhost:8000/search
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from retrieval import RetrievalPipeline
from domain import load_domain_config, list_domains, register_domain_config

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)

# Load valid API keys from env var (comma-separated)
# If not set, authentication is disabled (dev mode)
_VALID_API_KEYS: set[str] = set()
_raw = os.getenv("DEEPRAG_API_KEYS", "")
if _raw:
    _VALID_API_KEYS = {k.strip() for k in _raw.split(",") if k.strip()}
_AUTH_ENABLED = bool(_VALID_API_KEYS)


def require_api_key(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    """Validate API key from Authorization: Bearer header.

    If DEEPRAG_API_KEYS is not set, authentication is skipped (dev mode).
    """
    if not _AUTH_ENABLED:
        return None  # Dev mode — no auth required

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing API key. Use Authorization: Bearer <key>")

    token = credentials.credentials
    if token not in _VALID_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return token


def generate_api_key() -> str:
    """Generate a new API key."""
    return "sk-deeprag-" + secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

pipeline: RetrievalPipeline | None = None
_current_domain_name: str = "bcm"

from config import CONTENT_ANALYSIS_DIR
OUTPUT_DIR = Path(os.getenv("DEEPRAG_OUTPUT_DIR") or os.getenv("BCM_OUTPUT_DIR", str(CONTENT_ANALYSIS_DIR)))


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
    auth_enabled: bool
    stats: dict


class LLMConfigRequest(BaseModel):
    api_key: str = Field(default="", description="API key (defaults to env var)")
    base_url: str = Field(default="", description="API base URL")
    model: str = Field(default="", description="Model name")
    provider: str = Field(default="", description="Provider: ark, zhipu, deepseek")


class DomainInfoResponse(BaseModel):
    name: str
    display_name: str
    description: str
    entity_types: list[str]


class DomainRegisterRequest(BaseModel):
    name: str = Field(..., description="Domain name")
    config: dict = Field(default_factory=dict, description="Domain config dict")


class ApiKeyResponse(BaseModel):
    api_key: str
    note: str = "Save this key — it won't be shown again"


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load pipeline on startup, cleanup on shutdown."""
    global pipeline, _current_domain_name
    domain = load_domain_config(_current_domain_name)
    print("=" * 50)
    print(f"DeepRAG API Server v0.3.0")
    print(f"Domain: {domain.display_name}")
    print(f"Auth: {'enabled' if _AUTH_ENABLED else 'DISABLED (dev mode)'}")
    if _AUTH_ENABLED:
        print(f"Valid keys: {len(_VALID_API_KEYS)}")
    print("=" * 50)
    pipeline = (
        RetrievalPipeline(domain=domain)
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
    title="DeepRAG API",
    description="Domain-Adaptable Enterprise RAG Framework — API with key authentication",
    version="0.3.0",
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
# Routes — public (no auth required)
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check — no auth required."""
    if not pipeline or not pipeline.is_loaded:
        return HealthResponse(status="loading", loaded=False, auth_enabled=_AUTH_ENABLED, stats={})

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

    return HealthResponse(status="ok", loaded=True, auth_enabled=_AUTH_ENABLED, stats=stats)


# ---------------------------------------------------------------------------
# Routes — API key required
# ---------------------------------------------------------------------------

@app.get("/domains")
async def list_available_domains(_=Depends(require_api_key)):
    """List all available domain configs."""
    domains = []
    for name in list_domains():
        try:
            d = load_domain_config(name)
            domains.append(DomainInfoResponse(
                name=d.name, display_name=d.display_name,
                description=d.description, entity_types=d.entity_types,
            ))
        except Exception:
            pass
    return {"domains": [d.model_dump() for d in domains]}


@app.post("/domains/register")
async def register_domain(req: DomainRegisterRequest, _=Depends(require_api_key)):
    """Register a custom domain config."""
    from domain.config import DomainConfig
    config = DomainConfig.from_dict(req.config)
    register_domain_config(config)
    return {"status": "ok", "name": req.name}


@app.get("/key/generate", response_model=ApiKeyResponse)
async def generate_key(_=Depends(require_api_key)):
    """Generate a new API key (requires existing valid key).

    The generated key only works for the current server session.
    To persist, add it to DEEPRAG_API_KEYS env var and restart.
    """
    new_key = generate_api_key()
    _VALID_API_KEYS.add(new_key)
    return ApiKeyResponse(
        api_key=new_key,
        note="Save this key — it won't be shown again. Add to DEEPRAG_API_KEYS for persistence.",
    )


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest, _=Depends(require_api_key)):
    """Execute full retrieval pipeline. Requires API key."""
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
async def search_stream(req: SearchRequest, _=Depends(require_api_key)):
    """Execute retrieval and stream LLM answer tokens (SSE). Requires API key."""
    global pipeline
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    if not pipeline.llm:
        raise HTTPException(status_code=400, detail="LLM not configured. Call /llm/configure first.")

    result = pipeline.search(query=req.query, top_k=req.top_k, enable_llm=False)
    evidence = result["evidence"]

    async def generate():
        try:
            for token in pipeline.llm.answer_stream(evidence, req.query, result["intent"]):
                yield f"data: {token}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/llm/configure")
async def configure_llm(req: LLMConfigRequest, _=Depends(require_api_key)):
    """Configure LLM backend. Requires API key."""
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
        return {"status": "ok", "model": pipeline.llm.model if pipeline.llm else "none"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/modules")
async def list_modules(_=Depends(require_api_key)):
    """List all modules in the knowledge graph. Requires API key."""
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
    _=Depends(require_api_key),
):
    """Search entities in the knowledge graph. Requires API key."""
    if not pipeline or not pipeline.is_loaded:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet")

    results = pipeline.graph.search_entities(q, entity_type=entity_type)
    return {"query": q, "count": len(results), "entities": results[:50]}
