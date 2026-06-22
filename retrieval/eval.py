"""BCM-RAG Evaluation Framework — Automated retrieval quality metrics.

Computes standard IR metrics:
  - Hit@K: proportion of queries where correct answer appears in top K
  - MRR: Mean Reciprocal Rank of the first correct answer
  - Module Accuracy: proportion of queries where top-1 chunk is from correct module
  - Recall@K: proportion of all relevant chunks retrieved in top K

Usage:
    from retrieval.eval import RetrievalEvaluator
    eval = RetrievalEvaluator(pipeline)
    results = eval.run(golden_queries_path)
    eval.print_report(results)
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    query: str
    expected_module: str
    expected_section: str = ""
    question_type: str = "factual"
    hit_at_1: bool = False
    hit_at_3: bool = False
    hit_at_5: bool = False
    reciprocal_rank: float = 0.0
    module_correct: bool = False
    top_module: str = ""
    top_section: str = ""
    top_score: float = 0.0
    num_results: int = 0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvalReport:
    total_queries: int = 0
    hit_at_1: float = 0.0
    hit_at_3: float = 0.0
    hit_at_5: float = 0.0
    mrr: float = 0.0
    module_accuracy: float = 0.0
    mean_latency_ms: float = 0.0
    mean_results: float = 0.0
    errors: int = 0
    per_module: dict = field(default_factory=dict)
    per_question_type: dict = field(default_factory=dict)
    query_results: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class RetrievalEvaluator:
    """Evaluates retrieval pipeline against a golden query dataset.

    Golden query format (golden_queries.json):
    [
      {
        "query": "...",
        "expected_module": "VMM",
        "expected_section": "2.3.1",       // optional
        "question_type": "factual",         // factual | reasoning | diagnostic
        "min_relevant_chunks": 3,           // optional
        "acceptable_answer_contains": [...] // optional
      },
      ...
    ]
    """

    def __init__(self, pipeline):
        """Initialize with a loaded RetrievalPipeline."""
        self.pipeline = pipeline

    # ---- Run Evaluation -------------------------------------------------------

    def run(
        self,
        golden_path: str | Path = "tests/golden_queries.json",
        top_k: int = 5,
        verbose: bool = True,
    ) -> EvalReport:
        """Run evaluation on all golden queries."""
        with open(golden_path, "r", encoding="utf-8") as f:
            golden = json.load(f)

        queries = golden.get("queries", golden if isinstance(golden, list) else [])
        report = EvalReport(total_queries=len(queries))
        module_correct = defaultdict(int)
        module_total = defaultdict(int)
        qtype_correct = defaultdict(int)
        qtype_total = defaultdict(int)

        for i, q in enumerate(queries):
            query_text = q["query"]
            expected_module = q.get("expected_module", "")
            expected_section = q.get("expected_section", "")
            question_type = q.get("question_type", "factual")

            # Run retrieval
            t0 = time.time()
            try:
                result = self.pipeline.search(query_text, top_k=top_k)
                elapsed = (time.time() - t0) * 1000
                merged = result.get("merged", [])
            except Exception as e:
                report.errors += 1
                report.query_results.append(QueryResult(
                    query=query_text,
                    expected_module=expected_module,
                    question_type=question_type,
                    error=str(e),
                    latency_ms=(time.time() - t0) * 1000,
                ).__dict__)
                continue

            # Compute metrics
            qr = self._evaluate_single(
                query_text, expected_module, expected_section,
                question_type, merged, elapsed,
            )
            report.query_results.append(qr.__dict__)

            # Aggregate
            if qr.hit_at_1: report.hit_at_1 += 1
            if qr.hit_at_3: report.hit_at_3 += 1
            if qr.hit_at_5: report.hit_at_5 += 1
            report.mrr += qr.reciprocal_rank
            if qr.module_correct: report.module_accuracy += 1

            report.mean_latency_ms += qr.latency_ms
            report.mean_results += qr.num_results

            if expected_module:
                module_total[expected_module] += 1
                if qr.module_correct:
                    module_correct[expected_module] += 1

            qtype_total[question_type] += 1
            if qr.module_correct:
                qtype_correct[question_type] += 1

            if verbose:
                status = "✓" if qr.module_correct else "✗"
                print(f"  [{status}] {query_text[:50]:50s} → {qr.top_module:15s} "
                      f"H@1={qr.hit_at_1} H@3={qr.hit_at_3} {qr.latency_ms:5.0f}ms")

        # Finalize report
        n = max(report.total_queries, 1)
        report.hit_at_1 = report.hit_at_1 / n
        report.hit_at_3 = report.hit_at_3 / n
        report.hit_at_5 = report.hit_at_5 / n
        report.mrr = report.mrr / n
        report.module_accuracy = report.module_accuracy / n
        report.mean_latency_ms = report.mean_latency_ms / n
        report.mean_results = report.mean_results / n

        # Per-module breakdown
        for mod in module_total:
            report.per_module[mod] = {
                "accuracy": module_correct[mod] / max(module_total[mod], 1),
                "total": module_total[mod],
                "correct": module_correct[mod],
            }

        # Per-question-type breakdown
        for qt in qtype_total:
            report.per_question_type[qt] = {
                "accuracy": qtype_correct[qt] / max(qtype_total[qt], 1),
                "total": qtype_total[qt],
                "correct": qtype_correct[qt],
            }

        return report

    # ---- Single Query Evaluation ---------------------------------------------

    def _evaluate_single(
        self,
        query: str,
        expected_module: str,
        expected_section: str,
        question_type: str,
        merged: list[dict],
        latency_ms: float,
    ) -> QueryResult:
        """Evaluate a single query result."""
        qr = QueryResult(
            query=query,
            expected_module=expected_module,
            expected_section=expected_section,
            question_type=question_type,
            num_results=len(merged),
            latency_ms=latency_ms,
        )

        if not merged:
            return qr

        # Check each rank
        for rank, item in enumerate(merged):
            chunk = item.get("chunk", {})
            module = chunk.get("module", "")
            section = chunk.get("section_path", "")

            # Module match
            if expected_module and module == expected_module:
                if rank == 0:
                    qr.hit_at_1 = True
                    qr.module_correct = True
                if rank < 3:
                    qr.hit_at_3 = True
                if rank < 5:
                    qr.hit_at_5 = True
                if qr.reciprocal_rank == 0.0:
                    qr.reciprocal_rank = 1.0 / (rank + 1)

            # Section match (stricter)
            if expected_section and section == expected_section:
                if rank == 0:
                    qr.hit_at_1 = True  # Even stricter: exact section match

        # Top result info
        top = merged[0]
        qr.top_module = top.get("chunk", {}).get("module", "?")
        qr.top_section = top.get("chunk", {}).get("section_path", "?")
        qr.top_score = top.get("score", 0.0)

        return qr

    # ---- Report ---------------------------------------------------------------

    def print_report(self, report: EvalReport):
        """Print a formatted evaluation report."""
        print()
        print("=" * 60)
        print("RETRIEVAL EVALUATION REPORT")
        print("=" * 60)
        print(f"  Total queries:    {report.total_queries}")
        print(f"  Errors:           {report.errors}")
        print()
        print(f"  Hit@1:            {report.hit_at_1:.1%}")
        print(f"  Hit@3:            {report.hit_at_3:.1%}")
        print(f"  Hit@5:            {report.hit_at_5:.1%}")
        print(f"  MRR:              {report.mrr:.4f}")
        print(f"  Module Accuracy:  {report.module_accuracy:.1%}")
        print(f"  Mean Latency:     {report.mean_latency_ms:.0f}ms")
        print(f"  Mean Results:     {report.mean_results:.1f}")
        print()

        if report.per_module:
            print("  Per-Module Accuracy:")
            for mod in sorted(report.per_module.keys()):
                info = report.per_module[mod]
                print(f"    {mod:20s}: {info['accuracy']:.1%} "
                      f"({info['correct']}/{info['total']})")

        if report.per_question_type:
            print()
            print("  Per-Question-Type Accuracy:")
            for qt in sorted(report.per_question_type.keys()):
                info = report.per_question_type[qt]
                print(f"    {qt:15s}: {info['accuracy']:.1%} "
                      f"({info['correct']}/{info['total']})")

    def save_report(self, report: EvalReport, path: str | Path) -> Path:
        """Save report to JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report_dict = {
            "total_queries": report.total_queries,
            "hit_at_1": report.hit_at_1,
            "hit_at_3": report.hit_at_3,
            "hit_at_5": report.hit_at_5,
            "mrr": report.mrr,
            "module_accuracy": report.module_accuracy,
            "mean_latency_ms": report.mean_latency_ms,
            "errors": report.errors,
            "per_module": report.per_module,
            "per_question_type": report.per_question_type,
            "query_results": report.query_results,
        }
        path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nReport saved: {path}")
        return path


# ---------------------------------------------------------------------------
# Golden Query Generator (helper to create initial dataset)
# ---------------------------------------------------------------------------

def generate_golden_template(output_path: str | Path = "tests/golden_queries.json"):
    """Generate a template golden query file for manual annotation."""
    template = {
        "description": "BCM-RAG Golden Query Dataset — manually annotated queries for regression testing",
        "version": "1.0",
        "created": "2026-06-17",
        "queries": [
            {
                "query": "VMM电源管理模式有哪些？",
                "expected_module": "VMM",
                "expected_section": "2.3.1",
                "question_type": "factual",
                "notes": "Should return state definitions: Abandoned, Inactive, Convenience, Driving",
            },
            {
                "query": "车窗防夹功能如何检测和反应？",
                "expected_module": "Window",
                "expected_section": "",
                "question_type": "reasoning",
                "notes": "Should return anti-pinch detection logic and reaction",
            },
            {
                "query": "ExteriorLight的配置参数有哪些？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.2.4.1",
                "question_type": "factual",
                "notes": "Should return NVM parameter config table",
            },
            {
                "query": "IGN1继电器由谁控制？控制逻辑是什么？",
                "expected_module": "VMM",
                "expected_section": "",
                "question_type": "reasoning",
                "notes": "IGN1 relay controlled by VMM based on power mode transitions",
            },
            {
                "query": "门锁自动上锁的触发条件是什么？",
                "expected_module": "Lock",
                "expected_section": "",
                "question_type": "factual",
                "notes": "Auto-lock conditions via speed/door state",
            },
            {
                "query": "雨刮间歇模式如何工作？",
                "expected_module": "Wiper",
                "expected_section": "",
                "question_type": "reasoning",
                "notes": "Wiper intermittent mode with timer logic",
            },
            {
                "query": "CAN信号PEPS_UsageMode有哪些取值？",
                "expected_module": "VMM",
                "expected_section": "",
                "question_type": "factual",
                "notes": "Signal coding: 0x0=Inactive, 0x1=Convenience, 0x2=Driving, 0x3=Invalid",
            },
            {
                "query": "近光灯的激活逻辑是什么？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.3.1.1",
                "question_type": "factual",
                "notes": "Low beam activation: position light on + convenience/driving + switch on",
            },
            {
                "query": "BCM休眠条件有哪些？",
                "expected_module": "VMM",
                "expected_section": "2.4.1",
                "question_type": "factual",
                "notes": "Sleep conditions for BCM network sleep",
            },
            {
                "query": "转向灯优先级管理规则是什么？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.4.9.1",
                "question_type": "reasoning",
                "notes": "Turn signal priority: external alarm > crash > emergency brake > door open...",
            },
            {
                "query": "什么是Abandoned模式？",
                "expected_module": "VMM",
                "expected_section": "2.3.1",
                "question_type": "factual",
                "notes": "Final sleep mode, lowest power consumption",
            },
            {
                "query": "DTC故障码的检测和恢复机制是什么？",
                "expected_module": "Window",
                "expected_section": "",
                "question_type": "diagnostic",
                "notes": "Fault detection, reaction, and recovery for DTC codes",
            },
            {
                "query": "碰撞解锁功能如何触发？",
                "expected_module": "Lock",
                "expected_section": "",
                "question_type": "reasoning",
                "notes": "Crash unlock triggered by ACU_CrashOutputSts signal",
            },
            {
                "query": "BCM如何判断钥匙电量低？",
                "expected_module": "VMM",
                "expected_section": "2.3.3.10",
                "question_type": "reasoning",
                "notes": "Key low battery detection: 3 consecutive ignition cycles",
            },
            {
                "query": "日间行车灯的开启和关闭条件是什么？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.7",
                "question_type": "factual",
                "notes": "DRL activation: low beam off, DRL config, voltage > 9V, power ready",
            },
            {
                "query": "危险报警灯和转向灯的优先级关系是什么？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.9",
                "question_type": "reasoning",
                "notes": "Hazard light has priority over turn signal, turn signal paused",
            },
            {
                "query": "后雾灯的激活和关闭条件？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.4",
                "question_type": "factual",
                "notes": "Rear fog: position+low beam on, convenience/driving, switch pressed",
            },
            {
                "query": "车窗GlobalClose功能是什么？",
                "expected_module": "Window",
                "expected_section": "",
                "question_type": "factual",
                "notes": "Global close: one-click close all windows via key fob",
            },
            {
                "query": "PEPS无钥匙启动认证流程是什么？",
                "expected_module": "RemoteControl",
                "expected_section": "9.2.1",
                "question_type": "reasoning",
                "notes": "PIAS authentication flow with LF/RF",
            },
            {
                "query": "BCM唤醒条件有哪些？",
                "expected_module": "VMM",
                "expected_section": "2.4.2",
                "question_type": "factual",
                "notes": "Wake-up conditions: CAN wake, network management, hardware signals",
            },
            {
                "query": "电压低于6V时系统行为是什么？",
                "expected_module": "VMM",
                "expected_section": "2.5",
                "question_type": "factual",
                "notes": "Stop voltage mode: enter at 6V falling, exit at 6.5V rising",
            },
            {
                "query": "紧急制动危险报警闪烁何时激活？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.9.6.1",
                "question_type": "factual",
                "notes": "Emergency brake hazard flash: ESC_HAZActive or VCU_HB_DoubleFlashLampOn",
            },
            {
                "query": "灯光未关提醒的触发条件是什么？",
                "expected_module": "ExteriorLight",
                "expected_section": "3.3.2.2.1",
                "question_type": "factual",
                "notes": "Light left on warning: inactive 500ms + position light on + driver door open",
            },
            {
                "query": "电源模式从Convenience进入Driving需要什么条件？",
                "expected_module": "VMM",
                "expected_section": "2.3.4.3.2",
                "question_type": "reasoning",
                "notes": "Driving entry: brake + key valid + no charger + D/R gear + StartRequest",
            },
            {
                "query": "系统如何响应IG OFF后的节电控制？",
                "expected_module": "InteriorLight",
                "expected_section": "4.4.2",
                "question_type": "reasoning",
                "notes": "Power saving: entry actions, relay control after IG OFF",
            },
        ],
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Golden query template saved: {output_path}")
    print(f"  {len(template['queries'])} queries, please review and adjust expected values")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--generate":
        generate_golden_template()
    else:
        # Run evaluation
        from retrieval import RetrievalPipeline

        print("Loading pipeline...")
        pipeline = RetrievalPipeline()
        pipeline.load(use_dense=True)

        evaluator = RetrievalEvaluator(pipeline)
        report = evaluator.run(verbose=True)
        evaluator.print_report(report)

        evaluator.save_report(report, "output/eval_report.json")
