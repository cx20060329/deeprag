"""DeepRAG — BCM (Body Control Module) domain preset.

Contains ALL automotive BCM-specific patterns extracted from the
original hardcoded implementation. Serves as both a working preset
and a reference for creating new domain configs.
"""

from __future__ import annotations

from domain.config import (
    ChunkingConfig,
    DAGConfig,
    DAGTemplateDef,
    DomainConfig,
    ExtractionConfig,
    IntentConfig,
    LLMPromptConfig,
    StateMachineConfig,
)

# =============================================================================
# BCM Domain Config
# =============================================================================

BCM_DOMAIN = DomainConfig(
    name="bcm",
    display_name="汽车BCM车身控制模块",
    description="Automotive Body Control Module (BCM) functional specification documents",

    # -------------------------------------------------------------------------
    # Entity & Relationship Types
    # -------------------------------------------------------------------------
    entity_types=[
        "module",
        "state",
        "signal",
        "function",
        "parameter",
        "fault",
        "can_message",
        "hardware_pin",
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

    # -------------------------------------------------------------------------
    # Extraction Patterns
    # -------------------------------------------------------------------------
    extraction=ExtractionConfig(
        # -- Entity patterns --
        signal_pattern=(
            r"\b([A-Z][A-Za-z0-9_]{3,}(?:Sts|Mode|Status|SW|Cmd|Req|Relay|"
            r"Signal|Msg|Active|Enable|Disable|Request|State|Value|Cnt|Cntrl)?)\b"
        ),
        state_pattern=(
            r"\b(Inactive|Active|Driving|Convenience|Abandoned|"
            r"Disarmed|Armed|PreArmed|Alarm|Wakeup|Sleep|Standby|"
            r"OFF|ACC|ON|Idle|Run|Crank|Charging|Discharging)\b"
        ),
        state_cn_pattern=r"(\w{2,20})\s*(?:状态|模式)\b",
        parameter_pattern=r"\b(Cfg|cfg|Cal|cal|NVM_|nvm_)[A-Za-z0-9_]+\b",
        fault_pattern=r"故障|失效|丢失|超时|短路|断路|DTC|报警|异常|Fault|Error|Failure",
        can_id_pattern=r"\b(0x[0-9A-Fa-f]{3,8})\b",
        can_message_pattern=(
            r"\b(?:CAN\s*(?:报文|消息|信号|Message|Frame|ID)\s*[:：]?\s*)?"
            r"((?:BCM|VCU|PEPS|ESC|TCM|ABS|EMS|IC|GW|BMS)_[A-Za-z0-9_]{3,})\b"
        ),
        pin_pattern=(
            r"\b(PIN\s*\d{1,3}|Pin\s*\d{1,3}|pin\s*\d{1,3}|"
            r"KL30|KL15|KL31|KL87|GND|VBAT|"
            r"(?:HSD|LSD|H-Bridge|Relay)\s*\d*)\b"
        ),
        function_title_pattern=r"(?:功能描述|功能说明|激活逻辑|关闭逻辑|使能条件|关闭条件|控制逻辑)",
        function_text_pattern=(
            r"(GlobalClose|AutoLock|CrashUnlock|FollowMeHome|"
            r"WelcomeLight|ComingHome|LeavingHome|CorneringLight|"
            r"AutoFold|AutoFoldBack|GlobalOpen|KeyReminder|"
            r"[一-鿿]{2,15}(?:功能|控制|管理|保护|检测|诊断))"
        ),

        # -- Relationship patterns --
        transition_pattern=r"迁移到\s*(\w+)\s*状态",
        output_signal_pattern=r"发送\s*(?:CAN)?\s*信号\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)\s*:?\s*(\w+)?",
        output_generic_pattern=(
            r"(?:输出|驱动|拉高|拉低|置位|Set|Reset|Toggle)\s*"
            r"(?:信号|PIN)?\s*(\w{3,30})"
        ),
        trigger_pattern=(
            r"(?:当|一旦|若|如果)\s*"
            r"(\w{3,30})\s*(?:==?|≠|不等于?|大于|小于|变为?|切换到?|设置为?)\s*"
            r"(\w{0,20})\s*(?:时|后|之际|时，|时。|则|,)\s*(?:触发|激活|唤醒|启动|进入|退出|执行)"
        ),
        trigger_edge_pattern=r"(\w+)\s*(?:的)?\s*(?:上升沿|下降沿|边沿|变化)\s*(?:触发|激活)\s*(\w+)",
        trigger_kw_pattern=r"(?:触发条件|触发源|唤醒源|激活条件)[：:]\s*(\w{3,30})",
        depends_pattern=(
            r"(\w{3,30})\s*(?:依赖于|依赖|取决于|取决于信号|的前提是|的前置条件是|必要条件)"
            r"\s*(\w{3,30})"
        ),
        depends_state_pattern=(
            r"(?:需要|要求|前提)\s*(\w+)\s*(?:处于|为|=)\s*(\w+)\s*(?:状态|模式)?"
            r"\s*(?:才能|方可|可以|允许)"
        ),
        controls_pattern=(
            r"(\w{2,30})\s*(?:控制|管控(?:逻辑)?|管理)\s*(\w{2,30})"
            r"\s*(?:的)?\s*(?:输出|功能|状态|电源|继电器)"
        ),
        controls_enable_pattern=r"(\w{2,30})\s*(?:使能|启用|禁用|关闭|打开)\s*(\w{2,30})",
        requires_pattern=(
            r"(?:需要|要求|必须有|必须存在|必要条件)[：:]?\s*(\w{3,30})"
            r"\s*(?:信号|状态|报文|配置)?"
        ),
        configures_pattern=(
            r"(\w{3,30})\s*(?:配置|标定|设置|参数)\s*(?:了|为|成)?\s*(\w{2,30})"
            r"\s*(?:的)?\s*(?:功能|参数|值|选项|阈值|时间)"
        ),
        configures_via_pattern=r"(?:通过|使用|利用|经由)\s*(\w{3,30})\s*(?:配置|标定|设置)\s*(\w{2,30})",
        reports_pattern=(
            r"(\w{2,30})\s*(?:上报|报告|反馈|通知)\s*(\w{2,30})"
            r"\s*(?:故障|状态|事件|信号|DTC|报警)?"
        ),
        reports_dtc_pattern=r"(?:DTC|诊断(?:码)?|故障码)[：:]?\s*(\w{3,20})\s*(?:上报|报告|反馈)",
        references_pattern=(
            r"(?:参见|参考|详见|参照|见|参阅)\s*"
            r"(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:节|章|页|段|部分)?"
        ),
        references_section_pattern=(
            r"(?:如|按照|根据|参考)\s*(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:节|章)"
            r"\s*(?:所述|的定义|的规定|的描述)"
        ),
        cross_module_pattern=(
            r"(VMM|ExteriorLight|InteriorLight|Window|Lock|TheftProtection|Wiper|RemoteControl)"
            r"\s*(?:模块|的)\s*(\w{3,30})"
        ),

        # -- Classification heuristics --
        state_names=[
            "Inactive", "Active", "Driving", "Convenience", "Abandoned",
            "Disarmed", "Armed", "OFF", "ACC", "ON", "Idle", "Sleep",
        ],
        function_keywords=["Close", "Open", "Lock", "Unlock", "Fold", "Follow"],
        power_terminal_names=["KL30", "KL15", "KL31", "KL87", "GND", "VBAT"],
    ),

    # -------------------------------------------------------------------------
    # Chunking Patterns
    # -------------------------------------------------------------------------
    chunking=ChunkingConfig(
        chunk_type_patterns={
            "function_requirement": r"基本功能要求|功能定义.*描述|功能列表|功能需求规格",
            "division_table": r"设计职责.*分工|责任分工表|工作任务.*CH事业部.*供应商|R&A|S&A",
            "signal_table": r"信号名称|CAN\s*ID|信号位置|Signal Name|PIN脚",
            "state_transition": r"前置条件|触发条件|执行输出|迁移到.*状态",
            "state_machine": r"状态表|状态图|转移表|模式定义|State Table|State Machine",
            "function_desc": r"功能描述|激活逻辑|关闭逻辑|使能条件|关闭条件",
            "config_block": r"配置参数|NVM参数|常数参数|Parameter Name|默认值",
            "fault_handling": r"故障诊断|故障检测|故障处理|故障反应|故障恢复|故障码|DTC\s*码|失效模式|故障注入|故障模拟",
            "output_control": r"输出控制|Output Control|PWM|占空比|优先级",
        },
        key_term_patterns=[
            r"(以太网|Ethernet|SOMEIP|DoIP|CAN\s*FD|CANFD|LIN|FlexRay|AutoSAR|AUTOSAR|OSEK|UDS|OBD)",
            r"(Bootloader|刷写|烧写|调试工具|编译器|测试盒|休眠唤醒|网络管理|路由功能|诊断路由|诊断功能|信息安全|功能安全)",
            r"(R&A|S&A|CH事业部|供应商|埃泰克|负责|协助|验收|评审)",
            r"(ASIL\s*[A-D]|ISO\s*26262|GB\s*\d+|Q/BAIC|企标)",
            r"(EP1|EP2|PPV|PPAP|SOP|ESO|OTS|DV|PV)",
        ],
        module_abbrev_map={
            "VMM": "VMM",
            "ExteriorLight": "ExtLight",
            "InteriorLight": "IntLight",
            "Window": "Window",
            "Lock": "Lock",
            "TheftProtection": "ATWS",
            "Wiper": "Wiper",
            "RemoteControl": "Remote",
            "_TOC": "TOC",
        },
        target_token_min=800,
        target_token_max=2000,
    ),

    # -------------------------------------------------------------------------
    # State Machine Definitions
    # -------------------------------------------------------------------------
    state_machine=StateMachineConfig(
        module_states={
            "VMM": {
                "Abandoned": {"is_terminal": True, "power_mode": "OFF"},
                "Inactive": {"is_initial": True, "power_mode": "OFF"},
                "Convenience": {"power_mode": "Crank/ON"},
                "Driving": {"power_mode": "ON"},
            },
            "Window": {
                "Stopped": {},
                "Rising": {},
                "Falling": {},
                "AntiPinch": {},
            },
            "Lock": {
                "Unlocked": {},
                "Locked": {},
                "AutoLocked": {},
                "CrashUnlocked": {},
            },
            "ExteriorLight": {
                "Off": {},
                "PositionLight": {},
                "LowBeam": {},
                "HighBeam": {},
                "AutoLight": {},
            },
            "InteriorLight": {
                "Off": {},
                "On": {},
                "Dimmed": {},
            },
            "Wiper": {
                "Off": {},
                "Intermittent": {},
                "LowSpeed": {},
                "HighSpeed": {},
            },
            "RemoteControl": {
                "Disarmed": {},
                "Armed": {},
                "Alarm": {},
            },
            "TheftProtection": {
                "Disarmed": {},
                "PreArmed": {},
                "Armed": {},
                "Alarm": {},
            },
        },
        section_to_module_map={
            1: "Overview",
            2: "VMM",
            3: "ExteriorLight",
            4: "InteriorLight",
            5: "Window",
            6: "Lock",
            7: "Wiper",
            8: "Wiper",
            9: "RemoteControl",
            10: "ATWS",
            11: "Network",
        },
    ),

    # -------------------------------------------------------------------------
    # LLM System Prompts
    # -------------------------------------------------------------------------
    llm_prompts=LLMPromptConfig(
        answer_system_prompt="""你是汽车BCM（车身控制模块）功能规格专家。

你的任务是根据提供的证据片段回答用户问题。

规则：
1. 仅基于提供的证据片段回答问题，不要添加证据中不存在的推测
2. 如果证据不足，明确说明"根据现有文档无法确定"
3. 引用证据时注明章节号和模块名
4. 回答使用中文，技术术语保留英文原名
5. 对于状态转换问题，描述完整的状态链和触发条件
6. 对于信号问题，说明信号来源、用途和相关模块
7. 对于故障诊断问题，列出检测条件、故障反应和恢复方式
8. 使用结构化格式，必要时使用列表或表格""",

        compressor_system_prompt="""你是汽车BCM（车身控制模块）技术文档的摘要专家。
你的任务是将多个文档片段压缩为结构化摘要。
保留所有依赖关系、状态转换和规则逻辑。去掉重复信息。""",

        vlm_system_prompt="""Analyze this image from a Chinese automotive BCM (Body Control Module) specification document.

The image is one of:
- State machine / state transition diagram
- System block diagram / architecture overview
- Signal timing diagram / sequence diagram
- Flowchart (algorithm, key learning, etc.)
- Table screenshot

Extract ALL structured information. Output ONLY valid JSON:
{
  "image_type": "state_machine|block_diagram|timing_diagram|flowchart|table|other",
  "summary": "Chinese description of what this image shows",
  "states": [{"name": "StateName", "description": "What this state means"}],
  "transitions": [{"from": "StateA", "to": "StateB", "trigger": "condition"}],
  "signals": [{"name": "SIGNAL_NAME", "description": "What this signal represents", "value": "0xNN if shown"}],
  "modules": ["Module names referenced"],
  "functions": ["Function names described"],
  "parameters": ["Cfg* parameters or config values"],
  "text_content": "All readable text extracted from the image"
}""",

        vlm_simple_prompt="""Analyze this image from a Chinese BCM automotive document.
Output JSON:
{
  "image_type": "state_machine|block_diagram|timing_diagram|flowchart|table|screenshot|other",
  "summary": "Brief Chinese description",
  "text_content": "All readable text from the image",
  "key_entities": ["Entity names found"]
}""",

        agent_system_prompt="""你是埃泰克公司的BCM技术库问答专家。你的视角是埃泰克（供应商方）。

规则：
1. 仅基于DAG推理引擎返回的证据回答问题
2. 如果证据不足，明确说明
3. 引用证据时注明来源（依赖链编号、状态转移编号、章节号）
4. 回答使用中文，技术术语保留英文原名
5. 使用结构化格式""",

        agent_company_context="""- 文档中的"供应商" = 埃泰克（我们公司）
- 文档中的"CH事业部" = 客户（甲方/北汽）
- "乙方" = 埃泰克（我们公司）
- "甲方" = 客户（CH事业部/北汽）
- 分工表中 A(Accountable)=负责, R(Review)=评审, S(Support)=协助
- 分工表中 R&A=客户负责+供应商协助, S&A=供应商协助+客户验收""",
    ),

    # -------------------------------------------------------------------------
    # Intent Analysis
    # -------------------------------------------------------------------------
    intent=IntentConfig(
        module_aliases={
            "bcm": "VMM",
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
        },
        query_type_keywords={
            "factual": ["是什么", "定义", "取值", "编码", "含义", "描述", "有哪些", "列出", "参数", "配置", "PIN", "管脚"],
            "reasoning": ["为什么", "如何", "怎么", "原因", "影响", "导致"],
            "diagnostic": ["故障", "诊断", "失效", "错误", "异常", "不工作"],
        },
    ),

    # -------------------------------------------------------------------------
    # DAG Agent Templates
    # -------------------------------------------------------------------------
    dag=DAGConfig(
        default_module="VMM",
        state_names=[
            "abandoned", "inactive", "convenience", "driving",
            "休眠", "唤醒", "运行", "停止",
        ],
        templates=[
            DAGTemplateDef(
                name="factual_lookup",
                trigger_keywords=[
                    "是什么", "定义", "取值", "编码", "含义", "描述",
                    "有哪些", "列出", "参数", "配置", "PIN", "管脚",
                ],
                description="Factual lookup: answer questions about definitions, values, parameters",
                nodes=[
                    {"type": "search_chunks", "params": {"top_k": 10}},
                    {"type": "query_graph", "params": {}},
                ],
            ),
            DAGTemplateDef(
                name="state_transition",
                trigger_keywords=[
                    "迁移", "转移", "进入", "退出", "如何到达", "状态",
                    "Abandoned", "Inactive", "Convenience", "Driving",
                    "前置条件", "触发条件", "guard",
                ],
                description="State transition: trace state changes and their conditions",
                nodes=[
                    {"type": "query_state_machine", "params": {}},
                    {"type": "trace_path", "params": {"max_depth": 3}},
                    {"type": "search_chunks", "params": {"top_k": 5}},
                ],
            ),
            DAGTemplateDef(
                name="impact_analysis",
                trigger_keywords=[
                    "影响", "impact", "导致", "后果", "连锁", "失效",
                    "故障会影响", "会影响", "后果是什么",
                ],
                description="Impact analysis: trace downstream effects of a signal/state change",
                nodes=[
                    {"type": "analyze_impact", "params": {"max_depth": 3}},
                    {"type": "search_chunks", "params": {"top_k": 5}},
                ],
            ),
            DAGTemplateDef(
                name="path_finding",
                trigger_keywords=[
                    "如何从", "怎么从", "路径", "几步", "到达", "最短",
                    "所有路径", "经过",
                ],
                description="Path finding: find paths between two entities in the KG",
                nodes=[
                    {"type": "trace_path", "params": {"max_depth": 5}},
                    {"type": "query_graph", "params": {}},
                ],
            ),
            DAGTemplateDef(
                name="diagnostic",
                trigger_keywords=[
                    "为什么不能", "为何无法", "故障", "诊断", "失效", "错误",
                    "异常", "不工作", "无法启动", "无法进入", "检测条件",
                    "故障反应", "恢复",
                ],
                description="Diagnostic: troubleshoot why something isn't working",
                nodes=[
                    {"type": "query_rules", "params": {}},
                    {"type": "check_conflicts", "params": {}},
                    {"type": "search_chunks", "params": {"top_k": 5}},
                ],
            ),
            DAGTemplateDef(
                name="reachability_check",
                trigger_keywords=[
                    "不可达", "死锁", "活锁", "是否存在", "所有状态",
                    "连通", "可达", "永远无法",
                ],
                description="Reachability: check if states are reachable in the state machine",
                nodes=[
                    {"type": "check_reachability", "params": {}},
                    {"type": "query_state_machine", "params": {}},
                ],
            ),
        ],
        template_descriptions_prompt="""你是BCM（车身控制模块）知识库的查询路由器。
分析用户问题，选择最合适的推理模板。

可用模板：
1. factual_lookup — 事实查询（定义、取值、参数、列表）
   示例: "PEPS_UsageMode信号有哪些取值"

2. state_transition — 状态转换查询（如何进入/退出某状态）
   示例: "PEPS_UsageMode在Driving状态下有什么影响"

3. impact_analysis — 影响分析（信号/故障的下游影响）
   示例: "KeyLost会导致哪些功能失效"

4. path_finding — 路径查找（两个实体之间的依赖路径）
   示例: "从IGN1到GlobalClose的依赖路径"

5. diagnostic — 诊断查询（为什么某功能不工作）
   示例: "IGN1故障时为什么无法进入Convenience"

6. reachability_check — 可达性检查（状态是否可达）
   示例: "所有VMM状态是否都可达" """,

        keyword_override_map={
            "不吸合": "diagnostic",
            "无法启动": "diagnostic",
            "无法进入": "diagnostic",
            "无法退出": "diagnostic",
        },
    ),
)
