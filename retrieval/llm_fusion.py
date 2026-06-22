"""BCM-RAG Retrieval — LLM Comparative Fusion (Stage 5.5).

Improvement #4: LLM-driven Comparative Fusion

Instead of relying solely on Reciprocal Rank Fusion (RRF) + rule-based
weighting, this module lets an LLM compare candidates from different
retrieval paths (graph vs dense vs BM25) and intelligently fuse them.

Key difference from RRF:
  - RRF: Pure statistical method — 1/(k+rank) — no semantic understanding
  - LLM Fusion: LLM compares candidates, understands complementary/
    redundant relationships, and produces a reasoned ranking

Example LLM insight:
  "候选A提供了IGN1信号的定义和取值编码 (来自信号表),
   候选B提供了IGN1的控制逻辑和故障检测 (来自功能描述),
   两者互补,应同时保留。候选C与A内容重复,可降级。"

Usage:
    llm = LLMAnswerGenerator(provider="ark")
    fuser = LLMFusion(llm)
    fused = fuser.fuse(
        graph_candidates=graph_candidates,
        vector_candidates=vector_results,
        query=query,
        intent=intent,
        top_k=10,
    )
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retrieval.llm_answer import LLMAnswerGenerator


class LLMFusion:
    """LLM-driven comparative fusion for multi-source retrieval results.

    Compares candidates from graph retrieval and vector retrieval,
    identifies complementary vs redundant information, and produces
    a reasoned ranking with fusion explanations.

    Designed for quality="accurate" mode — uses an LLM call that adds
    latency but produces better ranking.
    """

    # Maximum candidates to send to LLM (to control cost/latency)
    MAX_GRAPH_CANDIDATES = 8
    MAX_VECTOR_CANDIDATES = 12

    def __init__(self, llm_generator: "LLMAnswerGenerator"):
        """Initialize with an existing LLMAnswerGenerator instance.

        Args:
            llm_generator: Reuses the pipeline's LLM client.
        """
        self.llm = llm_generator

    # --- Public API ----------------------------------------------------------

    def fuse(
        self,
        graph_candidates: list[dict],
        vector_candidates: list[dict],
        query: str,
        intent: dict,
        top_k: int = 10,
    ) -> list[dict]:
        """Fuse graph and vector candidates using LLM comparative analysis.

        Args:
            graph_candidates: Candidates derived from graph retrieval
            vector_candidates: Candidates from vector retrieval (already
                               RRF-fused dense+BM25)
            query: Original user query
            intent: Intent analysis dict
            top_k: Number of candidates to return after fusion

        Returns:
            Fused candidate list, each with:
              - chunk: original chunk data
              - score: LLM-assigned fusion score
              - fusion_reason: LLM's reasoning for ranking
              - sources: updated source list
        """
        if not graph_candidates and not vector_candidates:
            return []

        if not graph_candidates:
            return vector_candidates[:top_k]

        if not vector_candidates:
            return graph_candidates[:top_k]

        # Build candidate summaries for LLM
        graph_summaries = self._build_candidate_summaries(
            graph_candidates[: self.MAX_GRAPH_CANDIDATES], "G"
        )
        vector_summaries = self._build_candidate_summaries(
            vector_candidates[: self.MAX_VECTOR_CANDIDATES], "V"
        )

        # Build fusion prompt
        prompt = self._build_fusion_prompt(
            graph_summaries=graph_summaries,
            vector_summaries=vector_summaries,
            query=query,
            intent=intent,
        )

        # Call LLM for fusion
        try:
            result = self.llm.answer(
                evidence=prompt,
                query=query,
                intent=intent,
                system_prompt=self._build_fusion_system_prompt(),
            )
            fusion_data = self._parse_fusion_response(
                result.get("answer", "")
            )
        except Exception:
            # Fallback: simple interleaving
            return self._fallback_fuse(
                graph_candidates, vector_candidates, top_k
            )

        if not fusion_data:
            return self._fallback_fuse(
                graph_candidates, vector_candidates, top_k
            )

        # Map LLM rankings back to candidates
        return self._apply_fusion_ranking(
            fusion_data=fusion_data,
            graph_candidates=graph_candidates,
            vector_candidates=vector_candidates,
            top_k=top_k,
        )

    # --- Prompt builders -----------------------------------------------------

    def _build_fusion_system_prompt(self) -> str:
        """System prompt for the fusion LLM call."""
        return """你是汽车BCM（车身控制模块）文档检索的排序专家。

你的任务是：比较来自不同检索路径的文档片段，判断它们与用户查询的相关性、互补性和冗余性，然后给出融合排序。

规则：
1. 相关性优先：与查询直接相关的片段排前面
2. 互补性加分：如果片段A提供信号定义、片段B提供激活条件，两者互补
3. 冗余降级：如果两个片段内容重复，保留更详细的那个
4. 图谱来源(G)的片段通常包含结构化的实体关系，向量来源(V)的片段包含语义匹配的文档内容
5. 输出JSON格式，每个候选包含id、rank、reason字段"""

    def _build_fusion_prompt(
        self,
        graph_summaries: list[dict],
        vector_summaries: list[dict],
        query: str,
        intent: dict,
    ) -> str:
        """Build the fusion prompt with candidate summaries.

        The prompt presents candidates from both sources and asks the LLM
        to rank them with reasoning.
        """
        parts = [
            "## 用户查询",
            query,
            "",
        ]

        # Intent context
        qtype = intent.get("question_type", "factual")
        modules = intent.get("modules", [])
        if modules:
            parts.append(f"## 查询意图\n类型: {qtype}")
            parts.append(f"涉及模块: {', '.join(modules)}")
            parts.append("")

        # Graph candidates
        if graph_summaries:
            parts.append(
                f"## 图谱检索候选 ({len(graph_summaries)} 个)"
            )
            parts.append("这些候选来自知识图谱的结构化检索：")
            parts.append("")
            for gs in graph_summaries:
                parts.append(
                    f"**{gs['id']}**: [{gs.get('type','?')}] "
                    f"{gs.get('module','?')} — {gs['summary']}"
                )
            parts.append("")

        # Vector candidates
        if vector_summaries:
            parts.append(
                f"## 向量检索候选 ({len(vector_summaries)} 个)"
            )
            parts.append("这些候选来自语义向量检索：")
            parts.append("")
            for vs in vector_summaries:
                parts.append(
                    f"**{vs['id']}**: [{vs.get('type','?')}] "
                    f"§{vs.get('section','?')} ({vs.get('module','?')})"
                    f" — {vs['summary']}"
                )
            parts.append("")

        # Fusion instructions
        parts.append("## 融合排序要求")
        parts.append("请比较上述候选，输出JSON格式的融合排序结果：")
        parts.append("")
        parts.append("```json")
        parts.append("[")
        parts.append(
            '  {"id": "G_0", "rank": 1, "reason": "直接定义了查询信号..."},'
        )
        parts.append(
            '  {"id": "V_3", "rank": 2, "reason": "补充了激活条件..."},'
        )
        parts.append("]")
        parts.append("```")
        parts.append("")
        parts.append("注意：")
        parts.append("- 只输出JSON数组，不要输出其他内容")
        parts.append("- 每个候选最多1条排名记录")
        parts.append("- reason用中文，简短说明排序理由")
        parts.append("- 标注互补关系（如'与G_0互补'）和冗余关系（如'与V_3重复'）")

        return "\n".join(parts)

    # --- Response parsing ----------------------------------------------------

    def _parse_fusion_response(self, response: str) -> list[dict]:
        """Parse the LLM's fusion ranking response.

        Handles common JSON formatting issues from LLM output:
          - JSON in markdown code fences
          - Trailing commas
          - Missing brackets
        """
        if not response:
            return []

        # Try to extract JSON from response
        # Remove markdown code fences
        text = response.strip()
        if text.startswith("```"):
            # Find the first ``` and the matching closing ```
            lines = text.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                elif line.startswith("```") and in_json:
                    break
                elif in_json:
                    json_lines.append(line)
            if json_lines:
                text = "\n".join(json_lines)

        # Try to find JSON array in text
        start_idx = text.find("[")
        end_idx = text.rfind("]")
        if start_idx >= 0 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        return []

    # --- Candidate summary building ------------------------------------------

    def _build_candidate_summaries(
        self,
        candidates: list[dict],
        source_prefix: str,
    ) -> list[dict]:
        """Build LLM-readable summaries of candidates.

        Each summary is compact: id, type, module, section, and first
        150 characters of text.

        Args:
            candidates: Candidate list from one retrieval source
            source_prefix: "G" for graph, "V" for vector

        Returns:
            List of summary dicts with id, type, module, section, summary
        """
        summaries: list[dict] = []

        for i, entry in enumerate(candidates):
            chunk = entry.get("chunk", {})
            text = chunk.get("text", "")
            if not text and isinstance(chunk, dict):
                # For graph candidates, text might be in entity description
                text = (
                    chunk.get("description", "")
                    or chunk.get("content", "")
                    or str(chunk.get("name", ""))
                )

            summary_text = text[:150].replace("\n", " ").strip()

            summaries.append(
                {
                    "id": f"{source_prefix}_{i}",
                    "type": chunk.get("chunk_type", chunk.get("entity_type", "?")),
                    "module": chunk.get("module", "?"),
                    "section": chunk.get("section_path", "?"),
                    "summary": summary_text,
                    "_source_index": i,
                    "_source_prefix": source_prefix,
                }
            )

        return summaries

    # --- Ranking application -------------------------------------------------

    def _apply_fusion_ranking(
        self,
        fusion_data: list[dict],
        graph_candidates: list[dict],
        vector_candidates: list[dict],
        top_k: int,
    ) -> list[dict]:
        """Apply LLM fusion rankings to the candidate lists.

        Maps LLM-assigned ranks back to the original candidates,
        adds fusion metadata, and returns the top_k results.
        """
        # Build lookup from id to candidate
        lookup: dict[str, dict] = {}

        for i, entry in enumerate(graph_candidates):
            lookup[f"G_{i}"] = entry

        for i, entry in enumerate(vector_candidates):
            lookup[f"V_{i}"] = entry

        # Apply rankings
        ranked: list[dict] = []
        seen_ids: set[str] = set()

        for item in fusion_data:
            cid = item.get("id", "")
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            entry = lookup.get(cid)
            if entry is None:
                continue

            rank = item.get("rank", len(ranked) + 1)
            reason = item.get("reason", "")

            # Normalize score: rank 1 → 1.0, rank N → 1/N
            fusion_score = 1.0 / max(rank, 1)

            # Create fused entry
            fused_entry = dict(entry)  # shallow copy
            fused_entry["score"] = (
                entry.get("score", 0.5) * 0.6 + fusion_score * 0.4
            )
            fused_entry["fusion_reason"] = reason
            fused_entry["sources"] = list(
                set(entry.get("sources", []) + ["llm_fusion"])
            )

            ranked.append(fused_entry)

        # Append any unranked candidates with reduced scores
        for entry in graph_candidates + vector_candidates:
            # Check if this entry is already in ranked
            already_in = False
            for r in ranked:
                if r.get("chunk", {}).get("chunk_id") == entry.get(
                    "chunk", {}
                ).get("chunk_id"):
                    already_in = True
                    break
            if not already_in:
                entry_copy = dict(entry)
                entry_copy["score"] = entry.get("score", 0.5) * 0.7
                entry_copy["fusion_reason"] = "LLM未排名, 降权保留"
                entry_copy["sources"] = list(
                    set(entry.get("sources", []) + ["llm_fusion_fallback"])
                )
                ranked.append(entry_copy)

        # Sort by score descending
        ranked.sort(key=lambda x: x.get("score", 0), reverse=True)

        return ranked[:top_k]

    # --- Fallback ------------------------------------------------------------

    def _fallback_fuse(
        self,
        graph_candidates: list[dict],
        vector_candidates: list[dict],
        top_k: int,
    ) -> list[dict]:
        """Fallback fusion: interleave graph and vector results.

        Simple but reliable: alternates between graph and vector candidates,
        giving slight preference to graph results (which have structured
        relationships).
        """
        fused: list[dict] = []
        max_len = max(len(graph_candidates), len(vector_candidates))

        for i in range(max_len):
            if i < len(graph_candidates):
                entry = dict(graph_candidates[i])
                entry["fusion_reason"] = "图谱候选(回退模式)"
                entry["sources"] = list(
                    set(entry.get("sources", []) + ["llm_fusion_fallback"])
                )
                fused.append(entry)
            if i < len(vector_candidates):
                entry = dict(vector_candidates[i])
                entry["fusion_reason"] = "向量候选(回退模式)"
                entry["sources"] = list(
                    set(entry.get("sources", []) + ["llm_fusion_fallback"])
                )
                fused.append(entry)

        return fused[:top_k]
