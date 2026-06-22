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
                "params": {"top_k": 5},
                "required": True,
            },
        },
        edges=[
            {"from": "intent", "to": "chunks"},
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
                "params": {"top_k": 5},
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
                "params": {"top_k": 5},
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
                "params": {"top_k": 5},
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
                "params": {"top_k": 5},
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
                "params": {"top_k": 5},
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
        ],
    ),
}

# 供LLM选择模板时参考的模板描述（中文）
TEMPLATE_DESCRIPTIONS_FOR_LLM = """
你是BCM（车身控制模块）工程推理的路由器。你的任务是根据用户查询，从6个DAG模板中选择最合适的一个。

═══════════════════════════════════════════════════════════════
决策规则（必须遵守）：
═══════════════════════════════════════════════════════════════

1. 如果查询包含状态名（Inactive / Convenience / Driving / Abandoned）
   → 绝对不要选 factual_lookup。选 state_transition 或 path_finding。

2. 如果查询包含"影响"/"导致"/"后果"/"失效"
   → 选 impact_analysis。

3. 如果查询包含"为什么不能"/"无法"/"故障"/"诊断"/"检测"
   → 选 diagnostic。

4. 如果查询包含"路径"/"如何从"/"怎么从"/"几步"/"经过哪些"
   → 选 path_finding。

5. 如果查询包含"死锁"/"不可达"/"是否存在"/"所有状态"
   → 选 reachability_check。

6. 只有纯粹的"是什么"/"定义"/"有哪些"/"参数"查询才选 factual_lookup。

═══════════════════════════════════════════════════════════════
模板详情（每个模板有固定的 node_id 列表，输出时必须使用这些 node_id）：
═══════════════════════════════════════════════════════════════

1. factual_lookup — 事实/定义查询
   固定 node_id: ["intent", "chunks"]
   适用: IGN1信号是什么？有哪些模块？参数配置是多少？
   不适用: 任何包含状态名、故障、影响、路径的查询

2. state_transition — 状态转移推理
   固定 node_id: ["intent", "sm", "rules", "chunks"]
   适用: 如何进入Driving？Inactive的退出条件？Convenience→Driving需要什么？
   不适用: 纯定义查询

3. impact_analysis — 影响链分析
   固定 node_id: ["intent", "impact", "sm", "rules", "chunks"]
   适用: KeyLost会影响什么？IGN1故障的后果？

4. path_finding — 路径查找
   固定 node_id: ["intent", "path", "sm", "rules", "chunks"]
   适用: 从Abandoned如何到Driving？经过哪些状态？

5. diagnostic — 故障诊断
   固定 node_id: ["intent", "rules", "impact", "sm", "conflicts", "chunks"]
   适用: 为什么不能启动？车窗不工作？故障如何检测？

6. reachability_check — 可达性检查
   固定 node_id: ["intent", "reach", "sm", "rules", "chunks"]
   适用: 死锁？不可达状态？状态机完整吗？

═══════════════════════════════════════════════════════════════
输出格式（严格JSON，node_id 必须使用上述列表中的值，不能自己编）：
═══════════════════════════════════════════════════════════════

{
  "template": "模板名",
  "reasoning": "为什么选这个模板（中文，必须引用决策规则）",
  "nodes": {
    "intent": {"enabled": true},
    "sm": {"enabled": true, "params": {"states": ["从查询中提取的状态名"]}},
    "rules": {"enabled": true, "params": {"keywords": "", "modules": []}},
    "chunks": {"enabled": true, "params": {"top_k": 5}}
  },
  "custom_edges": []
}

注意：
- nodes 对象中只包含该模板的固定 node_id，不要用类型名（如 chunk_search）作为 node_id
- params 中可填从查询中提取的实体（状态名、信号名、模块名）
- 如果模板不需要某节点，不要包含在 nodes 中
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
    if sm and "transitions" in sm:
        for t in sm["transitions"]:
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
    module = params.get("module", "VMM")

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
    module = params.get("module", "VMM")

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
    """
    query = params.get("query", "")
    top_k = params.get("top_k", 5)

    # 如果未指定查询，从上游数据中收集关键词
    if not query:
        for up_data in upstream.values():
            if up_data.get("keywords"):
                query = " ".join(up_data["keywords"][:10])
                break

    if not query:
        return {"chunks": [], "error": "未提供搜索查询"}

    try:
        result = pipeline.search(query, top_k=top_k, enable_llm=False)
        chunks = []
        for entry in result.get("merged", [])[:top_k]:
            chunk = entry.get("chunk", {})
            chunks.append({
                "chunk_id": chunk.get("chunk_id", ""),
                "chunk_type": chunk.get("chunk_type", ""),
                "module": chunk.get("module", ""),
                "section_path": chunk.get("section_path", ""),
                "section_title": chunk.get("section_title", ""),
                "text": chunk.get("text", "")[:500],
                "score": entry.get("score", 0),
            })
        return {"chunks": chunks, "query_used": query}
    except Exception as e:
        return {"chunks": [], "error": str(e)}


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
    ) -> dict:
        """合成最终答案。

        参数:
            question: 用户原始查询
            dag_plan: 执行的DAG计划
            dag_result: DAG执行结果

        返回:
            {answer, confidence, citations, model, usage}
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            question, dag_plan, dag_result,
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

    def _build_system_prompt(self) -> str:
        """构建系统提示词。"""
        return """你是汽车BCM（车身控制模块）工程专家Agent。

你的任务是根据DAG推理引擎的执行结果，生成结构化的工程回答。

规则：
1. 每个结论必须引用具体的节点输出（格式：[节点类型] 内容）
2. DAG拓扑展示了推理步骤之间的依赖关系，请在回答中体现推理链
3. 对于状态转换问题，描述完整的状态链和触发条件
4. 对于信号问题，说明信号来源、用途和相关模块
5. 对于故障诊断问题，列出检测条件、故障反应和恢复方式
6. 如果证据不足，明确说明信息缺口
7. 使用中文回答，技术术语保留英文原名
8. 使用结构化格式，必要时使用列表或表格

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
  +0.05: chunk_search检索到3条以上相关文档片段
  +0.05: 多个节点输出互相印证（如状态转移与规则匹配一致）

扣分项（每项-0.05到-0.20）：
  -0.20: 有节点执行失败
  -0.15: state_machine返回空（0条转移边）
  -0.15: rule_lookup返回空（0条匹配规则）
  -0.10: chunk_search返回空或完全不相关
  -0.10: 意图分析提取到的实体少于2个
  -0.05: 数据流传递断裂（上游有输出但下游未收到）

最终置信度钳制在 [0.10, 0.95] 范围内。
在回答末尾输出: CONFIDENCE=X.XX（两位小数）"""

    def _build_user_prompt(
        self,
        question: str,
        dag_plan: dict,
        dag_result: DagResult,
    ) -> str:
        """构建用户提示词（含完整DAG执行结果）。"""
        parts = [
            f"# 用户问题\n{question}\n",
            f"# DAG推理模板: {dag_result.template}",
            f"# LLM选择理由: {dag_plan.get('reasoning', 'N/A')}\n",
            "# 推理拓扑（执行层级）",
        ]

        # 展示执行层级
        for i, level in enumerate(dag_result.execution_order):
            parts.append(f"  Level {i}: {' → '.join(level)}")

        parts.append("")

        # 展示数据流边
        if dag_plan.get("edges"):
            parts.append("# 数据流（节点间依赖）")
            for edge in dag_plan["edges"]:
                src = edge.get("from", "?")
                tgt = edge.get("to", "?")
                df = edge.get("data_flow", "")
                parts.append(f"  {src} → {tgt}" + (f" ({df})" if df else ""))
            parts.append("")

        # 展示各节点输出
        parts.append("# 节点执行结果")
        for node_id, output in dag_result.node_outputs.items():
            node_type = output.node_type
            status = output.status
            duration = output.duration_ms

            parts.append(f"\n## [{node_type}] {node_id} (状态: {status}, 耗时: {duration:.0f}ms)")

            if status == "error":
                parts.append(f"错误: {output.error}")
                continue

            data = output.output or {}
            self._format_node_output(parts, node_type, data)

        # ---- 注入 DAG 执行统计（供 LLM 按评分标准计算置信度） ----
        stats = self._compute_dag_stats(dag_result)
        parts.append("\n# DAG执行统计（用于置信度计算）")
        parts.append(f"总节点数: {stats['total_nodes']}")
        parts.append(f"成功节点数: {stats['success_nodes']}")
        parts.append(f"失败节点数: {stats['failed_nodes']}")
        parts.append(f"节点成功率: {stats['success_rate']:.0%}")
        parts.append(f"state_machine转移边数: {stats['sm_transitions']}")
        parts.append(f"rule_lookup匹配规则数: {stats['rule_matches']}")
        parts.append(f"impact_analysis影响实体数: {stats['impact_count']}")
        parts.append(f"path_finder路径数: {stats['path_count']}")
        parts.append(f"chunk_search文档片段数: {stats['chunk_count']}")
        parts.append(f"reachability/conflict发现数: {stats['reach_conflict_count']}")
        parts.append(f"数据流是否完整: {stats['dataflow_intact']}")
        if stats['failed_details']:
            parts.append(f"失败节点详情: {stats['failed_details']}")

        # 答案要求
        parts.append("\n# 请回答")
        parts.append("基于上述DAG推理结果，生成结构化工程回答：")
        parts.append("\n## 结论")
        parts.append("[用1-2句话直接回答用户问题]")
        parts.append("\n## 推理链")
        parts.append("[基于DAG拓扑展示推理步骤，说明各节点如何贡献最终结论]")
        parts.append("\n## 详细分析")
        parts.append("[基于各节点输出展开分析，引用具体的节点输出]")
        parts.append("\n## 证据来源")
        parts.append("[列出各节点的关键输出，标注章节号]")
        parts.append("\n## 置信度评估")
        parts.append("[必须按照System Prompt中的评分标准逐项计算，列出每项加分和扣分，最后给出CONFIDENCE=X.XX]")
        parts.append("CONFIDENCE=X.XX")

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
                    f"- {t['source']} → {t['target']}: {t.get('guard', '?')[:120]}"
                )
                if t.get("section"):
                    parts.append(f"  (§{t['section']})")

        elif node_type == "rule_lookup":
            for r in data.get("matched_rules", [])[:8]:
                parts.append(
                    f"- [{r.get('rule_id','?')}] {r.get('condition','')[:150]}"
                )
                if r.get("action"):
                    parts.append(f"  → {r['action'][:150]}")

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
                    f"({c.get('module','?')}): {c.get('text','')[:200]}"
                )

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

        防止LLM写死固定值（如始终输出0.80）：
          - 如果LLM有节点失败但声称置信度>0.85，自动下调
          - 如果LLM没输出CONFIDENCE（=0.5），根据DAG统计计算
          - 最终值与节点成功率强绑定
        """
        stats = self._compute_dag_stats(dag_result)

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
            penalties = stats["failed_nodes"] * 0.10
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
          3. 状态机JSON（output/content_analysis/state_machine_VMM.json）
          4. 规则库JSON（output/content_analysis/rules.json）
        """
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

        from retrieval import RetrievalPipeline
        self._pipeline = RetrievalPipeline()
        self._pipeline.load(use_dense=True)

        from retrieval.reasoning_engine import ReasoningEngine
        self._engine = ReasoningEngine()

        sm_path = Path("output/content_analysis/state_machine_VMM.json")
        rules_path = Path("output/content_analysis/rules.json")

        if sm_path.exists():
            self._engine.load_state_machine(sm_path)
            self._sm = json.loads(sm_path.read_text(encoding="utf-8"))

        if rules_path.exists():
            self._engine.load_rules(rules_path)
            self._rules = json.loads(rules_path.read_text(encoding="utf-8"))

        self._loaded = True
        print("DagAgent就绪。已加载6个推理模板。")
        return self

    def query(
        self,
        question: str,
        template: str | None = None,
    ) -> DagResult:
        """执行DAG推理查询。

        完整流程：
          1. 模板选择（LLM自动选择 或 手动指定）
          2. DAG执行（拓扑排序 → 层级并行 → 数据流传递）
          3. 答案合成（LLM消费DAG结果 → 结构化工程回答）
          4. 审计追踪（记录完整执行过程）

        参数:
            question: 用户工程查询
            template: 强制使用指定模板（None=LLM自动选择）。
                      可选值: factual_lookup | state_transition |
                             impact_analysis | path_finding |
                             diagnostic | reachability_check

        返回:
            DagResult（含答案、节点输出、审计追踪）
        """
        if not self._loaded:
            raise RuntimeError("DagAgent未加载。请先调用 .load()。")

        t0 = time.time()

        # ==== 步骤1: 选择模板 ====
        if template and template in DAG_TEMPLATES:
            # 手动指定模板
            dag_plan = self._build_plan_from_template(template, question)
        elif self._has_llm():
            # LLM自动选择模板 + 参数填充
            dag_plan = self._select_template_with_llm(question)
        else:
            # 回退：关键词匹配选模板
            dag_plan = self._select_template_fallback(question)

        # ==== 步骤2: 执行DAG ====
        result = self._executor.execute(
            dag_plan=dag_plan,
            pipeline=self._pipeline,
            engine=self._engine,
            sm=self._sm,
            rules=self._rules,
            query=question,
        )

        # ==== 步骤3: 合成答案 ====
        if self._has_llm():
            llm = self._get_llm()
            synthesizer = DagSynthesizer(llm)
            synth_result = synthesizer.synthesize(
                question=question,
                dag_plan=dag_plan,
                dag_result=result,
            )
            result.answer = synth_result["answer"]
            result.confidence = synth_result.get("confidence", 0.5)
        else:
            # 无LLM时回退到规则拼接
            synthesizer = DagSynthesizer(None)
            synth_result = synthesizer._fallback_synthesize(question, result)
            result.answer = synth_result["answer"]
            result.confidence = synth_result.get("confidence", 0.5)

        # ==== 步骤4: 构建审计追踪 ====
        result.audit_trail = self._build_audit_trail(result, dag_plan)

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
                return self._merge_with_template(plan)
        except Exception:
            pass

        return self._select_template_fallback(question)

    def _select_template_fallback(self, question: str) -> dict:
        """回退方案：关键词匹配选择模板。

        当LLM不可用时使用。对查询中的关键词与每个模板的
        trigger_keywords做匹配，选得分最高的模板。
        """
        ql = question.lower()

        scores = {}
        for name, tmpl in DAG_TEMPLATES.items():
            score = sum(
                1 for kw in tmpl.trigger_keywords if kw.lower() in ql
            )
            if score > 0:
                scores[name] = score

        if scores:
            best = max(scores, key=scores.get)
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

    def _build_audit_trail(
        self, result: DagResult, dag_plan: dict,
    ) -> str:
        """构建人类可读的审计追踪。

        包含: 查询信息、模板选择、执行拓扑、数据流边、节点详情。
        """
        lines = [
            f"## DAG Agent 审计追踪",
            f"查询: {result.question}",
            f"模板: {result.template}",
            f"LLM选择理由: {dag_plan.get('reasoning', 'N/A')}",
            f"总耗时: {result.total_duration_ms:.0f}ms",
            "",
            "## 执行拓扑（层级顺序）",
        ]

        for i, level in enumerate(result.execution_order):
            lines.append(f"  Level {i}: {', '.join(level)}")

        lines.append("")
        lines.append("## 数据流边（节点间依赖）")
        for edge in dag_plan.get("edges", []):
            src = edge.get("from", "?")
            tgt = edge.get("to", "?")
            df = edge.get("data_flow", "")
            lines.append(f"  {src} → {tgt}" + (f" [{df}]" if df else ""))

        lines.append("")
        lines.append("## 节点执行详情")
        for node_id, output in result.node_outputs.items():
            icon = "[OK]" if output.status == "success" else "[ERR]"
            lines.append(
                f"  {icon} {output.node_type}/{node_id} "
                f"({output.duration_ms:.0f}ms)"
            )
            if output.error:
                lines.append(f"    错误: {output.error}")

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

    # 五个预设测试查询，覆盖不同的推理模板
    tests = [
        "IGN1信号的定义是什么？",
        "从Abandoned模式如何进入Driving模式？",
        "KeyLost会影响哪些功能？",
        "为什么车辆无法从Inactive进入Driving？",
        "VMM状态机是否存在不可达状态？",
    ]

    for q in tests:
        print(f"\n{'='*70}")
        print(f"查询: {q}")
        print(f"{'='*70}")

        result = agent.query(q)

        print(f"\n模板: {result.template}")
        print(f"置信度: {result.confidence:.0%}")
        print(f"耗时: {result.total_duration_ms:.0f}ms")

        print(f"\n执行层级:")
        for i, level in enumerate(result.execution_order):
            print(f"  Level {i}: {level}")

        print(f"\n节点执行结果:")
        for nid, no in result.node_outputs.items():
            icon = "[OK]" if no.status == "success" else "[ERR]"
            print(f"  {icon} {nid} ({no.node_type}): {no.duration_ms:.0f}ms")
            if no.error:
                print(f"    错误: {no.error}")

        # LLM合成的答案（完整输出）
        print(f"\n{'─'*50}")
        print(f"LLM答案:")
        print(f"{'─'*50}")
        print(result.answer)
        print(f"\n{result.audit_trail}")
