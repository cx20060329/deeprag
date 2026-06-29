"""BCM-RAG Router Evaluation — 模板选择准确率测试。

评估:
  - Template Accuracy: 模板选对的比例
  - Confusion Matrix: 哪个模板最容易被误选
  - Keyword Override Rate: 关键词覆盖规则触发比例

Usage:
    python tests/eval_router.py                    # 仅关键词回退测试 (无需 LLM)
    python tests/eval_router.py --with-llm         # 含 LLM 选择测试 (需要 API key)
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_router_eval(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["queries"]


def eval_keyword_override(queries: list[dict]) -> dict:
    """测试关键词覆盖规则的准确率 (不依赖 LLM)。"""
    from agent.dag_agent import DagAgent

    agent = DagAgent.__new__(DagAgent)

    results = {
        "total": len(queries),
        "correct": 0,
        "wrong": 0,
        "no_override": 0,
        "confusion": defaultdict(list),
    }

    for q in queries:
        question = q["query"]
        expected = q["expected_template"]

        # 先试覆盖规则
        override = agent._apply_keyword_override(question)
        if override:
            # 用覆盖规则选模板
            plan = agent._select_template_fallback(question)
            actual = plan["template"]

            if actual == expected:
                results["correct"] += 1
            else:
                results["wrong"] += 1
                results["confusion"][expected].append(
                    {"question": question, "actual": actual}
                )
        else:
            # 覆盖规则未触发，用回退评分
            plan = agent._select_template_fallback(question)
            actual = plan["template"]

            if actual == expected:
                results["correct"] += 1
            else:
                results["wrong"] += 1
                results["no_override"] += 1
                results["confusion"][expected].append(
                    {"question": question, "actual": actual}
                )

    results["accuracy"] = (
        results["correct"] / results["total"]
        if results["total"] > 0
        else 0.0
    )
    results["override_rate"] = (
        (results["total"] - results["no_override"]) / results["total"]
        if results["total"] > 0
        else 0.0
    )

    return results


def eval_llm_router(queries: list[dict], agent) -> dict:
    """测试 LLM 模板选择的准确率 (需要 API key)。"""
    results = {
        "total": len(queries),
        "correct": 0,
        "wrong": 0,
        "errors": 0,
        "confusion": defaultdict(list),
    }

    for q in queries:
        question = q["query"]
        expected = q["expected_template"]

        try:
            plan = agent._select_template_with_llm(question)
            actual = plan["template"]

            if actual == expected:
                results["correct"] += 1
            else:
                results["wrong"] += 1
                results["confusion"][expected].append(
                    {"question": question, "actual": actual}
                )
        except Exception as e:
            results["errors"] += 1
            results["confusion"][expected].append(
                {"question": question, "actual": f"ERROR: {e}"}
            )

    results["accuracy"] = (
        results["correct"] / results["total"]
        if results["total"] > 0
        else 0.0
    )

    return results


def print_report(results: dict, mode: str = "keyword"):
    """打印评估报告。"""
    print("=" * 60)
    print(f"Router 评估报告 ({mode})")
    print("=" * 60)
    print(f"总条目: {results['total']}")
    print(f"正确: {results['correct']}")
    print(f"错误: {results['wrong']}")
    if "errors" in results:
        print(f"异常: {results['errors']}")
    print(f"准确率: {results['accuracy']:.1%}")
    if "override_rate" in results:
        print(f"关键词覆盖触发率: {results['override_rate']:.1%}")
    print()

    # Per-template accuracy
    print("## 按模板统计")
    print(f"{'期望模板':<25} {'正确':<6} {'错误':<6} {'准确率':<8}")
    print("-" * 50)
    template_stats = defaultdict(lambda: {"correct": 0, "wrong": 0})
    for expected, mistakes in results["confusion"].items():
        for m in mistakes:
            template_stats[expected]["wrong"] += 1
    # Count corrects
    from agent.dag_agent import DagAgent
    agent = DagAgent.__new__(DagAgent)
    for q in load_router_eval(
        str(Path(__file__).parent / "router_eval.json")
    ):
        expected = q["expected_template"]
        question = q["query"]
        plan = agent._select_template_fallback(question)
        actual = plan["template"]
        if actual == expected:
            template_stats[expected]["correct"] += 1

    for tmpl in sorted(template_stats.keys()):
        stats = template_stats[tmpl]
        total_t = stats["correct"] + stats["wrong"]
        acc = stats["correct"] / max(total_t, 1)
        print(f"{tmpl:<25} {stats['correct']:<6} {stats['wrong']:<6} {acc:.1%}")

    # Confusion matrix
    print()
    print("## 混淆详情")
    for expected, mistakes in results["confusion"].items():
        if mistakes:
            print(f"\n### {expected} → 误判为:")
            actual_counts = defaultdict(list)
            for m in mistakes:
                actual_counts[m["actual"]].append(m["question"])
            for actual, questions in actual_counts.items():
                print(f"  → {actual} ({len(questions)}次):")
                for q in questions[:3]:
                    print(f"    - {q}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BCM-RAG Router 评估")
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="测试 LLM 模板选择 (需要 API key)",
    )
    args = parser.parse_args()

    eval_path = Path(__file__).parent / "router_eval.json"
    queries = load_router_eval(str(eval_path))
    print(f"加载 Router 评测集: {len(queries)} 条\n")

    # Keyword override test
    kw_results = eval_keyword_override(queries)
    print_report(kw_results, "关键词回退")

    # LLM test
    if args.with_llm:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from agent.dag_agent import DagAgent
        agent = DagAgent(provider="deepseek")
        agent.load()
        print("\n")
        llm_results = eval_llm_router(queries, agent)
        print_report(llm_results, "LLM选择")
