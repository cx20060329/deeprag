"""BCM-RAG Retrieval — LLM Context Compressor (Stage 8 enhanced).

Improvement #1: LLM-driven Context Compression

Replaces simple text truncation with LLM-driven structured summarization.
Instead of cutting chunks at 800 characters (which loses middle information),
the compressor uses an LLM to rewrite multiple chunks into a structured
summary that preserves:

  - Dependency chains (X depends on Y, Y triggers Z)
  - State transitions (source→target: guard conditions)
  - Key rules and their activation logic
  - Source section references

This is a qualitative improvement over the old approach:
  Old: "取前800字符 → 丢失中间关键信息"
  New: "LLM重写为结构化摘要 → 保留逻辑关系和依赖链"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retrieval.llm_answer import LLMAnswerGenerator


class ContextCompressor:
    """LLM-driven context compressor for evidence chunks.

    Uses an LLM (via the existing LLMAnswerGenerator) to merge multiple
    retrieval chunks into a concise structured summary. This preserves
    dependency chains, state transitions, and rule logic that would be
    lost by simple text truncation.

    Usage:
        llm = LLMAnswerGenerator(provider="ark")
        compressor = ContextCompressor(llm)
        summary = compressor.compress(
            candidates=reranked_results,
            query="GlobalClose 的触发条件？",
            intent=intent_dict,
            graph_results=graph_results,
        )
    """

    # Maximum input characters to send to the LLM (to control cost)
    MAX_INPUT_CHARS = 4000
    # Target output characters for the compressed summary
    DEFAULT_MAX_OUTPUT_CHARS = 2000

    def __init__(self, llm_generator: "LLMAnswerGenerator"):
        """Initialize with an existing LLMAnswerGenerator instance.

        Args:
            llm_generator: Reuses the pipeline's LLM client. No new
                           client is created — this follows the
                           dependency injection pattern.
        """
        self.llm = llm_generator

    # --- Public API ----------------------------------------------------------

    def compress(
        self,
        candidates: list[dict],
        query: str,
        intent: dict,
        graph_results: list[dict] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> str:
        """Compress multiple retrieval candidates into a structured summary.

        Args:
            candidates: Stage 7 reranked candidate list (each has "chunk" key)
            query: Original user query
            intent: Intent analysis dict
            graph_results: Graph retrieval results (for dependency chain info)
            max_output_chars: Target maximum output characters

        Returns:
            Structured summary text with dependency chains, state transitions,
            and key facts preserved.
        """
        if not candidates:
            return f"# 查询: {query}\n\n未找到相关内容。"

        # Build the input text from candidates
        chunks_text = self._build_input_text(candidates)

        # Build graph context from graph_results
        graph_context = self._extract_graph_context(graph_results or [])

        # Build the compression prompt
        prompt = self._build_compression_prompt(
            chunks_text=chunks_text,
            query=query,
            graph_context=graph_context,
        )

        # Call LLM to compress
        try:
            result = self.llm.answer(
                evidence=prompt,
                query=query,
                intent=intent,
                system_prompt=self._build_compressor_system_prompt(),
            )
            compressed = result.get("answer", "")
            if compressed.startswith("[LLM Error]"):
                # Fallback to simple truncation on LLM error
                return self._fallback_compress(candidates, query, intent)
            return compressed
        except Exception:
            return self._fallback_compress(candidates, query, intent)

    def compress_structured(
        self,
        structured_evidence: str,
        query: str,
        intent: dict,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> str:
        """Compress already-structured evidence (from EvidenceBuilder).

        When both use_structured_evidence and use_llm_compress are enabled,
        this method applies LLM compression on top of the structured format.

        Args:
            structured_evidence: Output from EvidenceBuilder.format_for_llm()
            query: Original user query
            intent: Intent analysis dict
            max_output_chars: Target maximum output characters

        Returns:
            Further compressed summary preserving all key relationships
        """
        prompt = self._build_structured_compression_prompt(
            structured_evidence=structured_evidence,
            query=query,
        )

        try:
            result = self.llm.answer(
                evidence=prompt,
                query=query,
                intent=intent,
                system_prompt=self._build_compressor_system_prompt(),
            )
            compressed = result.get("answer", "")
            if compressed.startswith("[LLM Error]"):
                return structured_evidence  # Return original on error
            return compressed
        except Exception:
            return structured_evidence

    # --- Prompt builders -----------------------------------------------------

    def _build_compressor_system_prompt(self) -> str:
        """Build the system prompt for the compression LLM call."""
        return """你是汽车BCM（车身控制模块）技术文档的摘要专家。

你的任务是将多个文档片段压缩为结构化摘要。

规则：
1. 合并重复的信息，只保留一份
2. 保留依赖关系（X依赖Y, Y触发Z, X控制Y）
3. 保留状态转移链（源状态→目标状态: 转移条件）
4. 保留关键的阈值、时序、信号名称
5. 标注信息来源的章节号
6. 使用中文，技术术语保留英文原名
7. 输出控制在指定长度内，优先保留最关键的信息
8. 如果信息不足，明确说明，不要编造"""

    def _build_compression_prompt(
        self,
        chunks_text: str,
        query: str,
        graph_context: str,
    ) -> str:
        """Build the compression prompt with chunks and graph context.

        The prompt instructs the LLM to:
          1. Merge duplicate information
          2. Preserve dependency relationships
          3. Preserve state transition chains
          4. Annotate source section numbers
        """
        parts = [
            "## 用户查询",
            query,
            "",
        ]

        if graph_context:
            parts.append("## 知识图谱关系（请保留这些依赖链和状态转移）")
            parts.append(graph_context)
            parts.append("")

        parts.append("## 文档片段（需要压缩的内容）")
        parts.append(chunks_text)
        parts.append("")

        parts.append("## 压缩要求")
        parts.append("请将上述文档片段压缩为结构化摘要，保留以下信息：")
        parts.append("")
        parts.append("1. **依赖链**：信号/功能之间的因果关系")
        parts.append("   - 格式: A → B → C（关系类型：controls/depends_on/triggers）")
        parts.append("2. **状态转移**：状态机的迁移路径和条件")
        parts.append("   - 格式: SourceState → TargetState: guard条件")
        parts.append("3. **关键规则**：激活条件、故障检测逻辑、阈值参数")
        parts.append("4. **信息来源**：每项信息引用章节号")
        parts.append("")
        parts.append("输出格式使用Markdown，保留结构化信息以便后续引用。")

        return "\n".join(parts)

    def _build_structured_compression_prompt(
        self,
        structured_evidence: str,
        query: str,
    ) -> str:
        """Build compression prompt for already-structured evidence."""
        return "\n".join(
            [
                "## 用户查询",
                query,
                "",
                "## 结构化证据（已经包含依赖链和状态转移）",
                structured_evidence[: self.MAX_INPUT_CHARS],
                "",
                "## 压缩要求",
                "对上述结构化证据做进一步精简，保留：",
                "1. 所有依赖链（不要丢失任何因果关系）",
                "2. 所有状态转移（不要丢失guard条件）",
                "3. 与查询最相关的规则和文档片段",
                "4. 所有章节引用",
                "",
                "去除冗余描述，合并重复信息，但不要丢失逻辑关系。",
            ]
        )

    # --- Input builders ------------------------------------------------------

    def _build_input_text(self, candidates: list[dict]) -> str:
        """Build input text from candidates for compression.

        Takes up to 8 candidates, each truncated to 500 characters,
        totaling ~4000 characters max input.
        """
        parts: list[str] = []
        total_chars = 0
        max_total = self.MAX_INPUT_CHARS

        for i, entry in enumerate(candidates[:8], 1):
            chunk = entry.get("chunk", {})
            text = chunk.get("text", "")[:500]
            chunk_type = chunk.get("chunk_type", "?")
            section = chunk.get("section_path", "?")
            module = chunk.get("module", "?")

            header = f"### 片段{i} [{chunk_type}] §{section} ({module})"
            block = f"{header}\n{text}\n"

            if total_chars + len(block) > max_total:
                break

            parts.append(block)
            total_chars += len(block)

        return "\n".join(parts)

    def _extract_graph_context(self, graph_results: list[dict]) -> str:
        """Extract dependency chain and state transition descriptions
        from graph retrieval results.

        Produces a compact text representation suitable for inclusion
        in the compression prompt.
        """
        if not graph_results:
            return ""

        lines: list[str] = []
        seen_relations: set[str] = set()

        # Collect relationships
        for item in graph_results:
            entity = item.get("entity", {})
            rel_type = item.get("relationship", "")

            entity_name = entity.get("name", "")
            entity_type = entity.get("entity_type", "")

            if not entity_name or not rel_type:
                continue

            # Try to find connected entity
            connected = (
                entity.get("module", "")
                or entity.get("target", "")
                or entity.get("related_to", "")
                or entity.get("source", "")
            )

            if connected and connected != entity_name:
                key = f"{entity_name}|{rel_type}|{connected}"
                if key not in seen_relations:
                    seen_relations.add(key)
                    lines.append(
                        f"- {entity_name} --[{rel_type}]--> {connected}"
                        f"  ({entity_type})"
                    )

        if not lines:
            return ""

        return "依赖关系:\n" + "\n".join(lines[:20])

    # --- Fallback ------------------------------------------------------------

    def _fallback_compress(
        self,
        candidates: list[dict],
        query: str,
        intent: dict,
    ) -> str:
        """Fallback compression when LLM is unavailable.

        Uses the same logic as the original _compress_context() —
        truncation + dedup + top-5.
        """
        if not candidates:
            return f"# 查询: {query}\n\n未找到相关内容。"

        parts = [f"# 查询: {query}"]
        parts.append(f"# 意图: {intent.get('question_type', 'unknown')}")

        # Collect metadata
        modules = set()
        signals = set()
        states = set()
        for r in candidates:
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

        # Evidence chunks (top 5, deduped)
        n = min(5, len(candidates))
        parts.append(f"\n## 证据片段 (Top {n})")
        seen_texts = set()
        count = 0

        for r in candidates:
            chunk = r["chunk"]
            text = chunk.get("text", "")[:800]
            sig = text[:200]
            if sig in seen_texts:
                continue
            seen_texts.add(sig)
            count += 1

            parts.append(
                f"\n### 片段 {count} [{chunk.get('chunk_type', '?')}]"
            )
            parts.append(
                f"章节: {chunk.get('section_path', '?')} | "
                f"模块: {chunk.get('module', '?')}"
            )
            parts.append(
                f"得分: {r['score']:.3f} | 来源: {r.get('sources', [])}"
            )
            if chunk.get("has_image"):
                img_paths = [
                    ref.get("storage_path", "")
                    for ref in chunk.get("image_refs", [])
                ]
                if img_paths:
                    parts.append(f"图片: {', '.join(img_paths[:2])}")
            parts.append(f"\n{text}")

            if count >= 5:
                break

        return "\n".join(parts)
