"""DeepRAG Retrieval — LLM Answer Generator (Stage 9).

Generates answers from compressed evidence using an LLM backend.
Supports all OpenAI-compatible APIs via the unified llm module.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.base import LLMBackend


class LLMAnswerGenerator:
    """LLM-powered answer generator using the unified LLM backend.

    Wraps the llm.LLMBackend abstraction. All DeepRAG modules that need
    LLM calls should use this class (or the LLMBackend directly).

    Usage:
        # Auto-detect from environment
        llm = LLMAnswerGenerator()

        # Specify provider
        llm = LLMAnswerGenerator(provider="deepseek")

        # Pass an existing backend
        from llm import LLMFactory
        backend = LLMFactory.create("zhipu")
        llm = LLMAnswerGenerator(backend=backend)

        answer = llm.answer(evidence, query, intent)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.1,
        timeout: float = 60.0,
        backend: "LLMBackend | None" = None,
    ):
        """Initialize with either explicit params or a pre-built backend.

        Args:
            api_key: API key (auto-detected from env if not set).
            base_url: API base URL (uses provider default if not set).
            model: Model name (uses provider default if not set).
            provider: Provider shortcut: 'deepseek', 'zhipu', 'ark', 'openai'.
            max_tokens: Default max tokens per request.
            temperature: Default sampling temperature.
            timeout: HTTP request timeout in seconds.
            backend: Pre-built LLMBackend instance. If provided, all other
                     params are ignored and this backend is used directly.
        """
        self.max_tokens = max_tokens
        self.temperature = temperature

        if backend is not None:
            self._backend = backend
        else:
            from llm import LLMConfig, OpenAICompatBackend

            # Provider shortcut (backward compat)
            if provider:
                providers = LLMConfig.PROVIDERS
                if provider in providers:
                    preset = providers[provider]
                    base_url = base_url or preset["base_url"]
                    model = model or preset["model"]

            # Resolve API key
            if not api_key:
                if provider == "deepseek":
                    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
                elif provider == "zhipu":
                    api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("OPENAI_API_KEY")
                elif provider == "ark":
                    api_key = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
                else:
                    api_key = (
                        os.getenv("ARK_API_KEY")
                        or os.getenv("ZHIPU_API_KEY")
                        or os.getenv("DEEPSEEK_API_KEY")
                        or os.getenv("OPENAI_API_KEY")
                    )
            if not base_url:
                base_url = os.getenv("LLM_BASE_URL", "")
            if not model:
                model = os.getenv("LLM_MODEL", "doubao-vision-pro-32k")

            if not api_key:
                raise ValueError(
                    "No API key provided. Set one of: ARK_API_KEY, ZHIPU_API_KEY, "
                    "DEEPSEEK_API_KEY, OPENAI_API_KEY env vars, or pass api_key=."
                )

            config = LLMConfig(
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            self._backend = OpenAICompatBackend(config)

    # ------------------------------------------------------------------
    # Backend access
    # ------------------------------------------------------------------

    @property
    def backend(self) -> "LLMBackend":
        """Access the underlying LLMBackend for advanced use."""
        return self._backend

    @property
    def model(self) -> str:
        """Return the model name."""
        return self._backend.model_name

    @property
    def client(self):
        """Backward-compat: direct access to OpenAI client (for streaming etc.)."""
        if hasattr(self._backend, "client"):
            return self._backend.client
        return None

    # ------------------------------------------------------------------
    # Answer generation
    # ------------------------------------------------------------------

    def answer(
        self,
        evidence: str,
        query: str,
        intent: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        """Generate an answer from the compressed evidence package.

        Args:
            evidence: Compressed evidence text (from Stage 8).
            query: Original user query.
            intent: Intent analysis dict (optional).
            system_prompt: Override system prompt.

        Returns:
            {
                "answer": str,        # LLM-generated answer
                "model": str,         # Model used
                "usage": dict,        # Token usage
                "evidence_length": int,
            }
        """
        from llm.types import Message

        if system_prompt is None:
            system_prompt = self._build_system_prompt(intent)

        user_message = self._build_user_message(query, evidence)

        resp = self._backend.chat(
            messages=[
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_message),
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

        if resp.error:
            return {
                "answer": f"[LLM Error] {resp.error}",
                "model": resp.model,
                "usage": {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.total_tokens,
                },
                "evidence_length": len(evidence),
                "error": resp.error,
            }

        return {
            "answer": resp.content,
            "model": resp.model,
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
            "evidence_length": len(evidence),
        }

    def answer_stream(
        self,
        evidence: str,
        query: str,
        intent: dict | None = None,
    ):
        """Stream answer tokens. Yields text chunks."""
        from llm.types import Message

        system_prompt = self._build_system_prompt(intent)
        user_message = self._build_user_message(query, evidence)

        yield from self._backend.chat_stream(
            messages=[
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_message),
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

    # ------------------------------------------------------------------
    # Chat (generic LLM call for other modules)
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generic chat completion — for modules that need raw LLM access.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            response_format: Optional {"type": "json_object"} for JSON mode.

        Returns:
            {
                "content": str,
                "model": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
                "finish_reason": str,
                "error": str | None,
            }
        """
        from llm.types import Message

        llm_messages = [Message(role=m["role"], content=m["content"]) for m in messages]
        kwargs = {}
        if response_format:
            kwargs["response_format"] = response_format

        resp = self._backend.chat(
            messages=llm_messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            **kwargs,
        )

        return {
            "content": resp.content,
            "model": resp.model,
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
            "finish_reason": resp.finish_reason,
            "error": resp.error,
        }

    # ------------------------------------------------------------------
    # Prompts (domain-specific — will be parameterized in Step 8)
    # ------------------------------------------------------------------

    def _build_system_prompt(self, intent: dict | None = None, domain=None) -> str:
        """Build the system prompt from DomainConfig or BCM defaults."""
        qtype = intent.get("question_type", "factual") if intent else "factual"

        if domain is not None and domain.llm_prompts.answer_system_prompt:
            return domain.llm_prompts.answer_system_prompt

        base = """你是汽车BCM（车身控制模块）功能规格专家。

你的任务是根据提供的证据片段回答用户问题。

规则：
1. 仅基于提供的证据片段回答问题，不要添加证据中不存在的推测
2. 如果证据不足，明确说明"根据现有文档无法确定"
3. 引用证据时注明章节号和模块名
4. 回答使用中文，技术术语保留英文原名
5. 对于状态转换问题，描述完整的状态链和触发条件
6. 对于信号问题，说明信号来源、用途和相关模块
7. 对于故障诊断问题，列出检测条件、故障反应和恢复方式
8. 使用结构化格式，必要时使用列表或表格
"""

        if qtype == "reasoning":
            base += "\n特别注意：用户询问的是推理类问题。请分析证据中的依赖关系和状态转换逻辑，给出完整的推理链。"
        elif qtype == "diagnostic":
            base += "\n特别注意：用户询问的是诊断类问题。请列出故障码、检测条件、故障反应和恢复方式。"

        return base

    @staticmethod
    def _build_user_message(query: str, evidence: str) -> str:
        """Build the user message with evidence context."""
        if "## 依赖链" in evidence:
            return f"""## 用户问题

{query}

## 结构化证据

{evidence}

## 请回答

基于上述证据回答用户问题。证据中包含:
- **依赖链**: 信号/功能之间的因果关系（引用链编号）
- **状态转移**: 状态机的迁移路径和条件（引用转移编号）
- **相关规则**: 匹配的激活/故障检测规则
- **文档片段**: 原始规范文档内容

请结构化输出：

1. **结论**：用1-2句话直接回答
2. **详细分析**：基于证据展开说明，引用具体的依赖链编号和状态转移编号
3. **相关模块/信号/状态**：列出涉及的关键实体
4. **证据来源**：列出引用的依赖链、状态转移、规则和章节"""

        return f"""## 用户问题

{query}

## 证据片段

{evidence}

## 请回答

基于上述证据片段，回答用户问题。请结构化输出：

1. **结论**：用1-2句话直接回答
2. **详细分析**：基于证据展开说明
3. **相关模块/信号/状态**：列出涉及的关键实体
4. **证据来源**：列出引用的章节"""

    @staticmethod
    def format_evidence_for_llm(evidence: str, top_chunks: list[dict]) -> str:
        """Format evidence and top chunks for the LLM prompt."""
        parts = [evidence]

        sections = set()
        for r in top_chunks:
            chunk = r.get("chunk", {})
            sec = chunk.get("section_path", "")
            title = chunk.get("section_title", "")
            if sec:
                sections.add(f"{sec} {title}".strip())

        if sections:
            parts.append("\n## 参考章节")
            for s in sorted(sections):
                parts.append(f"- {s}")

        return "\n".join(parts)
