"""BCM-RAG Retrieval — Query Rewriter (HyDE / query2doc).

Improvement #2: Query Rewriting for Better Recall

Uses HyDE (Hypothetical Document Embedding) strategy:
  1. LLM generates a hypothetical document fragment that would answer
     the user's question
  2. The hypothetical fragment is concatenated with the original query
  3. The augmented query is used for retrieval, improving recall because
     the hypothetical fragment uses language patterns similar to the
     target document corpus

Reference:
  "Precise Zero-Shot Dense Retrieval without Relevance Labels"
  (Gao et al., 2022)

The key insight: instead of encoding a short query and hoping it matches
documents, we encode a hypothetical answer — which shares vocabulary and
structure with real documents — and use that for retrieval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retrieval.llm_answer import LLMAnswerGenerator


class QueryRewriter:
    """HyDE query rewriter for improved retrieval recall.

    Uses an LLM (via the existing LLMAnswerGenerator) to generate a
    hypothetical document fragment, then concatenates it with the
    original query for augmented retrieval.

    Usage:
        llm = LLMAnswerGenerator(provider="ark")
        rewriter = QueryRewriter(llm)
        result = rewriter.rewrite(query, intent, strategy="hyde")
        # Use result["augmented_query"] for retrieval
    """

    # Strategies
    STRATEGY_HYDE = "hyde"
    STRATEGY_QUERY2DOC = "query2doc"
    STRATEGY_KEYWORDS = "keywords"  # No LLM, just keyword expansion

    # Maximum tokens for the hypothetical document generation
    MAX_HYPOTHETICAL_TOKENS = 200

    def __init__(self, llm_generator: "LLMAnswerGenerator"):
        """Initialize with an existing LLMAnswerGenerator instance.

        Args:
            llm_generator: Reuses the pipeline's LLM client.
        """
        self.llm = llm_generator

    # --- Public API ----------------------------------------------------------

    def rewrite(
        self,
        query: str,
        intent: dict,
        strategy: str = "hyde",
    ) -> dict:
        """Rewrite the query using the specified strategy.

        Args:
            query: Original user query
            intent: Intent analysis dict from Stage 1
            strategy: Rewriting strategy
              - "hyde": Generate a hypothetical document fragment
              - "query2doc": Generate a pseudo-document summary
              - "keywords": Keyword-only expansion (no LLM)

        Returns:
            {
                "original_query": str,
                "hypothetical_doc": str | None,
                "augmented_query": str,
                "strategy": str,
                "usage": dict | None,
            }
        """
        result: dict = {
            "original_query": query,
            "hypothetical_doc": None,
            "augmented_query": query,
            "strategy": strategy,
            "usage": None,
        }

        if strategy == self.STRATEGY_KEYWORDS:
            result["augmented_query"] = self._expand_keywords(query, intent)
            return result

        # Generate hypothetical document via LLM
        try:
            hypo_doc = self._generate_hypothetical_doc(query, intent)
            if not hypo_doc:
                # Empty response from LLM — fall back to keywords
                raise ValueError("Empty hypothetical document generated")
            result["hypothetical_doc"] = hypo_doc
            result["augmented_query"] = self.build_augmented_query(
                query, hypo_doc, strategy
            )
        except Exception:
            # On LLM failure, fall back to keyword expansion
            result["strategy"] = self.STRATEGY_KEYWORDS
            result["augmented_query"] = self._expand_keywords(query, intent)

        return result

    def build_augmented_query(
        self,
        original_query: str,
        hypothetical_doc: str,
        strategy: str,
    ) -> str:
        """Build the augmented query by combining original query with
        the hypothetical document.

        Strategy:
          - hyde: hypothetical doc + original query (concat)
          - query2doc: original query + pseudo-doc summary

        The original query is always included (often at both ends) to
        ensure it carries weight in the retrieval.
        """
        if not hypothetical_doc:
            return original_query

        if strategy == self.STRATEGY_QUERY2DOC:
            # query2doc: query first, then pseudo-doc
            return f"{original_query}\n\n{hypothetical_doc[:500]}"
        else:
            # hyde: hypothetical doc provides the language pattern,
            # original query provides the search target
            return f"{hypothetical_doc[:800]}\n\n问题: {original_query}"

    # --- Private: LLM generation ---------------------------------------------

    def _generate_hypothetical_doc(
        self, query: str, intent: dict,
    ) -> str:
        """Use LLM to generate a hypothetical document fragment.

        The prompt asks the LLM to act as the BCM specification author
        and write a fragment that would appear in the document to answer
        the question. The generated text uses technical documentation
        language, matching the target corpus style.

        Temperature is kept low (0.1-0.3) for stable generation.
        Max output is limited to 200 tokens to prevent divergence.
        """
        qtype = intent.get("question_type", "factual")
        modules = intent.get("modules", [])
        signals = intent.get("signals", [])
        states = intent.get("states", [])

        # Build domain context
        context_parts = []
        if modules:
            context_parts.append(f"涉及模块: {', '.join(modules)}")
        if signals:
            context_parts.append(f"涉及信号: {', '.join(signals)}")
        if states:
            context_parts.append(f"涉及状态: {', '.join(states)}")
        domain_context = "\n".join(context_parts) if context_parts else ""

        # Build the generation prompt
        prompt = self._build_hypothetical_prompt(
            query=query,
            qtype=qtype,
            domain_context=domain_context,
        )

        # Use the LLM to generate (with a custom system prompt)
        try:
            result = self.llm.answer(
                evidence=prompt,
                query=query,
                intent=intent,
                system_prompt=self._build_hypothetical_system_prompt(),
            )
            return result.get("answer", "")
        except Exception:
            return ""

    def _build_hypothetical_system_prompt(self) -> str:
        """System prompt for hypothetical document generation."""
        return """你是汽车BCM（车身控制模块）功能规格文档的作者。

你的任务是：根据用户的问题，写一段可能在BCM规范文档中出现的内容片段。

要求：
1. 使用技术文档的语言风格（客观、精确、使用技术术语）
2. 100-200字即可，不需要完整回答，只需要相关内容片段
3. 保持中文，技术术语保留英文原名
4. 如果涉及信号，描述其取值和含义
5. 如果涉及状态，描述其转移条件和触发源
6. 如果涉及故障，描述其检测条件和反应
7. 不要使用"根据文档"、"文档显示"等引用性语言
8. 直接写出文档内容片段"""

    def _build_hypothetical_prompt(
        self,
        query: str,
        qtype: str,
        domain_context: str,
    ) -> str:
        """Build the prompt for hypothetical document generation."""
        parts = [
            "## 用户问题",
            query,
            "",
        ]

        if domain_context:
            parts.append("## 领域上下文")
            parts.append(domain_context)
            parts.append("")

        parts.append("## 任务")
        if qtype == "reasoning":
            parts.append(
                "请写一段BCM规范文档中关于上述问题的内容片段，"
                "包括依赖关系、状态转换逻辑或触发条件。"
            )
        elif qtype == "diagnostic":
            parts.append(
                "请写一段BCM规范文档中关于上述诊断问题的内容片段，"
                "包括故障码、检测条件、故障反应和恢复方式。"
            )
        else:
            parts.append(
                "请写一段BCM规范文档中关于上述问题的内容片段，"
                "包括信号定义、功能描述或配置参数。"
            )

        parts.append("")
        parts.append("文档片段:")

        return "\n".join(parts)

    # --- Private: keyword expansion (no LLM) ---------------------------------

    def _expand_keywords(self, query: str, intent: dict) -> str:
        """Expand query with extracted keywords from intent analysis.

        This is the zero-cost fallback that doesn't use an LLM.
        It simply appends all matched entities from the intent analysis
        to the original query.
        """
        parts = [query]

        # Add module names
        for mod in intent.get("modules", [])[:3]:
            parts.append(mod)

        # Add signal names
        for sig in intent.get("signals", [])[:5]:
            parts.append(sig)

        # Add state names
        for st in intent.get("states", [])[:3]:
            parts.append(st)

        # Add function names
        for func in intent.get("functions", [])[:3]:
            parts.append(func)

        return " ".join(parts)
