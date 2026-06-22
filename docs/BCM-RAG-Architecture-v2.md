# BCM-RAG v2 Architecture — Rule Engine + State Machine + Reasoning

> 基于实际数据分析的架构升级方案。数据来源：1802 entities, 1645 edges, 162 chunks。

---

## 第一部分：当前 KG 缺陷分析

### 1.1 数据质量审计

```
实际数据审计结果：

Entity 类型分布：
  signal        671  (47%) ← 大量是模块名/TOC条目，不是真正的信号
  function      377  (27%) ← 大量是章节标题，不是功能逻辑
  module        287  (20%) ← 包含 _TOC 等噪音
  state         177  (13%) ← 有价值的实体，有 description
  fault          91  (6%) ← 长句存储，非结构化 DTC 码
  can_message    67  (5%) ← 无信号映射关系
  parameter      52  (4%) ← 电压字符串，未结构化

Relationship 权重：
  weight=0:  1645/1645 (100%) ← 所有权重都是 0！
  belongs_to: 1444 (85%)     ← 纯结构关系
  其他关系:    252 (15%)     ← controls/outputs/requires/depends_on

关键缺失：
  transition_to: 0           ← 状态机核心关系完全缺失
  triggered_by: 存在但无数据 ← 触发条件未提取
  rule 实体: 不存在           ← 规则未建模
```

### 1.2 致命缺陷

| 缺陷 | 根因 | 后果 |
|------|------|------|
| **状态机缺失** | 迁移表内容在 table cells 中，entity_extractor 只提取了 heading，未解析 table body | "Driving如何进入？" → 无法回答 |
| **规则未建模** | 条件/动作逻辑存在于 prose text + table cells，没有提取为结构化 Rule | "KeyLost会影响什么？" → 关键词搜索 |
| **关系权重全0** | `kg_exporter.py` 未写入实际权重值 | graph traversal 无区分度 |
| **信号实体污染** | TOC 条目被错误分类为 signal，真正的 CAN 信号在 table 中未独立提取 | 信号查询命中噪音 |
| **Fault 是长文本** | 故障描述整段存储为 entity name | 无法按 DTC 码/故障类型查询 |
| **无跨模块推理** | 关系只有直接连接，无传递闭包，无依赖链 | "这个信号失效会影响哪些功能？" → 无法回答 |

### 1.3 核心差距

当前 KG = **文档结构的镜像** + **表面实体**。它不是行为模型。

```
当前:  Document → Entities → belongs_to edges → 搜索实体名 → 返回 chunk
目标:  Document → Rules → State Machine → Signal Graph → Forward/Backward Chain → 精确答案
```

---

## 第二部分：Neo4j Schema 设计

### 2.1 Node Types

```cypher
// ===== 核心领域实体 =====

// Module — 功能模块
(:Module {
  name: "VMM",
  full_name: "车辆模式管理",
  section_path: "2",
  section_title: "2 车辆模式管理（VMM）",
  description: "...",
  parent_module: "BCM"  // 模块层级
})

// State — 状态节点（状态机的节点）
(:State {
  name: "Driving",
  module: "VMM",
  description: "动力系统启动并准备提供扭矩",
  power_mode: "ON",           // OFF/Crank/ON/ACC
  is_initial: false,
  is_terminal: false,
  section_path: "2.3.1",
  row_index: 5,               // 在原表中的行号
  parent_state: null           // 嵌套状态：父状态名
})

// Signal — 信号（区分 CAN/LIN/硬线/内部）
(:Signal {
  name: "PEPS_UsageMode",
  signal_type: "CAN",          // CAN | LIN | Hardwire | Internal
  can_id: "0x3E8",            // CAN ID (CAN信号)
  can_signal_name: "PEPS_UsageMode",
  start_bit: 0,
  length: 8,
  coding: "0x0=Inactive, 0x1=Active, ...",
  direction: "input",          // input | output
  module: "VMM",
  source_module: "PEPS",       // 信号来源模块
  target_modules: ["VMM", "Window", "Lock"],  // 消费者
  section_path: "2.3.3"
})

// Function — 功能（可执行的功能逻辑）
(:Function {
  name: "GlobalClose",
  module: "Window",
  description: "一键关闭所有车窗",
  activation: "长按锁车键 > 2s",  // 激活方式
  deactivation: "松开按键",
  priority: 1,                      // 功能优先级
  preemptible: true                 // 是否可被抢占
})

// Rule — 规则（IF 条件 THEN 动作）
(:Rule {
  rule_id: "VMM_Driving_Entry_001",
  rule_type: "entry_condition",     // entry_condition | exit_condition | transition_guard | fault_reaction | config_rule | activation_rule
  module: "VMM",
  condition_expr: "PEPS_UsageMode = Active AND VCU_StartActive = ON AND VehicleSpeed = 0",
  action: "ENTER Driving",
  action_type: "state_transition",
  priority: 1,
  is_blocking: true,                // false = 非阻塞/可选条件
  timeout_ms: 0,                    // 超时限制 (0=无)
  exception: null,                  // 例外条件
  references: ["Section 2.3.4.3.2"],
  source_text: "当PEPS_UsageMode有效且VCU_StartActive信号为ON时，系统进入Driving状态",
  confidence: 0.95                  // 提取置信度
})

// Fault — 故障码
(:Fault {
  dtc: "B1000_11",
  fault_name: "IGN1_Relay_Fault",
  fault_type: "electrical",         // electrical | timeout | signal_invalid | mechanical | communication
  module: "VMM",
  detection: "IGN1继电器输入端开路或短路到地",
  detection_time_ms: 100,
  reaction: "发送PEPS_IGN1RelayValidity=0x0，切断IGN1输出",
  recovery: "输出与反馈一致且无故障时恢复",
  debounce_ms: 300,
  severity: "high"
})

// Event — 事件（触发信号/条件变化的瞬时事件）
(:Event {
  name: "KeyIn",
  event_type: "hardware",           // hardware | can_signal | lin_signal | timer | user_action | system
  trigger_signal: "PEPS_KeyStatus",
  trigger_value: "0x1=Valid",
  description: "检测到有效钥匙在车内"
})

// Action — 动作（系统执行的操作）
(:Action {
  name: "UnlockAllDoors",
  action_type: "output_control",    // output_control | signal_send | state_change | timer_start | alarm
  target: "DoorLockMotor",
  target_type: "hardware_pin",
  value: "Unlock",
  module: "Lock"
})

// Parameter — 配置参数
(:Parameter {
  name: "CfgTCMEOLOption",
  module: "Lock",
  param_type: "config",
  coding_nibble: 4,                 // 编码字节位置
  length: 2,
  default: 0x0,
  description: "TCM EOL 选项配置"
})
```

### 2.2 Relationship Types

```cypher
// ===== 结构关系 =====
(:Module)-[:CONTAINS]->(:Module)          // 模块层级
(:Module)-[:CONTAINS_STATE]->(:State)     // 模块拥有状态
(:Module)-[:CONTAINS_FUNCTION]->(:Function)
(:Module)-[:OWNS_SIGNAL]->(:Signal)
(:Module)-[:DEFINES_RULE]->(:Rule)
(:Module)-[:HAS_PARAMETER]->(:Parameter)
(:Module)-[:HAS_FAULT]->(:Fault)

// ===== 状态机关系 =====
(:State)-[:TRANSITIONS_TO {
  trigger: "PEPS_UsageMode=Active",
  guard: "VCU_StartActive=ON AND VehicleSpeed=0",
  action: "ActivatePowerTrain",
  priority: 1,
  source_section: "2.3.4.3.2",
  conditions: "[\"cond_1\", \"cond_2\"]"  // 指向 Rule 节点 ID
}]->(:State)

// ===== 信号流 =====
(:Signal)-[:PRODUCED_BY]->(:Module)       // 信号源
(:Signal)-[:CONSUMED_BY {
  usage: "state_trigger",                 // state_trigger | function_input | fault_detect | config
  condition: "..."
}]->(:Module)

// ===== 规则关系 =====
(:Rule)-[:REQUIRES_SIGNAL]->(:Signal)     // 规则依赖信号
(:Rule)-[:REQUIRES_STATE]->(:State)       // 规则前置状态
(:Rule)-[:TRIGGERS_ACTION]->(:Action)     // 规则触发动作
(:Rule)-[:TRIGGERS_EVENT]->(:Event)
(:Rule)-[:PRECEDES {weight: 1}]->(:Rule)  // 规则链条：Rule_A → Rule_B
(:Rule)-[:CONFLICTS_WITH]->(:Rule)        // 规则冲突
(:Rule)-[:OVERRIDES]->(:Rule)             // 规则优先级覆盖

// ===== 故障关系 =====
(:Fault)-[:DETECTED_BY]->(:Rule)          // 故障检测规则
(:Fault)-[:AFFECTS_SIGNAL]->(:Signal)     // 故障影响的信号
(:Fault)-[:TRIGGERS_STATE]->(:State)      // 故障触发的状态（如 LimpHome）
(:Fault)-[:REPORTS_TO]->(:Module)         // 故障上报目标

// ===== 推理关系（materialized paths）=====
(:State)-[:REACHABLE_IN {
  steps: 3,
  path: "Inactive→Convenience→Driving",
  conditions: ["Rule_VMM_Conv_Entry_001", "Rule_VMM_Driving_Entry_001"]
}]->(:State)

// ===== 跨模块影响 =====
(:Signal)-[:CAUSAL_CHAIN {
  depth: 3,
  chain: "PEPS_KeyLost→VMM_KeyValid→VMM_Abandoned",
  affected_functions: ["GlobalClose", "AutoLock", "RemoteStart"]
}]->(:Function)
```

### 2.3 约束与索引

```cypher
// 唯一性约束
CREATE CONSTRAINT unique_rule_id IF NOT EXISTS FOR (r:Rule) REQUIRE r.rule_id IS UNIQUE;
CREATE CONSTRAINT unique_signal_name IF NOT EXISTS FOR (s:Signal) REQUIRE (s.name, s.module) IS NODE KEY;
CREATE CONSTRAINT unique_fault_dtc IF NOT EXISTS FOR (f:Fault) REQUIRE f.dtc IS UNIQUE;
CREATE CONSTRAINT unique_state_id IF NOT EXISTS FOR (s:State) REQUIRE (s.name, s.module) IS NODE KEY;

// 全文索引（用于 entity resolution）
CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (n:Module|State|Signal|Function) ON EACH [n.name, n.description];

// 信号类型索引
CREATE INDEX signal_type IF NOT EXISTS FOR (s:Signal) ON (s.signal_type);
CREATE INDEX rule_module IF NOT EXISTS FOR (r:Rule) ON (r.module, r.rule_type);
```

---

## 第三部分：Rule Extraction 架构

### 3.1 规则分类与提取策略

```
规则类型                    提取方式          置信度    占比估计
─────────────────────────────────────────────────────────
电压范围进入/退出规则       Regex             0.95      15%
信号编码表规则              Regex + Schema    0.90      20%
故障检测/反应规则           Regex+LiteLLM     0.80      25%
状态迁移条件规则            Regex+LiteLLM     0.75      20%
功能激活/去激活规则         LLM               0.70      15%
优先级/互斥规则             LLM               0.65       5%
```

### 3.2 Regex 规则提取 — 适用场景

**场景1：电压范围表 → 电压规则**

输入文本：
```
9V-16V（Enter: ↑9V, ↓16V）| Normal Voltage 正常电压 | All the functions are working properly.
```

提取规则：
```json
{
  "rule_id": "VMM_Voltage_Normal_Entry",
  "rule_type": "voltage_entry",
  "module": "VMM",
  "condition_expr": "Voltage >= 9.0 AND Voltage <= 16.0",
  "entry_threshold": 9.0,
  "exit_threshold": 16.0,
  "state_on_entry": "NormalVoltage",
  "action": "All functions enabled",
  "priority": 0,
  "source_text": "9V-16V（Enter: ↑9V, ↓16V）",
  "confidence": 0.95
}
```

**场景2：信号编码表 → 信号规则**

输入（table）：
```
PEPS_UsageMode | 0x0=Inactive, 0x1=Active, 0x2=Invalid, 0x3=Fault
```

提取规则：每个编码值 → 一条 Rule：

```json
[
  {
    "rule_id": "SIG_PEPS_UsageMode_Active",
    "rule_type": "signal_value_definition",
    "module": "PEPS",
    "signal": "PEPS_UsageMode",
    "condition_expr": "PEPS_UsageMode = 0x1",
    "semantic_value": "Active",
    "action_type": "state_trigger",
    "confidence": 0.95
  },
  ...
]
```

**场景3：输出控制表 → 控制规则**

```
BCM_LightLeftwarning | IG OFF AND 小灯打开 AND 锁车 AND 3分钟无变化 → 0x1:Active
```

```json
{
  "rule_id": "ExtLight_WarningLeft_001",
  "rule_type": "activation_rule",
  "module": "ExteriorLight",
  "condition_expr": "IG = OFF AND ParkLight = ON AND (RemoteLock OR KeyLock) AND Timer[180s].elapsed",
  "action": "BCM_LightLeftwarning = 0x1:Active",
  "action_signal": "BCM_LightLeftwarning",
  "confidence": 0.85
}
```

### 3.3 LLM 规则提取 — 适用场景

**场景：复杂段落包含多条件逻辑**

```
在非driving状态，钥匙有效状态应保持60s。如果60s内未检测到有效钥匙，
则进入钥匙搜索流程。如果车速大于等于15公里/时，则不寻找钥匙。
```

LLM 提取 Prompt：

```
你是一个汽车BCM规范分析器。

从以下文本中提取结构化规则。每条规则必须包含:
- condition: 触发条件（用 AND/OR/NOT 连接）
- action: 执行的动作
- exception: 例外条件（如果有）
- module: 所属模块

文本：
{chunk_text}

输出严格JSON，不添加任何解释：
{ "rules": [...] }
```

预期输出：
```json
{
  "rules": [
    {
      "condition": "State != Driving AND KeyValid = true",
      "action": "Hold KeyValid for 60s",
      "exception": null
    },
    {
      "condition": "State != Driving AND KeyValid = false AND Timer[60s].elapsed",
      "action": "Start KeySearch",
      "exception": "VehicleSpeed >= 15"
    },
    {
      "condition": "VehicleSpeed >= 15",
      "action": "Stop KeySearch AND Set PEPS_Warning_No_key_found = 0x1",
      "exception": null
    }
  ]
}
```

### 3.4 反幻觉机制

```
1. Source Anchoring
   每条 Rule 必须包含 source_text + section_path + confidence
   → LLM 提取时要求输出 source_quote（原文字段）

2. Cross-Validation
   同一章节的 Regex 结果 和 LLM 结果 做交集验证
   → Regex 提取的 signal 名必须在 LLM 提取的规则中出现

3. Rule Consistency Check
   规则入库前检查：
   - condition 中引用的 signal/state 是否在 KG 中存在？
   - action 中引用的 signal 是否在 KG 中存在？
   - 同一 rule_id 的 condition 是否自相矛盾？

4. Human-in-the-loop (可配置)
   置信度 < 0.7 的规则标记为 candidate，不入推理链
   置信度 >= 0.85 的自动入库

5. 去重
   规则唯一键 = (module, condition_expr 归一化, action 归一化)
   归一化步骤：
   - 信号名标准化（PEPS_UsageMode = Active ↔ PEPS_UsageMode = 0x1）
   - 条件重排序（A AND B ↔ B AND A）
   - 去除冗余括号
```

### 3.5 冲突检测规则

```python
def detect_conflicts(rule1: Rule, rule2: Rule) -> Conflict | None:
    """
    检测两条规则是否冲突：
    1. 条件重叠（satisfiable_overlap）
    2. 动作矛盾（incompatible_actions）
    """
    # 条件重叠检测
    cond1 = normalize_condition(rule1.condition_expr)
    cond2 = normalize_condition(rule2.condition_expr)

    overlap = check_condition_overlap(cond1, cond2)  # 用 z3/SymPy 判定

    if not overlap:
        return None

    # 动作矛盾检测
    if are_incompatible(rule1.action, rule2.action):
        return Conflict(
            rule_a=rule1.rule_id,
            rule_b=rule2.rule_id,
            overlap_condition=overlap,
            conflict_type="action_conflict"
        )

    # 优先级相同 + 动作相同 + 条件重叠 = 冗余
    if rule1.priority == rule2.priority and rule1.action == rule2.action:
        return Conflict(
            rule_a=rule1.rule_id,
            rule_b=rule2.rule_id,
            conflict_type="redundant",
            severity="low"
        )
```

### 3.6 Rule JSON Schema

```json
{
  "$schema": "https://bcm-rag/rule-schema.json",
  "rule_id": "string (unique, pattern: {Module}_{Type}_{Seq})",
  "rule_type": "enum[voltage_entry, voltage_exit, entry_condition, exit_condition, transition_guard, activation_rule, deactivation_rule, fault_detection, fault_reaction, signal_value_definition, priority_rule, config_rule]",
  "module": "string (required)",
  "condition_expr": "string (normalized boolean expression with AND/OR/NOT)",
  "condition_signals": ["signal_name"],
  "condition_states": ["state_name"],
  "action": "string (required)",
  "action_type": "enum[state_transition, signal_output, function_call, timer_start, alarm, inhibit, enable]",
  "action_signals": ["signal_name"],
  "action_target_state": "string (for state_transition)",
  "priority": "integer (0=lowest, higher=override)",
  "is_blocking": "boolean",
  "timeout_ms": "integer",
  "exception": "string | null",
  "conflicts_with": ["rule_id"],
  "overridden_by": ["rule_id"],
  "source_text": "string (original document text)",
  "source_section": "string",
  "source_page": "integer",
  "confidence": "number (0.0-1.0)",
  "extraction_method": "enum[regex, llm, hybrid, manual]",
  "extraction_timestamp": "ISO datetime"
}
```

---

## 第四部分：State Machine Builder

### 4.1 状态上下文推断

BCM 文档的章节结构天然编码了状态层级：

```
2.3.1 模式定义              ← 定义所有状态名
2.3.4 状态迁移
  2.3.4.1 Abandoned模式     ← 状态的迁移上下文
    2.3.4.1.1 迁移到Inactive ← 单个迁移
  2.3.4.2 Inactive模式
    2.3.4.2.1 迁移到Abandoned
    2.3.4.2.2 迁移到Convenience
  2.3.4.3 Convenience模式
    2.3.4.3.1 迁移到Inactive
    2.3.4.3.2 迁移到Driving
```

**推断算法：**

```python
def infer_state_context(section_tree: dict, state_entities: list) -> dict:
    """
    从章节结构推断状态的完整上下文。
    
    Rule 1: 父章节标题含状态名 → 所有子章节属于该状态
    Rule 2: "迁移到X" → 目标状态 = X, 源状态 = 父章节状态
    Rule 3: 迁移章节在 X 模式下 → 该迁移的源状态 = X
    """
    state_contexts = {}
    
    for node in section_tree.walk():
        parent_title = node.parent.title if node.parent else ""
        
        # Rule 1: 匹配章节标题中的状态名
        for state in state_entities:
            if state.name in node.title:
                # 该节点（及子节点）属于此状态
                state_contexts[node.id] = {
                    "state": state.name,
                    "scope": "owner",  # 此章节描述该状态
                    "children_inherit": True
                }
        
        # Rule 2: "迁移到X"
        match = re.match(r"迁移到(\w+)", node.title)
        if match:
            target = match.group(1)
            source = resolve_parent_state(node, state_contexts)
            state_contexts[node.id] = {
                "source_state": source,
                "target_state": target,
                "scope": "transition",
                "transition_type": infer_transition_type(node)
            }
    
    return state_contexts
```

### 4.2 迁移表 → 状态图

发现文档中 transition_to=0 的根本原因：迁移条件在 **paragraph text** 中，不在 table cells 中。

**提取策略（三层fallback）：**

```python
def extract_transitions(section_node, chunk_text: str) -> list[Transition]:
    """
    Layer 1: 解析迁移表中的行（如果存在）
    Layer 2: 从 paragraph text 中提取 "IF-THEN" 模式
    Layer 3: LLM fallback
    """
    transitions = []
    
    # Layer 1: Table parsing
    if section_node.has_table():
        for row in section_node.table.rows:
            if len(row) >= 3:  # Source | Trigger | Target
                t = Transition(
                    source=row[0],
                    trigger=row[1],
                    target=row[2],
                    extraction="table"
                )
                transitions.append(t)
    
    # Layer 2: Text pattern matching
    patterns = [
        # "当X时，进入Y状态"
        (r"当(.+?)时.*进入(\w+)状态", "trigger_enter"),
        # "在X条件下，退出Y状态"
        (r"在(.+?)条件下.*退出(\w+)状态", "condition_exit"),
        # "如果X，则Y"
        (r"如果(.+?)，则(.+?)(?:状态|模式)", "if_then"),
    ]
    
    for pattern, ptype in patterns:
        for match in re.finditer(pattern, chunk_text):
            t = parse_transition_match(match, ptype, source_state)
            if t:
                transitions.append(t)
    
    # Layer 3: LLM extraction (if Layers 1+2 empty)
    if not transitions and chunk_text.strip():
        transitions = llm_extract_transitions(chunk_text)
    
    return transitions
```

### 4.3 状态机数据结构

```json
{
  "state_machine_id": "VMM_StateMachine",
  "module": "VMM",
  "states": [
    {
      "name": "Driving",
      "type": "composite",         // atomic | composite | initial | terminal | history
      "entry_actions": ["ActivatePowerTrain", "EnableAllFunctions"],
      "exit_actions": ["DeactivatePowerTrain"],
      "do_activities": [],
      "substates": [],              // 嵌套子状态
      "invariants": ["VehicleSpeed >= 0", "PEPS_UsageMode = Active"],
      "section_ref": "2.3.1"
    }
  ],
  "transitions": [
    {
      "id": "trans_Conv_to_Driving",
      "source": "Convenience",
      "target": "Driving",
      "trigger": "VCU_StartActive = ON",
      "guard": "PEPS_UsageMode = Active AND VehicleSpeed = 0 AND BrakePressed = true",
      "effect": "ActivatePowerTrain(); SetDisplayMode(ON)",
      "priority": 1,
      "is_automatic": false,
      "time_constraint": {"type": "within", "ms": 5000},
      "source_rules": ["VMM_Driving_Entry_001", "VMM_BrakeCheck_001"],
      "source_section": "2.3.4.3.2",
      "confidence": 0.85
    }
  ],
  "composite_states": {
    "Driving": {
      "substates": ["NormalDriving", "SportDriving", "EcoDriving"],
      "default_substate": "NormalDriving",
      "history_policy": "shallow"   // shallow | deep
    }
  }
}
```

### 4.4 合并重复迁移

```python
def merge_transitions(transitions: list[Transition]) -> list[Transition]:
    """
    合并策略：
    1. 相同 (source, target) 对 → 合并 guard 条件（AND）
    2. 相同 (source, target) + 不同 trigger → 合并为 OR
    3. 相同 (source, trigger) + 不同 target → 检查 guard 是否互斥
       - 互斥 → 保留为两个独立迁移
       - 非互斥 → 标记为非确定性迁移（nondeterministic warning）
    """
    groups = defaultdict(list)
    for t in transitions:
        key = (t.source, t.target, t.trigger)
        groups[key].append(t)
    
    merged = []
    for (src, tgt, trig), group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
        else:
            # Merge guards
            guards = [t.guard for t in group if t.guard]
            merged_guard = " AND ".join(guards)
            merged.append(Transition(
                source=src, target=tgt, trigger=trig,
                guard=merged_guard,
                effects=[t.effect for t in group],
                source_rules=[t.source_rule for t in group]
            ))
    
    return merged
```

---

## 第五部分：Reasoning Engine 设计

### 5.1 Forward Chaining — "KeyLost会影响什么？"

```
输入:  (Signal | Fault | Event, max_depth=5)
输出:  ImpactReport
```

**Neo4j Cypher：**

```cypher
// 前向影响分析：从起始信号展开所有下游影响
MATCH path = (s:Signal {name: "PEPS_KeyStatus", module: "PEPS"})
             -[:CONSUMED_BY|TRANSITIONS_TO*1..5]->
             (end)
WHERE s.signal_value = "0x0=KeyLost" OR s.signal_value CONTAINS "KeyLost"
WITH
  end,
  relationships(path) AS rels,
  nodes(path) AS nodes,
  length(path) AS depth
UNWIND nodes AS n
WITH DISTINCT
  n,
  depth,
  [r IN rels WHERE type(r) = 'TRANSITIONS_TO' | r] AS transitions,
  [r IN rels WHERE type(r) = 'CONSUMED_BY' | r] AS consumptions
RETURN
  collect(DISTINCT {
    type: labels(n)[0],
    name: n.name,
    module: n.module,
    depth: depth,
    via_transition: size(transitions) > 0,
    via_signal: size(consumptions) > 0
  }) AS impacted_entities
ORDER BY depth
```

**推理流程：**

```
ImpactReport 输出结构：

KeyLost (Signal, PEPS)
│
├──[depth=1] VMM.KeyValid = Invalid (Signal consumed by VMM)
│   │
│   ├──[depth=2] VMM.State → Abandoned (transition triggered)
│   │   │
│   │   ├──[depth=3] Window.GlobalClose = Disabled (function depends on VMM.State)
│   │   ├──[depth=3] Lock.AutoLock = Disabled
│   │   └──[depth=3] Wiper.ServiceMode = Disabled
│   │
│   └──[depth=2] PEPS_Warning_NoKeyFound = Active (signal output)
│
├──[depth=1] Lock.RemoteLock = Disabled
└──[depth=1] BCM.NetworkWakeup = Forbidden
```

### 5.2 Backward Chaining — "进入 Driving 需要什么条件？"

```
输入:  (State: "Driving", module: "VMM")
输出:  ConditionTree
```

**Neo4j Cypher：**

```cypher
// 反向链：找到所有到达 Driving 的迁移及其条件
MATCH (src:State)-[t:TRANSITIONS_TO]->(drv:State {name: "Driving", module: "VMM"})
OPTIONAL MATCH (t)-[:HAS_CONDITION]->(r:Rule)
OPTIONAL MATCH (r)-[:REQUIRES_SIGNAL]->(sig:Signal)
OPTIONAL MATCH (r)-[:REQUIRES_STATE]->(preState:State)
RETURN
  src.name AS source_state,
  t.trigger AS trigger,
  t.guard AS guard,
  collect(DISTINCT sig.name) AS required_signals,
  collect(DISTINCT preState.name) AS required_prestates,
  collect(DISTINCT r.rule_id) AS rules
```

**推理流程（递归回溯）：**

```python
def backward_chain(target_state: str, visited: set = None) -> ConditionTree:
    """
    递归构建到达 target_state 的完整条件树。
    
    伪代码：
    """
    if visited is None:
        visited = set()
    
    if target_state in visited:
        return ConditionTree.cycle_detected(target_state)
    
    visited.add(target_state)
    
    # Step 1: 找到所有直接迁移到 target_state 的边
    incoming_transitions = neo4j.query("""
        MATCH (src:State)-[t:TRANSITIONS_TO]->(:State {name: $target})
        RETURN src, t
    """, target=target_state)
    
    if not incoming_transitions:
        if is_initial_state(target_state):
            return ConditionTree.leaf("Initial State: No preconditions")
        else:
            return ConditionTree.leaf("ERROR: Unreachable state!")
    
    # Step 2: 每条入边展开其条件
    branches = []
    for trans in incoming_transitions:
        # 直接条件
        conditions = [
            trans.trigger,
            *trans.guard.split(" AND "),
        ]
        
        # 递归：源状态本身需要什么条件？
        sub_tree = backward_chain(trans.source_state, visited.copy())
        
        branches.append(AndNode(
            conditions=conditions,
            source_state=trans.source_state,
            sub_conditions=sub_tree
        ))
    
    # Step 3: 多条入边 = 任一满足即可
    return OrNode(branches=branches)
```

**输出格式：**

```json
{
  "target": "Driving",
  "condition_tree": {
    "type": "OR",
    "branches": [
      {
        "type": "AND",
        "source_state": "Convenience",
        "conditions": [
          {"signal": "PEPS_UsageMode", "value": "Active", "source": "PEPS"},
          {"signal": "VCU_StartActive", "value": "ON", "source": "VCU"},
          {"signal": "VehicleSpeed", "value": "0", "source": "ESC"},
          {"signal": "BrakePedal", "value": "Pressed", "source": "BCM"}
        ],
        "sub_conditions": {
          "target": "Convenience",
          "condition_tree": { "type": "OR", "branches": [...] }
        },
        "references": ["2.3.4.3.2", "2.3.3.11"]
      }
    ]
  }
}
```

### 5.3 Path Query — "Inactive → Driving 的完整路径"

```cypher
// 找到两个状态间的所有路径
MATCH path = (start:State {name: "Inactive", module: "VMM"})
             -[:TRANSITIONS_TO*1..6]->
             (end:State {name: "Driving", module: "VMM"})
RETURN
  [node IN nodes(path) | node.name] AS state_sequence,
  [rel IN relationships(path) | {
    trigger: rel.trigger,
    guard: rel.guard,
    action: rel.action
  }] AS transition_details,
  length(path) AS hop_count
ORDER BY hop_count
LIMIT 5
```

**输出格式：**

```json
{
  "paths": [
    {
      "sequence": ["Inactive", "Convenience", "Driving"],
      "hops": 2,
      "transitions": [
        {
          "from": "Inactive",
          "to": "Convenience",
          "trigger": "PEPS_KeyValid = true",
          "guard": "BrakePressed = true OR StartButton = Pressed",
          "action": "Enable Convenience Functions"
        },
        {
          "from": "Convenience",
          "to": "Driving",
          "trigger": "VCU_StartActive = ON",
          "guard": "PEPS_UsageMode = Active AND VehicleSpeed = 0",
          "action": "Activate PowerTrain"
        }
      ],
      "total_conditions": [
        "PEPS_KeyValid = true",
        "BrakePressed = true OR StartButton = Pressed",
        "VCU_StartActive = ON",
        "PEPS_UsageMode = Active",
        "VehicleSpeed = 0"
      ]
    }
  ],
  "shortest_path_hops": 2,
  "alternative_paths": 0
}
```

### 5.4 Conflict Detection

```python
def detect_all_conflicts(module: str = None) -> list[Conflict]:
    """
    全量冲突检测。
    
    检测类型：
    1. Action Conflict: 相同条件下两条规则要求不同动作
    2. Priority Conflict: 高优先级规则被低优先级规则覆盖
    3. Timing Conflict: 两条规则有时间窗口重叠但动作互斥
    4. Missing Priority: 条件重叠但未分配优先级
    5. Circular Dependency: A优先级高于B，B高于C，C高于A
    """
    rules = neo4j.query("""
        MATCH (r:Rule) WHERE $module IS NULL OR r.module = $module
        RETURN r
    """, module=module)
    
    conflicts = []
    
    for r1, r2 in itertools.combinations(rules, 2):
        # 跳过不同模块的规则（除非有跨模块依赖）
        if r1.module != r2.module and not has_cross_module_dep(r1, r2):
            continue
        
        conflict = detect_pairwise_conflict(r1, r2)
        if conflict:
            conflicts.append(conflict)
            # 写入 Neo4j
            neo4j.query("""
                MATCH (a:Rule {rule_id: $id1}), (b:Rule {rule_id: $id2})
                MERGE (a)-[:CONFLICTS_WITH {type: $ctype, condition_overlap: $overlap}]->(b)
            """, id1=r1.rule_id, id2=r2.rule_id, 
                 ctype=conflict.type, overlap=conflict.overlap_condition)
    
    return conflicts
```

**冲突检测 Cypher（已有 rule 图谱后）：**

```cypher
// 查找条件重叠的规则对
MATCH (r1:Rule)-[:REQUIRES_SIGNAL]->(s:Signal)<-[:REQUIRES_SIGNAL]-(r2:Rule)
WHERE r1.rule_id < r2.rule_id
  AND r1.action_signal IS NOT NULL
  AND r2.action_signal IS NOT NULL
  AND r1.action_signal = r2.action_signal
  AND r1.action_target <> r2.action_target
RETURN r1.rule_id, r2.rule_id, s.name AS shared_signal,
       "Action Conflict: Same signal, different targets" AS conflict_type
```

### 5.5 Reachability Analysis

```cypher
// 分析哪些状态不可达（无入边）
MATCH (s:State {module: "VMM"})
WHERE NOT (()-[:TRANSITIONS_TO]->(s))
  AND NOT s.is_initial = true
RETURN s.name AS unreachable_state,
       "No incoming transitions and not an initial state" AS reason

// 死锁检测：状态无出边（非终态）
MATCH (s:State {module: "VMM"})
WHERE NOT (s)-[:TRANSITIONS_TO]->()
  AND NOT s.is_terminal = true
RETURN s.name AS deadlock_state,
       "No outgoing transitions and not a terminal state" AS reason

// 活锁检测：存在循环但无进度
MATCH cycle = (s:State)-[:TRANSITIONS_TO*2..5]->(s)
WHERE all(r IN relationships(cycle) WHERE r.guard IS NULL OR r.guard = "")
RETURN [n IN nodes(cycle) | n.name] AS livelock_cycle,
       "Cycle without guard conditions: infinite loop risk" AS reason
```

---

## 第六部分：外部工具评估

### 6.1 GraphRAG (Microsoft)

```
评估：❌ 不适合 BCM-RAG

理由：
- GraphRAG 是「社区检测 + 摘要」模型，面向非结构化文本的全局理解
- BCM 文档是「结构化表格 + 规则 + 状态机」，不需要 Leiden 社区检测
- 成本：每个 community 生成一份 LLM 摘要，BCM 文档 7 个模块 → 7 个 community → 边际价值低
- GraphRAG 的 graph 是 LLM 自动生成的（entity → relationship → community），质量远低于我们手工设计的 Schema

唯一可用场景：用 GraphRAG community summary 作为文档的「全局索引」辅助检索
→ 成本/收益比不划算，不建议
```

### 6.2 LightRAG

```
评估：⚠️ 部分可用，但不替代自定义 KG

LightRAG 的优点：
- 轻量级，双索引（local + global）
- 不依赖 LLM 反复调用（比 GraphRAG 便宜）

在 BCM 中的定位：
- 可以作为「快速原型」或「第三层检索索引」
- 绝对不替代我们设计的 Rule + State Machine KG
- 可以作为向量检索的补充层

建议：Phase 3 之后，如果用 LightRAG 的 local/global search 改善 keyword 检索体验，可以试试
→ 但不要现在引入，避免架构复杂度爆炸
```

### 6.3 Memgraph

```
评估：🟡 有潜力，但不是必须

Memgraph 优势：
- Cypher 兼容（同 Neo4j）
- MAGE 算法库内置
- 内存级性能

与 Neo4j 对比：
- 如果数据量 < 10万节点，Neo4j Community 完全够用
- BCM KG 预估规模：1个文档 → ~3000 节点 + ~5000 边
- 10个文档的 GraphRAG → ~30000 节点 → 仍然适合 Neo4j

建议：当前用 Neo4j Community，不需要 Memgraph
→ 当节点量 > 50万 且查询延迟 > 100ms 时再考虑
```

### 6.4 Neo4j GDS (Graph Data Science)

```
评估：✅ 建议引入（有选择地使用）

推荐使用的算法：

1. PageRank → 识别「最重要」的信号/状态节点
   场景：当 500+ 信号时，找出哪些信号变更影响最大
   
2. Betweenness Centrality → 识别「瓶颈」节点
   场景：找出单点故障会影响最多模块的信号
   
3. Shortest Path (Dijkstra) → 替代手写 BFS
   场景：Path Query 中的最短迁移路径

4. Weakly Connected Components → 检测孤立模块
   场景：验证信号网络完整性，查找未连接的信号

5. Label Propagation → 自动聚类
   场景：发现隐含的功能分组（表未明确标注但信号强相关）

不推荐：
- Node2Vec / GraphSage → 需要大量同质图，BCM 图是异质的
- Triangle Count → BCM 图不形成三角结构
```

### 6.5 Drools (规则引擎)

```
评估：✅ 强烈建议引入（Phase 3）

BDM 规则引擎的完美场景：

应用模式：

1. 规则验证
   将提取的 Rule 转化为 Drools .drl 规则
   → 给定输入信号组合，Drools 推理引擎推断系统状态
   → 与文档预期状态对比 → 发现文档 bug

2. 冲突检测
   Drools 的 agenda 机制天然支持冲突检测
   → 两条规则在同一 activation group 中同时触发 = 冲突

3. Simulation
   输入：信号序列（时间线）
   Drools 推理 → 状态迁移序列
   → 验证状态机是否可达、是否有死锁

关键集成点：
   Neo4j (规则存储) → Drools (规则执行) → Neo4j (推理结果写回)
   
不建议用 Drools 的时机：
   - 规则 < 50 条时，Python 手写推理器足够
   - 规则 > 200 条时，Drools 的 RETE 算法显著优于 Python
```

### 6.6 Temporal Workflow (时序推理)

```
评估：🟡 Phase 4 考虑

BCM 中有大量时序逻辑：
- "3分钟内无变化则发送报警"
- "60s后进入钥匙搜索"
- "闪烁周期1000ms，占空比50%，持续5min"

当前困境：
- Neo4j 不支持时序推理
- Cypher 无法表达 "within 3 minutes" 或 "duration 5min"

Temporal 的定位：
- 不是替代 Neo4j，是补充
- 使用时序模型验证规则的正确性
- 例如：用 TLA+ 或 UPPAAL 建模关键状态机，做形式化验证

建议：
- Phase 1-3：时序约束存储为 Rule 的 timeout_ms 属性
- Phase 4：考虑引入 Temporal Logic 做形式化验证（UPPAAL 更合适）
→ 先不做，避免过度设计
```

---

## 第七部分：实施路线图

### Phase 2a: Rule Extraction MVP (3-4天)

```
输入: 162 chunks + 86 tables
输出: ~200-400 条结构化 Rule

Day 1: Regex Rule Extractor
  - 电压范围规则（已知 pattern）
  - 信号编码规则（已知 table structure）
  - 输出控制规则（已知 table structure）
  
Day 2: LLM Rule Extractor
  - 集成已有 Zhipu/Ark API
  - 输出 validated JSON
  - 防幻觉机制

Day 3: Rule Validation & Dedup
  - 条件归一化
  - 冲突检测
  - Neo4j 写入

Day 4: 质量评估
  - 人工抽检 20 条规则
  - 修正提取模式
```

### Phase 2b: State Machine Builder (2-3天)

```
Day 1: State Context Inference
Day 2: Transition Extraction (3-layer fallback)
Day 3: Merge + Neo4j写入 + 可视化
```

### Phase 3: Reasoning Engine (3-4天)

```
Day 1: Forward Chaining + Backward Chaining
Day 2: Path Query + Reachability Analysis
Day 3: Conflict Detection + Drools 集成
Day 4: API 暴露 + 前端查询界面
```

---

## 附录A：与当前 Pipeline 的集成点

```
当前 Pipeline                    升级点
─────────────────────────────────────────────────────
entity_extractor.py       →  RuleExtractor (新模块)
table_analyzer.py         →  SignalTableParser (增强)
                             TransitionTableParser (新)
kg_exporter.py            →  写入 Rule + Transition 节点
                             weight 修复
chunk_builder.py          →  增加 rule_refs 元数据
pipeline.py               →  GraphReasoner (新 stage)
                             ReasoningEngine API
vector_retriever.py       →  Rule 嵌入 (rule_embedding_text)
embedder.py               →  复用（无需改动）
```

## 附录B：最小可行 Cypher 初始化脚本

```cypher
// 创建 VMM 状态机（最小示例）
CREATE
  s_inactive:State {name: 'Inactive', module: 'VMM', is_initial: true},
  s_conv:State {name: 'Convenience', module: 'VMM'},
  s_driving:State {name: 'Driving', module: 'VMM'},
  s_abandoned:State {name: 'Abandoned', module: 'VMM', is_terminal: true}

CREATE
  (s_inactive)-[:TRANSITIONS_TO {
    trigger: 'PEPS_KeyValid = true',
    guard: 'BrakePressed OR StartButton',
    action: 'Enable Convenience Functions',
    priority: 1
  }]->(s_conv),

  (s_conv)-[:TRANSITIONS_TO {
    trigger: 'VCU_StartActive = ON',
    guard: 'PEPS_UsageMode = Active AND VehicleSpeed = 0',
    action: 'Activate PowerTrain',
    priority: 1
  }]->(s_driving),

  (s_driving)-[:TRANSITIONS_TO {
    trigger: 'VCU_StartActive = OFF',
    guard: 'VehicleSpeed = 0',
    action: 'Deactivate PowerTrain',
    priority: 1
  }]->(s_conv),

  (s_conv)-[:TRANSITIONS_TO {
    trigger: 'KeyRemoved OR Timeout[600s]',
    guard: '',
    action: 'Enter Low Power',
    priority: 0
  }]->(s_inactive),

  (s_inactive)-[:TRANSITIONS_TO {
    trigger: 'Timeout[1800s]',
    guard: 'NoWakeupEvent',
    action: 'Enter Deep Sleep',
    priority: 0
  }]->(s_abandoned)
```
