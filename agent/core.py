"""BCM-RAG Agent Core — Tool-use Agent with function calling.

The agent uses an LLM (Zhipu/Ark/DeepSeek) to:
  1. Understand the user's engineering question
  2. Select appropriate tools (search, graph, rules, state machine, reasoning)
  3. Execute tools and collect results
  4. Synthesize a structured answer with citations

Pattern: Plan → Execute → Synthesize (iterate up to 3 rounds)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Tool Definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_chunks",
            "description": "搜索BCM文档中的相关内容块。适用于事实查询、定义查询、信号取值查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询，中文或英文"},
                    "top_k": {"type": "integer", "default": 5, "description": "返回结果数量"},
                    "module": {"type": "string", "default": "", "description": "可选模块过滤，如VMM/ExteriorLight/Window/Lock/Wiper"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_graph",
            "description": "查询知识图谱中的实体和关系。适用于: 某个信号属于哪个模块？某个模块有哪些信号？两个实体之间有什么关系？",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "实体名称，如PEPS_UsageMode, IGN1, VMM"},
                    "entity_type": {"type": "string", "default": "", "description": "实体类型: module/signal/state/function/fault"},
                    "expand_hops": {"type": "integer", "default": 1, "description": "扩展跳数，1=直接邻居，2=邻居的邻居"},
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_rules",
            "description": "查询BCM规则库。适用于: 某功能的激活条件？某信号的编码定义？故障检测逻辑？条件推理问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {"type": "string", "default": "", "description": "模块名: VMM/ExteriorLight/Window/Lock/Wiper"},
                    "keywords": {"type": "string", "default": "", "description": "关键词，用空格分隔"},
                    "rule_type": {"type": "string", "default": "", "description": "规则类型: transition_guard/activation_rule/deactivation_rule/fault_detection/signal_value"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_state_machine",
            "description": "查询VMM状态机。适用于: 某状态如何进入/退出？状态间如何迁移？有哪些迁移边？",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": "状态名: Abandoned/Inactive/Convenience/Driving"},
                    "module": {"type": "string", "default": "VMM"},
                },
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_path",
            "description": "查找两个状态之间的最短迁移路径。适用于: 从A状态如何到达B状态？需要几步？",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "起始状态"},
                    "target": {"type": "string", "description": "目标状态"},
                },
                "required": ["source", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_impact",
            "description": "前向影响分析。适用于: 某信号失效会影响什么？某故障会导致什么后果？",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "实体名称: KeyLost/IGN1/PEPS_KeyStatus/Crash"},
                    "max_depth": {"type": "integer", "default": 3},
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_reachability",
            "description": "检查状态机的可达性和死锁。适用于: VMM是否有不可达状态？是否有死锁？",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {"type": "string", "default": "VMM"},
                },
            },
        },
    },
]

# System prompt for the agent
SYSTEM_PROMPT = """你是BCM（车身控制模块）工程专家Agent。

你可以使用以下工具来回答用户问题：
- search_chunks: 搜索文档内容
- query_graph: 查询知识图谱（实体和关系）
- query_rules: 查询规则库
- query_state_machine: 查询状态机
- trace_path: 查找状态迁移路径
- analyze_impact: 分析影响链
- check_reachability: 检查可达性

工作流程：
1. 分析用户问题的类型（事实查询/状态推理/影响分析/路径查找/规则查询）
2. 选择合适的工具（可以调用多个）
3. 根据工具结果给出结构化答案

回答要求：
- 引用具体的章节号和模块名
- 对于状态问题，描述完整的状态链和触发条件
- 对于信号问题，说明信号来源、用途和相关模块
- 对于故障问题，列出检测条件、故障反应和恢复方式
- 使用中文回答，技术术语保留英文原名
- 如果工具信息不足，明确说明"根据现有文档无法确定"
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class BCMAgent:
    """Tool-use Agent for BCM engineering queries.

    Usage:
        agent = BCMAgent()
        agent.load()
        answer = agent.ask("从Abandoned如何进入Driving？")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
        model: str = "glm-4-flash",
        provider: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.provider = provider
        self._pipeline = None
        self._engine = None
        self._kg = None
        self._rules = None
        self._sm = None
        self._loaded = False

    # ---- Load ----

    def load(self) -> "BCMAgent":
        """Load all subsystems: pipeline, KG, rules, state machine."""
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

        print("Loading BCM Agent subsystems...")

        # Retrieval pipeline
        from retrieval import RetrievalPipeline
        self._pipeline = RetrievalPipeline()
        self._pipeline.load(use_dense=True)
        print("  Pipeline: ready")

        # Reasoning engine
        from retrieval.reasoning_engine import ReasoningEngine
        self._engine = ReasoningEngine()
        sm_path = Path("output/content_analysis/state_machine_VMM.json")
        rules_path = Path("output/content_analysis/rules.json")
        kg_path = Path("output/content_analysis/knowledge_graph.json")

        if sm_path.exists():
            self._engine.load_state_machine(sm_path)
            self._sm = json.loads(sm_path.read_text(encoding="utf-8"))
            print(f"  State Machine: {len(self._sm.get('states',{}))} states, {len(self._sm.get('transitions',[]))} transitions")

        if rules_path.exists():
            self._engine.load_rules(rules_path)
            self._rules = json.loads(rules_path.read_text(encoding="utf-8"))
            print(f"  Rules: {self._rules['stats']['total']} rules")

        if kg_path.exists():
            self._engine.load_kg(kg_path)
            self._kg = json.loads(kg_path.read_text(encoding="utf-8"))
            print(f"  KG: {len(self._kg.get('entities',[]))} entities, {len(self._kg.get('relationships',[]))} edges")

        self._loaded = True
        print("Agent ready.")
        return self

    # ---- Main: Ask ----

    def ask(self, question: str, max_rounds: int = 3) -> dict:
        """Answer a BCM engineering question using the tool-use agent.

        Returns:
            {
                "question": str,
                "answer": str,
                "tool_calls": [...],  # which tools were called
                "evidence": str,
                "rounds": int,
            }
        """
        if not self._loaded:
            raise RuntimeError("Agent not loaded. Call .load() first.")

        tool_log = []
        evidence_parts = []

        # Round 1: First tool call based on question type
        tools_needed = self._plan_tools(question)

        # Execute tools
        for tool_name, tool_args in tools_needed:
            result = self._execute_tool(tool_name, tool_args)
            tool_log.append({"tool": tool_name, "args": tool_args, "result_summary": str(result)[:300]})
            if result:
                evidence_parts.append(f"[{tool_name}]: {str(result)[:500]}")

        # Run retrieval for evidence grounding
        retrieval = self._pipeline.search(question, top_k=5)
        evidence_parts.append(retrieval.get("evidence", ""))

        evidence = "\n\n".join(evidence_parts)

        # Synthesize answer with LLM
        answer = self._synthesize(question, evidence, tool_log)

        return {
            "question": question,
            "answer": answer,
            "tool_calls": tool_log,
            "evidence": evidence,
            "rounds": len(tool_log),
        }

    # ---- Tool Planning ----

    def _plan_tools(self, question: str) -> list[tuple[str, dict]]:
        """Determine which tools to use based on question analysis."""
        ql = question.lower()
        tools = []

        # State machine: mentions states or transitions
        state_names = ["abandoned", "inactive", "convenience", "driving"]
        mentioned_states = [s for s in state_names if s in ql]
        if len(mentioned_states) >= 2:
            tools.append(("trace_path", {"source": mentioned_states[0].title(),
                                          "target": mentioned_states[-1].title()}))
        elif mentioned_states:
            tools.append(("query_state_machine", {"state": mentioned_states[0].title()}))

        # Impact analysis
        if any(w in ql for w in ["影响", "impact", "导致", "后果", "连锁", "失效"]):
            for entity in ["KeyLost", "IGN1", "PEPS_KeyStatus", "Crash", "钥匙"]:
                if entity.lower() in ql:
                    tools.append(("analyze_impact", {"entity": entity}))
                    break

        # Rule query
        if any(w in ql for w in ["条件", "触发", "规则", "激活", "关闭", "为什么", "前置"]):
            mod = self._detect_module(question)
            tools.append(("query_rules", {"module": mod, "keywords": question[:100]}))

        # Graph query
        if any(w in ql for w in ["模块", "信号", "属于", "关系", "连接"]):
            entity = self._extract_entity(question)
            if entity:
                tools.append(("query_graph", {"entity": entity}))

        # Reachability
        if any(w in ql for w in ["不可达", "死锁", "活锁", "是否存在", "所有状态"]):
            tools.append(("check_reachability", {"module": "VMM"}))

        # Always do a chunk search for grounding
        tools.append(("search_chunks", {"query": question, "top_k": 5}))

        return tools[:5]  # Max 5 tool calls

    def _detect_module(self, question: str) -> str:
        ql = question.lower()
        mod_map = {
            "vmm": "VMM", "电源": "VMM", "模式": "VMM", "状态": "VMM",
            "exteriorlight": "ExteriorLight", "外灯": "ExteriorLight", "灯": "ExteriorLight",
            "window": "Window", "车窗": "Window", "防夹": "Window",
            "lock": "Lock", "门锁": "Lock", "锁": "Lock",
            "wiper": "Wiper", "雨刮": "Wiper",
            "interior": "InteriorLight", "内灯": "InteriorLight",
        }
        for key, mod in mod_map.items():
            if key in ql:
                return mod
        return ""

    def _extract_entity(self, question: str) -> str:
        """Extract a named entity from the question."""
        # Try English identifiers first
        en_matches = re.findall(r"[A-Z][A-Za-z0-9_]{2,}", question)
        if en_matches:
            return en_matches[0]
        # Try Chinese phrases
        cn_matches = re.findall(r"[一-鿿]{2,6}", question)
        return cn_matches[0] if cn_matches else ""

    # ---- Tool Execution ----

    def _execute_tool(self, tool_name: str, args: dict):
        """Execute a tool and return results."""
        try:
            if tool_name == "search_chunks":
                r = self._pipeline.search(args["query"], top_k=args.get("top_k", 5))
                merged = r.get("merged", [])
                return [{
                    "module": m["chunk"].get("module", ""),
                    "section": m["chunk"].get("section_path", ""),
                    "title": m["chunk"].get("section_title", ""),
                    "text": m["chunk"].get("text", "")[:200],
                } for m in merged[:3]]

            elif tool_name == "query_graph":
                entity = args["entity"]
                etype = args.get("entity_type", "")
                matches = self._pipeline.graph.search_entities(entity, entity_type=etype)
                results = []
                for m in matches[:5]:
                    results.append({
                        "name": m["name"], "type": m["entity_type"],
                        "module": m.get("module", ""), "section": m.get("section_path", ""),
                    })
                # Also show neighbors
                if matches:
                    eid = matches[0]["entity_id"]
                    neighbors = self._pipeline.graph.expand(eid, hops=args.get("expand_hops", 1))
                    for n in neighbors[:5]:
                        ne = n.get("entity", {})
                        results.append({
                            "name": ne.get("name", ""), "type": ne.get("entity_type", ""),
                            "relation": n.get("relationship", ""), "module": ne.get("module", ""),
                        })
                return results

            elif tool_name == "query_rules":
                mod = args.get("module", "")
                keywords = args.get("keywords", "").lower()
                rtype = args.get("rule_type", "")
                rules = self._rules.get("rules", [])
                matched = []
                for r in rules:
                    if mod and r.get("module") != mod:
                        continue
                    if rtype and r.get("rule_type") != rtype:
                        continue
                    rule_text = json.dumps(r, ensure_ascii=False).lower()
                    kw_list = keywords.split()
                    if any(kw in rule_text for kw in kw_list if len(kw) > 1):
                        matched.append(r)
                return [{
                    "rule_id": r["rule_id"], "type": r["rule_type"],
                    "module": r["module"], "section": r.get("source_section", ""),
                    "condition": r.get("condition_expr", "")[:200],
                    "action": r.get("action", "")[:200],
                } for r in matched[:5]]

            elif tool_name == "query_state_machine":
                state = args["state"]
                sm = self._sm
                transitions = sm.get("transitions", [])
                incoming = [t for t in transitions if t["target"] == state]
                outgoing = [t for t in transitions if t["source"] == state]
                return {
                    "state": state,
                    "entering_from": [{"source": t["source"], "guard": t.get("guard", "")[:150]} for t in incoming],
                    "exiting_to": [{"target": t["target"], "guard": t.get("guard", "")[:150]} for t in outgoing],
                }

            elif tool_name == "trace_path":
                paths = self._engine.path_query(args["source"], args["target"])
                return {
                    "source": args["source"], "target": args["target"],
                    "total_paths": paths["total_paths"],
                    "shortest_hops": paths["shortest_hops"],
                    "paths": [{"sequence": p["sequence"], "hops": p["hops"]} for p in paths["paths"][:3]],
                }

            elif tool_name == "analyze_impact":
                report = self._engine.forward_chain(args["entity"], max_depth=args.get("max_depth", 3))
                return {
                    "entity": report.trigger,
                    "total_impacted": report.total_impacted,
                    "impacts": [{"entity": i.entity, "type": i.entity_type, "depth": i.depth}
                               for i in report.impacted[:8]],
                }

            elif tool_name == "check_reachability":
                issues = self._engine.reachability_analysis(args.get("module", "VMM"))
                return {
                    "total_issues": len(issues),
                    "issues": [{"type": i["type"], "detail": i.get("detail", "")} for i in issues],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            return {"error": str(e), "tool": tool_name}

    # ---- Synthesis ----

    def _synthesize(self, question: str, evidence: str, tool_log: list) -> str:
        """Use LLM to synthesize a final answer from tool results."""
        if not self.api_key:
            # No LLM: return structured evidence
            return self._synthesize_no_llm(question, evidence, tool_log)

        from openai import OpenAI

        # Resolve provider
        if self.provider == "zhipu":
            client = OpenAI(api_key=self.api_key, base_url="https://open.bigmodel.cn/api/paas/v4/")
            model = self.model or "glm-4-flash"
        elif self.provider == "ark":
            client = OpenAI(api_key=self.api_key, base_url="https://ark.cn-beijing.volces.com/api/v3")
            model = self.model or "ep-20250616115653-bxlm6"
        else:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            model = self.model

        tool_summary = "\n".join(
            f"- {t['tool']}({json.dumps(t['args'], ensure_ascii=False)}): {t['result_summary'][:200]}"
            for t in tool_log
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"""## 用户问题
{question}

## 工具调用结果
{tool_summary}

## 文档证据
{evidence[:1500]}

请基于以上信息，给出结构化的工程回答。"""},
        ]

        try:
            r = client.chat.completions.create(model=model, messages=messages,
                                                max_tokens=1024, temperature=0.1)
            return r.choices[0].message.content
        except Exception as e:
            return self._synthesize_no_llm(question, evidence, tool_log)

    def _synthesize_no_llm(self, question: str, evidence: str, tool_log: list) -> str:
        """Fallback: structure evidence without LLM."""
        parts = [f"# 问题: {question}\n"]
        parts.append("## 工具调用")
        for t in tool_log:
            parts.append(f"- **{t['tool']}**: {t['result_summary'][:200]}")
        parts.append(f"\n## 证据\n{evidence[:1500]}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    agent = BCMAgent()
    agent.load()

    queries = [
        "从Abandoned如何进入Driving？",
        "KeyLost会影响哪些功能？",
        "为什么不能直接进入Driving？",
        "VMM状态机是否有死锁？",
        "PEPS_UsageMode信号属于哪个模块？",
    ]

    for q in queries[:3]:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        print('='*60)
        result = agent.ask(q)
        print(result["answer"][:600])
        print(f"\nTools used: {[t['tool'] for t in result['tool_calls']]}")
