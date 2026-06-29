"""BCM-RAG Agent — DAG模式推理引擎（推理级DAG + 模板 + LLM参数化）。

======================================================================
DAG模式Agent — 为什么存在
======================================================================

当前三种Agent的执行模式都有局限：
  - BCMAgent: 工具按正则匹配顺序执行，无依赖关系，无并行，结果被截断
  - AgenticRAGv1: 所有检索路径无条件全量执行，无选择性，无拓扑结构
  - AgenticRAGv2: LLM选择工具但tool_plan与证据链构建完全脱节，选择结果仅用于审计追踪

DagAgent使用DAG（有向无环图）执行模型解决上述问题：
  1. LLM根据查询选择合适的推理模板，填充节点参数，可添加自定义边
  2. 节点按拓扑顺序执行，同层级无依赖节点并行执行
  3. 节点间通过显式的data_flow规则传递数据
  4. 最终由LLM消费完整DAG输出，生成含推理链的结构化答案

核心设计理念："不要固定"
  - 每个查询获得自己的DAG拓扑结构
  - 节点可动态启用/禁用
  - LLM可添加自定义边（custom_edges）
  - 数据流显式可追踪

六种预定义推理模板：
  1. factual_lookup    — 事实/定义查询（信号是什么？参数取值？）
  2. state_transition  — 状态转移推理（如何进入某状态？迁移条件？）
  3. impact_analysis   — 影响链分析（某故障会影响什么？后果？）
  4. path_finding      — 路径查找（从A如何到达B？最短几步？）
  5. diagnostic        — 故障诊断（为什么不能启动？故障如何检测？）
  6. reachability_check — 可达性检查（死锁？不可达状态？）

架构层次：
  DagAgent（主控类）
    ├── DagTemplate（六种预定义模板）
    ├── DagExecutor（拓扑排序 → 并行执行 → 数据流传递）
    ├── DagSynthesizer（LLM消费DAG结果 → 结构化答案）
    └── LLM模板选择 + 参数填充
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


# ======================================================================
# 修复Windows GBK编码问题：强制UTF-8输出
# ======================================================================

def _fix_encoding():
    """强制标准输出使用UTF-8编码，避免Windows GBK编码崩溃。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_fix_encoding()


# ======================================================================
# 数据结构定义
# ======================================================================


@dataclass
class DagNodeOutput:
    """单个DAG节点的执行输出。

    属性:
        node_id: 节点标识符（如 "intent", "sm", "rules"）
        node_type: 节点类型（intent_analysis | state_machine | rule_lookup | ...）
        status: 执行状态（success | skipped | error）
        output: 节点产出的数据字典
        error: 错误信息（仅status=error时有值）
        duration_ms: 节点执行耗时（毫秒）
    """

    node_id: str
    node_type: str
    status: str
    output: dict | None = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class DagResult:
    """DAG推理的完整执行结果。

    属性:
        question: 用户原始查询
        template: 使用的模板名称
        dag_plan: LLM生成的完整DAG执行计划（含节点和边）
        node_outputs: 各节点的执行输出（node_id → DagNodeOutput）
        execution_order: 拓扑层级列表（每层是一组可并行执行的节点ID）
        answer: LLM合成的最终答案
        confidence: 置信度评分（0.0-1.0）
        audit_trail: 完整执行追踪（含拓扑、数据流、节点详情）
        total_duration_ms: 总耗时（毫秒）
    """

    question: str
    template: str
    dag_plan: dict
    node_outputs: dict[str, DagNodeOutput] = field(default_factory=dict)
    execution_order: list[list[str]] = field(default_factory=list)
    answer: str = ""
    confidence: float = 0.0
    audit_trail: str = ""
    total_duration_ms: float = 0.0
    # ── 反思闭环 & 自检 ──
    critique: str = ""                         # Answer自检结果（PASS 或 需要修正: ...）
    reflection_log: list[str] = field(default_factory=list)  # 每轮反思的日志
    iteration_count: int = 0                   # 实际执行的迭代次数
    # ── 节点利用率追踪 ──
    node_utilization: dict[str, float] = field(default_factory=dict)  # node_type → 执行比例
    node_execution_count: dict[str, int] = field(default_factory=dict)  # node_type → 成功执行次数
    node_total_queries: int = 0                # 累计查询数（用于计算利用率）


@dataclass
class DagTemplate:
    """预定义的DAG推理模板。

    每个模板定义了：
      - name: 模板标识符
      - description: 适用场景描述（供LLM选择时参考）
      - trigger_keywords: 触发该模板的关键词列表（回退方案用）
      - nodes: 节点定义（node_id → {type, params, required}）
      - edges: 边定义（[{from, to, data_flow}]）

    data_flow语法：
      - "node_id.field → target_field" — 直接字段映射
      - "node_id.field[*].subfield → target_field" — 数组展开映射
      - "node_id.field → target_field=value" — 静态赋值
      - 多条规则用 ";" 分隔
    """

    name: str
    description: str
    trigger_keywords: list[str]
    nodes: dict[str, dict]
    edges: list[dict]


# ======================================================================
# 完整性评估数据结构
# ======================================================================


@dataclass
class CompletenessDimension:
    """单个完整性评估维度。

    属性:
        name: 维度名称（如 "state_transitions", "matched_rules"）
        score: 0.0-1.0 的完整性评分
        threshold: 最低可接受阈值
        status: "sufficient" | "insufficient" | "missing"
        detail: 人类可读的解释
        gaps: 具体的信息缺口描述列表
    """

    name: str
    score: float = 0.0
    threshold: float = 0.5
    status: str = "missing"
    detail: str = ""
    gaps: list[str] = field(default_factory=list)


@dataclass
class CompletenessReport:
    """完整性评估的完整结果。

    属性:
        overall_score: 跨所有维度的加权平均分
        dimensions: 每个维度的评估结果
        is_sufficient: 所有必要维度是否达到阈值
        gap_queries: 填补信息缺口的跟进查询
        summary: 一段话的中文摘要
    """

    overall_score: float = 0.0
    dimensions: list = field(default_factory=list)
    is_sufficient: bool = False
    gap_queries: list[str] = field(default_factory=list)
    summary: str = ""


# ======================================================================
# 六种预定义DAG模板
# ======================================================================

DAG_TEMPLATES: dict[str, DagTemplate] = {
    # =========================================================================
    # 模板1: 事实查询 — 信号定义、参数取值、功能描述
    # =========================================================================
    "factual_lookup": DagTemplate(
        name="factual_lookup",
        description="事实/定义查询。用户询问信号定义、功能描述、参数配置等事实性信息。",
        trigger_keywords=[
            "是什么", "定义", "取值", "编码", "含义", "描述",
            "有哪些", "列出", "参数", "配置", "PIN", "管脚",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "chunks"},
            {"from": "intent", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),

    # =========================================================================
    # 模板2: 状态转移推理 — 进入/退出条件、迁移触发事件
    # =========================================================================
    "state_transition": DagTemplate(
        name="state_transition",
        description="状态转移推理。用户询问状态如何进入/退出、转移条件、触发事件。",
        trigger_keywords=[
            "迁移", "转移", "进入", "退出", "如何到达",
            "状态", "Abandoned", "Inactive", "Convenience", "Driving",
            "前置条件", "触发条件", "guard",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "sm": {
                "type": "state_machine",
                "params": {"states": []},  # 由data_flow从intent填充
                "required": True,
            },
            "rules": {
                "type": "rule_lookup",
                "params": {"keywords": "", "modules": []},
                "required": True,  # 必须执行：规则是推理的核心，不能跳过
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "state_transitions", "matched_rules", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "sm",
             "data_flow": "intent.states → sm.states"},
            {"from": "intent", "to": "rules",
             "data_flow": "intent.modules → rules.modules"},
            {"from": "intent", "to": "chunks"},
            {"from": "sm", "to": "rules",
             "data_flow": "sm.transitions[*].guard → rules.keywords"},
            {"from": "intent", "to": "eval"},
            {"from": "sm", "to": "eval"},
            {"from": "rules", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),

    # =========================================================================
    # 模板3: 影响链分析 — 信号/故障/状态的下游影响
    # =========================================================================
    "impact_analysis": DagTemplate(
        name="impact_analysis",
        description="影响分析。用户询问某个信号/故障/状态会影响什么，后果是什么。",
        trigger_keywords=[
            "影响", "impact", "导致", "后果", "连锁",
            "失效", "故障会影响", "会影响", "后果是什么",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "impact": {
                "type": "impact_analysis",
                "params": {"entity": "", "entity_type": "signal"},
                "required": True,
            },
            "sm": {
                "type": "state_machine",
                "params": {"states": []},
                "required": False,
            },
            "rules": {
                "type": "rule_lookup",
                "params": {"keywords": "", "modules": []},
                "required": True,  # 必须执行：规则是推理的核心，不能跳过
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "impact_chain", "state_transitions", "matched_rules", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "impact",
             "data_flow": "intent.signals[0] → impact.entity; intent.signals → impact.entity_type=signal"},
            {"from": "intent", "to": "chunks"},
            {"from": "impact", "to": "sm",
             "data_flow": "impact.impacted[*].entity → sm.states"},
            {"from": "impact", "to": "rules",
             "data_flow": "impact.impacted[*].entity → rules.keywords"},
            {"from": "sm", "to": "rules",
             "data_flow": "sm.transitions[*].guard → rules.keywords"},
            {"from": "intent", "to": "eval"},
            {"from": "impact", "to": "eval"},
            {"from": "sm", "to": "eval"},
            {"from": "rules", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),

    # =========================================================================
    # 模板4: 路径查找 — 状态间迁移路径
    # =========================================================================
    "path_finding": DagTemplate(
        name="path_finding",
        description="路径查找。用户询问如何从一个状态到达另一个状态，最短几步。",
        trigger_keywords=[
            "如何从", "怎么从", "路径", "几步",
            "到达", "最短", "所有路径", "经过",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "path": {
                "type": "path_finder",
                "params": {"source": "", "target": ""},
                "required": True,
            },
            "sm": {
                "type": "state_machine",
                "params": {"states": []},
                "required": False,
            },
            "rules": {
                "type": "rule_lookup",
                "params": {"keywords": "", "modules": []},
                "required": True,  # 必须执行：规则是推理的核心，不能跳过
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "paths_found", "state_transitions", "matched_rules", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "path",
             "data_flow": "intent.states[0] → path.source; intent.states[-1] → path.target"},
            {"from": "intent", "to": "chunks"},
            {"from": "path", "to": "sm",
             "data_flow": "path.paths[*].sequence[*] → sm.states"},
            {"from": "path", "to": "rules",
             "data_flow": "path.paths[*].conditions → rules.keywords"},
            {"from": "sm", "to": "rules",
             "data_flow": "sm.transitions[*].guard → rules.keywords"},
            {"from": "intent", "to": "eval"},
            {"from": "path", "to": "eval"},
            {"from": "sm", "to": "eval"},
            {"from": "rules", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),

    # =========================================================================
    # 模板5: 故障诊断 — 假设检验 + 规则匹配 + 影响链
    # =========================================================================
    "diagnostic": DagTemplate(
        name="diagnostic",
        description="故障诊断。用户询问为什么不能启动、故障原因、诊断方法。",
        trigger_keywords=[
            "为什么不能", "为何无法", "故障", "诊断", "失效",
            "错误", "异常", "不工作", "无法启动", "无法进入",
            "检测条件", "故障反应", "恢复",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "rules": {
                "type": "rule_lookup",
                "params": {"keywords": "", "modules": []},
                "required": True,
            },
            "impact": {
                "type": "impact_analysis",
                "params": {"entity": "", "entity_type": "fault"},
                "required": False,
            },
            "sm": {
                "type": "state_machine",
                "params": {"states": []},
                "required": False,
            },
            "conflicts": {
                "type": "conflict_detection",
                "params": {"module": "VMM"},
                "required": False,
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "matched_rules", "impact_chain", "state_transitions", "conflicts_found", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "rules",
             "data_flow": "intent.keywords → rules.keywords; intent.modules → rules.modules"},
            {"from": "intent", "to": "chunks"},
            {"from": "intent", "to": "conflicts",
             "data_flow": "intent.modules[0] → conflicts.module"},
            {"from": "rules", "to": "impact",
             "data_flow": "rules.matched_rules[*].signals → impact.entity"},
            {"from": "rules", "to": "sm",
             "data_flow": "rules.matched_rules[*].states → sm.states"},
            {"from": "impact", "to": "sm",
             "data_flow": "impact.impacted[*].entity → sm.states"},
            {"from": "sm", "to": "rules",
             "data_flow": "sm.transitions[*].guard → rules.keywords"},
            {"from": "intent", "to": "eval"},
            {"from": "rules", "to": "eval"},
            {"from": "impact", "to": "eval"},
            {"from": "sm", "to": "eval"},
            {"from": "conflicts", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),

    # =========================================================================
    # 模板6: 可达性检查 — 不可达状态、死锁、活锁检测
    # =========================================================================
    "reachability_check": DagTemplate(
        name="reachability_check",
        description="可达性检查。用户询问是否存在不可达状态、死锁、活锁。",
        trigger_keywords=[
            "不可达", "死锁", "活锁", "是否存在",
            "所有状态", "连通", "可达", "永远无法",
        ],
        nodes={
            "intent": {
                "type": "intent_analysis",
                "params": {},
                "required": True,
            },
            "reach": {
                "type": "reachability",
                "params": {"module": "VMM"},
                "required": True,
            },
            "sm": {
                "type": "state_machine",
                "params": {"states": []},
                "required": False,
            },
            "rules": {
                "type": "rule_lookup",
                "params": {"keywords": "", "modules": []},
                "required": True,  # 必须执行：规则是推理的核心，不能跳过
            },
            "chunks": {
                "type": "chunk_search",
                "params": {"top_k": 8},
                "required": True,
            },
            "eval": {
                "type": "completeness_eval",
                "params": {"dimensions": ["intent_coverage", "reachability_issues", "state_transitions", "matched_rules", "document_chunks"]},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "reach",
             "data_flow": "intent.modules[0] → reach.module"},
            {"from": "intent", "to": "chunks"},
            {"from": "reach", "to": "sm",
             "data_flow": "reach.issues[*].state → sm.states"},
            {"from": "reach", "to": "rules",
             "data_flow": "reach.issues[*].state → rules.keywords"},
            {"from": "sm", "to": "rules",
             "data_flow": "sm.transitions[*].guard → rules.keywords"},
            {"from": "intent", "to": "eval"},
            {"from": "reach", "to": "eval"},
            {"from": "sm", "to": "eval"},
            {"from": "rules", "to": "eval"},
            {"from": "chunks", "to": "eval"},
        ],
    ),
}

# 供LLM选择模板时参考的模板描述（中文）
TEMPLATE_DESCRIPTIONS_FOR_LLM = """
你是BCM（车身控制模块）工程推理的路由器。你的任务是根据用户查询，从6个DAG模板中选择最合适的一个。

═══════════════════════════════════════════════════════════════
决策规则（必须遵守，按优先级排序）：
═══════════════════════════════════════════════════════════════

规则0（最高优先级 — 状态名检测）：
  如果查询包含状态名（Inactive / Convenience / Driving / Abandoned）
  → 绝对不要选 factual_lookup。在 state_transition / path_finding / diagnostic 中选择。
  → "Driving的定义是什么" 虽然包含"定义"，但因为出现了状态名 Driving，
    应该选 state_transition（查状态转移上下文），而非 factual_lookup。

规则1（影响分析）：
  如果查询包含"影响"/"导致"/"后果"/"失效"/"连锁"/"波及"
  → 选 impact_analysis。
  反例: "IGN1信号是什么" 不含影响词 → 不是 impact_analysis。

规则2（故障诊断）：
  如果查询包含"为什么不能"/"为何无法"/"故障"/"诊断"/"检测"/"不工作"/
  "无法启动"/"无法进入"/"不吸合"/"不自动"/"异常"
  → 选 diagnostic。
  注意: 即使查询同时包含状态名和"为什么"，也优先选 diagnostic，因为
  用户本质上是在诊断"为什么状态转移失败"。
  例: "为什么车辆无法从Inactive进入Driving" → diagnostic（不是 path_finding）。

规则3（路径查找）：
  如果查询包含"路径"/"如何从"/"怎么从"/"几步"/"经过哪些"/"最短"/
  "从...到..."结构 且 不含"为什么"/"无法"
  → 选 path_finding。
  反例: "为什么从Inactive无法进入Driving" → 含"为什么"和"无法" → diagnostic。
  反例: "从Abandoned到Driving的状态转移条件" → 是 state_transition，不是 path_finding。

规则4（可达性检查）：
  如果查询包含"死锁"/"不可达"/"是否存在"/"所有状态"/"永远无法"/"活锁"/
  "连通"/"状态机完整"
  → 选 reachability_check。

规则5（状态转移推理）：
  如果查询涉及状态进入/退出条件、迁移触发事件，且不含"为什么"/"无法"/"故障"
  → 选 state_transition。
  例: "进入Driving需要什么条件" → state_transition。
  例: "Inactive的退出条件" → state_transition。

规则6（事实查询 — 最低优先级）：
  只有纯粹的"是什么"/"定义"/"有哪些"/"参数"/"取值"/"编码"/"含义"/
  "PIN"/"管脚"查询才选 factual_lookup。
  关键反例: 如果查询同时包含"定义"和状态名 → 不是 factual_lookup。
  "PEPS_UsageMode信号有哪些取值" → factual_lookup（纯定义）。
  "PEPS_UsageMode在Driving状态下有什么影响" → impact_analysis（含"影响"）。

═══════════════════════════════════════════════════════════════
边界案例训练（常见LLM错误）：
═══════════════════════════════════════════════════════════════

❌ 错误: "为什么无法进入Driving" → path_finding
✅ 正确: → diagnostic（"为什么"/"无法"优先于路径意图）

❌ 错误: "Driving的定义是什么" → factual_lookup
✅ 正确: → state_transition（状态名出现后不选 factual_lookup）

❌ 错误: "KeyLost是什么" → factual_lookup
✅ 正确: → impact_analysis（KeyLost 是故障，应分析其影响）

❌ 错误: "从Inactive如何到达Driving" → state_transition
✅ 正确: → path_finding（"从...到达..."是路径查找的强信号）

❌ 错误: "VMM有哪些状态" → state_transition
✅ 正确: → factual_lookup（这是列举事实，不是推理状态转移）

❌ 错误: "IGN1故障" → factual_lookup
✅ 正确: → diagnostic（"故障"是诊断信号）或 impact_analysis（如果后面有"影响"）

═══════════════════════════════════════════════════════════════
模板详情（每个模板有固定的 node_id 列表，输出时必须使用这些 node_id）：
═══════════════════════════════════════════════════════════════

1. factual_lookup — 事实/定义查询
   固定 node_id: ["intent", "chunks", "eval"]
   适用: IGN1信号是什么？有哪些模块？参数配置是多少？VMM有哪些状态？
   不适用: 任何包含状态名+推理词、故障、影响、路径的查询

2. state_transition — 状态转移推理
   固定 node_id: ["intent", "sm", "rules", "chunks", "eval"]
   适用: 如何进入Driving？Inactive的退出条件？Convenience→Driving需要什么？
   不适用: 纯定义查询、"从A到B"的路径查询、含"为什么/无法"的诊断查询

3. impact_analysis — 影响链分析
   固定 node_id: ["intent", "impact", "sm", "rules", "chunks", "eval"]
   适用: KeyLost会影响什么？IGN1故障的后果？PEPS失效的影响范围？

4. path_finding — 路径查找
   固定 node_id: ["intent", "path", "sm", "rules", "chunks", "eval"]
   适用: 从Abandoned如何到Driving？经过哪些状态？最短路径？需要几步？
   不适用: 含"为什么/无法"的查询（此类是 diagnostic）

5. diagnostic — 故障诊断
   固定 node_id: ["intent", "rules", "impact", "sm", "conflicts", "chunks", "eval"]
   适用: 为什么不能启动？车窗不工作？故障如何检测？为什么无法进入X状态？

6. reachability_check — 可达性检查
   固定 node_id: ["intent", "reach", "sm", "rules", "chunks", "eval"]
   适用: 死锁？不可达状态？状态机完整吗？所有状态是否互相连通？

═══════════════════════════════════════════════════════════════
输出格式（严格JSON，node_id 必须使用上述列表中的值，不能自己编）：
═══════════════════════════════════════════════════════════════

{
  "template": "模板名",
  "reasoning": "为什么选这个模板（中文，必须引用上述决策规则编号，如'规则2触发：含为什么+无法'）",
  "nodes": {
    "intent": {"enabled": true},
    "sm": {"enabled": true, "params": {"states": ["从查询中提取的状态名"]}},
    "rules": {"enabled": true, "params": {"keywords": "", "modules": []}},
    "chunks": {"enabled": true, "params": {"top_k": 8}}
  },
  "custom_edges": []
}

注意：
- nodes 对象中只包含该模板的固定 node_id，不要用类型名（如 chunk_search）作为 node_id
- params 中可填从查询中提取的实体（状态名、信号名、模块名）
- 如果模板不需要某节点，不要包含在 nodes 中
- 对于 diagnostic 模板，conflicts 节点默认启用
"""



# ======================================================================
# 节点执行器（每种节点类型对应一个执行函数）
# ======================================================================
# 每个执行器接收六个参数：
#   pipeline: RetrievalPipeline实例
#   engine: ReasoningEngine实例
#   sm: 状态机数据字典
#   rules: 规则库数据字典
#   params: 节点参数字典（来自模板定义 + data_flow合并）
#   upstream: 上游节点输出字典（node_id → output_dict）
# 返回: 节点输出字典


def _exec_intent_analysis(pipeline, engine, sm, rules, params, upstream):
    """执行意图分析节点。

    输入: query（用户查询字符串）
    输出: modules, signals, states, functions, faults, keywords, question_type
    """
    query = params.get("query", "")
    if not query:
        return {"error": "未提供查询字符串"}

    intent = pipeline._analyze_intent(query)
    return {
        "modules": intent.get("modules", []),
        "signals": intent.get("signals", []),
        "states": intent.get("states", []),
        "functions": intent.get("functions", []),
        "faults": intent.get("faults", []),
        "keywords": intent.get("keywords", []),
        "question_type": intent.get("question_type", "factual"),
        "hint_signal_def": intent.get("hint_signal_def", False),
        "hint_transition": intent.get("hint_transition", False),
    }


def _exec_state_machine(pipeline, engine, sm, rules, params, upstream):
    """执行状态机查询节点。

    输入: states（状态名称列表）
    输出: transitions（[{source, target, guard, effect, section, module}]）
    """
    states = params.get("states", [])
    # 如果未指定状态，尝试从上游数据中收集
    if not states and sm:
        all_states = set()
        for up_data in upstream.values():
            for s in up_data.get("states", []):
                all_states.add(s)
        states = list(all_states)

    if not states:
        return {"transitions": [], "note": "未指定查询状态"}

    transitions = []
    seen = set()

    # 从加载的状态机中查找转移边
    # 兼容两种格式: 旧格式 sm["transitions"], 新格式 sm["modules"][mod]["transitions"]
    all_transitions = []
    if sm:
        if "transitions" in sm:
            all_transitions = sm["transitions"]
        elif "modules" in sm:
            for mod_data in sm["modules"].values():
                all_transitions.extend(mod_data.get("transitions", []))

    for t in all_transitions:
            source = t.get("source", "")
            target = t.get("target", "")
            if source in states or target in states:
                key = (source, target, t.get("guard", ""))
                if key not in seen:
                    seen.add(key)
                    transitions.append({
                        "source": source,
                        "target": target,
                        "guard": t.get("guard", t.get("condition", "")),
                        "effect": t.get("effect", t.get("action", "")),
                        "section": t.get("source_section", t.get("section", "")),
                        "module": sm.get("module", ""),
                    })

    # 如果状态图可用，执行 backward_chain 获取前置条件树
    if engine and engine.state_ready:
        for state in states[:5]:
            try:
                tree = engine.backward_chain(state, max_depth=2)
                if tree and tree.tree:
                    transitions.append({
                        "source": f"(进入{state}的前置条件)",
                        "target": state,
                        "guard": _flatten_condition_tree(tree.tree),
                        "effect": f"进入 {state}",
                        "section": "",
                        "module": tree.module or "",
                    })
            except Exception:
                pass

    return {"transitions": transitions, "states_queried": states}


def _exec_rule_lookup(pipeline, engine, sm, rules, params, upstream):
    """执行规则查询节点。

    输入: keywords（查询关键词）, modules（模块过滤列表）
    输出: matched_rules（[{rule_id, module, condition, action, rule_type, section, match_score}]）
    """
    keywords = params.get("keywords", "")
    modules = params.get("modules", [])

    # 从上游数据中收集关键词和模块
    if not keywords:
        kw_parts = []
        for up_data in upstream.values():
            for t in up_data.get("transitions", []):
                guard = t.get("guard", "")
                if guard:
                    kw_parts.append(guard[:100])
            for s in up_data.get("states", []):
                kw_parts.append(s)
            for kw in up_data.get("keywords", []):
                kw_parts.append(kw)
            for sig in up_data.get("signals", []):
                kw_parts.append(sig)
        keywords = " ".join(kw_parts)

    if not modules:
        mod_set = set()
        for up_data in upstream.values():
            for m in up_data.get("modules", []):
                mod_set.add(m)
        modules = list(mod_set)

    matched = []
    if rules and "rules" in rules:
        kw_lower = keywords.lower()
        for rule in rules["rules"]:
            rule_text = json.dumps(rule, ensure_ascii=False).lower()
            rule_module = str(rule.get("module", "")).lower()

            # 模块过滤
            if modules and rule_module not in [m.lower() for m in modules]:
                continue

            # 关键词匹配评分
            score = 0
            for word in kw_lower.split()[:20]:
                if len(word) > 1 and word in rule_text:
                    score += 1

            if score >= 1 or (not keywords and modules and rule_module in [m.lower() for m in modules]):
                matched.append({
                    "rule_id": rule.get("rule_id", ""),
                    "module": rule.get("module", ""),
                    "condition": str(rule.get("condition_expr", rule.get("condition", "")))[:200],
                    "action": str(rule.get("action", rule.get("action_text", "")))[:200],
                    "rule_type": rule.get("rule_type", ""),
                    "section": rule.get("section", rule.get("source_section", "")),
                    "match_score": score,
                })

    matched.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return {"matched_rules": matched[:10], "keywords_used": keywords}


def _exec_path_finder(pipeline, engine, sm, rules, params, upstream):
    """执行路径查找节点。

    输入: source（源状态）, target（目标状态）
    输出: paths（[{sequence, hops, transitions, conditions}]）, total_paths, shortest_hops
    """
    source = params.get("source", "")
    target = params.get("target", "")

    # 如果未指定，从上游意图分析中获取
    if not source or not target:
        for up_data in upstream.values():
            states = up_data.get("states", [])
            if len(states) >= 2 and not source:
                source = states[0]
                target = states[-1]

    if not source or not target:
        return {"paths": [], "error": "未指定源状态和目标状态"}

    if not engine or not engine.state_ready:
        return {"paths": [], "error": "状态图未加载，无法执行路径查找"}

    try:
        result = engine.path_query(source, target, max_hops=6)
        paths = []
        for p in result.get("paths", [])[:5]:
            paths.append({
                "sequence": p.get("sequence", []),
                "hops": p.get("hops", 0),
                "transitions": p.get("transitions", []),
                "conditions": p.get("total_conditions", []),
            })
        return {
            "paths": paths,
            "source": source,
            "target": target,
            "total_paths": result.get("total_paths", 0),
            "shortest_hops": result.get("shortest_hops", -1),
        }
    except Exception as e:
        return {"paths": [], "error": str(e)}


def _exec_impact_analysis(pipeline, engine, sm, rules, params, upstream):
    """执行影响分析节点（前向链推理）。

    输入: entity（实体名称）, entity_type（实体类型: signal/state/fault）
    输出: impacted（[{entity, entity_type, module, depth, via, effect}]）
    """
    entity = params.get("entity", "")
    entity_type = params.get("entity_type", "signal")

    # 如果未指定实体，从上游数据中获取
    if not entity:
        for up_data in upstream.values():
            signals = up_data.get("signals", [])
            faults = up_data.get("faults", [])
            if signals:
                entity = signals[0]
                entity_type = "signal"
                break
            if faults:
                entity = faults[0]
                entity_type = "fault"
                break

    if not entity:
        return {"impacted": [], "error": "未指定分析实体"}

    if not engine or not engine.kg_ready:
        return {"impacted": [], "error": "知识图谱未加载，无法执行前向链"}

    try:
        report = engine.forward_chain(entity, entity_type=entity_type, max_depth=5)
        impacted = []
        for imp in report.impacted[:15]:
            impacted.append({
                "entity": imp.entity,
                "entity_type": imp.entity_type,
                "module": imp.module,
                "depth": imp.depth,
                "via": imp.via,
                "effect": imp.effect,
            })
        return {
            "impacted": impacted,
            "trigger": entity,
            "trigger_type": entity_type,
            "total_impacted": report.total_impacted,
        }
    except Exception as e:
        return {"impacted": [], "error": str(e)}


def _exec_conflict_detection(pipeline, engine, sm, rules, params, upstream):
    """执行规则冲突检测节点。

    输入: module（模块名称）
    输出: conflicts（[{rule1, rule2, type, detail}]）
    """
    module = params.get("module", self._domain.dag.default_module if self._domain and self._domain.dag.default_module else "VMM")

    if not engine or not engine.kg_ready:
        return {"conflicts": [], "error": "知识图谱未加载，无法执行冲突检测"}

    try:
        conflicts = engine.detect_conflicts(module=module)
        return {"conflicts": conflicts, "module": module, "total": len(conflicts)}
    except Exception as e:
        return {"conflicts": [], "error": str(e)}


def _exec_reachability(pipeline, engine, sm, rules, params, upstream):
    """执行可达性分析节点。

    输入: module（模块名称）
    输出: issues（[{type, state, detail, recommendation}]）
    """
    module = params.get("module", self._domain.dag.default_module if self._domain and self._domain.dag.default_module else "VMM")

    if not engine or not engine.state_ready:
        return {"issues": [], "error": "状态图未加载，无法执行可达性分析"}

    try:
        issues = engine.reachability_analysis(module=module)
        return {"issues": issues, "module": module, "total": len(issues)}
    except Exception as e:
        return {"issues": [], "error": str(e)}


def _exec_chunk_search(pipeline, engine, sm, rules, params, upstream):
    """执行文档片段检索节点。

    输入: query（搜索查询）, top_k（返回数量）
    输出: chunks（[{chunk_id, chunk_type, module, section_path, text, score}]）

    检索策略：使用原始问题全文检索。检索管线内置 query expansion 自动补充关联词
    （如"以太网"→"Ethernet SOMEIP"，"分工"→"R&A S&A 供应商 CH事业部"等）。
    """
    search_query = params.get("query", "")
    top_k = params.get("top_k", 8)

    # 如果未指定查询，从上游关键词中收集
    if not search_query:
        for up_data in upstream.values():
            kws = up_data.get("keywords", [])
            if kws:
                search_query = " ".join(kws[:10])
                break

    if not search_query:
        return {"chunks": [], "error": "未提供搜索查询"}

    try:
        result = pipeline.search(search_query, top_k=top_k, enable_llm=False)
        chunks = []
        for entry in result.get("merged", [])[:top_k]:
            chunk = entry.get("chunk", {})
            chunks.append({
                "chunk_id": chunk.get("chunk_id", ""),
                "chunk_type": chunk.get("chunk_type", ""),
                "module": chunk.get("module", ""),
                "section_path": chunk.get("section_path", ""),
                "section_title": chunk.get("section_title", ""),
                "text": chunk.get("text", "")[:2500],
                "score": entry.get("score", 0),
            })
        return {"chunks": chunks, "query_used": search_query}
    except Exception as e:
        return {"chunks": [], "error": str(e)}


# ======================================================================
# 完整性评估执行器
# ======================================================================

# 维度定义: (维度名, 上游字段, 充分阈值, 评分上限除数, 权重)
_COMPLETENESS_DIMENSION_DEFS: dict[str, tuple] = {
    "intent_coverage":     ("_intent_entities", 2, 5, 0.05),
    "state_transitions":   ("transitions",      2, 5, 0.20),
    "matched_rules":       ("matched_rules",    2, 5, 0.20),
    "impact_chain":        ("impacted",         1, 3, 0.15),
    "paths_found":         ("paths",            1, 3, 0.15),
    "document_chunks":     ("chunks",           3, 5, 0.15),
    "reachability_issues": ("issues",           1, 3, 0.05),
    "conflicts_found":     ("conflicts",        1, 3, 0.05),
}

# LLM gap analysis 系统提示词
_GAP_ANALYSIS_SYSTEM_PROMPT = (
    "你是BCM工程信息完整性评估专家。"
    "只输出JSON，不要其他文字。"
)


def _build_gap_analysis_prompt(
    question: str,
    question_type: str,
    gathered: dict,
    dim_results: list[dict],
) -> str:
    """构建LLM gap analysis的提示词。

    参数:
        question: 用户原始查询
        question_type: 问题类型（factual/reasoning/diagnostic）
        gathered: 已收集信息的计数摘要
        dim_results: 各维度的评估结果

    返回:
        格式化的提示词字符串
    """
    insufficient_dims = [d for d in dim_results if d.get("status") != "sufficient"]
    dim_lines = []
    for d in dim_results:
        icon = {"sufficient": "[OK]", "insufficient": "[~]", "missing": "[!!]"}.get(
            d.get("status", ""), "[?]"
        )
        dim_lines.append(
            f"  {icon} {d['name']}: {d['score']:.0%} "
            f"(找到 {d.get('found', 0)} 项, 阈值 {d.get('threshold', 0)})"
        )

    return f"""你是BCM工程信息完整性评估专家。

用户问题: {question}
问题类型: {question_type}

已收集信息摘要:
- 状态转移: {gathered.get('transitions', 0)} 条
- 匹配规则: {gathered.get('rules', 0)} 条
- 查找路径: {gathered.get('paths', 0)} 条
- 影响实体: {gathered.get('impacts', 0)} 个
- 文档片段: {gathered.get('chunks', 0)} 条
- 识别信号: {gathered.get('signals', [])}
- 识别状态: {gathered.get('states', [])}
- 识别故障: {gathered.get('faults', [])}

各维度评估结果:
{chr(10).join(dim_lines)}

{'以下维度存在信息缺口，请识别具体缺失内容:' if insufficient_dims else '信息看似充足，但仍请检查是否有潜在缺口:'}

请评估上述信息是否足以完整回答用户问题。输出严格JSON格式:

{{
  "overall_score": 0.0-1.0,
  "is_sufficient": true/false,
  "missing_signals": ["信号名"],
  "missing_transitions": ["缺失的转移描述"],
  "missing_rules": ["缺失的规则描述"],
  "missing_sections": ["缺失的文档章节描述"],
  "follow_up_queries": ["用于填补缺口的跟进查询"],
  "summary": "中文摘要，简要说明信息完整性和缺口"
}}

如果信息充分，missing_* 字段可以为空数组。"""


def _exec_completeness_eval(pipeline, engine, sm, rules, params, upstream):
    """执行完整性评估节点。

    两阶段评估:
      Stage A — 基于规则的统计评估（始终执行，无LLM成本）
      Stage B — 基于LLM的深度缺口分析（仅当LLM可用且有缺口时执行）

    输入（从 upstream 收集）:
      - transitions: 状态转移边计数
      - matched_rules: 匹配规则计数
      - paths: 路径计数
      - impacted: 影响实体计数
      - chunks: 文档片段计数
      - issues: 可达性问题计数
      - conflicts: 冲突计数
      - signals, states, faults: 意图分析识别的实体
      - question_type: 问题类型

    参数:
        dimensions: 要评估的维度列表（从模板 params 传入）
        question: 用户原始查询（从 params 传入）

    输出:
        overall_score, dimensions, is_sufficient, gap_queries, summary
    """
    query = params.get("query", "")
    requested_dims = params.get("dimensions", [])

    # ---- 从 upstream 收集数据 ----
    gathered: dict[str, Any] = {
        "transitions": 0, "rules": 0, "paths": 0,
        "impacts": 0, "chunks": 0, "reachability_issues": 0,
        "conflicts": 0, "signals": [], "states": [], "faults": [],
        "question_type": "factual", "modules": [],
    }
    # 意图分析实体总数（用于 intent_coverage 维度）
    intent_entities = 0

    for _node_id, data in upstream.items():
        if not isinstance(data, dict):
            continue
        if "transitions" in data:
            gathered["transitions"] += len(data["transitions"])
        if "matched_rules" in data:
            gathered["rules"] += len(data["matched_rules"])
        if "paths" in data:
            gathered["paths"] += len(data["paths"])
        if "impacted" in data:
            gathered["impacts"] += len(data["impacted"])
        if "chunks" in data:
            gathered["chunks"] += len(data["chunks"])
        if "issues" in data:
            gathered["reachability_issues"] += len(data["issues"])
        if "conflicts" in data:
            gathered["conflicts"] += len(data["conflicts"])
        if "signals" in data:
            gathered["signals"] = data["signals"]
        if "states" in data:
            gathered["states"] = data["states"]
        if "faults" in data:
            gathered["faults"] = data["faults"]
        if "question_type" in data:
            gathered["question_type"] = data["question_type"]
        if "modules" in data:
            gathered["modules"] = data["modules"]

    # 计算意图分析实体总数
    intent_entities = (
        len(gathered.get("signals", []))
        + len(gathered.get("states", []))
        + len(gathered.get("faults", []))
        + len(gathered.get("modules", []))
    )

    # ---- Stage A: 基于规则的统计评估 ----
    # 字段名到 gathered key 的映射
    _field_to_key = {
        "intent_coverage":     "_intent",
        "state_transitions":   "transitions",
        "matched_rules":       "rules",
        "impact_chain":        "impacts",
        "paths_found":         "paths",
        "document_chunks":     "chunks",
        "reachability_issues": "reachability_issues",
        "conflicts_found":     "conflicts",
    }

    dim_results: list[dict] = []
    total_weight = 0.0
    weighted_sum = 0.0
    has_gaps = False

    for dim_name in requested_dims:
        defn = _COMPLETENESS_DIMENSION_DEFS.get(dim_name)
        if not defn:
            continue

        _field, threshold, cap_divisor, weight = defn

        # 获取该维度的实际计数
        if dim_name == "intent_coverage":
            count = intent_entities
        else:
            gkey = _field_to_key.get(dim_name, "")
            count = gathered.get(gkey, 0)

        # 计算评分: min(count / cap_divisor, 1.0)
        score = min(count / max(cap_divisor, 1), 1.0)

        # 确定状态
        if count >= threshold:
            status = "sufficient"
            gaps = []
            detail = f"找到 {count} 项，满足阈值 {threshold}"
        elif count > 0:
            status = "insufficient"
            gaps = [f"{dim_name} 仅找到 {count} 项，不足 {threshold} 项"]
            detail = f"找到 {count} 项，低于阈值 {threshold}"
            has_gaps = True
        else:
            status = "missing"
            gaps = [f"{dim_name} 未找到任何数据"]
            detail = "未找到数据"
            has_gaps = True

        dim_results.append({
            "name": dim_name,
            "score": round(score, 2),
            "threshold": threshold,
            "status": status,
            "detail": detail,
            "gaps": gaps,
            "found": count,
        })

        total_weight += weight
        weighted_sum += score * weight

    overall_score = round(weighted_sum / max(total_weight, 0.01), 2)
    is_sufficient = overall_score >= 0.60 and not has_gaps

    # ---- Stage B: LLM 深度缺口分析 ----
    gap_queries: list[str] = []
    summary = ""
    llm_used = False

    if has_gaps and _has_llm_available(pipeline):
        try:
            llm = _get_llm_from_pipeline(pipeline)
            prompt = _build_gap_analysis_prompt(
                query, gathered.get("question_type", "factual"),
                gathered, dim_results,
            )
            result = llm.answer(
                evidence=prompt,
                query=query,
                system_prompt=_GAP_ANALYSIS_SYSTEM_PROMPT,
            )
            text = result.get("answer", "")

            # 尝试解析 JSON
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                llm_data = json.loads(match.group(0))
                # LLM 的 overall_score 作为参考，与规则评分取平均
                llm_score = float(llm_data.get("overall_score", overall_score))
                overall_score = round((overall_score + llm_score) / 2, 2)
                is_sufficient = bool(llm_data.get("is_sufficient", is_sufficient))
                gap_queries = llm_data.get("follow_up_queries", [])
                summary = llm_data.get("summary", "")
                llm_used = True
        except Exception:
            pass  # LLM 失败时回退到规则结果

    # ---- 回退: 无 LLM 时生成规则型摘要和跟进查询 ----
    if not llm_used:
        sufficient_count = sum(
            1 for d in dim_results if d["status"] == "sufficient"
        )
        missing_dims = [d["name"] for d in dim_results if d["status"] != "sufficient"]
        summary = (
            f"信息完整性评估: {sufficient_count}/{len(dim_results)} 维度充分。"
        )
        if missing_dims:
            summary += f"缺口维度: {', '.join(missing_dims)}。"

        # 仅在有缺口时生成跟进查询
        if has_gaps:
            if gathered["transitions"] == 0 and gathered.get("states"):
                gap_queries.append(
                    f"查找 {gathered['states'][0]} 的状态转移条件"
                )
            if gathered["rules"] == 0:
                kw = query[:60]
                gap_queries.append(f"搜索与 '{kw}' 相关的规则")
            if gathered["chunks"] == 0:
                gap_queries.append(f"搜索文档中与 '{query[:60]}' 相关的章节")
            if gathered["impacts"] == 0 and gathered.get("signals"):
                gap_queries.append(
                    f"分析信号 {gathered['signals'][0]} 的下游影响"
                )

    return {
        "overall_score": overall_score,
        "dimensions": dim_results,
        "is_sufficient": is_sufficient,
        "gap_queries": gap_queries,
        "summary": summary,
        "llm_used": llm_used,
        "report_type": "completeness_eval",
    }


def _has_llm_available(pipeline) -> bool:
    """检查 pipeline 是否有可用的 LLM。"""
    return hasattr(pipeline, "llm") and pipeline.llm is not None


def _get_llm_from_pipeline(pipeline):
    """从 pipeline 获取 LLM 实例。"""
    if hasattr(pipeline, "llm") and pipeline.llm is not None:
        return pipeline.llm
    # 回退: 尝试从 retrieval.llm_answer 创建
    try:
        from retrieval.llm_answer import LLMAnswerGenerator
        return LLMAnswerGenerator()
    except Exception:
        return None


# 节点执行器注册表（node_type → 执行函数）
NODE_EXECUTORS: dict[str, Callable] = {
    "intent_analysis": _exec_intent_analysis,
    "state_machine": _exec_state_machine,
    "rule_lookup": _exec_rule_lookup,
    "path_finder": _exec_path_finder,
    "impact_analysis": _exec_impact_analysis,
    "conflict_detection": _exec_conflict_detection,
    "reachability": _exec_reachability,
    "chunk_search": _exec_chunk_search,
    "completeness_eval": _exec_completeness_eval,
}


# ======================================================================
# 数据流引擎 — 解析data_flow规则，在上游节点间传递数据
# ======================================================================


def _resolve_data_flow(
    node_outputs: dict[str, dict],
    data_flow_rule: str,
) -> dict[str, Any]:
    """解析data_flow规则，从上游节点输出中提取数据。

    支持的语法：
      - "node_id.field → target_field" — 直接字段访问
      - "node_id.field → target_field=value" — 静态赋值
      - "node_id.field[*].subfield → target_field" — 数组展开
      - "规则1; 规则2" — 多条规则用分号分隔

    参数:
        node_outputs: {node_id: output_dict} 上游节点的输出
        data_flow_rule: 数据流规则字符串

    返回:
        {target_field: value} 待合并到下游节点参数中的字典
    """
    result: dict[str, Any] = {}

    if not data_flow_rule:
        return result

    rules = [r.strip() for r in data_flow_rule.split(";") if r.strip()]

    for rule in rules:
        parts = rule.split("→")
        if len(parts) != 2:
            continue

        source_expr = parts[0].strip()
        target_expr = parts[1].strip()

        # 解析源表达式: "node_id.field" 或 "node_id.field[*].subfield"
        # 先将 "[*]" 拆分为独立路径段
        raw_parts = source_expr.split(".")
        if len(raw_parts) < 2:
            continue

        src_node = raw_parts[0]
        src_field_path = []
        for part in raw_parts[1:]:
            if "[*]" in part:
                before, after = part.split("[*]", 1)
                if before:
                    src_field_path.append(before)
                src_field_path.append("[*]")
                if after:
                    src_field_path.append(after)
            else:
                src_field_path.append(part)

        # 解析目标表达式: "target_field" 或 "target_field=value"
        if "=" in target_expr:
            target_field, target_value = target_expr.split("=", 1)
            target_field = target_field.strip()
            # 静态赋值（例如 entity_type=signal）
            result[target_field] = target_value.strip()
            continue
        else:
            target_field = target_expr.strip()

        # 获取上游节点输出
        up_output = node_outputs.get(src_node, {})
        if not up_output:
            continue

        # 按字段路径导航取值
        value = _navigate_field_path(up_output, src_field_path)
        if value is not None:
            result[target_field] = value

    return result


def _navigate_field_path(data: dict, field_path: list[str]) -> Any:
    """按字段路径从嵌套字典中取值，支持数组展开。

    示例:
      ["states"] → data["states"]
      ["transitions", "[*]", "guard"] → 展开所有transition的guard字段并拼接
      ["signals", "0"] → data["signals"][0]
    """
    current = data
    for i, segment in enumerate(field_path):
        if segment == "[*]":
            # 数组展开：剩余路径应用到每个元素
            if isinstance(current, list):
                rest_path = field_path[i + 1 :]
                if rest_path:
                    results = []
                    for item in current:
                        if isinstance(item, dict):
                            val = _navigate_field_path(item, rest_path)
                            if val is not None:
                                results.append(val)
                    return " ".join(str(r) for r in results) if results else None
                return current
            return None
        elif isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            idx = int(segment)
            if idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def _merge_upstream_data(
    node_id: str,
    edges: list[dict],
    node_outputs: dict[str, dict],
) -> dict[str, Any]:
    """根据边定义中的data_flow规则，合并上游节点的数据。

    对每条指向当前节点的边，解析其data_flow规则，
    从上游节点输出中提取数据，合并为参数字典。

    参数:
        node_id: 当前节点ID
        edges: 所有边的列表
        node_outputs: 所有已执行节点的输出

    返回:
        合并后的参数字典
    """
    merged: dict[str, Any] = {}

    for edge in edges:
        if edge.get("to") == node_id:
            upstream_id = edge.get("from", "")
            data_flow = edge.get("data_flow", "")
            if upstream_id in node_outputs:
                resolved = _resolve_data_flow(
                    {upstream_id: node_outputs[upstream_id]},
                    data_flow,
                )
                merged.update(resolved)

    return merged


# ======================================================================
# DAG执行引擎 — 拓扑排序 + 层级并行执行
# ======================================================================


class DagExecutor:
    """DAG执行引擎。

    算法流程：
      1. 从边列表构建邻接表和入度表
      2. Kahn算法拓扑排序 → 得到执行层级（每层内节点可并行）
      3. 逐层执行：同层节点用ThreadPoolExecutor并行执行
      4. 每层完成后解析data_flow规则，将数据传递给下一层
      5. 汇总所有节点输出 → DagResult
    """

    # 最大并行worker数（避免资源竞争）
    MAX_WORKERS = 4

    def execute(
        self,
        dag_plan: dict,
        pipeline,
        engine,
        sm: dict | None,
        rules: dict | None,
        query: str,
    ) -> DagResult:
        """执行一个DAG计划。

        参数:
            dag_plan: LLM生成的执行计划（含nodes和edges）
            pipeline: RetrievalPipeline实例
            engine: ReasoningEngine实例
            sm: 状态机数据字典
            rules: 规则库数据字典
            query: 用户原始查询

        返回:
            DagResult（含所有节点输出和执行元数据）
        """
        t_start = time.time()

        nodes = dag_plan.get("nodes", {})
        edges = dag_plan.get("edges", [])
        template_name = dag_plan.get("template", "unknown")

        # 构建邻接表和入度表
        adj: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = defaultdict(int)

        all_node_ids = set(nodes.keys())
        for nid in all_node_ids:
            in_degree[nid] = 0

        for edge in edges:
            src = edge.get("from", "")
            tgt = edge.get("to", "")
            if src in all_node_ids and tgt in all_node_ids:
                adj[src].append(tgt)
                in_degree[tgt] += 1

        # Kahn算法：按拓扑层级分组
        queue = deque([nid for nid in all_node_ids if in_degree[nid] == 0])
        levels: list[list[str]] = []
        visited: set[str] = set()

        while queue:
            level = list(queue)
            levels.append(level)
            next_queue = deque()

            for nid in level:
                visited.add(nid)
                for neighbor in adj.get(nid, []):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0 and neighbor not in visited:
                        next_queue.append(neighbor)

            queue = next_queue

        # 处理可能的孤立节点（理论上DAG不应该有）
        remaining = all_node_ids - visited
        if remaining:
            levels.append(list(remaining))

        # 逐层执行
        node_outputs: dict[str, DagNodeOutput] = {}
        execution_order: list[list[str]] = []

        for level in levels:
            # 过滤出已启用的节点
            enabled_nodes = [
                nid for nid in level
                if nodes.get(nid, {}).get("enabled", True)
            ]
            execution_order.append(enabled_nodes)

            if not enabled_nodes:
                continue

            if len(enabled_nodes) == 1:
                # 单节点：直接执行
                nid = enabled_nodes[0]
                upstream = {
                    uid: no.output
                    for uid, no in node_outputs.items()
                    if no.status == "success" and no.output
                }
                node_outputs[nid] = self._execute_node(
                    nid, nodes[nid], edges, upstream,
                    pipeline, engine, sm, rules, query,
                )
            else:
                # 多节点：并行执行
                with ThreadPoolExecutor(
                    max_workers=min(self.MAX_WORKERS, len(enabled_nodes))
                ) as executor:
                    futures = {}
                    for nid in enabled_nodes:
                        upstream = {
                            uid: no.output
                            for uid, no in node_outputs.items()
                            if no.status == "success" and no.output
                        }
                        futures[
                            executor.submit(
                                self._execute_node,
                                nid, nodes[nid], edges, upstream,
                                pipeline, engine, sm, rules, query,
                            )
                        ] = nid

                    for future in as_completed(futures):
                        nid = futures[future]
                        try:
                            node_outputs[nid] = future.result()
                        except Exception as e:
                            node_outputs[nid] = DagNodeOutput(
                                node_id=nid,
                                node_type=nodes[nid].get("type", "?"),
                                status="error",
                                error=str(e),
                            )

        total_duration = (time.time() - t_start) * 1000

        return DagResult(
            question=query,
            template=template_name,
            dag_plan=dag_plan,
            node_outputs=node_outputs,
            execution_order=execution_order,
            total_duration_ms=total_duration,
        )

    def _execute_node(
        self,
        node_id: str,
        node_config: dict,
        edges: list[dict],
        upstream: dict[str, dict],
        pipeline,
        engine,
        sm,
        rules,
        query: str,
    ) -> DagNodeOutput:
        """执行单个DAG节点。

        流程：
          1. 从注册表中查找节点类型对应的执行函数
          2. 合并节点自身参数 + data_flow传递的上游数据
          3. 调用执行函数
          4. 包装为DagNodeOutput返回
        """
        t0 = time.time()
        node_type = node_config.get("type", "?")

        executor_fn = NODE_EXECUTORS.get(node_type)
        if not executor_fn:
            return DagNodeOutput(
                node_id=node_id,
                node_type=node_type,
                status="error",
                error=f"未知节点类型: {node_type}",
            )

        # 合并参数：节点自身参数 + data_flow上游数据
        params = dict(node_config.get("params", {}))
        if "query" not in params:
            params["query"] = query

        flow_params = _merge_upstream_data(node_id, edges, upstream)
        # 只填充节点参数中为空的字段
        for k, v in flow_params.items():
            if not params.get(k):
                params[k] = v

        try:
            output = executor_fn(pipeline, engine, sm, rules, params, upstream)
            duration = (time.time() - t0) * 1000
            return DagNodeOutput(
                node_id=node_id,
                node_type=node_type,
                status="success",
                output=output,
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.time() - t0) * 1000
            return DagNodeOutput(
                node_id=node_id,
                node_type=node_type,
                status="error",
                error=str(e),
                duration_ms=duration,
            )


# ======================================================================
# DAG答案合成器 — 消费DAG执行结果，由LLM生成结构化答案
# ======================================================================


class DagSynthesizer:
    """DAG答案合成器。

    与管道中LLMAnswerGenerator的区别：
      - LLMAnswerGenerator: 接收evidence纯文本，做简单QA
      - DagSynthesizer: 接收完整DAG拓扑 + 各节点输出 + 数据流路径，
        生成含推理链、证据引用和置信度评估的结构化答案

    工作流程：
      1. 将DAG执行结果格式化为结构化prompt
      2. 每个节点输出作为独立section
      3. DAG拓扑展示推理步骤之间的依赖关系
      4. LLM生成含完整推理链的最终答案
    """

    def __init__(self, llm_generator):
        """初始化合成器。

        参数:
            llm_generator: LLMAnswerGenerator实例（复用现有LLM客户端）
        """
        self.llm = llm_generator

    def synthesize(
        self,
        question: str,
        dag_plan: dict,
        dag_result: DagResult,
        date_context: str = "",
    ) -> dict:
        """合成最终答案。

        参数:
            question: 用户原始查询
            dag_plan: 执行的DAG计划
            dag_result: DAG执行结果
            date_context: 日期解析上下文（可选）
        """
        template_name = dag_result.template if dag_result.template else dag_plan.get("template", "")
        system_prompt = self._build_system_prompt(template_name)
        user_prompt = self._build_user_prompt(
            question, dag_plan, dag_result, date_context,
        )

        try:
            result = self.llm.answer(
                evidence=user_prompt,
                query=question,
                system_prompt=system_prompt,
            )
            answer_text = result.get("answer", "")
            if answer_text.startswith("[LLM Error]"):
                return self._fallback_synthesize(question, dag_result)

            # 从LLM输出中提取置信度
            confidence = self._extract_confidence(answer_text)

            # 动态校验：用DAG实际执行统计验证LLM输出的置信度是否合理
            confidence = self._validate_confidence(confidence, answer_text, dag_result)

            citations = self._extract_citations(answer_text, dag_result)

            return {
                "answer": answer_text,
                "confidence": confidence,
                "citations": citations,
                "model": result.get("model", ""),
                "usage": result.get("usage", {}),
            }
        except Exception:
            return self._fallback_synthesize(question, dag_result)

    # ── 模板主数据源映射 ──────────────────────────────────────────
    # 每个模板有一个或多个"主数据源"节点。主数据源有结果时，
    # 辅助数据源为空的扣分应该减免（因为辅助数据对该问题类型本来就非必需）。
    _TEMPLATE_PRIMARY_SOURCES: dict[str, list[str]] = {
        "factual_lookup":    ["chunks"],
        "state_transition":  ["sm"],           # 核心是转移边，rules 是辅助
        "impact_analysis":   ["impact"],        # 核心是影响链
        "path_finding":      ["path"],          # 核心是路径
        "diagnostic":        ["rules"],         # 核心是规则匹配
        "reachability_check": ["reach"],        # 核心是可达性分析
    }

    def _build_system_prompt(self, template_name: str = "") -> str:
        """构建系统提示词，可选传入模板名用于动态调整评分规则。"""
        # 根据模板生成动态评分指导
        primary = self._TEMPLATE_PRIMARY_SOURCES.get(template_name, [])
        rule_penalty_hint = ""
        sm_penalty_hint = ""
        impact_penalty_hint = ""
        path_penalty_hint = ""

        if primary:
            if "sm" in primary:
                rule_penalty_hint = (
                    "    注意：当前模板以 state_machine 为主数据源，"
                    "rule_lookup 为空只扣 -0.05（而非 -0.15），"
                    "因为规则库不一定包含状态转移定义。"
                )
            if "rules" in primary:
                sm_penalty_hint = (
                    "    注意：当前模板以 rule_lookup 为主数据源，"
                    "state_machine 为空只扣 -0.05。"
                )
            if "impact" in primary:
                sm_penalty_hint = (
                    "    注意：当前模板以 impact_analysis 为主数据源，"
                    "state_machine 为空只扣 -0.05。"
                )
            if "path" in primary:
                sm_penalty_hint = (
                    "    注意：当前模板以 path_finder 为主数据源，"
                    "state_machine 为空只扣 -0.05。"
                )

        return if self._domain is not None and self._domain.llm_prompts.agent_system_prompt:
            system = self._domain.llm_prompts.agent_system_prompt
            if self._domain.llm_prompts.agent_company_context:
                system += "

" + self._domain.llm_prompts.agent_company_context
            return system
        return f"""你是埃泰克公司的BCM技术文档知识库问答专家。你的视角是埃泰克（供应商方）。

你的任务是根据检索到的文档内容，组织为结构化的工程回答。

角色映射（非常重要）：
- 文档中的"供应商" = 埃泰克（我们公司）
- 文档中的"CH事业部" = 客户（甲方/北汽）
- "乙方" = 埃泰克（我们公司）
- "甲方" = 客户（CH事业部/北汽）
- 分工表中 A(Accountable)=负责, R(Review)=评审, S(Support)=协助
- 分工表中 R&A=客户负责+供应商协助, S&A=供应商协助+客户验收

核心原则：
- 仅基于提供的文档片段中的数据回答，不要编造证据中不存在的信息
- 如果关键数据缺失，直接说明"根据现有文档无法确定"
- state_machine 节点的转移边是系统设计文档的原文引用，属于一级证据
- **严禁从目录标题推断结论**：看到"总线相关要求"章节名不能推断"必有以太网"；看到"信息安全"不能推断"必有以太网安全"。必须有明确的原文引用（如"支持网络唤醒(CAN/CANFD/以太网)"）才算证据。目录标题不是证据

输出规则：
1. **结论就是数据，不是摘要**：结论部分必须逐条列出证据中的所有具体数据（时间、数值、状态名、信号名、条件等）。禁止在结论里写"文档中列出了..."这类概括性废话——直接把数据列出来
2. 每个结论必须引用具体的章节号和文档原文，**严禁**输出DAG内部节点名（如 [intent_analysis]、[chunk_search]、[completeness_eval]）
3. 状态转换问题：逐条列出**所有**转移边，每条包含源状态→目标状态、guard条件、触发效果、来源章节。不要只列一两条就停，把所有返回的转移边都写出来
4. 信号问题：说明信号来源、用途、取值含义、相关模块，列出所有匹配到的信号定义
5. 故障诊断问题：逐条列出检测条件、故障反应和恢复方式，不要省略任何一条匹配规则
6. **禁止编造过滤条件**：这是最容易犯的错误。文档没说的条件你不准自己加。比如文档没说"必须在评审日期之后"，你就不能以此为由排除EP1/EP2。你只能使用文档中明确给出的筛选条件
7. **禁止省略**：这是最重要的规则。证据中有10条数据就列出10条，不要只列2-3条然后说"等"。每一条都要写出来。如果用户问"哪些节点不足半年"，就把所有满足条件的都列出来，不要说"最相关的是X"，应该说"以下N个都满足条件"
8. **做判断必须给出理由**：如果你做了任何筛选或排序，必须在分析里逐条解释：(a)用了什么筛选条件、(b)这个条件来自文档哪里还是你的推断、(c)每条数据为什么入选或落选。让读者能完整审计你的每一步判断
9. **详细展开**：不要用一句话总结。分析部分必须逐条展开论述，每条用独立段落。宁可重复也不要遗漏
10. 使用结构化格式，回答**必须详尽完整**，禁止一两句话就结束
11. **禁止从章节标题推断内容**：目录标题（如"1.4.1 总线相关要求"）只是标题，不代表正文内容。不能说"因为存在XX章节标题，所以必然包含YY内容"。必须有正文原文引用才能作为证据

═══════════════════════════════════════════════════════════════
置信度评分标准（必须严格按此计算，禁止写死固定值）：
═══════════════════════════════════════════════════════════════

置信度 = 基础分 + 加分项 - 扣分项

基础分（0.3-0.5）：
  - 0.5: DAG中所有启用节点全部成功执行
  - 0.4: 80%以上节点成功
  - 0.3: 不到80%节点成功

加分项（每项+0.05到+0.15）：
  +0.15: state_machine节点提供了具体的转移边和guard条件
  +0.15: rule_lookup节点匹配到2条以上相关规则
  +0.10: impact_analysis节点找到了影响链（impacted > 0）
  +0.10: path_finder节点找到了有效路径（total_paths > 0）
  +0.10: conflict_detection或reachability节点有实际发现
  +0.10: completeness_eval节点整体评分 >= 0.7（信息充分）
  +0.05: completeness_eval节点整体评分 >= 0.5（信息基本可用）
  +0.05: chunk_search检索到3条以上相关文档片段
  +0.05: 多个节点输出互相印证（如状态转移与规则匹配一致）

扣分项（每项-0.05到-0.20）：
  -0.20: 有节点执行失败
  -0.15: state_machine返回空（0条转移边）——但若该模板主数据源非sm，则只扣 -0.05
  -0.15: rule_lookup返回空（0条匹配规则）——但若该模板主数据源非rules，则只扣 -0.05
  -0.15: completeness_eval节点整体评分 < 0.4（信息严重不足）
  -0.10: completeness_eval节点报告缺失关键维度
  -0.10: chunk_search返回空或完全不相关
  -0.10: 意图分析提取到的实体少于2个
  -0.05: 数据流传递断裂（上游有输出但下游未收到）

模板感知的扣分减免（根据当前模板 "{template_name}" 的主数据源 {primary}）：
{rule_penalty_hint}{sm_penalty_hint}{impact_penalty_hint}{path_penalty_hint}

最终置信度钳制在 [0.10, 0.95] 范围内。
在回答末尾输出: CONFIDENCE=X.XX（两位小数）"""

    def _build_user_prompt(
        self,
        question: str,
        dag_plan: dict,
        dag_result: DagResult,
        date_context: str = "",
    ) -> str:
        """构建用户提示词（仅含与回答相关的数据，不暴露DAG内部节点名）。"""
        parts = [f"# 用户问题\n{question}\n"]

        # 日期上下文（最高优先级）
        if date_context:
            parts.append(f"## 日期信息\n{date_context}\n")

        # ---- 意图分析摘要 ----
        intent_output = dag_result.node_outputs.get("intent")
        if intent_output and intent_output.status == "success" and intent_output.output:
            intent_data = intent_output.output
            parts.append("## 问题类型")
            parts.append(f"类型: {intent_data.get('question_type', '?')}")
            if intent_data.get("modules"):
                parts.append(f"涉及模块: {', '.join(intent_data['modules'])}")
            if intent_data.get("signals"):
                parts.append(f"涉及信号: {', '.join(intent_data['signals'])}")
            if intent_data.get("states"):
                parts.append(f"涉及状态: {', '.join(intent_data['states'])}")
            parts.append("")

        # ---- 检索到的文档片段（核心数据） ----
        chunk_output = dag_result.node_outputs.get("chunk_search") or dag_result.node_outputs.get("chunks")
        if chunk_output and chunk_output.status == "success" and chunk_output.output:
            chunks = chunk_output.output.get("chunks", [])
            if chunks:
                parts.append(f"## 检索到的文档片段 ({len(chunks)}条)")
                for i, c in enumerate(chunks, 1):
                    section = c.get("section_path", "?")
                    module = c.get("module", "")
                    text = c.get("text", "")[:2500]
                    mod_label = f" [{module}]" if module else ""
                    parts.append(f"\n### 片段{i} — §{section}{mod_label}")
                    parts.append(text)
                parts.append("")

        # ---- 状态机转移边（如果存在） ----
        sm_output = dag_result.node_outputs.get("state_machine") or dag_result.node_outputs.get("sm")
        if sm_output and sm_output.status == "success" and sm_output.output:
            transitions = sm_output.output.get("transitions", [])
            if transitions:
                parts.append(f"## 状态转移 ({len(transitions)}条)")
                for t in transitions[:8]:
                    src, tgt = t.get("source", "?"), t.get("target", "?")
                    guard = t.get("guard", "")[:300]
                    sec = t.get("section", "")
                    parts.append(f"- {src} → {tgt}: {guard}" + (f" (§{sec})" if sec else ""))
                parts.append("")

        # ---- 规则匹配（如果存在） ----
        rule_output = dag_result.node_outputs.get("rule_lookup") or dag_result.node_outputs.get("rules")
        if rule_output and rule_output.status == "success" and rule_output.output:
            matched = rule_output.output.get("matched_rules", [])
            if matched:
                parts.append(f"## 匹配规则 ({len(matched)}条)")
                for r in matched[:5]:
                    rid = r.get("rule_id", "?")
                    cond = r.get("condition", "")[:400]
                    act = r.get("action", "")[:400]
                    parts.append(f"- [{rid}] {cond}" + (f" → {act}" if act else ""))
                parts.append("")

        # ---- 影响分析 / 路径查找（如果存在） ----
        for node_key, label in [("impact_analysis", "影响分析"), ("path_finder", "路径查找"),
                                 ("reachability", "可达性检查"), ("conflict_detection", "冲突检测")]:
            node_out = dag_result.node_outputs.get(node_key)
            if node_out and node_out.status == "success" and node_out.output:
                data = node_out.output
                if data:
                    parts.append(f"## {label}")
                    parts.append(json.dumps(data, ensure_ascii=False, indent=2)[:800])
                    parts.append("")

        # 答案要求
        parts.append("\n# 请回答（以下要求必须全部满足，禁止敷衍）")
        parts.append("基于上述数据，生成结构化工程回答。")
        parts.append("**核心要求：回答必须详尽完整。证据中有多少条数据就列出多少条，禁止用'等'字省略。禁止一两句话敷衍。**")
        parts.append("用户看不懂DAG节点名，请用自然语言回答。")
        parts.append("")
        parts.append("## 结论")
        parts.append("【这是整个回答最重要的部分。结论不是摘要，结论是数据本身。")
        parts.append("必须把证据中的所有具体数据逐条列在结论里。")
        parts.append("如果用户问'哪些节点不足半年'，就把所有满足条件的节点全部列出，")
        parts.append("不要说'最相关的是X'——应该列出完整的满足条件列表。")
        parts.append("格式示例（当成表格来写，每条一行）：")
        parts.append("  - EP1 到件: 2026年4月30日（已过56天，不足半年 ✓）")
        parts.append("  - EP2 到件: 2026年5月30日（已过26天，不足半年 ✓）")
        parts.append("  - PPV 到件: 2026年9月30日（还剩97天，不足半年 ✓）")
        parts.append("  - PP 到件: 2026年11月30日（还剩158天，不足半年 ✓）")
        parts.append("  - SOP: 2027年3月（还剩约250天，超过半年 ✗）")
        parts.append("以此类推，每个数据点标注是否满足条件，一个都不准漏。】")
        parts.append("")
        parts.append("## 筛选条件说明（如果涉及任何筛选）")
        parts.append("【如果你对数据做了任何筛选（比如'不足半年''最早''首次'），必须在这里明确写出：")
        parts.append("  (a) 筛选条件是什么（精确的判定规则，如'半年=180天，从评审日期2026/6/25起算，截止2026/12/22'）")
        parts.append("  (b) 这个条件来自用户问题中的哪个词")
        parts.append("  (c) 逐条列出每条数据为什么入选或落选。不要自己加条件——文档没说的条件不准加")
        parts.append("  (d) 特别警告：评审日期之前的数据也同样有效！'已过去'不等于'不满足条件'！】")
        parts.append("")
        parts.append("## 详细分析")
        parts.append("【逐条展开论述。每条证据都要单独列为一个分析点。")
        parts.append("格式：")
        parts.append("  1. [分析点标题]")
        parts.append("     - 数据内容: ...")
        parts.append("     - 来源章节: §X.Y.Z")
        parts.append("     - 入选/落选理由: 明确说明为什么这条数据满足或不满足筛选条件")
        parts.append("  2. [下一个分析点]")
        parts.append("     ...")
        parts.append("不要合并多条证据为一点，不要用'等'或'...'省略。】")
        parts.append("")
        parts.append("## 证据来源")
        parts.append("【列出本次回答中引用的所有章节号和对应的文档原文摘要。每条证据都要对应到分析中的某一点。】")
        parts.append("")
        parts.append("## 置信度评估")
        parts.append("【说明本次回答的置信度理由：哪些数据充分、哪些数据不足、对最终答案可信度的影响。最后给出 CONFIDENCE=X.XX（两位小数）】")
        parts.append("")
        parts.append("═══════════════════════════════════════════════════════════════")
        parts.append("再次强调：")
        parts.append("1. 不要自己加过滤条件。评审日期之前的数据同样有效。")
        parts.append("2. 列出所有满足条件的数据，不要只选一个'最相关'的。")
        parts.append("3. 每条数据的入选/落选理由必须明确写出。")

        return "\n".join(parts)

    def _format_node_output(
        self, parts: list[str], node_type: str, data: dict,
    ):
        """格式化单个节点的输出数据到prompt中。

        不同节点类型有不同的格式化方式：
          - intent_analysis: 列出模块/信号/状态/问题类型
          - state_machine: 列出转移边（源→目标: guard条件）
          - rule_lookup: 列出匹配的规则（条件→动作）
          - path_finder: 列出路径序列
          - impact_analysis: 列出受影响实体
          - conflict_detection: 列出冲突
          - reachability: 列出问题
          - chunk_search: 列出文档片段
        """
        if node_type == "intent_analysis":
            if data.get("modules"):
                parts.append(f"模块: {', '.join(data['modules'])}")
            if data.get("signals"):
                parts.append(f"信号: {', '.join(data['signals'])}")
            if data.get("states"):
                parts.append(f"状态: {', '.join(data['states'])}")
            parts.append(f"问题类型: {data.get('question_type', '?')}")

        elif node_type == "state_machine":
            for t in data.get("transitions", [])[:8]:
                parts.append(
                    f"- {t['source']} -> {t['target']}: {t.get('guard', '?')[:600]}"
                )
                if t.get("section"):
                    parts.append(f"  (§{t['section']})")

        elif node_type == "rule_lookup":
            for r in data.get("matched_rules", [])[:8]:
                parts.append(
                    f"- [{r.get('rule_id','?')}] {r.get('condition','')[:400]}"
                )
                if r.get("action"):
                    parts.append(f"  -> {r['action'][:400]}")

        elif node_type == "path_finder":
            parts.append(
                f"从 {data.get('source','?')} 到 {data.get('target','?')}: "
                f"{data.get('total_paths',0)} 条路径, 最短 {data.get('shortest_hops','?')} 跳"
            )
            for p in data.get("paths", [])[:3]:
                parts.append(f"- {' → '.join(p.get('sequence', []))} ({p.get('hops',0)}跳)")

        elif node_type == "impact_analysis":
            parts.append(
                f"触发: {data.get('trigger','?')} "
                f"({data.get('trigger_type','?')}), "
                f"影响: {data.get('total_impacted',0)} 个实体"
            )
            for imp in data.get("impacted", [])[:8]:
                parts.append(
                    f"- [{imp.get('entity_type','?')}] {imp.get('entity','?')} "
                    f"(深度{imp.get('depth',0)}, 通过{imp.get('via','?')})"
                )

        elif node_type == "conflict_detection":
            parts.append(f"检测到 {data.get('total',0)} 个冲突")
            for c in data.get("conflicts", [])[:5]:
                parts.append(f"- {c.get('type','?')}: {json.dumps(c, ensure_ascii=False)[:200]}")

        elif node_type == "reachability":
            parts.append(f"检测到 {data.get('total',0)} 个问题")
            for iss in data.get("issues", [])[:5]:
                parts.append(
                    f"- [{iss.get('type','?')}] {iss.get('state','?')}: "
                    f"{iss.get('detail','')[:150]}"
                )

        elif node_type == "chunk_search":
            for c in data.get("chunks", [])[:5]:
                parts.append(
                    f"- [{c.get('chunk_type','?')}] §{c.get('section_path','?')} "
                    f"({c.get('module','?')}): {c.get('text','')[:600]}"
                )

        elif node_type == "completeness_eval":
            score = data.get("overall_score", 0)
            bar_len = 10
            filled = int(score * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            parts.append(f"整体完整性评分: {bar} {score:.0%}")
            parts.append(f"是否充分: {'是' if data.get('is_sufficient') else '否'}")
            for dim in data.get("dimensions", []):
                icon_map = {"sufficient": "[✓]", "insufficient": "[~]", "missing": "[✗]"}
                icon = icon_map.get(dim.get("status", ""), "[?]")
                parts.append(
                    f"  {icon} {dim['name']}: {dim['score']:.0%} "
                    f"({dim.get('found', '?')} found, 阈值 {dim.get('threshold', '?')})"
                )
            if data.get("gap_queries"):
                parts.append("建议跟进查询:")
                for q in data["gap_queries"][:3]:
                    parts.append(f"  - {q}")
            if data.get("summary"):
                parts.append(f"评估摘要: {data['summary']}")


    def _extract_confidence(self, answer: str) -> float:
        """从LLM答案中提取置信度数值。

        支持多种格式：
          - CONFIDENCE=0.85 或 CONFIDENCE: 0.85
          - 置信度: 0.85 或 置信度：85%
          - 综合置信度: 0.80
          - 单独百分比: 85%
        """
        # 1. 精确匹配 CONFIDENCE=X.XX
        match = re.search(r"CONFIDENCE\s*[=:：]\s*([0-9.]+)", answer, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            if val > 1:
                val = val / 100.0
            return max(0.0, min(1.0, val))

        # 2. 匹配"置信度"关键词后的数字（含中文冒号）
        match = re.search(r"置信度[^0-9]*?([0-9.]+)", answer)
        if match:
            val = float(match.group(1))
            if val > 1:
                val = val / 100.0
            return max(0.0, min(1.0, val))

        # 3. 匹配"综合置信度"
        match = re.search(r"综合置信度[^0-9]*?([0-9.]+)", answer)
        if match:
            val = float(match.group(1))
            if val > 1:
                val = val / 100.0
            return max(0.0, min(1.0, val))

        # 4. 匹配百分比格式
        match = re.search(r"(\d+)%", answer)
        if match:
            return float(match.group(1)) / 100.0

        # 5. 最后一行数字
        lines = answer.strip().split("\n")
        for line in reversed(lines):
            match = re.search(r"([0-9.]+)", line)
            if match:
                val = float(match.group(1))
                if 0 < val <= 1:
                    return val
                elif 1 < val <= 100:
                    return val / 100.0
                break

        return 0.5

    def _validate_confidence(
        self, llm_confidence: float, answer: str, dag_result,
    ) -> float:
        """动态校验LLM输出的置信度是否合理。

        模板感知：主数据源有结果时，辅助数据源为空的扣分减免。
        """
        stats = self._compute_dag_stats(dag_result)
        template = dag_result.template
        primary = self._TEMPLATE_PRIMARY_SOURCES.get(template, [])

        # 有失败节点但LLM声称高置信度 → 下调
        if stats["failed_nodes"] > 0 and llm_confidence > 0.85:
            llm_confidence = min(llm_confidence, 0.80)

        # LLM没输出置信度 → 根据实际DAG统计计算
        if llm_confidence == 0.5 and "CONFIDENCE" not in answer.upper():
            base = 0.30 + stats["success_rate"] * 0.25
            bonuses = 0.0
            if stats["sm_transitions"] > 0:
                bonuses += 0.10
            if stats["rule_matches"] >= 2:
                bonuses += 0.10
            if stats["impact_count"] > 0:
                bonuses += 0.08
            if stats["path_count"] > 0:
                bonuses += 0.08
            if stats["chunk_count"] >= 3:
                bonuses += 0.05
            if stats["reach_conflict_count"] > 0:
                bonuses += 0.05

            # 模板感知扣分：辅助数据源为空时减免
            penalties = stats["failed_nodes"] * 0.10
            if stats["sm_transitions"] == 0:
                penalties += 0.05 if "sm" not in primary else 0.15
            if stats["rule_matches"] == 0:
                penalties += 0.05 if "rules" not in primary else 0.15
            if stats["impact_count"] == 0:
                penalties += 0.05 if "impact" not in primary else 0.10
            if stats["path_count"] == 0:
                penalties += 0.05 if "path" not in primary else 0.10
            if stats["chunk_count"] == 0:
                penalties += 0.10

            llm_confidence = max(0.10, min(0.95, base + bonuses - penalties))

        return round(llm_confidence, 2)

    def _extract_citations(
        self, answer: str, dag_result: DagResult,
    ) -> list[str]:
        """从答案中提取引用的章节/规则ID。"""
        citations = []
        seen = set()
        for node_id, output in dag_result.node_outputs.items():
            if output.status != "success" or not output.output:
                continue
            data = output.output
            for t in data.get("transitions", []):
                sec = t.get("section", "")
                if sec and sec in answer and sec not in seen:
                    seen.add(sec)
                    citations.append(f"[state_machine] §{sec}")
            for r in data.get("matched_rules", []):
                rid = r.get("rule_id", "")
                if rid and rid in answer and rid not in seen:
                    seen.add(rid)
                    citations.append(f"[rule] {rid}")
            for c in data.get("chunks", []):
                sec = c.get("section_path", "")
                if sec and sec in answer and sec not in seen:
                    seen.add(sec)
                    citations.append(f"[chunk] §{sec}")
        return citations

    def _compute_dag_stats(self, dag_result: DagResult) -> dict:
        """从 DAG 执行结果中提取统计指标，供 LLM 按评分标准计算置信度。

        返回的字典包含评分标准中每一项需要的原始数据，
        LLM 根据这些数据按照 System Prompt 中的公式计算最终置信度。
        """
        outputs = dag_result.node_outputs
        total = len(outputs)
        success = sum(1 for o in outputs.values() if o.status == "success")
        failed = sum(1 for o in outputs.values() if o.status == "error")

        # 各节点的关键指标
        sm_transitions = 0
        rule_matches = 0
        impact_count = 0
        path_count = 0
        chunk_count = 0
        reach_conflict_count = 0
        failed_details = []
        completeness_score = 0.0
        completeness_sufficient = True
        completeness_gaps = 0

        for nid, no in outputs.items():
            if no.status != "success" or not no.output:
                if no.status == "error":
                    failed_details.append(f"{nid}({no.node_type}): {no.error}")
                continue

            data = no.output
            nt = no.node_type

            if nt == "state_machine":
                sm_transitions = len(data.get("transitions", []))
            elif nt == "rule_lookup":
                rule_matches = len(data.get("matched_rules", []))
            elif nt == "impact_analysis":
                impact_count = data.get("total_impacted", len(data.get("impacted", [])))
            elif nt == "path_finder":
                path_count = data.get("total_paths", len(data.get("paths", [])))
            elif nt == "chunk_search":
                chunk_count = len(data.get("chunks", []))
            elif nt in ("reachability", "conflict_detection"):
                reach_conflict_count += data.get("total", len(data.get("issues", data.get("conflicts", []))))
            elif nt == "completeness_eval":
                completeness_score = data.get("overall_score", 0)
                completeness_sufficient = data.get("is_sufficient", True)
                # 统计不充分/缺失的维度数
                for dim in data.get("dimensions", []):
                    if dim.get("status") != "sufficient":
                        completeness_gaps += 1

        # 数据流完整性：检查是否有节点输出非空但下游节点输入为空的情况
        dataflow_intact = "是" if failed == 0 else ("部分断裂" if failed < total else "严重断裂")

        return {
            "total_nodes": total,
            "success_nodes": success,
            "failed_nodes": failed,
            "success_rate": success / max(total, 1),
            "sm_transitions": sm_transitions,
            "rule_matches": rule_matches,
            "impact_count": impact_count,
            "path_count": path_count,
            "chunk_count": chunk_count,
            "reach_conflict_count": reach_conflict_count,
            "dataflow_intact": dataflow_intact,
            "failed_details": "; ".join(failed_details) if failed_details else "",
            "completeness_score": completeness_score,
            "completeness_sufficient": completeness_sufficient,
            "completeness_gaps": completeness_gaps,
        }

    def _fallback_synthesize(
        self, question: str, dag_result: DagResult,
    ) -> dict:
        """LLM不可用时的回退合成方案。

        将各节点输出拼接为结构化文本，不通过LLM生成。
        """
        parts = [f"# {question}\n"]
        parts.append(f"## DAG模板: {dag_result.template}\n")

        for node_id, output in dag_result.node_outputs.items():
            parts.append(f"### [{output.node_type}] {node_id}")
            if output.status == "error":
                parts.append(f"错误: {output.error}")
            elif output.output:
                parts.append(
                    json.dumps(output.output, ensure_ascii=False, indent=2)[:1000]
                )
            parts.append("")

        # 计算置信度：成功节点数 / 总节点数
        conf = min(
            sum(
                1 for o in dag_result.node_outputs.values()
                if o.status == "success"
            )
            / max(len(dag_result.node_outputs), 1),
            0.95,
        )
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        parts.append(f"## 置信度: {bar} {conf:.0%} (回退模式: LLM不可用)")

        return {
            "answer": "\n".join(parts),
            "confidence": conf,
            "citations": [],
            "model": "fallback",
            "usage": {},
        }


# ======================================================================
# 辅助函数：展平条件树
# ======================================================================


def _flatten_condition_tree(node) -> str:
    """递归展平ConditionNode树为可读字符串。

    将推理引擎返回的AND/OR/LEAF条件树转换为：
      "信号A=值 AND 信号B=值 OR 信号C=值"
    """
    if node is None:
        return ""
    node_type = getattr(node, "type", "LEAF")
    if node_type == "LEAF":
        signal = getattr(node, "signal", "")
        value = getattr(node, "value", "")
        if signal and value:
            return f"{signal}={value}"
        return str(signal or value or "")
    elif node_type == "AND":
        children = getattr(node, "children", [])
        parts = [_flatten_condition_tree(c) for c in children if _flatten_condition_tree(c)]
        return " AND ".join(parts) if parts else ""
    elif node_type == "OR":
        children = getattr(node, "children", [])
        parts = [_flatten_condition_tree(c) for c in children if _flatten_condition_tree(c)]
        return " OR ".join(parts) if parts else ""
    return str(getattr(node, "description", ""))


# ======================================================================
# DagAgent — DAG模式Agent主控类
# ======================================================================


class DagAgent:
    """DAG模式BCM工程推理Agent。

    使用DAG模板 + LLM参数化替代固定的顺序工具执行。

    使用方式:
        # 方式1: 交互式
        agent = DagAgent(provider="deepseek")
        agent.load()
        result = agent.query("从Abandoned如何进入Driving？")
        print(result.answer)
        print(result.audit_trail)

        # 方式2: 强制指定模板（不使用LLM选择）
        result = agent.query("...", template="path_finding")

        # 方式3: 运行内置CLI demo
        python -m agent.dag_agent

    环境变量:
        DEEPSEEK_API_KEY — DeepSeek API密钥
        ARK_API_KEY — 火山引擎Ark API密钥
        ZHIPU_API_KEY — 智谱GLM API密钥
    """

    def __init__(
        self,
        api_key: str | None = None,
        provider: str = "deepseek",
    ):
        """初始化DagAgent。

        参数:
            api_key: API密钥（不传则从环境变量读取）
            provider: LLM提供商（deepseek | ark | zhipu）
        """
        self.api_key = api_key
        self.provider = provider
        self._pipeline = None
        self._engine = None
        self._sm: dict | None = None
        self._rules: dict | None = None
        self._loaded = False
        self._executor = DagExecutor()
        self._llm = None

    def load(self) -> "DagAgent":
        """加载所有子系统：检索管道、推理引擎、状态机、规则库。

        加载顺序：
          1. RetrievalPipeline（图谱 + BM25 + 稠密向量）
          2. ReasoningEngine（前向链/后向链/路径/冲突/可达性）
          3. 状态机JSON（CONTENT_ANALYSIS_DIR/state_machine_VMM.json）
          4. 规则库JSON（CONTENT_ANALYSIS_DIR/rules.json）
        """
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

        from retrieval import RetrievalPipeline
        self._pipeline = RetrievalPipeline()
        self._pipeline.load(use_dense=True)

        from retrieval.reasoning_engine import ReasoningEngine
        self._engine = ReasoningEngine()

        from config import CONTENT_ANALYSIS_DIR
        rules_path = CONTENT_ANALYSIS_DIR / "rules.json"

        # 加载所有模块的状态机（VMM + Window + Lock + ExteriorLight + ...）
        sm_dir = CONTENT_ANALYSIS_DIR
        sm_count = 0
        for sm_path in sm_dir.glob("state_machine_*.json"):
            try:
                self._engine.load_state_machine(sm_path)
                sm_data = json.loads(sm_path.read_text(encoding="utf-8"))
                mod = sm_data.get("module", sm_path.stem.replace("state_machine_", ""))
                # 合并所有状态机到 self._sm
                if self._sm is None:
                    self._sm = {"modules": {}}
                self._sm["modules"][mod] = sm_data
                sm_count += 1
            except Exception:
                pass

        # 兼容旧代码：如果只有 VMM，self._sm 也可以直接访问
        if self._sm and "modules" in self._sm and len(self._sm["modules"]) == 1:
            only_mod = list(self._sm["modules"].keys())[0]
            self._sm = self._sm["modules"][only_mod]

        if rules_path.exists():
            self._engine.load_rules(rules_path)
            self._rules = json.loads(rules_path.read_text(encoding="utf-8"))

        self._loaded = True
        print(f"DagAgent就绪。已加载{sm_count}个模块状态机，6个推理模板。")
        return self

    def _resolve_date_expressions(self, question: str) -> tuple[str, str]:
        """自动解析问题中的日期表达式，返回(改写后问题, 日期上下文)。

        支持：
          - "今天" / "现在" → 当前日期
          - "昨天" / "前天" → 计算偏移
          - "XX天前" / "XX天后" → 计算偏移
          - "半年" / "三个月" 等时间跨度
          - "距离XX不足XX" → 直接计算是否满足条件

        Returns:
            (enhanced_question, date_context_string)
            如 question 无日期表达式，原样返回且 date_context 为空。
        """
        today = datetime.now()
        date_context_parts = [f"今天日期: {today.strftime('%Y-%m-%d')}"]

        # 替换"今天"为具体日期
        if "今天" in question:
            question = question.replace("今天", today.strftime("%Y-%m-%d"))
        if "现在" in question and "日期" not in question:
            question = question.replace("现在", today.strftime("%Y-%m-%d"))

        # 检测"不足半年"等时间条件
        time_spans = {
            "半年": 180, "一年": 365, "两年": 730,
            "一个月": 30, "两个月": 60, "三个月": 90,
            "一周": 7, "两周": 14, "三周": 21,
        }
        for span_name, span_days in time_spans.items():
            if span_name in question:
                ref_date = today
                # 计算参考日期
                target_date = ref_date
                target_str = target_date.strftime("%Y-%m-%d")
                diff_days = (ref_date - target_date).days
                date_context_parts.append(
                    f"时间跨度「{span_name}」={span_days}天。"
                    f"从 {target_str} 起算，{span_name}后是 "
                    f"{(target_date + __import__('datetime').timedelta(days=span_days)).strftime('%Y-%m-%d')}。"
                )
                break

        # 检测具体日期引用
        import re
        date_pattern = re.compile(
            r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?'
        )
        dates_found = date_pattern.findall(question)
        for y, m, d in dates_found:
            try:
                dt = datetime(int(y), int(m), int(d))
                diff = (today - dt).days
                date_context_parts.append(
                    f"文档日期 {y}-{m}-{d} 距今 {abs(diff)} 天"
                    f"({'已过去' if diff > 0 else '未来' if diff < 0 else '今天'})"
                )
            except ValueError:
                pass

        date_context = "\n".join(date_context_parts) if len(date_context_parts) > 1 else ""
        return question, date_context

    # ======================================================================
    # 查询入口
    # ======================================================================

    def query(
        self,
        question: str,
        template: str | None = None,
        max_iterations: int = 2,
    ) -> DagResult:
        """执行DAG推理查询。

        完整流程：
          1. 模板选择（LLM自动选择 或 手动指定）
          2. DAG执行（拓扑排序 → 层级并行 → 数据流传递）
          3. 反思闭环：检查节点输出是否有缺口，有则重新规划再执行
          4. 答案合成（LLM消费DAG结果 → 结构化工程回答）
          5. Answer自检：LLM检查答案是否有证据支撑
          6. 节点利用率追踪（记录各节点类型执行情况）
          7. 审计追踪（记录完整执行过程）

        参数:
            question: 用户工程查询
            template: 强制使用指定模板（None=LLM自动选择）
            max_iterations: 反思闭环最大迭代次数（默认2轮）

        返回:
            DagResult（含答案、节点输出、审计追踪、节点利用率）
        """
        if not self._loaded:
            raise RuntimeError("DagAgent未加载。请先调用 .load()。")

        t0 = time.time()
        all_reflections: list[str] = []
        all_dag_plans: list[dict] = []  # 记录每轮的计划（用于审计）

        # ==== 步骤0: 日期解析（自动计算时间差，注入到 question） ====
        question, date_context = self._resolve_date_expressions(question)

        # ==== 步骤1: 选择模板 ====
        if template and template in DAG_TEMPLATES:
            dag_plan = self._build_plan_from_template(template, question)
        elif self._has_llm():
            dag_plan = self._select_template_with_llm(question)
        else:
            dag_plan = self._select_template_fallback(question)

        all_dag_plans.append(dag_plan)

        # ==== 步骤2: DAG执行 + 反思闭环 ====
        actual_iterations = 0
        for iteration in range(max_iterations):
            actual_iterations = iteration + 1
            result = self._executor.execute(
                dag_plan=dag_plan,
                pipeline=self._pipeline,
                engine=self._engine,
                sm=self._sm,
                rules=self._rules,
                query=question,
            )

            # 反思：检查节点输出是否有实质性缺口
            reflections = self._reflect_on_result(result)
            all_reflections.extend(reflections)

            if not reflections:
                break  # 无缺口，停止迭代

            if iteration < max_iterations - 1:
                # 有缺口且还有迭代次数：调整计划再执行
                dag_plan = self._augment_plan_from_reflections(
                    dag_plan, reflections,
                )
                all_dag_plans.append(dag_plan)

        result.reflection_log = all_reflections
        result.iteration_count = actual_iterations

        # ==== 步骤3: 计算节点利用率（基于最终执行结果） ====
        result.node_utilization = self._compute_node_utilization(result)

        # ==== 步骤4: 合成答案 ====
        if self._has_llm():
            llm = self._get_llm()
            synthesizer = DagSynthesizer(llm)
            synth_result = synthesizer.synthesize(
                question=question,
                dag_plan=dag_plan,
                dag_result=result,
                date_context=date_context,
            )
            result.answer = synth_result["answer"]
            result.confidence = synth_result.get("confidence", 0.5)

            # 步骤4.5: Answer自检
            critique = self._self_critique_answer(
                question, result.answer, result,
            )
            result.critique = critique  # 始终记录（PASS 或 需要修正）
        else:
            synthesizer = DagSynthesizer(None)
            synth_result = synthesizer._fallback_synthesize(question, result)
            result.answer = synth_result["answer"]
            result.confidence = synth_result.get("confidence", 0.5)

        # ==== 步骤5: 构建审计追踪 ====
        result.audit_trail = self._build_audit_trail(
            result, dag_plan, all_reflections, all_dag_plans,
        )

        result.total_duration_ms = (time.time() - t0) * 1000
        return result

    # ==================================================================
    # 模板选择（LLM驱动 + 回退方案）
    # ==================================================================

    def _select_template_with_llm(self, question: str) -> dict:
        """使用LLM选择模板并填充节点参数。

        LLM接收：查询 + 六种模板描述
        LLM返回：JSON格式的DAG执行计划（模板名 + 节点参数 + 自定义边）
        """
        if not self._has_llm():
            return self._select_template_fallback(question)

        llm = self._get_llm()

        prompt = f"""你是BCM工程专家。根据用户查询选择最合适的DAG推理模板。

用户查询: "{question}"

{TEMPLATE_DESCRIPTIONS_FOR_LLM}

请选择最合适的模板，启用需要的节点，填充参数，必要时添加自定义边(custom_edges)。

输出严格JSON格式:
{{
  "template": "模板名",
  "reasoning": "为什么选择这个模板（中文说明）",
  "nodes": {{
    "intent": {{"enabled": true}},
    "sm": {{"enabled": true, "params": {{"states": ["Driving"]}}}},
    ...
  }},
  "edges": [...],
  "custom_edges": [...]
}}

只输出JSON对象，不要其他文字。"""

        try:
            result = llm.answer(
                evidence=prompt,
                query=question,
                system_prompt="你是BCM工程DAG推理专家。只输出JSON，不要其他文字。",
            )
            text = result.get("answer", "")
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                plan = json.loads(match.group(0))
                merged = self._merge_with_template(plan)
                # 关键词覆盖校验：防止 LLM 选错模板
                override = self._apply_keyword_override(question)
                if override and merged.get("template") != override:
                    merged["template"] = override
                    merged["reasoning"] = (
                        f"LLM chose {merged.get('template','?')}, "
                        f"keyword override → {override}"
                    )
                return merged
        except Exception:
            pass

        return self._select_template_fallback(question)

    # 关键词覆盖规则: (关键词组合) → 模板名
    # 优先级高于 trigger_keywords 评分，用于纠正 LLM 常见错误
    # 规则按 specificity 排序：越具体的规则排在越前面
    _KEYWORD_OVERRIDE = [
        # ── diagnostic（最高优先级：为什么/无法类） ──
        (["为什么", "无法"], "diagnostic"),
        (["为什么", "不能"], "diagnostic"),
        (["为何", "无法"], "diagnostic"),
        (["为何", "不能"], "diagnostic"),
        (["怎么", "诊断"], "diagnostic"),
        (["如何", "诊断"], "diagnostic"),
        (["故障", "原因"], "diagnostic"),
        (["故障", "检测"], "diagnostic"),
        (["故障", "怎么"], "diagnostic"),
        (["不吸合"], "diagnostic"),
        (["不工作"], "diagnostic"),
        (["不自动"], "diagnostic"),
        (["不响应"], "diagnostic"),
        (["无法", "启动"], "diagnostic"),
        (["无法", "进入"], "diagnostic"),
        (["无法", "退出"], "diagnostic"),
        (["异常", "原因"], "diagnostic"),
        # ── path_finding（从A到B的路径） ──
        (["有哪些", "路径"], "path_finding"),
        (["从", "到", "路径"], "path_finding"),
        (["从", "如何", "进入"], "path_finding"),  # "从X如何进入Y" but check diagnostic first
        (["从", "怎么", "到"], "path_finding"),
        (["如何", "到达"], "path_finding"),
        (["经过", "哪些", "状态"], "path_finding"),
        (["最短", "路径"], "path_finding"),
        (["从", "到", "几步"], "path_finding"),
        (["从", "到", "最短"], "path_finding"),
        (["所有", "路径"], "path_finding"),
        # ── impact_analysis ──
        (["影响", "哪些"], "impact_analysis"),
        (["影响", "什么"], "impact_analysis"),
        (["影响", "范围"], "impact_analysis"),
        (["会导致", "后果"], "impact_analysis"),
        (["连锁", "反应"], "impact_analysis"),
        (["后果", "什么"], "impact_analysis"),
        (["失效", "影响"], "impact_analysis"),
        (["故障", "影响"], "impact_analysis"),
        (["故障", "后果"], "impact_analysis"),
        # ── state_transition ──
        (["进入", "需要", "条件"], "state_transition"),
        (["如何", "退出"], "state_transition"),
        (["退出", "条件"], "state_transition"),
        (["转移", "条件"], "state_transition"),
        (["迁移", "条件"], "state_transition"),
        (["前置", "条件"], "state_transition"),
        (["进入", "条件"], "state_transition"),
        (["触发", "事件"], "state_transition"),
        (["什么", "条件", "进入"], "state_transition"),
        # ── reachability_check ──
        (["永远无法"], "reachability_check"),
        (["不可达"], "reachability_check"),
        (["死锁"], "reachability_check"),
        (["活锁"], "reachability_check"),
        (["是否", "连通"], "reachability_check"),
        (["状态机", "完整"], "reachability_check"),
    ]

    # 状态名集合 — 用于检测查询中是否包含 BCM 状态
    _BCM_STATE_NAMES = {
        "abandoned", "inactive", "convenience", "driving",
        "休眠", "唤醒", "运行", "停止",
    }

    def _get_state_names(self) -> set:
        """Get state names from DomainConfig or BCM defaults."""
        if self._domain is not None and self._domain.dag.state_names:
            return set(self._domain.dag.state_names)
        return self._BCM_STATE_NAMES

    def _apply_keyword_override(self, question: str) -> str | None:
        """检查查询是否匹配关键词覆盖规则。

        返回模板名或 None（不覆盖）。

        两层检测:
          1. 精确关键词组合匹配（_KEYWORD_OVERRIDE 列表）
          2. 状态名+危险模式检测（防止误选 factual_lookup）
        """
        ql = question.lower()

        # 第一层: 精确关键词组合匹配
        for keywords, template in self._KEYWORD_OVERRIDE:
            if all(kw.lower() in ql for kw in keywords):
                return template

        # 第二层: 状态名+危险词检测
        # 如果查询包含状态名且含"是什么"/"定义" → 不能选 factual_lookup
        has_state = any(s in ql for s in self._BCM_STATE_NAMES)
        has_definition_word = any(
            w in ql for w in ["是什么", "定义", "含义", "什么是"]
        )
        if has_state and has_definition_word:
            # 查询同时有状态名和定义词 → 倾向 state_transition
            # 例: "Driving的定义是什么" → 不能选 factual_lookup
            return "state_transition"

        return None

    def _select_template_fallback(self, question: str) -> dict:
        """回退方案：关键词匹配选择模板。

        先检查关键词覆盖规则（精确匹配），再按 trigger_keywords 评分。
        额外保护：如果查询包含 BCM 状态名，禁止选 factual_lookup。
        """
        # 0. 优先检查覆盖规则
        override = self._apply_keyword_override(question)
        if override:
            return self._build_plan_from_template(override, question)

        ql = question.lower()

        # 0.5 检测是否含状态名 → 排除 factual_lookup 的候选资格
        has_state = any(s in ql for s in self._BCM_STATE_NAMES)

        scores = {}
        for name, tmpl in DAG_TEMPLATES.items():
            if has_state and name == "factual_lookup":
                continue  # 含状态名时禁止选 factual_lookup
            score = sum(
                1 for kw in tmpl.trigger_keywords if kw.lower() in ql
            )
            if score > 0:
                scores[name] = score

        if scores:
            best = max(scores, key=scores.get)
        elif has_state:
            # 含状态名但无模板关键词匹配 → 默认 state_transition
            best = "state_transition"
        else:
            best = "factual_lookup"

        return self._build_plan_from_template(best, question)

    def _build_plan_from_template(
        self, template_name: str, question: str,
    ) -> dict:
        """从模板构建DAG执行计划。

        将模板定义转换为可执行的计划，自动填充required节点的enabled=True。
        """
        tmpl = DAG_TEMPLATES.get(template_name)
        if not tmpl:
            tmpl = DAG_TEMPLATES["factual_lookup"]

        nodes = {}
        for nid, nconfig in tmpl.nodes.items():
            node_entry = {
                "enabled": nconfig.get("required", False),
                "type": nconfig["type"],
                "params": dict(nconfig.get("params", {})),
            }
            if nconfig.get("required"):
                node_entry["enabled"] = True
            nodes[nid] = node_entry

        return {
            "template": template_name,
            "reasoning": f"关键词回退选择: {question[:80]}",
            "nodes": nodes,
            "edges": [dict(e) for e in tmpl.edges],
            "custom_edges": [],
        }

    def _merge_with_template(self, llm_plan: dict) -> dict:
        """将LLM生成的计划与模板默认值合并。

        模板提供默认的节点定义和边，LLM可以：
          - 启用/禁用节点
          - 覆盖节点参数
          - 添加自定义边

        容错处理：
          - LLM 用类型名（如 chunk_search）作为 node_id → 自动映射到模板的 node_id
          - LLM 的自定义节点缺少 type → 从 NODE_EXECUTORS 推断
        """
        template_name = llm_plan.get("template", "factual_lookup")
        tmpl = DAG_TEMPLATES.get(template_name)

        if not tmpl:
            return llm_plan

        # 构建 node_type → 模板 node_id 的反向映射（用于 LLM 纠错）
        # 例如: {"chunk_search": "chunks", "state_machine": "sm", ...}
        type_to_template_nid: dict[str, str] = {}
        for nid, nconfig in tmpl.nodes.items():
            ntype = nconfig.get("type", "")
            if ntype:
                type_to_template_nid[ntype] = nid

        # 从模板默认值开始
        merged_nodes = {}
        for nid, nconfig in tmpl.nodes.items():
            merged_nodes[nid] = {
                "enabled": nconfig.get("required", False),
                "type": nconfig["type"],
                "params": dict(nconfig.get("params", {})),
            }

        # 用LLM的选择覆盖
        llm_nodes = llm_plan.get("nodes", {})
        for nid, nconfig in llm_nodes.items():
            # ---- 容错1: LLM 用了类型名作为 node_id → 映射回模板的 node_id ----
            mapped_nid = nid
            if nid not in merged_nodes and nid in type_to_template_nid:
                mapped_nid = type_to_template_nid[nid]

            if mapped_nid in merged_nodes:
                merged_nodes[mapped_nid]["enabled"] = nconfig.get(
                    "enabled", merged_nodes[mapped_nid]["enabled"]
                )
                if "params" in nconfig:
                    merged_nodes[mapped_nid]["params"].update(nconfig["params"])
            else:
                # ---- 容错2: LLM 添加的自定义节点，确保有 type 字段 ----
                node_entry = dict(nconfig)
                if "type" not in node_entry:
                    # 尝试从 NODE_EXECUTORS 推断类型
                    if nid in NODE_EXECUTORS:
                        node_entry["type"] = nid
                    else:
                        # 无法推断，跳过该节点
                        continue
                merged_nodes[nid] = node_entry

        # 合并边：模板默认边 + LLM自定义边
        edges = [dict(e) for e in tmpl.edges]
        custom_edges = llm_plan.get("custom_edges", [])
        edges.extend(custom_edges)

        return {
            "template": template_name,
            "reasoning": llm_plan.get("reasoning", ""),
            "nodes": merged_nodes,
            "edges": edges,
            "custom_edges": custom_edges,
        }

    # ==================================================================
    # 节点利用率追踪
    # ==================================================================

    def _compute_node_utilization(self, result: DagResult) -> dict[str, float]:
        """计算单次查询中各节点类型的执行比例。

        返回: {node_type: proportion} 例如 {"state_machine": 1.0, "rule_lookup": 1.0, ...}

        用途:
          - 如果 rule_lookup 利用率 < 20% → 系统退化为普通 RAG
          - 如果 reachability 利用率 = 0% → 可达性分析从未触发
          - 用于审计追踪和系统健康检查
        """
        type_counts: dict[str, int] = {}
        total_enabled = 0

        for nid, no in result.node_outputs.items():
            ntype = no.node_type
            type_counts[ntype] = type_counts.get(ntype, 0) + 1
            total_enabled += 1

        if total_enabled == 0:
            return {}

        utilization = {
            ntype: count / total_enabled
            for ntype, count in type_counts.items()
        }

        # 同时填充执行计数（用于跨查询聚合）
        result.node_execution_count = dict(type_counts)
        result.node_total_queries = 1

        return utilization

    def get_aggregated_utilization_report(
        self, results: list[DagResult],
    ) -> str:
        """跨多次查询聚合节点利用率，生成系统健康报告。

        参数:
            results: 多次查询的 DagResult 列表

        返回:
            格式化的健康报告字符串

        典型用法:
            results = [agent.query(q) for q in benchmark_questions]
            print(agent.get_aggregated_utilization_report(results))
        """
        if not results:
            return "无查询数据。"

        # 聚合统计
        type_executions: dict[str, int] = defaultdict(int)
        type_empty: dict[str, int] = defaultdict(int)
        total_queries = len(results)
        total_success = 0
        total_failed = 0
        template_counts: dict[str, int] = defaultdict(int)
        total_reflections = 0
        total_iterations = 0
        critique_pass_count = 0
        critique_fail_count = 0

        for r in results:
            for nid, no in r.node_outputs.items():
                ntype = no.node_type
                type_executions[ntype] += 1
                if no.status == "success":
                    total_success += 1
                    if no.output:
                        # 检测空数据节点
                        if (
                            (ntype == "state_machine" and len(no.output.get("transitions", [])) == 0) or
                            (ntype == "rule_lookup" and len(no.output.get("matched_rules", [])) == 0) or
                            (ntype == "impact_analysis" and len(no.output.get("impacted", [])) == 0) or
                            (ntype == "path_finder" and len(no.output.get("paths", [])) == 0) or
                            (ntype == "chunk_search" and len(no.output.get("chunks", [])) == 0)
                        ):
                            type_empty[ntype] += 1
                elif no.status == "error":
                    total_failed += 1

            template_counts[r.template] += 1
            total_reflections += len(r.reflection_log)
            total_iterations += r.iteration_count
            if r.critique == "PASS":
                critique_pass_count += 1
            elif r.critique:
                critique_fail_count += 1

        # 构建报告
        lines = [
            "=" * 70,
            "BCM-RAG DAG Agent 系统健康报告",
            "=" * 70,
            f"总查询数: {total_queries}",
            f"节点总执行次数: {total_success + total_failed}",
            f"节点成功率: {total_success / max(total_success + total_failed, 1):.1%}",
            f"平均反思次数/查询: {total_reflections / max(total_queries, 1):.1f}",
            f"平均迭代次数/查询: {total_iterations / max(total_queries, 1):.1f}",
            f"自检通过率: {critique_pass_count}/{critique_pass_count + critique_fail_count}"
            f" ({critique_pass_count / max(critique_pass_count + critique_fail_count, 1):.0%})",
            "",
            "─" * 50,
            "模板分布",
            "─" * 50,
        ]
        for tmpl, count in sorted(template_counts.items(), key=lambda x: -x[1]):
            prop = count / total_queries
            bar = "█" * int(prop * 20) + "░" * (20 - int(prop * 20))
            lines.append(f"  {tmpl:<25s} {bar} {count} ({prop:.0%})")

        lines.append("")
        lines.append("─" * 50)
        lines.append("节点类型执行频率（跨所有查询）")
        lines.append("─" * 50)
        for ntype, count in sorted(type_executions.items(), key=lambda x: -x[1]):
            prop = count / max(total_queries, 1)
            empty_count = type_empty.get(ntype, 0)
            empty_rate = empty_count / max(count, 1)
            bar = "█" * min(int(prop * 20), 20) + "░" * max(20 - int(prop * 20), 0)
            warning = ""
            if empty_rate > 0.5:
                warning = f" ⚠ 空数据率 {empty_rate:.0%}"
            lines.append(f"  {ntype:<25s} {bar} {prop:.0%} ({count}次){warning}")

        # 系统退化检测
        lines.append("")
        lines.append("─" * 50)
        lines.append("系统退化检测")
        lines.append("─" * 50)
        rule_util = type_executions.get("rule_lookup", 0) / max(total_queries, 1)
        reach_util = type_executions.get("reachability", 0) / max(total_queries, 1)
        sm_util = type_executions.get("state_machine", 0) / max(total_queries, 1)
        impact_util = type_executions.get("impact_analysis", 0) / max(total_queries, 1)
        path_util = type_executions.get("path_finder", 0) / max(total_queries, 1)

        degradation_risk = False
        if rule_util < 0.2:
            lines.append(f"  ⚠ 规则节点利用率 {rule_util:.0%} < 20% — 系统可能退化为普通 RAG！")
            lines.append(f"    建议: (1)检查模板选择是否过于偏向 factual_lookup")
            lines.append(f"          (2)检查 rules.json 的数据质量")
            degradation_risk = True
        if reach_util == 0:
            lines.append(f"  ⓘ 可达性分析未触发 — 如无可达性查询则正常。"
                         f"考虑在 benchmark 中增加 G 类问题。")
        if sm_util < 0.1:
            lines.append(f"  ⚠ 状态机利用率 {sm_util:.0%} < 10% — 推理能力未充分利用。")
            degradation_risk = True
        if not degradation_risk:
            lines.append(f"  ✓ 所有关键推理节点利用率在健康范围内。")

        # 综合评分
        reasoning_node_util = (rule_util + reach_util + sm_util + impact_util + path_util) / 5
        lines.append("")
        lines.append(f"  推理节点综合利用率: {reasoning_node_util:.0%}")
        if reasoning_node_util > 0.5:
            lines.append(f"  系统评级: 工程推理系统 ✓")
        elif reasoning_node_util > 0.2:
            lines.append(f"  系统评级: 部分推理能力 ⚠")
        else:
            lines.append(f"  系统评级: 退化为普通 RAG ✗")

        return "\n".join(lines)

    # ==================================================================
    # 辅助方法
    # ==================================================================

    def _has_llm(self) -> bool:
        """检测LLM是否可用（api_key或环境变量任一存在即可）。"""
        if self.api_key:
            return True
        import os
        return bool(
            os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("ARK_API_KEY")
            or os.getenv("ZHIPU_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )

    def _get_llm(self):
        """获取LLMAnswerGenerator实例（懒加载）。

        复用LLMAnswerGenerator的provider预设配置。
        """
        from retrieval.llm_answer import LLMAnswerGenerator
        if self._llm is None:
            self._llm = LLMAnswerGenerator(
                api_key=self.api_key,
                provider=self.provider,
            )
        return self._llm

    # ==================================================================
    # 反思闭环 + Answer自检
    # ==================================================================

    def _reflect_on_result(self, result: DagResult) -> list[str]:
        """检查DAG执行结果是否有实质性信息缺口。

        三层检测:
          Level 1 — 节点执行失败（最高优先级，必须处理）
          Level 2 — 核心节点返回空数据（数据缺失）
          Level 3 — 完整性评估认为不充分（质量不足）

        返回: 反思建议列表（空列表 = 无需调整）
        """
        reflections = []

        # ── Level 1: 节点执行失败 ──
        failed_nodes = []
        for nid, no in result.node_outputs.items():
            if no.status == "error":
                failed_nodes.append((nid, no.node_type, no.error or "未知错误"))

        if failed_nodes:
            for nid, ntype, err in failed_nodes:
                reflections.append(
                    f"[FAIL] 节点 {nid}({ntype}) 执行失败: {err}。"
                    f"建议: 检查上游数据传递是否完整，或降级为关键词检索。"
                )
            # 如果有失败节点，先不检查 Level 2/3 — 优先修复失败
            if len(failed_nodes) >= len(result.node_outputs) * 0.5:
                reflections.append(
                    "[CRITICAL] 超过50%节点失败，建议回退到基础 Hybrid RAG 模式。"
                )
            return reflections

        # ── Level 2: 核心节点返回空数据 ──
        for nid, no in result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue

            data = no.output
            nt = no.node_type

            if nt == "state_machine":
                transitions = data.get("transitions", [])
                if len(transitions) == 0:
                    states_queried = data.get("states_queried", [])
                    if states_queried:
                        reflections.append(
                            f"[GAP] 状态机查询 {states_queried} 返回0条转移边。"
                            f"可能原因: (1)状态名拼写不匹配 (2)状态机数据缺失。"
                            f"建议: 用 chunk_search 搜索 '{states_queried[0]}' 获取文档原文。"
                        )
                    else:
                        reflections.append(
                            "[GAP] 状态机节点返回0条转移边，且未指定查询状态。"
                            "建议: 确保意图分析正确提取了状态名。"
                        )

            if nt == "rule_lookup":
                matched = data.get("matched_rules", [])
                if len(matched) == 0:
                    kw = data.get("keywords_used", "")[:60]
                    reflections.append(
                        f"[GAP] 规则查询返回0条匹配规则 (关键词: '{kw}')。"
                        f"可能原因: (1)规则库缺少相关条目 (2)关键词传递断裂。"
                        f"建议: 扩大 chunk_search top_k 用文档内容补充规则推理。"
                    )

            if nt == "impact_analysis":
                impacted = data.get("impacted", [])
                if len(impacted) == 0:
                    trigger = data.get("trigger", "?")
                    reflections.append(
                        f"[GAP] 影响分析对 '{trigger}' 返回0个影响实体。"
                        f"可能原因: (1)KG缺少 controls/depends_on 边"
                        f" (2)实体名称与KG节点名不匹配。"
                        f"建议: 用 rule_lookup 搜索 '{trigger}' 相关规则作为补充。"
                    )

            if nt == "path_finder":
                paths = data.get("paths", [])
                if len(paths) == 0:
                    source = data.get("source", "?")
                    target = data.get("target", "?")
                    error_msg = data.get("error", "")
                    reflections.append(
                        f"[GAP] 路径查找 ({source} → {target}) 返回0条路径。"
                        f"{'错误: ' + error_msg if error_msg else '状态图可能不连通。'}"
                        f"建议: 用 state_machine 分别查询 {source} 和 {target} 的出边。"
                    )

            if nt == "chunk_search":
                chunks = data.get("chunks", [])
                if len(chunks) == 0:
                    query_used = data.get("query_used", "")[:60]
                    reflections.append(
                        f"[GAP] 文档检索返回0条片段 (查询: '{query_used}')。"
                        f"建议: 检查 embedding 索引是否正常加载，或用原始 query 重试。"
                    )

        # ── Level 3: 完整性评估认为不充分 ──
        for nid, no in result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            if no.node_type == "completeness_eval":
                if not no.output.get("is_sufficient", True):
                    score = no.output.get("overall_score", 0)
                    gap_queries = no.output.get("gap_queries", [])
                    dims_insufficient = [
                        d["name"] for d in no.output.get("dimensions", [])
                        if d.get("status") != "sufficient"
                    ]
                    reflections.append(
                        f"[QUALITY] 完整性评估不充分 (评分: {score:.0%})。"
                        f"不足维度: {', '.join(dims_insufficient) if dims_insufficient else '无'}"
                        f"{'; 建议跟进: ' + ', '.join(gap_queries[:2]) if gap_queries else ''}"
                    )

        return reflections

    def _augment_plan_from_reflections(
        self, dag_plan: dict, reflections: list[str],
    ) -> dict:
        """根据反思结果调整DAG计划（参数级 + 拓扑级）。

        策略矩阵:
          - 规则查询为空 + 状态机有转移边 → 用 guard 条件作为 rules 关键词
          - 影响分析为空 + 意图有信号 → 将信号名作为 impact entity
          - 路径查找为空 + 意图有≥2状态 → 确保 path 节点启用
          - 状态机为空 + 文档有片段 → 启用 sm 并用文档提到状态名
          - 文档检索为空 → 增大 top_k
          - 完整性不足 → 确保所有 optional 节点启用

        返回修改后的 dag_plan（深拷贝）。
        """
        import copy
        dag_plan = copy.deepcopy(dag_plan)
        nodes = dag_plan.get("nodes", {})
        reflection_text = " ".join(reflections).lower()

        # ── 参数级调整 ──

        # 规则缺失 + 状态机有转移边 → 用 guard 条件中的关键词丰富 rules
        if ("rule" in reflection_text or "规则" in reflection_text) and "gap" in reflection_text:
            if "rules" in nodes:
                nodes["rules"]["enabled"] = True
                # 尝试从上游 sm 输出获取 guard 关键词（在 re-execute 时生效）
                if "sm" in nodes:
                    nodes["sm"]["enabled"] = True

        # 影响分析为空 → 确保 impact 启用 + 确保有 entity 参数
        if "影响" in reflection_text or "impact" in reflection_text:
            if "impact" in nodes:
                nodes["impact"]["enabled"] = True
                # 如果 impact 的 entity 参数为空，尝试从 intent 获取
                if not nodes["impact"].get("params", {}).get("entity"):
                    # 标记需要从上游填充（在数据流中自动处理）
                    nodes["impact"]["params"]["entity"] = ""

        # 路径为空 → 确保 path 启用 + 标记源/目标从意图获取
        if "路径" in reflection_text or "path" in reflection_text:
            if "path" in nodes:
                nodes["path"]["enabled"] = True

        # 状态机为空 → 确保 sm 启用
        if ("状态机" in reflection_text or "state_machine" in reflection_text) and "gap" in reflection_text:
            if "sm" in nodes:
                nodes["sm"]["enabled"] = True

        # 文档检索为空 → 增大 top_k
        if ("文档检索" in reflection_text or "chunk" in reflection_text) and "gap" in reflection_text:
            if "chunks" in nodes:
                current_top_k = nodes["chunks"].get("params", {}).get("top_k", 5)
                nodes["chunks"]["params"]["top_k"] = min(current_top_k + 5, 20)

        # ── 完整性不足 → 启用所有 optional 节点 ──
        if "quality" in reflection_text or "不充分" in reflection_text:
            for nid in nodes:
                if not nodes[nid].get("enabled", True):
                    nodes[nid]["enabled"] = True

        # ── 强制启用 conflicts（诊断场景） ──
        if "conflict" in reflection_text or "冲突" in reflection_text:
            if "conflicts" in nodes:
                nodes["conflicts"]["enabled"] = True

        # ── 确保 eval 节点始终启用（需要完整性评估） ──
        if "eval" in nodes:
            nodes["eval"]["enabled"] = True

        dag_plan["nodes"] = nodes
        dag_plan["reasoning"] = (
            dag_plan.get("reasoning", "")
            + f" [反思迭代: {'; '.join(reflections[:2])}]"
        )
        return dag_plan

    def _self_critique_answer(
        self, question: str, answer: str, dag_result: DagResult,
    ) -> str:
        """LLM自检：检查答案是否有证据支撑，是否与DAG节点输出一致。

        四维检查:
          1. 长度检查 — 答案是否过短（< 50 字）
          2. 幻觉检查 — 答案中的关键断言是否有 chunk/规则/状态机输出支撑
          3. 遗漏检查 — DAG节点有输出但答案未引用
          4. 置信度一致性 — LLM声称的置信度是否与DAG统计匹配

        返回:
          "PASS" — 答案质量合格
          "需要修正: [具体问题]" — 需要重新合成
        """
        if not answer or len(answer) < 50:
            return "需要修正: 答案过短（不足50字符），可能未完整合成DAG输出。"

        # 收集节点输出摘要（含实际内容，不只统计数字）
        evidence_parts = []
        node_success_count = 0
        node_total = len(dag_result.node_outputs)

        for nid, no in dag_result.node_outputs.items():
            if no.status == "success":
                node_success_count += 1
            if no.status == "success" and no.output:
                data = no.output
                if no.node_type == "chunk_search":
                    for c in data.get("chunks", [])[:2]:
                        evidence_parts.append(
                            f"[chunk §{c.get('section_path','?')}] {c.get('text','')[:200]}"
                        )
                elif no.node_type == "state_machine":
                    for t in data.get("transitions", [])[:2]:
                        evidence_parts.append(
                            f"[sm] {t.get('source','?')}→{t.get('target','?')}: "
                            f"{t.get('guard','?')[:200]}"
                        )
                elif no.node_type == "rule_lookup":
                    for r in data.get("matched_rules", [])[:2]:
                        evidence_parts.append(
                            f"[rule {r.get('rule_id','?')}] {r.get('condition','')[:200]}"
                        )
                elif no.node_type == "impact_analysis":
                    trigger = data.get("trigger", "?")
                    count = data.get("total_impacted", len(data.get("impacted", [])))
                    evidence_parts.append(f"[impact] {trigger} → {count} entities")
                elif no.node_type == "path_finder":
                    paths = data.get("paths", [])
                    if paths:
                        seq = " → ".join(paths[0].get("sequence", []))
                        evidence_parts.append(f"[path] {seq} ({paths[0].get('hops',0)} hops)")
                elif no.node_type == "completeness_eval":
                    evidence_parts.append(
                        f"[eval] score={data.get('overall_score',0):.0%} "
                        f"sufficient={data.get('is_sufficient',True)}"
                    )

        evidence_text = " | ".join(evidence_parts) if evidence_parts else "无证据"

        prompt = f"""你是BCM工程答案质量检查员。对LLM生成的答案进行严格自检。

═══════════════════════════════════════════════════════════════
用户问题:
{question}

证据来源 (DAG节点输出摘要):
{evidence_text}

DAG执行统计:
- 成功节点: {node_success_count}/{node_total}
- 模板: {dag_result.template}

LLM答案:
{answer[:1200]}

═══════════════════════════════════════════════════════════════
检查清单（每一项都要检查）:
═══════════════════════════════════════════════════════════════

1. 幻觉检查: 答案中的每个关键断言是否都能在证据中找到对应内容？
   - 如果有声称"根据§X.Y.Z"但证据中没有该章节 → 幻觉
   - 如果有声称具体数值但证据中没有 → 幻觉
   - 注意: "状态机转移边"和"规则匹配"是系统推理的输出，属于合法证据

2. 遗漏检查: DAG节点返回了有意义的数据但答案未引用？
   - 如果 sm 节点有3条转移边但答案只提到1条 → 遗漏
   - 如果 impact 节点有5个影响实体但答案只说"有影响"不列举 → 遗漏
   - 如果 path 节点找到了路径但答案未展示完整路径 → 遗漏

3. 置信度一致性: 答案中的 CONFIDENCE 值是否与证据覆盖面相匹配？
   - 如果 chunk=0且rule=0但 CONFIDENCE>0.8 → 高估
   - 如果所有节点都有数据但 CONFIDENCE<0.5 → 低估

4. 结构完整性: 答案是否包含结论/分析/证据来源/置信度四个部分？

═══════════════════════════════════════════════════════════════
判定规则:
- 全部通过 → 回复 "PASS"
- 有任何问题 → 回复 "需要修正: [1-2句话描述最严重的问题]"
- 如果多个问题，只报告最严重的那个
═══════════════════════════════════════════════════════════════"""

        try:
            llm = self._get_llm()
            result = llm.answer(
                evidence=prompt,
                query=question,
                system_prompt="你是BCM工程答案质量检查员。严格检查幻觉、遗漏和置信度一致性。只回复PASS或需要修正。",
            )
            critique = result.get("answer", "")
            if "PASS" in critique.upper():
                return "PASS"
            return critique[:300]  # 截断过长的 critique
        except Exception:
            return ""  # LLM不可用时跳过自检

    def _build_audit_trail(
        self, result: DagResult, dag_plan: dict,
        reflections: list[str] | None = None,
        all_plans: list[dict] | None = None,
    ) -> str:
        """构建人类可读的审计追踪。

        包含:
          - 查询信息、模板选择、选择理由
          - 执行拓扑（层级顺序）
          - 数据流边（节点间依赖）
          - 节点执行详情
          - 反思闭环日志（如有）
          - 节点利用率报告
          - Answer自检结果（如有）
          - 关键诊断指标
        """
        lines = [
            "=" * 70,
            "DAG Agent 审计追踪",
            "=" * 70,
            f"查询: {result.question}",
            f"模板: {result.template}",
            f"LLM选择理由: {dag_plan.get('reasoning', 'N/A')}",
            f"迭代次数: {result.iteration_count}",
            f"总耗时: {result.total_duration_ms:.0f}ms",
            f"置信度: {result.confidence:.0%}",
            "",
        ]

        # ── 执行拓扑 ──
        lines.append("─" * 50)
        lines.append("执行拓扑（层级顺序）")
        lines.append("─" * 50)
        for i, level in enumerate(result.execution_order):
            lines.append(f"  Level {i}: {', '.join(level)}")

        # ── 数据流边 ──
        lines.append("")
        lines.append("─" * 50)
        lines.append("数据流边（节点间依赖）")
        lines.append("─" * 50)
        for edge in dag_plan.get("edges", []):
            src = edge.get("from", "?")
            tgt = edge.get("to", "?")
            df = edge.get("data_flow", "")
            lines.append(f"  {src} → {tgt}" + (f" [{df}]" if df else ""))
        if dag_plan.get("custom_edges"):
            lines.append("  自定义边:")
            for ce in dag_plan["custom_edges"]:
                lines.append(f"    {ce.get('from','?')} → {ce.get('to','?')}")

        # ── 节点执行详情 ──
        lines.append("")
        lines.append("─" * 50)
        lines.append("节点执行详情")
        lines.append("─" * 50)
        success_count = 0
        error_count = 0
        for node_id, output in result.node_outputs.items():
            icon = "[OK]" if output.status == "success" else "[ERR]"
            lines.append(
                f"  {icon} {output.node_type}/{node_id} "
                f"({output.duration_ms:.0f}ms)"
            )
            if output.error:
                lines.append(f"    错误: {output.error}")
                error_count += 1
            elif output.status == "success":
                success_count += 1
                # 输出关键指标摘要
                if output.output:
                    data = output.output
                    if output.node_type == "state_machine":
                        n_trans = len(data.get("transitions", []))
                        lines.append(f"    → {n_trans} 条转移边")
                    elif output.node_type == "rule_lookup":
                        n_rules = len(data.get("matched_rules", []))
                        lines.append(f"    → {n_rules} 条匹配规则")
                    elif output.node_type == "impact_analysis":
                        n_imp = data.get("total_impacted", len(data.get("impacted", [])))
                        lines.append(f"    → {n_imp} 个影响实体")
                    elif output.node_type == "path_finder":
                        n_paths = data.get("total_paths", len(data.get("paths", [])))
                        lines.append(f"    → {n_paths} 条路径")
                    elif output.node_type == "chunk_search":
                        n_chunks = len(data.get("chunks", []))
                        lines.append(f"    → {n_chunks} 条文档片段")
                    elif output.node_type == "completeness_eval":
                        score = data.get("overall_score", 0)
                        suff = "充分" if data.get("is_sufficient") else "不充分"
                        lines.append(f"    → 完整性 {score:.0%} ({suff})")

        lines.append(f"\n  成功: {success_count}, 失败: {error_count}, "
                      f"总计: {success_count + error_count}")

        # ── 反思闭环日志 ──
        if reflections:
            lines.append("")
            lines.append("─" * 50)
            lines.append(f"反思闭环日志 ({len(reflections)} 条)")
            lines.append("─" * 50)
            for i, r in enumerate(reflections, 1):
                lines.append(f"  [{i}] {r}")

            # 多轮计划变更
            if all_plans and len(all_plans) > 1:
                lines.append("")
                lines.append("  计划变更历史:")
                for i, plan in enumerate(all_plans):
                    reason = plan.get("reasoning", "?")[:100]
                    lines.append(f"    第{i+1}轮: {reason}")

        # ── 节点利用率 ──
        if result.node_utilization:
            lines.append("")
            lines.append("─" * 50)
            lines.append("节点利用率")
            lines.append("─" * 50)
            for ntype, prop in sorted(result.node_utilization.items(),
                                       key=lambda x: -x[1]):
                bar_len = 10
                filled = int(prop * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                lines.append(f"  {ntype:<25s} {bar} {prop:.0%}")

            # 系统健康诊断
            rule_util = result.node_utilization.get("rule_lookup", 0)
            reach_util = result.node_utilization.get("reachability", 0)
            sm_util = result.node_utilization.get("state_machine", 0)
            if rule_util < 0.2:
                lines.append(f"  ⚠ 规则节点利用率 ({rule_util:.0%}) < 20% — 系统可能退化为普通RAG")
            if reach_util == 0:
                lines.append(f"  ⚠ 可达性分析从未触发 — 如非查询类型所致则正常")
            if sm_util < 0.1:
                lines.append(f"  ⚠ 状态机利用率 ({sm_util:.0%}) < 10% — 检查模板选择是否正确")

        # ── Answer自检结果 ──
        if result.critique:
            lines.append("")
            lines.append("─" * 50)
            lines.append("Answer 自检")
            lines.append("─" * 50)
            if result.critique == "PASS":
                lines.append("  ✓ 自检通过")
            else:
                lines.append(f"  ⚠ {result.critique[:500]}")

        # ── 关键诊断指标 ──
        lines.append("")
        lines.append("─" * 50)
        lines.append("关键诊断指标")
        lines.append("─" * 50)
        # 统计 DAG 执行的整体健康状况
        total_nodes = len(result.node_outputs)
        failed_nodes = sum(1 for o in result.node_outputs.values() if o.status == "error")
        empty_data_nodes = sum(
            1 for o in result.node_outputs.values()
            if o.status == "success" and o.output and
            (
                (o.node_type == "state_machine" and len(o.output.get("transitions", [])) == 0) or
                (o.node_type == "rule_lookup" and len(o.output.get("matched_rules", [])) == 0) or
                (o.node_type == "impact_analysis" and len(o.output.get("impacted", [])) == 0) or
                (o.node_type == "path_finder" and len(o.output.get("paths", [])) == 0) or
                (o.node_type == "chunk_search" and len(o.output.get("chunks", [])) == 0)
            )
        )
        lines.append(f"  总节点数: {total_nodes}")
        lines.append(f"  失败节点: {failed_nodes}")
        lines.append(f"  空数据节点: {empty_data_nodes}")
        lines.append(f"  反思触发: {'是' if reflections else '否'} ({len(reflections) if reflections else 0} 条)")
        lines.append(f"  迭代次数: {result.iteration_count}")
        lines.append(f"  自检结果: {result.critique[:80] if result.critique else '未执行'}")

        return "\n".join(lines)


# ======================================================================
# CLI Demo — 直接运行 python -m agent.dag_agent 即可测试
# ======================================================================

if __name__ == "__main__":
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"

    # 使用DeepSeek作为默认LLM（从环境变量 DEEPSEEK_API_KEY 读取密钥）
    agent = DagAgent(provider="deepseek")
    agent.load()

    # 六个预设测试查询，覆盖所有推理模板
    tests = [
        ("factual_lookup", "IGN1信号的定义是什么？"),
        ("state_transition", "进入Driving需要什么条件？"),
        ("path_finding", "从Abandoned模式如何进入Driving模式？"),
        ("impact_analysis", "KeyLost会影响哪些功能？"),
        ("diagnostic", "为什么车辆无法从Inactive进入Driving？"),
        ("reachability_check", "VMM状态机是否存在不可达状态？"),
    ]

    all_results = []
    for expected_tmpl, q in tests:
        print(f"\n{'='*70}")
        print(f"查询 [{expected_tmpl}]: {q}")
        print(f"{'='*70}")

        result = agent.query(q)
        all_results.append(result)

        print(f"\n模板: {result.template} (期望: {expected_tmpl})")
        print(f"置信度: {result.confidence:.0%}")
        print(f"耗时: {result.total_duration_ms:.0f}ms")
        print(f"迭代: {result.iteration_count}轮")
        print(f"反思: {len(result.reflection_log)}条")

        print(f"\n执行层级:")
        for i, level in enumerate(result.execution_order):
            print(f"  Level {i}: {level}")

        print(f"\n节点执行结果:")
        for nid, no in result.node_outputs.items():
            icon = "[OK]" if no.status == "success" else "[ERR]"
            extra = ""
            if no.status == "success" and no.output:
                data = no.output
                if no.node_type == "state_machine":
                    extra = f" — {len(data.get('transitions',[]))} transitions"
                elif no.node_type == "rule_lookup":
                    extra = f" — {len(data.get('matched_rules',[]))} rules"
                elif no.node_type == "impact_analysis":
                    extra = f" — {data.get('total_impacted',0)} impacted"
                elif no.node_type == "path_finder":
                    extra = f" — {data.get('total_paths',0)} paths"
                elif no.node_type == "chunk_search":
                    extra = f" — {len(data.get('chunks',[]))} chunks"
            print(f"  {icon} {nid} ({no.node_type}): {no.duration_ms:.0f}ms{extra}")
            if no.error:
                print(f"    错误: {no.error}")

        # 节点利用率
        if result.node_utilization:
            print(f"\n节点利用率:")
            for ntype, prop in sorted(result.node_utilization.items(), key=lambda x: -x[1]):
                bar = "█" * int(prop * 10) + "░" * (10 - int(prop * 10))
                print(f"  {ntype:<25s} {bar} {prop:.0%}")

        # 自检结果
        if result.critique:
            status = "✓ 通过" if result.critique == "PASS" else f"⚠ {result.critique[:100]}"
            print(f"\n自检: {status}")

        # LLM合成的答案
        print(f"\n{'─'*50}")
        print(f"LLM答案 (前500字符):")
        print(f"{'─'*50}")
        print(result.answer[:500])

    # 打印跨查询系统健康报告
    if len(all_results) >= 3:
        print(f"\n\n")
        print(agent.get_aggregated_utilization_report(all_results))
