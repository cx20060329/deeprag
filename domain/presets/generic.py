"""DeepRAG — Generic domain preset.

Minimal configuration for general-purpose documents.
Serves as a starting point for creating custom domain configs.
"""

from __future__ import annotations

from domain.config import (
    ChunkingConfig,
    DAGConfig,
    DomainConfig,
    ExtractionConfig,
    IntentConfig,
    LLMPromptConfig,
)

GENERIC_DOMAIN = DomainConfig(
    name="generic",
    display_name="通用文档",
    description="General-purpose document RAG (no domain-specific patterns)",

    entity_types=[
        "module",
        "state",
        "signal",
        "function",
        "parameter",
        "fault",
    ],
    relationship_types=[
        "belongs_to",
        "transition_to",
        "triggered_by",
        "depends_on",
        "controls",
        "outputs",
        "requires",
        "configures",
        "reports",
        "references",
    ],

    extraction=ExtractionConfig(
        # Generic patterns — less specific than BCM
        signal_pattern=r"\b([A-Z][A-Za-z0-9_]{3,})\b",
        state_pattern=r"\b(Active|Inactive|Idle|Running|Stopped|On|Off|Standby)\b",
        state_cn_pattern=r"(\w{2,20})\s*(?:状态|模式)\b",
        parameter_pattern=r"\b(Cfg|cfg|Cal|cal|NVM_|nvm_|Param|param_)[A-Za-z0-9_]+\b",
        fault_pattern=r"故障|失效|丢失|超时|错误|异常|Fault|Error|Failure",
        function_title_pattern=r"(?:功能描述|功能说明|控制逻辑)",
        function_text_pattern=r"([一-鿿]{2,15}(?:功能|控制|管理))",
        # Disable automotive-specific patterns
        can_message_pattern="",
        pin_pattern="",
        can_id_pattern="",
        # Generic relationship patterns
        transition_pattern=r"(?:迁移|转换|过渡)到\s*(\w+)\s*(?:状态|模式)?",
        depends_pattern=r"(\w{3,30})\s*(?:依赖于|依赖|取决于)\s*(\w{3,30})",
        controls_pattern=r"(\w{2,30})\s*(?:控制|管理)\s*(\w{2,30})",
        configures_pattern=r"(\w{3,30})\s*(?:配置|设置)\s*(?:了|为)?\s*(\w{2,30})",
        references_pattern=r"(?:参见|参考|详见|参照)\s*(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:节|章|页)?",
        state_names=["Active", "Inactive", "Idle", "Running", "Stopped", "On", "Off", "Standby"],
        function_keywords=[],
        power_terminal_names=[],
    ),

    chunking=ChunkingConfig(
        chunk_type_patterns={
            "function_desc": r"功能描述|功能说明|控制逻辑",
            "state_transition": r"前置条件|触发条件|执行输出",
            "config_block": r"配置参数|Parameter|默认值",
            "fault_handling": r"故障|错误|异常|Fault|Error",
        },
        key_term_patterns=[],
        module_abbrev_map={},
        target_token_min=800,
        target_token_max=2000,
    ),

    llm_prompts=LLMPromptConfig(
        answer_system_prompt="""你是一个技术文档知识库问答专家。

你的任务是根据提供的证据片段回答用户问题。

规则：
1. 仅基于提供的证据片段回答问题，不要添加证据中不存在的推测
2. 如果证据不足，明确说明"根据现有文档无法确定"
3. 引用证据时注明章节号和来源
4. 使用结构化格式，必要时使用列表或表格""",

        compressor_system_prompt="""你是技术文档的摘要专家。
你的任务是将多个文档片段压缩为结构化摘要。
保留所有依赖关系和逻辑结构。去掉重复信息。""",

        vlm_system_prompt="""Analyze this image from a technical document.
Extract ALL structured information. Output ONLY valid JSON.""",

        vlm_simple_prompt="""Analyze this image. Output JSON with image_type, summary, text_content, key_entities.""",

        agent_system_prompt="""你是技术文档知识库问答专家。

规则：
1. 仅基于DAG推理引擎返回的证据回答问题
2. 如果证据不足，明确说明
3. 引用证据时注明来源
4. 使用结构化格式""",

        agent_company_context="",
    ),

    intent=IntentConfig(
        module_aliases={},
        query_type_keywords={
            "factual": ["是什么", "定义", "有哪些", "列出", "参数"],
            "reasoning": ["为什么", "如何", "怎么", "原因", "影响"],
            "diagnostic": ["故障", "错误", "异常"],
        },
    ),

    dag=DAGConfig(
        default_module="",
        state_names=["active", "inactive", "idle", "running", "stopped"],
        templates=[],
        template_descriptions_prompt="",
        keyword_override_map={},
    ),

    state_machine=None,
)
