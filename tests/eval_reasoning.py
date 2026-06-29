"""BCM-RAG Reasoning Evaluation — 四层评测体系。

Layer 1: Intent Accuracy — 查询意图识别对不对
Layer 2: Template Accuracy — DAG模板选对没
Layer 3: Node Accuracy — 每个节点输出是否正确
  - Path Accuracy: 路径编辑距离
  - Impact Recall: 影响实体召回率
  - Reachability Accuracy: 检测问题匹配率
  - Guard Recall: 状态转移条件关键词召回率
Layer 4: Final Answer Accuracy — 最终答案对不对

Usage:
    python tests/eval_reasoning.py                    # 完整四层评估
    python tests/eval_reasoning.py --layer 3          # 只评估 Node Accuracy
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ======================================================================
# Data Structures
# ======================================================================


@dataclass
class LayerScore:
    layer: int
    name: str
    score: float  # 0.0 - 1.0
    details: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    entry_id: str
    category: str
    question: str
    layer_scores: list[LayerScore] = field(default_factory=list)
    actual_template: str = ""
    actual_path: list[str] = field(default_factory=list)
    actual_entities: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def overall_score(self) -> float:
        if not self.layer_scores:
            return 0.0
        return sum(s.score for s in self.layer_scores) / len(self.layer_scores)


# ======================================================================
# Evaluator
# ======================================================================


class ReasoningEvaluator:
    """四层推理评估器。"""

    def __init__(self, ground_truth_path: str):
        with open(ground_truth_path, encoding="utf-8") as f:
            data = json.load(f)
        self.entries = data["entries"]
        self.meta = data["meta"]

    def evaluate(
        self,
        query_fn: callable,
        entries: list[dict] | None = None,
        layers: tuple = (1, 2, 3, 4),
    ) -> list[EvalResult]:
        """对 ground truth 条目运行评估。

        Args:
            query_fn: function(question) -> DagResult
            entries: 要评估的条目 (None = 全部)
            layers: 要评估的层级

        Returns:
            EvalResult 列表
        """
        if entries is None:
            entries = self.entries

        results: list[EvalResult] = []

        for entry in entries:
            result = EvalResult(
                entry_id=entry["id"],
                category=entry["category"],
                question=entry["question"],
            )

            try:
                dag_result = query_fn(entry["question"])

                if 1 in layers:
                    result.layer_scores.append(
                        self._eval_layer1(entry, dag_result)
                    )
                if 2 in layers:
                    result.layer_scores.append(
                        self._eval_layer2(entry, dag_result)
                    )
                if 3 in layers:
                    result.layer_scores.append(
                        self._eval_layer3(entry, dag_result)
                    )
                if 4 in layers:
                    result.layer_scores.append(
                        self._eval_layer4(entry, dag_result)
                    )

                result.actual_template = dag_result.template

            except Exception as e:
                result.error = str(e)

            results.append(result)

        return results

    # ---- Layer 1: Intent Accuracy ----

    def _eval_layer1(self, entry: dict, dag_result) -> LayerScore:
        """检查意图分析是否正确识别了查询类型。"""
        details = []
        score = 1.0

        intent_data = {}
        for nid, no in dag_result.node_outputs.items():
            if no.node_type == "intent_analysis" and no.status == "success":
                intent_data = no.output or {}
                break

        category = entry["category"]

        # 检查 question_type 是否正确
        if category in ("path", "state_transition"):
            expected_qtype = "reasoning"
            actual_qtype = intent_data.get("question_type", "")
            if expected_qtype not in str(actual_qtype).lower():
                score -= 0.3
                details.append(
                    f"question_type mismatch: expected reasoning-like, got {actual_qtype}"
                )
            # 应检测到状态
            if not intent_data.get("states"):
                score -= 0.2
                details.append("intent did not extract states")

        elif category == "impact":
            if not intent_data.get("signals") and not intent_data.get("faults"):
                score -= 0.3
                details.append("intent did not extract signals or faults")

        elif category == "diagnostic":
            actual_qtype = intent_data.get("question_type", "")
            if "diagnostic" not in str(actual_qtype).lower():
                score -= 0.2
                details.append(
                    f"question_type should be diagnostic, got {actual_qtype}"
                )

        return LayerScore(
            layer=1,
            name="Intent Accuracy",
            score=max(0.0, score),
            details=details,
        )

    # ---- Layer 2: Template Accuracy ----

    def _eval_layer2(self, entry: dict, dag_result) -> LayerScore:
        """检查 DAG 模板是否选对。"""
        expected = entry.get("expected_template", "")
        actual = dag_result.template
        details = []

        if expected and actual == expected:
            score = 1.0
            details.append(f"template match: {actual}")
        elif expected:
            score = 0.0
            details.append(f"template mismatch: expected {expected}, got {actual}")
        else:
            score = 0.5
            details.append("no expected template specified")

        return LayerScore(
            layer=2,
            name="Template Accuracy",
            score=score,
            details=details,
        )

    # ---- Layer 3: Node Accuracy ----

    def _eval_layer3(self, entry: dict, dag_result) -> LayerScore:
        """按类别评估节点输出的正确性。"""
        category = entry["category"]

        if category == "path":
            return self._eval_path_accuracy(entry, dag_result)
        elif category == "impact":
            return self._eval_impact_recall(entry, dag_result)
        elif category == "reachability":
            return self._eval_reachability_accuracy(entry, dag_result)
        elif category == "state_transition":
            return self._eval_guard_recall(entry, dag_result)
        elif category == "diagnostic":
            return self._eval_diagnostic_accuracy(entry, dag_result)
        else:
            return LayerScore(layer=3, name="Node Accuracy", score=0.5)

    def _eval_path_accuracy(self, entry: dict, dag_result) -> LayerScore:
        """路径编辑距离评估。"""
        expected_path = entry.get("expected_path", [])
        expected_hops = entry.get("expected_hops", 0)
        key_conditions = entry.get("key_conditions", [])
        forbidden = entry.get("forbidden_paths", [])
        details = []

        # Extract actual path from path_finder or state_machine node
        actual_path = []
        actual_conditions = []

        for nid, no in dag_result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            data = no.output

            if no.node_type == "path_finder":
                paths = data.get("paths", [])
                if paths:
                    actual_path = paths[0].get("sequence", [])
                    actual_conditions = paths[0].get("conditions", [])
            elif no.node_type == "state_machine":
                for t in data.get("transitions", []):
                    guard = t.get("guard", "")
                    if guard:
                        actual_conditions.append(guard)

        if not actual_path:
            return LayerScore(
                layer=3, name="Path Accuracy", score=0.0,
                details=["no path found in output"]
            )

        # 1. Path sequence similarity (edit distance)
        if expected_path:
            matches = sum(
                1 for a, e in zip(actual_path, expected_path) if a == e
            )
            path_score = matches / max(len(expected_path), 1)
            details.append(
                f"path match: {matches}/{len(expected_path)} "
                f"(actual={'→'.join(actual_path)}, expected={'→'.join(expected_path)})"
            )
        else:
            path_score = 0.5

        # 2. Hops accuracy
        actual_hops = len(actual_path) - 1
        if expected_hops > 0:
            hops_score = 1.0 if actual_hops == expected_hops else max(
                0.0, 1.0 - abs(actual_hops - expected_hops) / max(expected_hops, 1)
            )
            details.append(f"hops: actual={actual_hops}, expected={expected_hops}")
        else:
            hops_score = 0.5

        # 3. Condition recall
        if key_conditions:
            all_text = " ".join(actual_conditions).lower()
            found = sum(
                1 for c in key_conditions if c.lower() in all_text
            )
            cond_score = found / len(key_conditions)
            details.append(
                f"conditions: {found}/{len(key_conditions)} found"
            )
        else:
            cond_score = 0.5

        # 4. Forbidden path check
        forbidden_score = 1.0
        for fp in forbidden:
            if len(fp) <= len(actual_path):
                if all(
                    a == f for a, f in zip(actual_path, fp)
                ):
                    forbidden_score = 0.0
                    details.append(
                        f"forbidden path matched: {'→'.join(fp)}"
                    )

        score = (
            path_score * 0.35
            + hops_score * 0.15
            + cond_score * 0.35
            + forbidden_score * 0.15
        )

        return LayerScore(
            layer=3,
            name="Path Accuracy",
            score=min(1.0, score),
            details=details,
        )

    def _eval_impact_recall(self, entry: dict, dag_result) -> LayerScore:
        """影响实体召回率。"""
        expected_entities = set(
            e.lower() for e in entry.get("expected_entities", [])
        )
        forbidden = set(
            e.lower() for e in entry.get("forbidden_entities", [])
        )
        min_depth = entry.get("expected_depth_min", 0)
        details = []

        actual_entities = set()
        actual_max_depth = 0

        for nid, no in dag_result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            data = no.output

            if no.node_type == "impact_analysis":
                for imp in data.get("impacted", []):
                    entity = imp.get("entity", "")
                    if entity:
                        actual_entities.add(entity.lower())
                    depth = imp.get("depth", 0)
                    actual_max_depth = max(actual_max_depth, depth)

        if not actual_entities:
            return LayerScore(
                layer=3, name="Impact Recall", score=0.0,
                details=["no impacted entities found"]
            )

        # Recall
        if expected_entities:
            recall = len(expected_entities & actual_entities) / len(expected_entities)
            details.append(
                f"recall: {len(expected_entities & actual_entities)}/{len(expected_entities)}"
            )
            details.append(f"actual entities: {list(actual_entities)[:10]}")
        else:
            recall = 0.5

        # Precision penalty for forbidden entities
        if forbidden:
            false_positives = len(forbidden & actual_entities)
            precision_penalty = false_positives / max(len(actual_entities), 1)
        else:
            precision_penalty = 0.0

        # Depth check
        if min_depth > 0:
            depth_score = min(1.0, actual_max_depth / min_depth)
            details.append(f"depth: actual={actual_max_depth}, min={min_depth}")
        else:
            depth_score = 0.5

        score = (
            recall * 0.5
            + (1.0 - precision_penalty) * 0.3
            + depth_score * 0.2
        )

        return LayerScore(
            layer=3,
            name="Impact Recall",
            score=max(0.0, min(1.0, score)),
            details=details,
        )

    def _eval_reachability_accuracy(self, entry: dict, dag_result) -> LayerScore:
        """可达性问题检测匹配率。"""
        expected_issues = set(entry.get("expected_issues", []))
        forbidden = set(entry.get("forbidden_issues", []))
        details = []

        actual_issues = set()

        for nid, no in dag_result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            data = no.output
            if no.node_type == "reachability":
                for iss in data.get("issues", []):
                    actual_issues.add(iss.get("type", ""))

        details.append(f"actual issues: {list(actual_issues)}")
        details.append(f"expected issues: {list(expected_issues)}")

        if not expected_issues and not actual_issues:
            # Expected no issues, found none → perfect
            return LayerScore(
                layer=3, name="Reachability Accuracy", score=1.0,
                details=["correctly found no issues"]
            )

        if not expected_issues:
            score = 1.0 - len(actual_issues) * 0.2
        else:
            recall = len(expected_issues & actual_issues) / len(expected_issues)
            precision = (
                len(expected_issues & actual_issues) / max(len(actual_issues), 1)
                if actual_issues else 0.5
            )
            # Penalty for forbidden issues
            fp = len(forbidden & actual_issues)
            fp_penalty = fp * 0.2
            score = max(0.0, (recall * 0.5 + precision * 0.5) - fp_penalty)

        return LayerScore(
            layer=3,
            name="Reachability Accuracy",
            score=max(0.0, min(1.0, score)),
            details=details,
        )

    def _eval_guard_recall(self, entry: dict, dag_result) -> LayerScore:
        """状态转移条件关键词召回率。"""
        expected_guards = set(
            g.lower() for g in entry.get("expected_guards", [])
        )
        expected_sources = set(entry.get("expected_source_states", []))
        expected_sections = set(entry.get("expected_sections", []))
        details = []

        actual_guards = []
        actual_sources = set()
        actual_sections = set()

        for nid, no in dag_result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            data = no.output

            if no.node_type == "state_machine":
                for t in data.get("transitions", []):
                    guard = t.get("guard", "")
                    if guard:
                        actual_guards.append(guard.lower())
                    actual_sources.add(t.get("source", ""))
                    sec = t.get("section", "")
                    if sec:
                        actual_sections.add(sec)

        all_guard_text = " ".join(actual_guards)

        # Guard recall
        if expected_guards:
            found = sum(
                1 for g in expected_guards if g.lower() in all_guard_text
            )
            guard_score = found / len(expected_guards)
            details.append(f"guards: {found}/{len(expected_guards)} found")
        else:
            guard_score = 0.5

        # Source state accuracy
        if expected_sources:
            source_match = len(expected_sources & actual_sources) / len(expected_sources)
            details.append(f"source states: {list(actual_sources)}")
        else:
            source_match = 0.5

        # Section accuracy
        if expected_sections:
            sec_match = len(expected_sections & actual_sections) / len(expected_sections)
            details.append(f"sections: {list(actual_sections)}")
        else:
            sec_match = 0.5

        score = guard_score * 0.5 + source_match * 0.25 + sec_match * 0.25

        return LayerScore(
            layer=3,
            name="Guard Recall",
            score=min(1.0, score),
            details=details,
        )

    def _eval_diagnostic_accuracy(self, entry: dict, dag_result) -> LayerScore:
        """故障诊断根因分析准确性。"""
        expected_causes = set(
            c.lower() for c in entry.get("expected_root_causes", [])
        )
        details = []

        # Collect all text from nodes
        all_text_parts = []
        for nid, no in dag_result.node_outputs.items():
            if no.status != "success" or not no.output:
                continue
            data = no.output
            if no.node_type == "rule_lookup":
                for r in data.get("matched_rules", []):
                    all_text_parts.append(str(r.get("condition", "")))
                    all_text_parts.append(str(r.get("action", "")))
            elif no.node_type == "state_machine":
                for t in data.get("transitions", []):
                    all_text_parts.append(str(t.get("guard", "")))

        all_text = " ".join(all_text_parts).lower()

        if expected_causes:
            found = sum(1 for c in expected_causes if c.lower() in all_text)
            score = found / len(expected_causes)
            details.append(f"root causes: {found}/{len(expected_causes)} found")
        else:
            score = 0.5

        return LayerScore(
            layer=3,
            name="Diagnostic Accuracy",
            score=min(1.0, score),
            details=details,
        )

    # ---- Layer 4: Final Answer Accuracy ----

    def _eval_layer4(self, entry: dict, dag_result) -> LayerScore:
        """评估最终答案质量。"""
        answer = dag_result.answer or ""
        details = []

        # Check answer is non-empty
        if not answer or len(answer) < 20:
            return LayerScore(
                layer=4, name="Answer Quality", score=0.0,
                details=["empty or too short answer"]
            )

        score = 0.5  # Base score for non-empty answer

        # Check structured sections
        if "结论" in answer or "## 结论" in answer:
            score += 0.1
            details.append("has conclusion section")
        if "推理" in answer or "## 推理链" in answer:
            score += 0.1
            details.append("has reasoning section")
        if "证据" in answer or "## 证据" in answer:
            score += 0.1
            details.append("has evidence section")
        if "CONFIDENCE" in answer.upper():
            score += 0.1
            details.append("has confidence score")

        # Check that answer references node outputs
        category = entry["category"]
        if category == "path":
            if "跳" in answer or "hops" in answer.lower() or "→" in answer:
                score += 0.1
                details.append("references path details")
        elif category in ("state_transition", "impact"):
            if "§" in answer or "章节" in answer or "section" in answer.lower():
                score += 0.1
                details.append("references sections")

        return LayerScore(
            layer=4,
            name="Answer Quality",
            score=min(1.0, score),
            details=details,
        )

    # ---- Report Generation ----

    def generate_report(self, results: list[EvalResult]) -> str:
        """生成评估报告。"""
        lines = [
            "=" * 60,
            "BCM-RAG 推理评估报告",
            "=" * 60,
            f"总条目: {len(results)}",
            f"错误: {sum(1 for r in results if r.error)}",
            "",
        ]

        # Category breakdown
        by_category = defaultdict(list)
        for r in results:
            by_category[r.category].append(r)

        lines.append("## 按类别统计")
        lines.append("")
        lines.append(
            f"{'类别':<20} {'数量':<6} {'Layer1':<8} {'Layer2':<8} "
            f"{'Layer3':<8} {'Layer4':<8} {'综合':<8}"
        )
        lines.append("-" * 66)

        for cat, cat_results in sorted(by_category.items()):
            l1 = sum(
                next(
                    (s.score for s in r.layer_scores if s.layer == 1), 0
                )
                for r in cat_results
            ) / max(len(cat_results), 1)
            l2 = sum(
                next(
                    (s.score for s in r.layer_scores if s.layer == 2), 0
                )
                for r in cat_results
            ) / max(len(cat_results), 1)
            l3 = sum(
                next(
                    (s.score for s in r.layer_scores if s.layer == 3), 0
                )
                for r in cat_results
            ) / max(len(cat_results), 1)
            l4 = sum(
                next(
                    (s.score for s in r.layer_scores if s.layer == 4), 0
                )
                for r in cat_results
            ) / max(len(cat_results), 1)
            overall = (l1 + l2 + l3 + l4) / 4

            lines.append(
                f"{cat:<20} {len(cat_results):<6} "
                f"{l1:.2f}     {l2:.2f}     {l3:.2f}     {l4:.2f}     {overall:.2f}"
            )

        # Overall scores
        all_l1 = sum(
            next((s.score for s in r.layer_scores if s.layer == 1), 0)
            for r in results
        ) / max(len(results), 1)
        all_l2 = sum(
            next((s.score for s in r.layer_scores if s.layer == 2), 0)
            for r in results
        ) / max(len(results), 1)
        all_l3 = sum(
            next((s.score for s in r.layer_scores if s.layer == 3), 0)
            for r in results
        ) / max(len(results), 1)
        all_l4 = sum(
            next((s.score for s in r.layer_scores if s.layer == 4), 0)
            for r in results
        ) / max(len(results), 1)

        lines.append("-" * 66)
        lines.append(
            f"{'总体':<20} {len(results):<6} "
            f"{all_l1:.2f}     {all_l2:.2f}     {all_l3:.2f}     {all_l4:.2f}     "
            f"{(all_l1+all_l2+all_l3+all_l4)/4:.2f}"
        )

        # Per-entry details
        lines.append("")
        lines.append("## 逐条详情")
        lines.append("")

        for r in results:
            lines.append(f"### {r.entry_id}: {r.question[:60]}")
            if r.error:
                lines.append(f"  错误: {r.error}")
            else:
                lines.append(f"  实际模板: {r.actual_template}")
                for s in r.layer_scores:
                    lines.append(f"  Layer{s.layer} {s.name}: {s.score:.2f}")
                    for d in s.details:
                        lines.append(f"    - {d}")
            lines.append("")

        return "\n".join(lines)


# ======================================================================
# CLI
# ======================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BCM-RAG 推理评估")
    parser.add_argument(
        "--layer",
        type=int,
        default=0,
        help="只评估指定层级 (1-4, 0=全部)",
    )
    parser.add_argument(
        "--category",
        type=str,
        default="",
        help="只评估指定类别 (path/impact/reachability/state_transition/diagnostic)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只加载 Ground Truth 不执行查询",
    )
    args = parser.parse_args()

    gt_path = Path(__file__).parent / "reasoning_ground_truth.json"
    evaluator = ReasoningEvaluator(str(gt_path))

    print(f"加载 Ground Truth: {len(evaluator.entries)} 条")
    print(f"类别: {evaluator.meta['categories']}")
    print()

    if args.dry_run:
        print("Dry run — 不执行查询。")
        for entry in evaluator.entries:
            print(f"  [{entry['id']}] {entry['category']}: {entry['question'][:60]}")
        sys.exit(0)

    print("需要 DagAgent 实例来执行查询。")
    print("用法: 从代码中调用 ReasoningEvaluator.evaluate(query_fn)")
    print(f"  evaluator = ReasoningEvaluator('{gt_path}')")
    print("  results = evaluator.evaluate(agent.query)")
    print("  print(evaluator.generate_report(results))")
