"""BCM-RAG Retrieval — Full Retrieval Pipeline.

Implements the 9-stage pipeline from CLAUDE.md:
  Query → Intent Analysis → Graph Retrieval → Document Tree Localization
  → Vector Retrieval → Merge Candidates → Cross Encoder Rerank
  → Rule-Based Rerank → Context Compression → LLM Answer

All 9 stages implemented.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from retrieval.graph_retriever import GraphRetriever
from retrieval.vector_retriever import KeywordRetriever


class RetrievalPipeline:
    """End-to-end retrieval: query → graph + vector → merged + ranked results.

    Supports both BM25 (always available) and dense vector search (when embeddings built).
    Supports LLM-powered answer generation via OpenAI-compatible APIs.
    """

    def __init__(self):
        self.graph = GraphRetriever()
        self.vector = KeywordRetriever()
        self.dense = None          # DenseRetriever — loaded when embeddings available
        self.llm = None            # LLMAnswerGenerator — set via configure_llm()
        self.section_tree: dict = {}
        self._loaded = False

    # ---- Load --------------------------------------------------------------

    def load(
        self,
        kg_path: str | Path = "output/content_analysis/knowledge_graph.json",
        chunks_path: str | Path = "output/content_analysis/chunks.json",
        tree_path: str | Path = "output/content_analysis/section_tree.json",
        points_path: str | Path = "output/content_analysis/vector_points.json",
        use_dense: bool = True,
    ) -> "RetrievalPipeline":
        """Load all data sources.

        Args:
            kg_path: Path to knowledge graph JSON
            chunks_path: Path to chunks JSON (for BM25)
            tree_path: Path to section tree JSON
            points_path: Path to vector points JSON (for dense search)
            use_dense: Whether to attempt loading dense retriever
        """
        print("Loading retrieval pipeline...")

        self.graph.load(kg_path)
        print(f"  Graph: {self.graph.stats['nodes']} nodes, {self.graph.stats['edges']} edges")

        self.vector.load(chunks_path)
        print(f"  BM25:   {self.vector.stats['chunks']} chunks, {self.vector.stats['vocabulary']} terms")

        # Pre-load embedder once, share across dense retriever.
        # Detect model from vector_points.json if available, otherwise use default.
        embedder = None
        if use_dense and Path(points_path).exists():
            try:
                from retrieval.embedder import EmbeddingGenerator
                # Read model name from vector_points.json
                with open(points_path, 'r', encoding='utf-8') as f:
                    vp = json.load(f)
                stored_model = vp.get('model', '')
                stored_dim = vp.get('dim', 0)
                # Use stored model if it differs from default
                model_to_load = stored_model if stored_model else None
                embedder = EmbeddingGenerator(model_name=model_to_load)
                embedder.load()
                if stored_dim and embedder.dim != stored_dim:
                    print(f"  Embedder: dim mismatch ({embedder.dim} vs stored {stored_dim}), forcing match...")
                    # Force the correct model
                    embedder = EmbeddingGenerator(model_name=stored_model)
                    embedder.load()
            except Exception as e:
                print(f"  Embedder: not available ({e})")

        # Try loading dense retriever (with shared embedder)
        if embedder and Path(points_path).exists():
            try:
                from retrieval.dense_retriever import DenseRetriever
                self.dense = DenseRetriever()
                self.dense.load(points_path, embedder=embedder, auto_embed=True)
                print(f"  Dense:  {self.dense.stats['points']} points, {self.dense.stats['dim']}-dim ({self.dense.stats['model']})")
            except Exception as e:
                print(f"  Dense:  not available ({e})")

        if Path(tree_path).exists():
            with open(tree_path, "r", encoding="utf-8") as f:
                self.section_tree = json.load(f)
            print(f"  Tree:   {len(self.section_tree.get('nodes', {}))} sections")

        self._loaded = True
        print("  Ready.")
        return self

    def configure_llm(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str = "",
        **kwargs,
    ) -> "RetrievalPipeline":
        """Configure the LLM answer generator.

        Args:
            api_key: API key (defaults to env var)
            base_url: API base URL (defaults to env var)
            model: Model name (defaults to env var)
            provider: Provider shortcut ("ark", "zhipu", "deepseek")

        Example:
            pipeline.configure_llm(provider="ark")
            pipeline.configure_llm(provider="zhipu", model="glm-4-flash")
        """
        from retrieval.llm_answer import LLMAnswerGenerator
        self.llm = LLMAnswerGenerator(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider=provider,
            **kwargs,
        )
        print(f"LLM configured: {self.llm.model}")
        return self

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ---- Search ------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        enable_llm: bool = False,
        quality: str = "fast",
        # Improvement #1: LLM context compression
        use_llm_compress: bool = False,
        # Improvement #2: HyDE query rewriting
        enable_query_rewrite: bool = False,
        rewrite_strategy: str = "hyde",
        # Improvement #3: Structured evidence package
        use_structured_evidence: bool = False,
        # Improvement #4: LLM comparative fusion
        enable_llm_fusion: bool = False,
    ) -> dict:
        """Execute full retrieval pipeline.

        Args:
            query: User query string
            top_k: Number of top results to return
            enable_llm: Whether to call LLM for answer (requires configure_llm())
            quality: "fast" or "accurate" (enables more expensive steps)
            use_llm_compress: Use LLM to compress chunks into structured summary
            enable_query_rewrite: Use HyDE to rewrite query for better recall
            rewrite_strategy: "hyde", "query2doc", or "keywords"
            use_structured_evidence: Build structured evidence with dependency chains
            enable_llm_fusion: Use LLM to fuse graph + vector candidates

        Returns:
            {
                "query": str,
                "intent": {...},
                "graph_results": [...],
                "tree_sections": [...],
                "vector_results": [...],
                "merged": [...],      # merged + ranked candidates
                "evidence": str,      # compressed context for LLM
                "answer": str | None, # LLM-generated answer (if enable_llm)
                "usage": dict | None, # Token usage from LLM
            }
        """
        if not self._loaded:
            raise RuntimeError("Pipeline not loaded. Call .load() first.")

        # Guard: empty query
        if not query or not query.strip():
            return {
                "query": query,
                "intent": {"query_type": "empty", "question_type": "factual", "keywords": []},
                "graph_results": [],
                "tree_sections": [],
                "vector_results": [],
                "merged": [],
                "evidence": "# 查询为空\n\n请输入有效的查询内容。",
                "answer": None,
                "usage": None,
            }

        # ---- Stage 1: Intent Analysis --------------------------------------
        intent = self._analyze_intent(query)

        # ---- Stage 1.5: Query Rewriting (HyDE / query2doc) -------------------
        # Improvement #2: Rewrite query to improve retrieval recall.
        # LLM generates a hypothetical document fragment, then the augmented
        # query is used for Stages 2-4 retrieval.
        rewritten_query = query
        query_rewrite_info: dict | None = None
        if enable_query_rewrite and self.llm:
            from retrieval.query_rewriter import QueryRewriter
            rewriter = QueryRewriter(self.llm)
            rewrite_result = rewriter.rewrite(
                query, intent, strategy=rewrite_strategy
            )
            rewritten_query = rewrite_result["augmented_query"]
            query_rewrite_info = {
                "enabled": True,
                "strategy": rewrite_result["strategy"],
                "hypothetical_doc": rewrite_result.get("hypothetical_doc"),
                "augmented_query": rewritten_query
                if rewritten_query != query
                else None,
            }

        # ---- Stage 2: Graph Retrieval --------------------------------------
        graph_results = self._graph_retrieve(rewritten_query, intent)

        # ---- Stage 3: Document Tree Localization ---------------------------
        tree_sections = self._tree_localize(graph_results)

        # ---- Stage 4: Vector Retrieval -------------------------------------
        vector_results = self._vector_retrieve(rewritten_query, intent, graph_results)

        # ---- Stage 5: Merge Candidates -------------------------------------
        merged = self._merge_candidates(graph_results, vector_results, tree_sections, intent)

        # ---- Stage 5.5: LLM Comparative Fusion -------------------------------
        # Improvement #4: LLM compares candidates from graph vs vector retrieval
        # and produces a reasoned fusion ranking.
        if enable_llm_fusion and self.llm and graph_results and vector_results:
            from retrieval.llm_fusion import LLMFusion
            fuser = LLMFusion(self.llm)
            graph_candidates = self._graph_results_to_candidates(graph_results)
            merged = fuser.fuse(
                graph_candidates=graph_candidates,
                vector_candidates=vector_results,
                query=query,
                intent=intent,
                top_k=top_k * 2,  # Keep more candidates before reranking
            )

        # ---- Stage 6: Cross Encoder Rerank ----------------------------------
        self._quality_mode = (quality == "accurate")
        reranked = self._rerank_semantic(merged, query)

        # ---- Stage 7: Rule-Based Rerank ------------------------------------
        reranked = self._rerank_rules(reranked, intent, graph_results)

        # ---- Stage 8: Context Compression ----------------------------------
        evidence = self._compress_context(
            reranked[:top_k], query, intent,
            graph_results=graph_results,
            use_llm_compress=use_llm_compress,
            use_structured_evidence=use_structured_evidence,
        )

        result = {
            "query": query,
            "intent": intent,
            "graph_results": graph_results,
            "tree_sections": tree_sections,
            "vector_results": vector_results[:5],
            "merged": reranked[:top_k],
            "evidence": evidence,
            "answer": None,
            "usage": None,
        }

        # Attach query rewrite info if used
        if query_rewrite_info:
            result["query_rewrite"] = query_rewrite_info

        # ---- Stage 9: LLM Answer -------------------------------------------
        if enable_llm:
            if not self.llm:
                result["answer"] = "[LLM not configured] Call pipeline.configure_llm() first."
                result["usage"] = {}
            else:
                llm_result = self.llm.answer(evidence, query, intent)
                result["answer"] = llm_result["answer"]
                result["usage"] = llm_result.get("usage", {})
                result["model"] = llm_result.get("model", "")

        return result

    # ---- Stage 1: Intent Analysis ------------------------------------------

    # Module name aliases: common terms → canonical module name
    _MODULE_ALIASES = {
        "bcm": "VMM",           # BCM system → VMM (Vehicle Mode Management)
        "车身控制": "VMM",
        "电源管理": "VMM",
        "灯光": "ExteriorLight",
        "车灯": "ExteriorLight",
        "大灯": "ExteriorLight",
        "车窗": "Window",
        "门锁": "Lock",
        "雨刮": "Wiper",
        "雨刷": "Wiper",
        "钥匙": "RemoteControl",
        "无钥匙": "RemoteControl",
        "peps": "RemoteControl",
        "阅读灯": "InteriorLight",
        "车内灯": "InteriorLight",
        "顶灯": "InteriorLight",
        "防盗": "TheftProtection",
        "atws": "TheftProtection",
    }

    # Query patterns that indicate signal definition lookup
    _SIGNAL_DEF_PATTERNS = re.compile(
        r"取值|定义|编码|信号.*有哪些|CAN.*ID|报文|信号名称|信号类型|PIN",
        re.IGNORECASE,
    )

    # Query patterns that indicate state transition lookup
    _TRANSITION_PATTERNS = re.compile(
        r"迁移|转移|进入.*条件|如何.*进入|怎么.*进入|触发.*条件|前置.*条件",
        re.IGNORECASE,
    )

    def _analyze_intent(self, query: str) -> dict:
        """Analyze query intent: module, function, state, signal, fault, or general.

        Enhanced with:
          - Module name aliases (BCM → VMM, 灯光 → ExteriorLight, etc.)
          - Signal→module routing via KG BELONGS_TO lookup
          - Query type hints (signal_def, transition, etc.)
        """
        intent = {
            "query_type": "general",
            "modules": [],
            "functions": [],
            "states": [],
            "signals": [],
            "faults": [],
            "parameters": [],
            "keywords": [],
            "question_type": "factual",
            "hint_signal_def": False,
            "hint_transition": False,
        }

        q = query.lower()

        # Detect query type — diagnostic first (may contain "how" words too)
        if any(w in q for w in ("故障", "诊断", "失效", "错误", "dtc", "fault", "error")):
            intent["question_type"] = "diagnostic"
        elif any(w in q for w in ("如何", "怎么", "为什么", "how", "why", "流程", "过程")):
            intent["question_type"] = "reasoning"

        # Detect query hints
        if self._SIGNAL_DEF_PATTERNS.search(query):
            intent["hint_signal_def"] = True
        if self._TRANSITION_PATTERNS.search(query):
            intent["hint_transition"] = True

        # Extract keywords first
        # Chinese phrases
        cn_phrases = re.findall(r"[一-鿿]{2,8}", query)
        intent["keywords"] = cn_phrases[:10]
        # English identifiers
        en_ids = re.findall(r"[A-Z][A-Za-z0-9_]+", query)
        intent["keywords"].extend(en_ids[:5])
        # Module aliases
        for alias, module in self._MODULE_ALIASES.items():
            if alias in q:
                intent["keywords"].append(module)
        intent["keywords"] = intent["keywords"][:15]

        # Search graph for matching entities — try full query AND individual keywords
        entity_types = ["module", "function", "state", "signal", "fault", "parameter"]
        seen_names = {et: set() for et in entity_types}

        # Search terms: try English identifiers first (exact + specific),
        # then longer Chinese phrases, then shorter keywords
        en_ids_sorted = sorted(en_ids, key=len, reverse=True) if en_ids else []
        cn_sorted = sorted(cn_phrases, key=len, reverse=True) if cn_phrases else []
        search_terms = [query] + en_ids_sorted[:5] + cn_sorted[:8]

        for term in search_terms:
            if len(term) < 2:
                continue
            for etype in entity_types:
                # For signals/functions: try exact name match first (more precise)
                exact_matches = self.graph.get_by_name(term, etype)
                for m in exact_matches[:3]:
                    name = m.get("name", "")
                    if name and name not in seen_names[etype]:
                        seen_names[etype].add(name)
                        self._add_to_intent(intent, etype, name)

                # Then substring search (broader)
                matches = self.graph.search_entities(term, entity_type=etype)
                for m in matches[:5]:
                    name = m.get("name", "")
                    if name not in seen_names[etype]:
                        seen_names[etype].add(name)
                        self._add_to_intent(intent, etype, name)

        # Signal→module routing: use signal entity's own module field
        for sig_name in intent["signals"]:
            sig_entities = self.graph.get_by_name(sig_name, "signal")
            for se in sig_entities[:3]:
                # Direct module attribution from entity metadata
                sig_module = se.get("module", "")
                if sig_module and sig_module not in intent["modules"]:
                    intent["modules"].append(sig_module)
                # Also check BELONGS_TO for cross-module references
                neighbors = self.graph.expand(se["entity_id"], hops=1)
                for n in neighbors:
                    neighbor_mod = n.get("entity", {}).get("module", "")
                    if neighbor_mod and neighbor_mod not in intent["modules"]:
                        intent["modules"].append(neighbor_mod)

        return intent

    @staticmethod
    def _add_to_intent(intent: dict, etype: str, name: str) -> None:
        """Add an entity name to the appropriate intent list."""
        key_map = {
            "module": "modules",
            "function": "functions",
            "state": "states",
            "signal": "signals",
            "fault": "faults",
            "parameter": "parameters",
        }
        key = key_map.get(etype, "")
        if key and key in intent:
            intent[key].append(name)

    # ---- Stage 2: Graph Retrieval ------------------------------------------

    def _graph_retrieve(self, query: str, intent: dict) -> list[dict]:
        """Retrieve entities and their neighbors from the knowledge graph."""
        results: list[dict] = []
        seen: set = set()

        # Start from matched entities
        start_entities = []
        for sig in intent.get("signals", []):
            for e in self.graph.get_by_name(sig, "signal"):
                start_entities.append(e["entity_id"])
        for func in intent.get("functions", []):
            for e in self.graph.get_by_name(func, "function"):
                start_entities.append(e["entity_id"])
        for state in intent.get("states", []):
            for e in self.graph.get_by_name(state, "state"):
                start_entities.append(e["entity_id"])
        for mod in intent.get("modules", []):
            for e in self.graph.get_by_name(mod, "module"):
                start_entities.append(e["entity_id"])

        # Keyword search fallback
        if not start_entities:
            for kw in intent.get("keywords", []):
                matches = self.graph.search_entities(kw)
                for m in matches[:3]:
                    start_entities.append(m["entity_id"])

        # Expand 1-hop from each start entity
        for eid in start_entities[:10]:
            if eid in seen:
                continue
            seen.add(eid)

            entity = self.graph.get_entity(eid)
            if entity:
                results.append({"entity": entity, "distance": 0, "relationship": "self"})

            neighbors = self.graph.expand(eid, hops=1)
            for n in neighbors:
                if n["entity"].get("entity_id", "") not in seen:
                    seen.add(n["entity"].get("entity_id", ""))
                    results.append(n)

        return results

    # ---- Stage 3: Document Tree Localization --------------------------------

    def _tree_localize(self, graph_results: list[dict]) -> list[dict]:
        """Find document sections that contain graph-matched entities.

        Enhanced: matches parent sections too (e.g. entity at 2.3.4.1 also
        matches 2.3.4 and 2.3), returns deeper sections first.
        """
        sections = []
        section_ids: set = set()
        nodes = self.section_tree.get("nodes", {})

        for gr in graph_results:
            entity = gr.get("entity", {})
            section_path = entity.get("section_path", "")
            if not section_path:
                continue

            # Match exact section AND all parent prefix sections
            parts = section_path.split(".")
            for i in range(len(parts), 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in section_ids:
                    continue
                section_ids.add(prefix)

                for nid, node in nodes.items():
                    if node.get("number") == prefix:
                        sections.append({
                            "section_id": nid,
                            "number": node.get("number"),
                            "title": node.get("title", ""),
                            "level": node.get("level", 0),
                            "depth": len(prefix.split(".")),
                            "page": node.get("page", -1),
                            "is_exact": (prefix == section_path),
                        })
                        break

        # Sort: exact matches first, then by depth (deeper first)
        sections.sort(key=lambda s: (not s.get("is_exact", False), -s.get("depth", 0)))
        return sections

    # ---- Stage 4: Vector Retrieval -----------------------------------------

    def _vector_retrieve(
        self, query: str, intent: dict, graph_results: list[dict],
    ) -> list[dict]:
        """Hybrid vector retrieval: dense (BGE) + sparse (BM25).

        Optimizations:
          1. KG Query Expansion: add module names and related terms to query
          2. Signal-to-module routing: when signal found, prioritize its module
          3. Two-stage: broad search → module-filtered re-search
        """
        # Collect entity IDs from graph results for hybrid search
        entity_ids = [
            gr.get("entity", {}).get("entity_id", "")
            for gr in graph_results
            if gr.get("entity", {}).get("entity_id")
        ]

        # Module filter from intent
        filter_module = intent.get("modules", [None])[0] if intent.get("modules") else ""

        if self.dense and self.dense.is_loaded:
            # Dense search with entity boost
            dense_results = self.dense.search(query, top_k=20)
            dense_results = self.dense.hybrid_search(query, entity_ids, top_k=20)

            # Also get BM25 results for fusion
            bm25_results = self.vector.hybrid_search(query, entity_ids, top_k=10)

            # Reciprocal Rank Fusion
            return self._fuse_results(dense_results, bm25_results)
        else:
            # BM25-only fallback
            return self.vector.hybrid_search(query, entity_ids, top_k=20)

    def _fuse_results(
        self,
        dense_results: list[dict],
        bm25_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion: combine dense and sparse rankings."""
        scores: dict[str, float] = {}
        chunks: dict[str, dict] = {}

        for rank, r in enumerate(dense_results):
            cid = r["chunk"].get("chunk_id", str(rank))
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
            chunks[cid] = r

        for rank, r in enumerate(bm25_results):
            cid = r["chunk"].get("chunk_id", str(rank))
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
            if cid not in chunks:
                chunks[cid] = r

        # Merge and sort
        merged = []
        for cid, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            entry = chunks[cid]
            entry["score"] = score
            merged.append(entry)

        return merged[:20]

    # ---- Stage 5: Merge Candidates -----------------------------------------

    def _merge_candidates(
        self,
        graph_results: list[dict],
        vector_results: list[dict],
        tree_sections: list[dict],
        intent: dict | None = None,
    ) -> list[dict]:
        """Merge graph + vector candidates, deduplicate, assign initial scores.

        Enhanced with:
          - Signal→module routing: chunks from signal's owning module get +40% boost
          - Signal definition boost: signal_table chunks get +25% when query asks for signal values
          - Child section preference: deeper section matches get higher tree_support
        """
        merged: dict[str, dict] = {}  # chunk_id → merged entry

        # Vector results as base
        for i, vr in enumerate(vector_results):
            chunk = vr["chunk"]
            cid = chunk.get("chunk_id", str(i))
            merged[cid] = {
                "chunk": chunk,
                "score": vr["score"],
                "graph_support": 0,
                "tree_support": 0,
                "sources": ["vector"],
            }

        # Collect module information from graph results
        graph_entity_ids: set = set()
        signal_modules: set = set()  # modules that OWN the matched signals
        for gr in graph_results:
            eid = gr.get("entity", {}).get("entity_id", "")
            if eid:
                graph_entity_ids.add(eid)
            # Track signal→module (BELONGS_TO) for routing
            if gr.get("relationship") == "belongs_to":
                module_name = gr.get("entity", {}).get("name", "")
                if module_name:
                    signal_modules.add(module_name)

        # Also use intent modules
        if intent:
            signal_modules.update(intent.get("modules", []))
            hint_signal_def = intent.get("hint_signal_def", False)
        else:
            hint_signal_def = False

        graph_sections = {ts.get("number", "") for ts in tree_sections}

        for cid, entry in merged.items():
            chunk = entry["chunk"]
            chunk_section = chunk.get("section_path", "")
            chunk_module = chunk.get("module", "")
            chunk_type = chunk.get("chunk_type", "")

            # Entity-level boost: chunk contains specific graph-matched entity IDs
            chunk_entities = set(chunk.get("entities", []))
            entity_overlap = len(chunk_entities & graph_entity_ids)
            if entity_overlap > 0:
                entry["graph_support"] = entity_overlap
                entry["score"] *= 1 + 0.3 * entity_overlap
                if "graph" not in entry["sources"]:
                    entry["sources"].append("graph")

            # Signal→module routing: chunk from signal's owning module gets strong boost
            if chunk_module and chunk_module in signal_modules:
                boost = 1.6
                # When query asks for signal definition, owning module is even more important
                if hint_signal_def and chunk_type == "signal_table":
                    boost = 2.5
                entry["score"] *= boost
                entry["tree_support"] += 1
                if "graph" not in entry["sources"]:
                    entry["sources"].append("graph")

            # Signal definition query hint → boost signal_table type chunks
            if hint_signal_def and chunk_type == "signal_table":
                entry["score"] *= 1.35

            # Penalize chunks that just reference a signal (not define it)
            # when the query explicitly asks for signal definition
            if hint_signal_def and chunk_type == "general_text" and chunk_module not in signal_modules:
                entry["score"] *= 0.85

            # State transition query hint → boost state_transition chunks
            if intent and intent.get("hint_transition") and chunk_type == "state_transition":
                entry["score"] *= 1.3

            # Tree/section match (prefer deeper/more specific sections)
            if chunk_section in graph_sections:
                section_depth = chunk_section.count(".") + 1
                entry["tree_support"] = section_depth  # deeper = higher
                entry["score"] *= 1.2
                if "tree" not in entry["sources"]:
                    entry["sources"].append("tree")

        # Dedup by text similarity
        result_list = list(merged.values())
        result_list.sort(key=lambda x: x["score"], reverse=True)

        # Remove near-duplicates
        deduped = []
        seen_sigs: set = set()
        for entry in result_list:
            text = entry["chunk"].get("text", "")
            sig = text[:100]
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                deduped.append(entry)

        return deduped

    # ---- Stage 6: Semantic Rerank ------------------------------------------

    def _rerank_semantic(self, candidates: list[dict], query: str) -> list[dict]:
        """Semantic reranking: Cross-Encoder (BGE-Reranker) with Jaccard fallback.

        Uses CrossEncoderReranker by default when available.
        Falls back to Jaccard similarity only if Cross-Encoder fails to load.
        The reranker is lazy-loaded on first call.
        """
        # Lazy-init Cross-Encoder reranker
        if not hasattr(self, "_x_reranker"):
            try:
                from retrieval.reranker import CrossEncoderReranker
                self._x_reranker = CrossEncoderReranker()
                self._x_reranker.load()
                if self._x_reranker.is_loaded:
                    print("  Stage 6 (Rerank): Cross-Encoder")
                else:
                    print("  Stage 6 (Rerank): Jaccard fallback")
            except Exception as e:
                print(f"  Stage 6 (Rerank): Cross-Encoder unavailable ({e}), Jaccard fallback")
                self._x_reranker = None

        if self._x_reranker and self._x_reranker.is_loaded:
            # Cross-Encoder: re-rank top 12 candidates with semantic model.
            # Rest stay with their original scores (usually 20 total).
            limit = min(12, len(candidates))
            top_n = candidates[:limit]
            rest = candidates[limit:]
            reranked = self._x_reranker.rerank_safe(query, top_n)
            return reranked + rest

        # Jaccard fallback (always available, complementary lexical signal)
        query_terms = set(KeywordRetriever._tokenize(query))
        for entry in candidates:
            text = entry["chunk"].get("embedding_text", "") or entry["chunk"].get("text", "")
            chunk_terms = set(KeywordRetriever._tokenize(text))
            if query_terms and chunk_terms:
                overlap = len(query_terms & chunk_terms)
                union = len(query_terms | chunk_terms)
                jaccard = overlap / union if union > 0 else 0
                entry["semantic_score"] = jaccard
                entry["score"] = entry["score"] * (0.5 + 0.5 * jaccard)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # ---- Stage 7: Rule-Based Rerank ----------------------------------------

    def _rerank_rules(
        self, candidates: list[dict], intent: dict, graph_results: list[dict],
    ) -> list[dict]:
        """Rule-based scoring: same module, same function, same state bonuses."""
        target_modules = set(intent.get("modules", []))
        target_functions = set(intent.get("functions", []))
        target_states = set(intent.get("states", []))
        target_signals = set(intent.get("signals", []))

        graph_modules = {
            gr.get("entity", {}).get("module", "")
            for gr in graph_results if gr.get("entity", {}).get("module")
        }
        target_modules |= graph_modules

        for entry in candidates:
            chunk = entry["chunk"]
            rule_bonus = 0.0

            # Same module
            if chunk.get("module", "") in target_modules:
                rule_bonus += 0.2

            # Contains target signal names
            chunk_signals = set(chunk.get("signals", []))
            if chunk_signals & target_signals:
                rule_bonus += 0.15 * len(chunk_signals & target_signals)

            # Contains target state names
            chunk_states = set(chunk.get("states", []))
            if chunk_states & target_states:
                rule_bonus += 0.15 * len(chunk_states & target_states)

            # Has table (structured data is high value)
            if chunk.get("has_table"):
                rule_bonus += 0.1

            # Signal definition hint → extra boost for signal_table
            if intent.get("hint_signal_def") and chunk.get("chunk_type") == "signal_table":
                rule_bonus += 0.2

            # Transition hint → extra boost for state_transition/state_machine
            if intent.get("hint_transition") and chunk.get("chunk_type") in (
                "state_transition", "state_machine",
            ):
                rule_bonus += 0.2

            # Has image (visual evidence)
            if chunk.get("has_image"):
                rule_bonus += 0.05

            entry["rule_score"] = rule_bonus
            entry["score"] = entry["score"] * (1 + rule_bonus)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # ---- Stage 8: Context Compression --------------------------------------

    def _compress_context(
        self,
        top_results: list[dict],
        query: str,
        intent: dict,
        graph_results: list[dict] | None = None,
        use_llm_compress: bool = False,
        use_structured_evidence: bool = False,
    ) -> str:
        """Compress top results into an Evidence Package.

        Supports three modes:
          1. Default: simple truncation + dedup (original behavior)
          2. Structured: build evidence with dependency chains + state transitions
          3. LLM Compress: LLM rewrites chunks into structured summary

        Args:
            top_results: Stage 7 reranked candidates
            query: Original user query
            intent: Intent analysis dict
            graph_results: Graph retrieval results (for structured evidence)
            use_llm_compress: Use LLM for compression
            use_structured_evidence: Build structured evidence package
        """
        if not top_results:
            return f"# 查询: {query}\n\n未找到相关内容。"

        # ---- Mode 3: Structured + LLM Compress -------------------------------
        if use_structured_evidence and use_llm_compress and self.llm and graph_results:
            from retrieval.evidence_builder import EvidenceBuilder
            from retrieval.context_compressor import ContextCompressor

            builder = EvidenceBuilder()
            structured = builder.build(
                graph_results=graph_results,
                merged_candidates=top_results,
                intent=intent,
                query=query,
            )
            formatted = builder.format_for_llm(structured)

            compressor = ContextCompressor(self.llm)
            return compressor.compress_structured(
                structured_evidence=formatted,
                query=query,
                intent=intent,
            )

        # ---- Mode 2: Structured Evidence Only --------------------------------
        if use_structured_evidence and graph_results:
            from retrieval.evidence_builder import EvidenceBuilder

            builder = EvidenceBuilder()
            structured = builder.build(
                graph_results=graph_results,
                merged_candidates=top_results,
                intent=intent,
                query=query,
            )
            return builder.format_for_llm(structured)

        # ---- Mode 1b: LLM Compress Only --------------------------------------
        if use_llm_compress and self.llm:
            from retrieval.context_compressor import ContextCompressor

            compressor = ContextCompressor(self.llm)
            return compressor.compress(
                candidates=top_results,
                query=query,
                intent=intent,
                graph_results=graph_results or [],
            )

        # ---- Mode 1a: Default (original behavior) ----------------------------

        parts = [f"# 查询: {query}"]
        parts.append(f"# 意图: {intent.get('question_type', 'unknown')}")

        # Modules and functions involved
        modules = set()
        functions = set()
        states = set()
        signals = set()

        for r in top_results:
            chunk = r["chunk"]
            if chunk.get("module"):
                modules.add(chunk["module"])
            for s in chunk.get("signals", [])[:5]:
                signals.add(s)
            for s in chunk.get("states", [])[:5]:
                states.add(s)

        if modules:
            parts.append(f"\n## 涉及模块\n{', '.join(sorted(modules))}")
        if signals:
            parts.append(f"\n## 相关信号\n{', '.join(sorted(signals)[:10])}")
        if states:
            parts.append(f"\n## 相关状态\n{', '.join(sorted(states)[:10])}")

        # Compressed evidence (top 5 chunks, deduped)
        parts.append(f"\n## 证据片段 (Top {min(5, len(top_results))})")
        seen_texts = set()
        count = 0
        for r in top_results:
            chunk = r["chunk"]
            text = chunk.get("text", "")[:800]
            # Dedup
            sig = text[:200]
            if sig in seen_texts:
                continue
            seen_texts.add(sig)

            count += 1
            parts.append(f"\n### 片段 {count} [{chunk.get('chunk_type', '?')}]")
            parts.append(f"章节: {chunk.get('section_path', '?')} | 模块: {chunk.get('module', '?')}")
            parts.append(f"得分: {r['score']:.3f} | 来源: {r.get('sources', [])}")
            if chunk.get("has_image"):
                # Include image storage paths for LLM context
                img_paths = [ref.get("storage_path", "") for ref in chunk.get("image_refs", [])]
                if img_paths:
                    parts.append(f"图片: {', '.join(img_paths[:2])}")
            parts.append(f"\n{text}")

            if count >= 5:
                break

        return "\n".join(parts)

    # ---- Helper: Graph Results to Candidates ---------------------------------

    def _graph_results_to_candidates(
        self, graph_results: list[dict],
    ) -> list[dict]:
        """Convert graph retrieval results to candidate format.

        Wraps graph entity data into a structure compatible with
        vector candidates so LLMFusion can compare them.

        Args:
            graph_results: Results from _graph_retrieve()

        Returns:
            List of candidate dicts with "chunk" and "score" keys
        """
        candidates: list[dict] = []

        for i, item in enumerate(graph_results):
            entity = item.get("entity", {})
            rel_type = item.get("relationship", "")
            distance = item.get("distance", 1)

            # Build a text description from entity data
            entity_name = entity.get("name", "")
            entity_type = entity.get("entity_type", "")
            description = entity.get("description", "")
            module = entity.get("module", "")
            section_path = entity.get("section_path", "")

            text_parts = []
            if entity_name:
                text_parts.append(f"[{entity_type}] {entity_name}")
            if module:
                text_parts.append(f"模块: {module}")
            if section_path:
                text_parts.append(f"章节: {section_path}")
            if description:
                text_parts.append(description)
            if rel_type:
                connected = entity.get("target", entity.get("related_to", ""))
                if connected:
                    text_parts.append(f"关系: --[{rel_type}]--> {connected}")
                else:
                    text_parts.append(f"关系: {rel_type}")

            # Score: closer entities (fewer hops) get higher scores
            score = 0.8 / max(distance, 1)

            chunk_data = {
                "chunk_id": entity.get("entity_id", f"graph_{i}"),
                "chunk_type": entity_type or "graph_entity",
                "module": module,
                "section_path": section_path,
                "text": "\n".join(text_parts),
                "entities": [entity.get("entity_id", "")] if entity.get("entity_id") else [],
                "signals": [entity_name] if entity_type == "signal" else [],
                "states": [entity_name] if entity_type == "state" else [],
            }

            candidates.append(
                {
                    "chunk": chunk_data,
                    "score": score,
                    "sources": ["graph"],
                    "graph_support": 1,
                    "tree_support": 0,
                }
            )

        return candidates
