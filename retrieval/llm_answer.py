"""BCM-RAG Retrieval — LLM Answer Generator (Stage 9).

Generates answers from compressed evidence using an LLM.
Supports OpenAI-compatible APIs: Ark (Doubao), Zhipu (GLM), DeepSeek, etc.

Configuration is read from environment variables or passed directly.
"""

from __future__ import annotations

import json
import os
from typing import Optional


class LLMAnswerGenerator:
    """OpenAI-compatible LLM for answering questions with evidence context.

    Supports:
    - Ark (ByteDance Doubao):  base_url="https://ark.cn-beijing.volces.com/api/v3"
    - Zhipu (GLM):            base_url="https://open.bigmodel.cn/api/paas/v4/"
    - DeepSeek:               base_url="https://api.deepseek.com/v1"
    - Any OpenAI-compatible endpoint

    Usage:
        llm = LLMAnswerGenerator(
            api_key="...",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model="doubao-vision-pro-32k",
        )
        answer = llm.answer(evidence, query, intent)
    """

    # Known provider presets
    PROVIDERS = {
        "ark": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-vision-pro-32k",
        },
        "zhipu": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model": "glm-4-flash",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-flash",
        },
    }

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.1,
        timeout: float = 60.0,
    ):
        # Provider shortcut
        if provider and provider in self.PROVIDERS:
            preset = self.PROVIDERS[provider]
            base_url = base_url or preset["base_url"]
            model = model or preset["model"]

        # Resolve from env vars
        if not api_key:
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

        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

        self._client = None

    @property
    def client(self):
        """Lazy-init OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    # ---- Answer --------------------------------------------------------------

    def answer(
        self,
        evidence: str,
        query: str,
        intent: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        """Generate an answer from the compressed evidence package.

        Args:
            evidence: Compressed evidence text (from Stage 8)
            query: Original user query
            intent: Intent analysis dict (optional)
            system_prompt: Override system prompt

        Returns:
            {
                "answer": str,       # LLM-generated answer
                "model": str,        # Model used
                "usage": dict,       # Token usage
                "evidence_length": int,
            }
        """
        if system_prompt is None:
            system_prompt = self._build_system_prompt(intent)

        user_message = self._build_user_message(query, evidence)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except Exception as e:
            return {
                "answer": f"[LLM Error] {e}",
                "model": self.model,
                "usage": {},
                "evidence_length": len(evidence),
                "error": str(e),
            }

        return {
            "answer": response.choices[0].message.content,
            "model": response.model or self.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
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
        system_prompt = self._build_system_prompt(intent)
        user_message = self._build_user_message(query, evidence)

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )

        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ---- Prompts -------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(intent: dict | None = None) -> str:
        """Build the system prompt for BCM engineering domain."""
        qtype = intent.get("question_type", "factual") if intent else "factual"

        base = """你是汽车BCM（车身控制模块）功能规格专家。

你的任务是根据提供的证据片段回答用户问题。

规则：
1. 仅基于提供的证据片段回答问题，不要添加工证据中不存在的推测
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
        """Build the user message with evidence context.

        Automatically detects evidence format:
          - If evidence contains "## 依赖链" (structured evidence),
            uses a prompt that guides the LLM to reference dependency
            chains and state transitions explicitly.
          - Otherwise uses the original format.
        """
        # Detect structured evidence format (Improvement #3)
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

        # Original format (backward compatible)
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
        """Format evidence and top chunks for the LLM prompt.

        This is an enhanced formatter that includes richer metadata.
        """
        parts = [evidence]

        # Add section references
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
