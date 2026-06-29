# BCM-RAG 工程化优化路线图

> 版本: 2.0 | 日期: 2026-06-22
>
> 基于架构审计 + KG数据实测 + Benchmark能力评估 + 推理Ground Truth评审

---

## 当前基线数据

| 指标 | 数值 |
|------|------|
| 测试 | 134 passed, 6 errors (pre-existing path issues) |
| KG 实体 | 1717 (signal 666, function 336, module 287, state 179, fault 91, can_message 67, parameter 52, pin 39) |
| KG 关系 | 1686 (belongs_to 1439 / 85.4%, controls 145 / 8.6%, references 37, outputs 20, reports 16, triggered_by 9, configures 8, requires 7, depends_on 5) |
| Benchmark 题库 | 100 题 (7 类别: A-Factual 22, B-MultiHop 18, C-State 15, D-Conditional 15, E-Path 10, F-Conflict 10, G-Reachability 10) |
| Golden Queries | 33 条人工标注 |
| 状态机 | 1 模块 (VMM), 4 状态, 7 转移 |
| 规则库 | 122 条 |

---

## 核心问题诊断

当前系统最缺的不是"检索到内容"，而是**"证明推理过程真的正确"**。

现有 benchmark 只能测"答案对不对"，不能测"推理对不对"。

```
问题: 从Abandoned如何进入Driving
正确路径: Abandoned → Inactive → Convenience → Driving (3跳)
系统输出: Abandoned → Driving (路径缺失2个中间状态)
答案可能看起来也对，但推理错了。
```

这就是 **Answer Correct, Reasoning Wrong** 问题。

---

## 阶段 0: 建立评测体系（当前立即执行）

### 目标

建立四层评测体系，从"答案对不对"升级到"推理对不对"。

### 0.1 运行现有 Benchmark 生成基线

```bash
python tests/benchmark_runner.py
```

产出 `output/benchmark_report_baseline.txt`

### 0.2 构建 Reasoning Ground Truth 数据集

这是最关键的新增工作。在 `tests/` 下新建 `reasoning_ground_truth.json`。

#### 路径推理 Ground Truth

```json
{
  "question": "从Abandoned如何进入Driving",
  "expected_template": "path_finding",
  "expected_path": ["Abandoned", "Inactive", "Convenience", "Driving"],
  "expected_hops": 3,
  "expected_conditions": ["DoorOpen=TRUE", "KeyValid=TRUE", "BrakePressed=TRUE"]
}
```

#### 影响分析 Ground Truth

```json
{
  "question": "KeyLost会影响哪些功能",
  "expected_entities": ["PEPS_UsageMode", "AutoLock", "GlobalClose"],
  "expected_depth": 2,
  "forbidden_entities": []
}
```

#### 可达性 Ground Truth

```json
{
  "question": "VMM状态机是否存在不可达状态",
  "expected_issues": ["unreachable"],
  "expected_states": []
}
```

#### 状态转移 Ground Truth

```json
{
  "question": "进入Driving需要什么条件",
  "expected_source": ["Inactive", "Convenience"],
  "expected_guards": ["BrakePressed", "KeyValid", "GearInDrive"],
  "expected_sections": ["2.3.4.3.2"]
}
```

目标: 每种推理类型 5-10 条，总计 30-50 条。

### 0.3 新增推理评估维度

在现有 `BenchmarkScorer` 基础上新增:

| 维度 | 评估方法 | 适用类别 |
|------|---------|---------|
| Path Accuracy | 输出路径与 ground truth 路径的编辑距离 | C, E |
| Impact Recall | 影响的实体集合的召回率 | B, E |
| Reachability Accuracy | 检测到的问题类型匹配率 | G |
| Guard Recall | 状态转移条件关键词召回率 | C, D |
| Node Utilization | 每个节点类型在100次查询中的执行比例 | 全部 |

### 0.4 新增 Node Utilization 追踪

在 `DagAgent.query()` 中增加节点执行统计:

```python
# 追加到 audit_trail
stats = {
    "intent": 100%,
    "sm": 12%,
    "rules": 8%,
    "path": 3%,
    "impact": 5%,
    "reach": 0%,
    "conflicts": 0%,
    "chunks": 100%
}
```

如果 `rules` < 20% 或 `reach` = 0%，说明系统退化为普通 RAG。

### 0.5 产出

| 产出 | 文件 |
|------|------|
| 基线报告 | `output/benchmark_report_baseline.txt` |
| Ground Truth | `tests/reasoning_ground_truth.json` |
| 评估脚本 | `tests/eval_reasoning.py` |
| 节点利用率报告 | `output/node_utilization.json` |

---

## 阶段 1: Router 准确率提升

### 为什么先做这个

```
问题分类错误 → 后面全部白做
```

如果"为什么无法进入Driving"被 Router 判为 `factual_lookup`，那么 `rules`/`impact`/`conflicts` 全部不会执行，直接退化为 Hybrid RAG。

### 1.1 创建 Router 评测集

`tests/router_eval.json`, 30 条，覆盖 6 种模板 + 边界情况:

```json
[
  {
    "query": "从Abandoned如何进入Driving？",
    "expected_template": "path_finding"
  },
  {
    "query": "为什么车辆无法从Inactive进入Driving？",
    "expected_template": "diagnostic"
  },
  {
    "query": "IGN1信号的定义是什么？",
    "expected_template": "factual_lookup"
  },
  {
    "query": "KeyLost会影响哪些功能？",
    "expected_template": "impact_analysis"
  }
]
```

### 1.2 Router 准确率测试

```python
# 对每条评测查询，运行 _select_template_with_llm()
# 统计:
#   - Template 准确率 (选对模板的比例)
#   - Node 启用准确率 (每个节点是否正确启用/禁用)
#   - 混淆矩阵 (哪个模板最容易被误选)
```

### 1.3 优化 LLM 提示词

根据混淆矩阵迭代 `TEMPLATE_DESCRIPTIONS_FOR_LLM`:

- 增加反例: "包含状态名时不选 factual_lookup"
- 增加边界案例: "同时有'定义'和状态名 → 选 state_transition 而非 factual_lookup"
- 增加触发词: "为什么不能/无法/不工作 → diagnostic"

### 1.4 加入回退规则

当 LLM 不可用或输出格式错误时，关键词回退覆盖:

```python
_keyword_override = {
    ("Driving", "进入"): "state_transition",
    ("为什么", "无法"): "diagnostic",
    ("影响",): "impact_analysis",
    ("从", "如何", "到"): "path_finding",
    ("死锁", "不可达"): "reachability_check",
}
```

### 1.5 目标

- Template 准确率 > 85%
- 混淆矩阵中 factual_lookup 的误选率 < 10%

---

## 阶段 2: KG 关系补全

### 为什么这个阶段做

```
belongs_to 占 85.4% → 图本质上是"实体→模块"的归属树
controls 只有 145 条 → 信号→功能的控制链几乎不存在
depends_on 只有 5 条 → 依赖推理几乎不可用
```

Router 准确了，但推理节点没有足够的 KG 边来支撑。

### 2.1 新增关系提取规则

在 `content_analysis/kg_exporter.py` 中新增:

| 关系类型 | 来源 | 提取方法 |
|---------|------|---------|
| Signal → controls → Function | 功能描述段落中的 "XX信号控制/触发/激活YY功能" | 正则 + 依存句法 |
| Function → triggers → State | 功能描述中的 "XX功能使BCM进入YY状态" | 规则匹配 |
| Signal → consumed_by → Module | 信号表中的 "输入模块" 列 | 表格解析 |
| Fault → detected_by → Rule | 故障诊断段落中的检测条件 | 规则引擎数据反哺 |
| State → guarded_by → Signal | 状态转移条件中的信号引用 | 状态机 JSON 显式导出 |

### 2.2 运行提取

```bash
python run_pipeline_v3.py
```

### 2.3 验证

- 关系类型从 9 种增加到 12+ 种
- `controls` 从 145 → 300+
- `depends_on` 从 5 → 50+
- `triggered_by` 从 9 → 30+

### 2.4 重新跑 Benchmark

对比阶段 0 基线，预期 B/C/D/E 类别提升 10-15%。

---

## 阶段 3: 多模块状态机

### 为什么这个阶段做

当前只有 VMM 一个模块的状态机。BCM 有 8 个模块。

### 3.1 状态机提取泛化

修改 `content_analysis/state_machine.py`:

- Window: 上升/下降/防夹/停止
- Lock: 解锁/闭锁/自动上锁/碰撞解锁
- ExteriorLight: 关闭/位置灯/近光/远光/自动
- Wiper: 停止/间歇/低速/高速

### 3.2 运行提取

```bash
python run_pipeline_v3.py
```

预期产出 5-8 个状态机 JSON 文件。

### 3.3 加载到推理引擎

```python
for sm_path in Path("output/content_analysis").glob("state_machine_*.json"):
    engine.load_state_machine(sm_path)
```

### 3.4 目标

- 状态机模块数: 1 → 5+
- `path_finding` 模板可用于非 VMM 模块

---

## 阶段 4: Agent 闭环反思

### 为什么这个阶段做

Level 4→5 的关键一步。需要前面所有基础设施稳定后。

### 4.1 将 `_reflect()` 闭环回 Plan

```
DAG Plan → Execute → Reflect → 发现缺规则信息
    → DAG Plan v2 (增加 rule_lookup 节点) → Execute → Answer
```

### 4.2 添加 Answer Self-Critique

- 检查每个结论是否有证据支撑
- 检查是否有未引用的节点输出
- 如果 Critique 发现严重问题，重新合成

### 4.3 目标

- Reflection 闭环率 > 50%
- Answer 自检覆盖率 100%

---

## 阶段总览

| 阶段 | 名称 | 优先级 | 核心问题 | 预计工作量 |
|------|------|--------|---------|-----------|
| 0 | 建立评测体系 | **P0** | 不知道推理对不对 | 1天 |
| 1 | Router 准确率 | **P0** | 模板选错后面全错 | 1天 |
| 2 | KG 关系补全 | **P0** | 推理边不够 | 2天 |
| 3 | 多模块状态机 | P1 | 只有VMM能推理 | 2天 |
| 4 | Agent 闭环反思 | P2 | Reflect未闭环 | 2天 |

---

## 四层评测体系

```
Layer 1: Intent Accuracy
  "状态查询 / 影响分析 / 路径查找 / 故障诊断" 识别对不对
  ↓
Layer 2: Template Accuracy
  intent → template 选对没
  ↓
Layer 3: Node Accuracy
  每个节点的输出是否正确 (path 路径对不对, impact 影响链对不对, reach 检测对不对)
  ↓
Layer 4: Final Answer Accuracy
  最终答案对不对
```

好处: 最终答案错了，你能知道是 Router 错、Reasoner 错、还是 LLM 合成错。

---

## 不做的

以下方向**收益低，暂不投入**:

1. 换更大的 Embedding 模型 — BGE-M3 1024维对 BCM 文档已足够
2. 引入更多 LLM Provider — DeepSeek/GLM/豆包 三种已覆盖
3. Neo4j/Qdrant 生产化部署 — 内存 NetworkX/Numpy 对单文档场景足够
4. DAG 从 Template 升级为 Tool DAG — 6 种模板已覆盖 90% 查询
5. Chunk 策略大改 — 逻辑分块已优于固定 token，在 KG 和 Router 稳定后再优化
6. Parent-Child Chunk / Hierarchical Retrieval — 当前检索 recall 不低，优先级后移
