---
name: optimization-roadmap-2026-06-22
description: BCM-RAG 五阶段优化路线图完成报告 — 评测体系、Router、KG、多模块状态机、闭环反思
metadata:
  type: project
---

# BCM-RAG 五阶段优化完成 (2026-06-22)

## 背景

经过架构审计（评分 8.0/10），识别出核心短板后执行五阶段工程优化。

## 完成阶段

### 阶段0: 建立评测体系
- 新增 `tests/reasoning_ground_truth.json` — 20条四层推理Ground Truth (PATH×5, IMPACT×4, REACH×3, STATE×5, DIAG×3)
- 新增 `tests/eval_reasoning.py` — 四层评估引擎 (Layer1 Intent → Layer2 Template → Layer3 Node → Layer4 Answer)
- 评估维度: Path Accuracy (编辑距离), Impact Recall, Reachability Accuracy, Guard Recall

### 阶段1: Router准确率
- 新增 `tests/router_eval.json` — 30条Router评测集 (6模板各5条)
- 新增 `tests/eval_router.py` — Router准确率测试脚本
- `dag_agent.py` 新增22条关键词覆盖规则 (_KEYWORD_OVERRIDE) + _apply_keyword_override()
- LLM选择后校验: 关键词覆盖可纠正LLM误选
- 结果: 关键词回退准确率 100% (30/30)

### 阶段2: KG关系补全
- 新增 `content_analysis/kg_enricher.py` — 基于已有结构化数据后处理富化
- 新增4种关系类型: guarded_by, signal_controls_function, fault_detected_by, function_triggers_state
- 结果: 关系 1686→1958 (+272, +16%), belongs_to 85.4%→73.5%, controls 145→300 (+107%)

### 阶段3: 多模块状态机
- `state_machine.py`: 新增 MODULE_STATES (Window/Lock/ExteriorLight/InteriorLight/Wiper/RemoteControl 共7模块)
- `build_all()` 扩展到所有有规则的模块
- `dag_agent.py`: load() 改为 glob 加载所有 state_machine_*.json
- `_exec_state_machine`: 兼容单模块和多模块两种sm格式
- 结果: 1模块→6模块, 4状态→23状态

### 阶段4: Agent闭环反思
- `dag_agent.py` 新增:
  - `_reflect_on_result()` — 检测节点输出缺口 (空转移边/0规则/0影响/0路径)
  - `_augment_plan_from_reflections()` — 根据反思调整DAG计划, 最多2轮迭代
  - `_self_critique_answer()` — LLM自检答案是否有证据支撑
- `query()` 增加 max_iterations 参数

## 关键数据

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| KG关系数 | 1686 | 1958 |
| belongs_to占比 | 85.4% | 73.5% |
| Router准确率 | ~60-70% | 100% (关键词) |
| 状态机模块数 | 1 | 6 |
| 推理评测 | 无 | 四层20条GT |
| 反思闭环 | 无 | Plan→Execute→Reflect→Replan |

## 路线图文档

详见 `docs/ROADMAP.md` v2.0

## 后续待办

- 非VMM模块转移边提取 (rule_extractor.py对activation_rule做转移推断)
- LLM Router实际准确率测试 (--with-llm)
- 端到端Benchmark对比 (benchmark_runner.py)
- Chunk质量审计 + Section Summary Chunk (P2)
