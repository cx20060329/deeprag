# BCM-RAG 系统设计文档

> 基于 PA2A 中央集控器功能规范 V1.0 的实际内容分析
> 文档来源：`PA2A_中央集控器20250813(1).docx`
> 解析引擎：Docling（主）/ MinerU（备）
> 解析结果：166,337 字符 Markdown，87 张表格

---

## 1. 文档结构分析

### 1.1 文档元信息

| 属性 | 值 |
|------|-----|
| 文档名称 | PA2A 中央集控器 功能规范 V1.0 |
| 来源 | 浙江吉智新能源科技有限公司 |
| 版本日期 | 2021-08-03 |
| 解析后长度 | 166,337 字符 |
| 表格数量 | 87 张 |
| 语言 | 中文（含英文术语和 CAN 信号名） |

### 1.2 章节结构

```
1 目录
2 概述
  2.1 系统框图
3 车辆模式管理（VMM）
  3.1 系统概述
  3.2 系统信号（硬件/CAN/LIN/配置参数）
  3.3 车身模式管理（模式定义/流程图/状态判断及报警/状态迁移）
  3.4 唤醒与休眠
  3.5 电压管理
4 外灯（ExteriorLight）
  4.1 系统概述
  4.2 系统信号（硬件/CAN/LIN/配置参数）
  4.3 功能描述（位置灯/近光灯/后雾灯/刹车灯/倒车灯/日间行车灯/伴我回家/转向提醒）
  4.4 输出控制
  4.5 重启后行为
  4.6 功能安全
  4.7 诊断服务
5 内灯（InteriorLight）
  5.1 系统概述
  5.2 系统信号
  5.3 电源电压模式管理
  5.4 功能描述（室内灯/节电控制/指示灯）
  5.5 功能安全/重启行为/诊断服务
6 车窗（Windows）
  6.1 系统概述
  6.2 系统信号
  6.3 功能描述（拓扑描述/集成信号/使能状态/开关采集/全局关窗/全局开窗/遥控窗控/转发车窗/开关故障）
  6.4 功能安全/诊断服务/电源电压管理
7 锁（Locking）
  7.1 系统概述
  7.2 系统信号
  7.3 模式管理
  7.4 功能描述（门锁状态/遥控解闭锁/优先级/中控解闭锁/机械钥匙/驻车自动解锁/碰撞解锁/车速自动闭锁/自动重上锁/电机热保护/后备箱/指示灯）
8 防盗（TheftProtection）
  8.1 系统概述
  8.2 系统信号
  8.3 功能描述（概述/ATWS状态图/状态表/转移表/报警记录）
9 雨刮（Wiper）
  9.1 系统概述
  9.2 系统信号
  9.3 功能描述（开关检测/输出控制/前雨刮清洗功能）
10 远程控制（RemoteControl）
  10.1 钥匙学习
  10.2 无钥匙启动认证
```

### 1.3 文档特征分析

| 特征 | 说明 |
|------|------|
| 层级深度 | 最深 6 级（H2 章 → H3 节 → H4 小节 → H5 子节 → H6 条目） |
| 表格类型 | 信号矩阵表、状态定义表、状态转移表、输出控制表、参数配置表、故障表 |
| 状态机 | VMM 4状态机、ATWS 7状态机、室内灯控制状态机、节电控制状态机 |
| 信号密度 | 每个模块平均 20-50 个 CAN/硬件/LIN 信号 |
| 跨模块引用 | 车窗→灯光（防夹报警）、VMM→锁（电源模式）、锁→防盗（闭锁触发设防） |
| 配置参数 | 大量 `Cfg*` 和 `cfg*` 前缀的标定参数，控制功能行为分支 |
| 图片依赖 | 流程图和状态图以图片形式嵌入，Docling 无法提取文字 |

---

## 2. 目录树设计（Document Tree Layer）

### 2.1 节点类型

```
DocumentTree
├── Document (root)
│   ├── Chapter (章节)
│   │   ├── Section (节)
│   │   │   ├── SubSection (小节)
│   │   │   │   ├── Leaf (叶子节点 - 实际内容块)
│   │   │   │   │   ├── Table (表格)
│   │   │   │   │   └── Content (文本内容)
```

### 2.2 节点 Schema

```yaml
DocumentTreeNode:
  node_id: string            # 唯一ID，如 "ch3_sec3.4_ss3.4.2"
  node_type: enum            # root | chapter | section | subsection | leaf
  title: string              # 标题文本
  title_en: string           # 英文标题（如有）
  level: int                 # 层级深度 0-6
  path: string               # 完整路径 "3 > 3.3 > 3.3.4 > 3.3.4.2"
  parent_id: string          # 父节点ID
  children: [string]         # 子节点ID列表
  page_start: int            # 起始页码
  page_end: int              # 结束页码
  content_type: enum         # state_machine | signal_table | function_desc | config_block | fault_block | mixed
  tables: [string]           # 包含的表格ID列表
  chunk_ids: [string]        # 关联的chunk ID列表
  graph_node_ids: [string]   # 关联的图谱节点ID列表
```

### 2.3 具体目录树

```
Root: PA2A_BCM_Functional_Spec_V1.0
│
├── [Ch1] 概述
│   └── [Sec1.1] 系统框图
│
├── [Ch2] 车辆模式管理（VMM）
│   ├── [Sec2.1] 系统概述
│   │   ├── [SS2.1.1] 功能概述
│   │   └── [SS2.1.2] 子系统框图
│   ├── [Sec2.2] 系统信号
│   │   ├── [SS2.2.1] 硬件信号（输入/输出）
│   │   ├── [SS2.2.2] CAN信号（输入/输出）
│   │   ├── [SS2.2.3] LIN信号
│   │   └── [SS2.2.4] 配置参数（NVM/常数）
│   ├── [Sec2.3] 车身模式管理
│   │   ├── [SS2.3.1] 模式定义（Abandoned/Inactive/Convenience/Driving）
│   │   ├── [SS2.3.2] 系统流程图
│   │   ├── [SS2.3.3] 内部状态判断及报警（10个子项）
│   │   └── [SS2.3.4] 状态迁移（4个模式间迁移规则）
│   ├── [Sec2.4] 唤醒与休眠
│   └── [Sec2.5] 电压管理（5种电压模式）
│
├── [Ch3] 外灯（ExteriorLight）
│   ├── [Sec3.1] 系统概述
│   ├── [Sec3.2] 系统信号
│   ├── [Sec3.3] 功能描述
│   │   ├── [SS3.3.1] 外灯开关输入
│   │   ├── [SS3.3.2] 位置灯
│   │   ├── [SS3.3.3] 近光灯
│   │   ├── [SS3.3.4] 后雾灯
│   │   ├── [SS3.3.5] 刹车灯
│   │   ├── [SS3.3.6] 倒车灯
│   │   ├── [SS3.3.7] 日间行车灯
│   │   ├── [SS3.3.8] 伴我回家灯
│   │   └── [SS3.3.9] 转向或提醒（16种闪烁模式）
│   ├── [Sec3.4] 输出控制
│   └── [Sec3.5-3.7] 重启行为/功能安全/诊断
│
├── [Ch4] 内灯（InteriorLight）
│   ├── [Sec4.1] 系统概述
│   ├── [Sec4.2] 系统信号
│   ├── [Sec4.3] 电源电压模式管理
│   └── [Sec4.4] 功能描述
│       ├── [SS4.4.1] 室内灯（状态图+状态表+转移表+渐亮渐灭+MMI设置）
│       ├── [SS4.4.2] 节电控制（状态图+状态表+转移表）
│       └── [SS4.4.3] 指示灯
│
├── [Ch5] 车窗（Windows）
│   ├── [Sec5.1] 系统概述
│   ├── [Sec5.2] 系统信号
│   └── [Sec5.3] 功能描述
│       ├── [SS5.3.1] 拓扑描述
│       ├── [SS5.3.2] 集成信号输出（9个集成信号）
│       ├── [SS5.3.3] 车窗使能状态
│       ├── [SS5.3.4] 中控车窗开关采集
│       ├── [SS5.3.5] 全局关窗
│       ├── [SS5.3.6] 全局开窗
│       ├── [SS5.3.7] 遥控窗控停止
│       ├── [SS5.3.8] 转发车窗
│       └── [SS5.3.9] 开关故障
│
├── [Ch6] 锁（Locking）
│   ├── [Sec6.1] 系统概述
│   ├── [Sec6.2] 系统信号
│   ├── [Sec6.3] 模式管理
│   └── [Sec6.4] 功能描述
│       ├── [SS6.4.1] 门及锁状态定义
│       ├── [SS6.4.2] 遥控解闭锁
│       ├── [SS6.4.3] 优先级处理
│       ├── [SS6.4.4] 中控解闭锁
│       ├── [SS6.4.5] 机械钥匙解闭锁
│       ├── [SS6.4.6] 驻车自动解锁
│       ├── [SS6.4.7] 碰撞解锁
│       ├── [SS6.4.8] 车速自动闭锁
│       ├── [SS6.4.9] 自动重新上锁
│       ├── [SS6.4.10] 电机热保护
│       ├── [SS6.4.11] 后备箱解锁
│       └── [SS6.4.12] 中控锁指示灯
│
├── [Ch7] 防盗（TheftProtection）
│   ├── [Sec7.1] 系统概述
│   ├── [Sec7.2] 系统信号
│   └── [Sec7.3] 功能描述
│       ├── [SS7.3.1] ATWS 状态图
│       ├── [SS7.3.2] ATWS 状态表（7状态）
│       ├── [SS7.3.3] ATWS 转移表（18条转移规则）
│       └── [SS7.3.4] 报警记录
│
├── [Ch8] 雨刮（Wiper）
│   ├── [Sec8.1] 系统概述
│   ├── [Sec8.2] 系统信号
│   └── [Sec8.3] 功能描述
│       ├── [SS8.3.1] 开关检测
│       ├── [SS8.3.2] 输出控制
│       └── [SS8.3.3] 前雨刮/清洗功能
│
└── [Ch9] 远程控制（RemoteControl）
    ├── [Sec9.1] 钥匙学习
    └── [Sec9.2] 无钥匙启动认证
```

---

## 3. 实体类型设计（Knowledge Graph Entities）

### 3.1 实体类型定义

#### 3.1.1 Module（模块）

```yaml
Entity: Module
Label: "模块"
Properties:
  module_id: string          # "VMM", "ExteriorLight", "InteriorLight", "Window", "Lock", "TheftProtection", "Wiper", "RemoteControl"
  name: string               # 中文名称
  name_en: string            # 英文名称
  chapter: string            # 所属章节号
  description: string        # 功能概述
  page_range: [int, int]     # 页码范围
Examples:
  - module_id: "VMM"
    name: "车辆模式管理"
    name_en: "Vehicle Mode Management"
    chapter: "3"
  - module_id: "ExteriorLight"
    name: "外灯"
    name_en: "Exterior Light"
    chapter: "4"
  - module_id: "Lock"
    name: "锁"
    name_en: "Locking"
    chapter: "7"
```

#### 3.1.2 State（状态）

```yaml
Entity: State
Label: "状态"
Properties:
  state_id: string           # 全局唯一ID
  name: string               # 状态名称
  module_id: string          # 所属模块
  state_machine: string      # 所属状态机名称
  description: string        # 状态说明
  entry_actions: [string]    # 进入动作
  exit_actions: [string]     # 退出动作
  internal_actions: [string] # 内部动作
  timers: [string]           # 关联计时器
Examples:
  - state_id: "VMM_Abandoned"
    name: "Abandoned"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "最终休眠模式，最低系统功耗"
  - state_id: "VMM_Inactive"
    name: "Inactive"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "典型休息模式，有限功能可用"
  - state_id: "VMM_Convenience"
    name: "Convenience"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "便利功能可用，需有效钥匙"
  - state_id: "VMM_Driving"
    name: "Driving"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "动力系统启动，全功能可用"
  - state_id: "ATWS_Disarmed"
    name: "Disarmed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "解防状态"
  - state_id: "ATWS_Armed"
    name: "Armed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "设防状态"
  - state_id: "ATWS_Alarm"
    name: "Alarm"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "报警状态"
```

#### 3.1.3 Signal（信号）

```yaml
Entity: Signal
Label: "信号"
Properties:
  signal_id: string          # 信号名称（CAN信号名或硬件信号名）
  signal_type: enum          # CAN_IN | CAN_OUT | HW_IN | HW_OUT | LIN_IN | LIN_OUT
  can_id: string             # CAN ID（如 "0x1E2"）
  bit_position: string       # 位位置（如 "Bit34-32"）
  value_encoding: string     # 值编码（如 "0x0:Inactive 0x1:Convenience 0x2:Driving"）
  module_id: string          # 所属模块
  description: string        # 说明
  source_module: string      # 信号源模块（输入信号）
  target_module: string      # 信号目标模块（输出信号）
Examples:
  - signal_id: "PEPS_UsageMode"
    signal_type: "CAN_OUT"
    can_id: "0x1E2"
    bit_position: "Bit34-32"
    value_encoding: "0x0:Inactive 0x1:Convenience 0x2:Driving"
    module_id: "VMM"
  - signal_id: "VCU_ePTReady"
    signal_type: "CAN_IN"
    can_id: "0x165"
    value_encoding: "0x0:Inactive 0x1:Active"
    module_id: "VMM"
    source_module: "VCU"
  - signal_id: "BCM_ATWS_St"
    signal_type: "CAN_OUT"
    can_id: "0x284"
    value_encoding: "0x0:Armed 0x1:Prearmed 0x2:Disarmed 0x3:Remind 0x4:Alarm 0x5:PartiallyArmed 0x6:Predisarmed"
    module_id: "TheftProtection"
```

#### 3.1.4 Function（功能）

```yaml
Entity: Function
Label: "功能"
Properties:
  function_id: string        # 功能唯一ID
  name: string               # 功能名称
  name_en: string            # 英文名称
  module_id: string          # 所属模块
  section_path: string       # 文档路径
  description: string        # 功能描述
  trigger_conditions: [string]  # 触发条件（|| 或 && 关系）
  enable_conditions: [string]   # 使能条件
  outputs: [string]          # 执行输出
  related_configs: [string]  # 关联配置参数
Examples:
  - function_id: "GlobalClose"
    name: "全局关窗"
    name_en: "Global Window Close"
    module_id: "Window"
    section_path: "6.4.5"
  - function_id: "CrashUnlock"
    name: "碰撞解锁"
    name_en: "Crash Unlock"
    module_id: "Lock"
    section_path: "7.4.7"
  - function_id: "AutoLock"
    name: "车速自动闭锁"
    name_en: "Speed Auto Lock"
    module_id: "Lock"
    section_path: "7.4.8"
  - function_id: "FollowMeHome"
    name: "伴我回家灯"
    name_en: "Follow Me Home"
    module_id: "ExteriorLight"
    section_path: "4.3.8"
```

#### 3.1.5 Parameter（参数）

```yaml
Entity: Parameter
Label: "参数"
Properties:
  param_id: string           # 参数名
  param_type: enum           # NVM | CONSTANT | CALIBRATION
  module_id: string          # 所属模块
  description: string        # 参数说明
  default_value: string      # 默认值
  value_range: string        # 取值范围
  affects_functions: [string] # 影响的功能列表
Examples:
  - param_id: "CfgTCMEOLOption"
    param_type: "CONSTANT"
    module_id: "ExteriorLight"
    description: "组合开关类型配置（0x1=硬线自锁, 0x2=CAN TCM报文）"
    affects_functions: ["外灯开关输入", "近光灯", "位置灯", "后雾灯"]
  - param_id: "cfgDoorLatchDuration"
    param_type: "CALIBRATION"
    module_id: "Lock"
    description: "门锁电机驱动持续时间"
  - param_id: "cfgAPWLOption"
    param_type: "CONSTANT"
    module_id: "Window"
    description: "车窗类型配置（0x0=不使用, 0x1=LIN无防夹, 0x3=LIN四门防夹）"
```

#### 3.1.6 Fault（故障）

```yaml
Entity: Fault
Label: "故障"
Properties:
  fault_id: string           # 故障ID
  name: string               # 故障名称
  module_id: string          # 所属模块
  trigger_condition: string  # 触发条件
  response: string           # 系统响应
  alarm_type: enum           # 报警类型
Examples:
  - fault_id: "KeyLost"
    name: "钥匙丢失"
    module_id: "VMM"
    trigger_condition: "行驶中有效钥匙信号丢失"
    alarm_type: "仪表报警"
  - fault_id: "WindowJam"
    name: "车窗防夹"
    module_id: "Window"
    trigger_condition: "车窗运行中检测到阻力超过阈值"
    response: "车窗反转"
  - fault_id: "SignalTimeout"
    name: "信号超时"
    module_id: "VMM"
```

#### 3.1.7 Timer（计时器）

```yaml
Entity: Timer
Label: "计时器"
Properties:
  timer_id: string           # 计时器名称
  module_id: string          # 所属模块
  duration: string           # 时长
  purpose: string            # 用途
Examples:
  - timer_id: "ATWSPrearmingTimer"
    module_id: "TheftProtection"
    purpose: "预设防计时器"
  - timer_id: "ATWSAlarmingTimer"
    module_id: "TheftProtection"
    purpose: "报警持续时间"
  - timer_id: "KeyDetectionTimer"
    module_id: "TheftProtection"
    purpose: "钥匙检测超时"
```

#### 3.1.8 PowerMode（电源模式）- 关键跨模块概念

```yaml
Entity: PowerMode
Label: "电源模式"
Properties:
  mode_id: string            # 模式ID
  name: string               # 模式名称
  peaps_usage_mode: string   # 对应 CAN 信号 PEPS_UsageMode 值
  description: string        # 说明
  available_functions: [string] # 此模式下可用的功能
Note: >
  电源模式是跨模块的核心概念，几乎所有功能都有电源模式约束。
  建议作为独立实体以支持跨模块推理。
```

---

## 4. 关系类型设计

### 4.1 关系矩阵

```yaml
Relationship Types:

# 结构关系
belongs_to:
  source: [Function, State, Signal, Parameter, Fault]
  target: [Module]
  description: "实体属于某个模块"
  example: "GlobalClose -[belongs_to]-> Window"

contains:
  source: [Module]
  target: [Function, State]
  description: "模块包含功能/状态机"

# 状态迁移关系
transition_to:
  source: [State]
  target: [State]
  properties:
    conditions: [string]     # 迁移条件（AND/OR 组合）
    priority: int            # 优先级
    preconditions: [string]  # 前置条件
    outputs: [string]        # 执行输出
  example: "VMM_Inactive -[transition_to]-> VMM_Convenience {conditions: ['主驾门开且解防', '制动踏板踩下']}"

# 信号关系
triggers:
  source: [Signal]
  target: [Function, State]
  description: "信号触发功能或状态迁移"
  example: "PEPS_UsageMode -[triggers]-> GlobalClose"

outputs:
  source: [Function, State]
  target: [Signal]
  description: "功能/状态输出信号"
  example: "VMM_Driving -[outputs]-> PEPS_UsageMode=0x2"

controls:
  source: [Signal]
  target: [Signal]
  description: "信号控制另一个信号（如集成信号）"
  example: "L_Drv_Wdw_PositionSts -[controls]-> AllWindowClosedSts"

# 功能依赖关系
depends_on:
  source: [Function]
  target: [Function, State, Signal, Parameter]
  description: "功能依赖"
  example: "GlobalClose -[depends_on]-> WindowEnable"
  example: "AutoLock -[depends_on]-> ESC_VehicleSpeed"

# 配置关系
configures:
  source: [Parameter]
  target: [Function]
  description: "参数配置功能行为"
  example: "CfgTCMEOLOption -[configures]-> 外灯开关输入"

# 故障关系
reports:
  source: [Function]
  target: [Fault]
  description: "功能检测并上报故障"
  example: "碰撞解锁检测 -[reports]-> 碰撞传感器故障"

# 跨模块引用
references:
  source: [Module]
  target: [Module]
  description: "模块间引用（如车窗→灯光 防夹报警）"
  example: "Window -[references]-> ExteriorLight {reason: '防夹报警需灯光闪烁提醒'}"

# 电源模式约束
requires:
  source: [Function]
  target: [PowerMode]
  description: "功能需要特定电源模式"
  example: "遥控解闭锁 -[requires]-> VMM_Inactive"
```

### 4.2 关键关系路径示例

```
关系路径1: 全局关窗依赖链
GlobalClose
  -[depends_on]-> WindowEnable
  -[depends_on]-> Driving (state)
  -[depends_on]-> PEPS_UsageMode (signal)
  -[depends_on]-> cfgAPWLOption (parameter)

关系路径2: 碰撞解锁链路
CrashUnlock
  -[belongs_to]-> Lock
  -[triggers]-> 所有门解锁
  -[outputs]-> 危险报警闪烁
  -[depends_on]-> 碰撞信号 (CAN from Airbag)
  -[requires]-> 任意电源模式

关系路径3: ATWS 状态迁移链路
ATWS_Disarmed
  -[transition_to]-> ATWS_Prearmed  {trigger: 闭锁成功}
  -[transition_to]-> ATWS_Armed     {trigger: 自动重闭锁}
  -[transition_to]-> ATWS_Remind    {trigger: 闭锁成功且门未全关}
  -[transition_to]-> ATWS_Alarm     {trigger: 发动机防盗认证失败}
```

---

## 5. Chunk 类型设计

### 5.1 Chunk 类型定义

```yaml
Chunk Types:

1. StateTransitionChunk（状态迁移块）
  description: "状态机中的单个迁移规则"
  target_size: 300-800 tokens
  contains:
    - 源状态/目标状态
    - 前置条件（AND 逻辑）
    - 触发条件（OR 逻辑）
    - 执行输出
    - CAN 信号输出
  source_example: "3.3.4.2.2 迁移到Convenience状态"

2. StateMachineChunk（状态机定义块）
  description: "完整状态机定义（状态表+转移表）"
  target_size: 1000-2000 tokens
  contains:
    - 所有状态定义
    - 状态转移矩阵
    - 关联计时器
  source_example: "8.4.3 ATWS状态表 + 8.4.4 ATWS转移表"

3. SignalTableChunk（信号表块）
  description: "信号定义表格"
  target_size: 500-1500 tokens
  contains:
    - 信号名称
    - CAN ID / 硬件引脚
    - 位位置
    - 值编码
    - 说明
  source_example: "3.2.2 CAN信号 输入/输出"

4. FunctionDescChunk（功能描述块）
  description: "单个功能的完整描述"
  target_size: 400-1200 tokens
  contains:
    - 触发条件
    - 使能条件
    - 执行输出
    - 配置依赖
  source_example: "7.4.2.1 遥控解锁"

5. ConfigBlockChunk（配置参数块）
  description: "配置参数定义"
  target_size: 200-600 tokens
  contains:
    - 参数名
    - 参数类型（NVM/常数）
    - 默认值/取值范围
    - 影响的功能
  source_example: "4.2.4 配置参数"

6. FaultHandlingChunk（故障处理块）
  description: "故障检测与处理逻辑"
  target_size: 200-600 tokens
  contains:
    - 故障触发条件
    - 报警方式
    - 系统响应
  source_example: "3.3.3.5 未找到钥匙报警"

7. OutputControlChunk（输出控制块）
  description: "硬件输出控制逻辑"
  target_size: 300-800 tokens
  contains:
    - 输出信号名
    - 控制逻辑
    - 优先级
  source_example: "4.5.9 转向提醒输出（含16种闪烁模式优先级）"

8. CrossReferenceChunk（跨模块引用块）
  description: "跨模块引用说明"
  target_size: 100-300 tokens
  contains:
    - 引用源模块
    - 引用目标模块
    - 引用原因
  source_example: "5.5.4 参考车窗功能规范的中控车窗开关采集章节"
```

### 5.2 Chunk 切分策略

```
切分原则:
1. 优先按 H4/H5 标题边界切分
2. 状态转移按单个转移规则切分
3. 表格按语义完整性保留（不截断表格）
4. 功能描述按"触发条件 → 使能条件 → 执行输出"三元组切分
5. 配置参数按参数组切分

切分粒度控制:
- 最小 chunk: 200 tokens（配置参数块）
- 目标 chunk: 800-1500 tokens（功能描述块、信号表块）
- 最大 chunk: 2000 tokens（状态机定义块）
- 超过 2000 tokens 的内容块需要二次切分（按子标题或逻辑边界）
```

---

## 6. Metadata 设计

### 6.1 Chunk Metadata Schema

```yaml
ChunkMetadata:
  # === 文档定位 ===
  chunk_id: string              # 唯一ID "chunk_VMM_001"
  chunk_type: enum              # StateTransitionChunk | SignalTableChunk | FunctionDescChunk | ConfigBlockChunk | FaultHandlingChunk | OutputControlChunk | StateMachineChunk | CrossReferenceChunk
  module: string                # 所属模块 "VMM" | "ExteriorLight" | ...
  section_path: string          # 文档路径 "3.3.4.2.2"
  parent_section: string        # 父级路径 "3.3.4"
  page_start: int               # 起始页码
  page_end: int                 # 结束页码

  # === 内容标签 ===
  function: string              # 所属功能名（如 "遥控解锁"）
  functions: [string]           # 涉及的功能列表
  states: [string]              # 涉及的状态列表
  signals: [string]             # 涉及的信号列表
  parameters: [string]          # 涉及的配置参数列表
  faults: [string]              # 涉及的故障列表

  # === 图谱关联 ===
  graph_node_ids: [string]      # 关联的图谱实体节点ID

  # === 跨模块标记 ===
  cross_module_refs: [string]   # 引用的其他模块

  # === 质量标记 ===
  has_table: bool               # 是否包含表格
  has_state_machine: bool       # 是否包含状态机
  has_condition: bool           # 是否包含条件逻辑（触发/使能条件）
  completeness: enum            # complete | partial（图片缺失标记为partial）
```

### 6.2 典型 Metadata 示例

```yaml
# 示例1: 状态迁移 Chunk
chunk_id: "chunk_VMM_012"
chunk_type: "StateTransitionChunk"
module: "VMM"
section_path: "3.3.4.2.2"
parent_section: "3.3.4"
page_start: 28
page_end: 29
function: ""
functions: []
states: ["VMM_Inactive", "VMM_Convenience"]
signals: ["PEPS_UsageMode", "PEPS_PowerMode", "PEPS_IGN1RelaySts"]
parameters: []
faults: []
graph_node_ids: ["state_VMM_Inactive", "state_VMM_Convenience"]
cross_module_refs: ["PEPS"]
has_table: false
has_state_machine: false
has_condition: true
completeness: "complete"

# 示例2: 功能描述 Chunk
chunk_id: "chunk_Lock_008"
chunk_type: "FunctionDescChunk"
module: "Lock"
section_path: "7.4.2.1"
parent_section: "7.4.2"
page_start: 122
page_end: 123
function: "遥控解锁"
functions: ["遥控解锁"]
states: ["VMM_Inactive"]
signals: []
parameters: ["cfgUnlockType", "cfgDoorLatchDuration"]
faults: []
graph_node_ids: ["func_RemoteUnlock", "param_cfgUnlockType"]
cross_module_refs: ["VMM"]
has_table: false
has_state_machine: false
has_condition: true
completeness: "complete"

# 示例3: 状态机 Chunk
chunk_id: "chunk_ATWS_001"
chunk_type: "StateMachineChunk"
module: "TheftProtection"
section_path: "8.4.3"
parent_section: "8.4"
page_start: 139
page_end: 143
function: "ATWS"
functions: ["ATWS"]
states: ["ATWS_Disarmed", "ATWS_Prearmed", "ATWS_Armed", "ATWS_Alarm", "ATWS_PartiallyArmed", "ATWS_Remind", "ATWS_Predisarmed"]
signals: ["BCM_ATWS_St", "VCU_ePTReleaseSig"]
parameters: ["CfgATWSRemindingTimer", "ATWSPrearmingTimer"]
faults: []
graph_node_ids: ["state_ATWS_Disarmed", "state_ATWS_Prearmed", "state_ATWS_Armed", "state_ATWS_Alarm", "state_ATWS_PartiallyArmed", "state_ATWS_Remind", "state_ATWS_Predisarmed"]
cross_module_refs: ["Lock", "VMM"]
has_table: true
has_state_machine: true
has_condition: true
completeness: "partial"  # 状态图以图片形式存在，无法提取
```

---

## 7. 检索链路设计

### 7.1 完整检索流水线

```
User Query
    │
    ▼
┌─────────────────────────┐
│ Stage 1: Intent Analysis │  分析查询意图，提取实体和关系
└────────────┬────────────┘
             │ 输出: {intent_type, entities, relations, modules}
             ▼
┌─────────────────────────┐
│ Stage 2: Graph Retrieval │  Neo4j 图谱检索依赖链
└────────────┬────────────┘
             │ 输出: {related_entities, dependency_paths, hop_distance}
             ▼
┌─────────────────────────────┐
│ Stage 3: Document Tree      │  定位相关章节
│          Localization       │
└────────────┬────────────────┘
             │ 输出: {relevant_sections, sibling_sections}
             ▼
┌─────────────────────────┐
│ Stage 4: Vector Retrieval│  Qdrant 语义检索
└────────────┬────────────┘
             │ 输出: {candidate_chunks, similarity_scores}
             ▼
┌─────────────────────────┐
│ Stage 5: Merge Candidates│  合并图谱+向量结果，去重
└────────────┬────────────┘
             │ 输出: {merged_candidates, source_tracking}
             ▼
┌─────────────────────────┐
│ Stage 6: Cross-Encoder   │  BGE-Reranker / Qwen-Reranker
│          Rerank          │
└────────────┬────────────┘
             │ 输出: {reranked_candidates, semantic_scores}
             ▼
┌─────────────────────────┐
│ Stage 7: Rule-Based      │  模块/状态/信号/图谱距离加权
│          Rerank          │
└────────────┬────────────┘
             │ 输出: {final_candidates, combined_scores}
             ▼
┌─────────────────────────┐
│ Stage 8: Context         │  去重→合并→保留关键关系→打包
│          Compression     │
└────────────┬────────────┘
             │ 输出: EvidencePackage
             ▼
┌─────────────────────────┐
│ Stage 9: LLM Answer      │  基于 Evidence Package 生成回答
└─────────────────────────┘
```

### 7.2 各阶段详细设计

#### Stage 1: Intent Analysis（意图分析）

```yaml
Intent Types:
  - STATE_QUERY:           "Driving模式的条件是什么？"
  - TRANSITION_QUERY:      "如何从Inactive迁移到Convenience？"
  - SIGNAL_QUERY:          "PEPS_UsageMode 信号定义？"
  - FUNCTION_QUERY:        "全局关窗如何触发？"
  - DEPENDENCY_QUERY:      "碰撞解锁依赖哪些信号？"
  - FAULT_QUERY:           "钥匙丢失如何处理？"
  - CONFIG_QUERY:          "cfgAPWLOption 配置含义？"
  - CROSS_MODULE_QUERY:    "车窗防夹如何影响灯光？"
  - COMPARISON_QUERY:      "Comfort模式 vs Driving模式？"

Extraction Targets:
  - entities: 从查询中提取模块名、状态名、信号名、功能名、参数名
  - relations: 从查询中推断关系类型
  - modules: 定位目标模块（可多个）
  - query_type: 查询类型分类
```

#### Stage 2: Graph Retrieval（图谱检索）

```yaml
Traversal Strategy:
  depth: 2-hop (default), configurable 1-3
  
  Starting Nodes:
    - 匹配的实体节点（从 Intent Analysis 获得）
  
  Traversal Rules:
    - 优先遍历: transition_to, depends_on, triggers, controls
    - 辅助遍历: belongs_to, contains, configures
    - 排除: references（仅当明确跨模块查询时纳入）
  
  Output:
    - dependency_paths: 实体间依赖路径
    - related_entities: 关联实体列表
    - hop_distance: 跳数标记（用于后续规则重排加权）
  
  Example:
    Query: "GlobalClose的依赖"
    Start: func_GlobalClose
    1-hop: state_Driving, signal_PEPS_UsageMode, param_cfgAPWLOption
    2-hop: state_VMM_Inactive, func_WindowEnable, signal_ESC_VehicleSpeed
```

#### Stage 3: Document Tree Localization（目录树定位）

```yaml
Strategy:
  1. 根据图谱检索到的实体，反查所属 section_path
  2. 扩展至同级 sibling sections（如 3.3.4.2 的 siblings）
  3. 向上追溯至父级 section（如追溯至 3.3）
  4. 收集所有相关 section 下的 chunk_ids
  
  Output:
    - relevant_sections: 相关章节路径列表
    - section_chunk_ids: 这些章节下的所有 chunk ID
```

#### Stage 4: Vector Retrieval（向量检索）

```yaml
Query Processing:
  - 原始查询 + 图谱检索到的实体名 + 关键信号名 → 混合查询向量
  
  Retrieval Config:
    - top_k: 20 (initial)
    - score_threshold: 0.6
    - prefer: same module chunks
    
  Filtering:
    - 优先: 与 Intent Analysis 模块匹配的 chunks
    - 其次: 图谱相关实体的 chunks
    - 排除: 与查询模块完全无关的 chunks
```

#### Stage 5: Merge Candidates（候选合并）

```yaml
Merge Strategy:
  1. 收集 Stage 3（目录树）的 chunk_ids
  2. 收集 Stage 4（向量检索）的 chunk_ids
  3. 按 chunk_id 去重
  4. 保留 source_tracking（标记每个 chunk 的来源：graph | tree | vector）
  5. 优先级: graph+tree > graph > tree+vector > vector
  
  Output Size: 15-25 candidates (before rerank)
```

#### Stage 6: Cross-Encoder Rerank（语义重排）

```yaml
Model: BGE-Reranker-v2-m3 或 Qwen3-Reranker
  
  Input:
    - query: 原始用户查询
    - passages: merged candidates 的文本内容
    - top_n: 10
  
  Output:
    - reranked_candidates: 重排后的 top-10
    - semantic_scores: 每条的语义相关分数 [0, 1]
```

#### Stage 7: Rule-Based Rerank（规则重排）

```yaml
Rule Scoring (additive):

  same_module_bonus:        +0.15  # chunk.module == query.module
  same_state_bonus:         +0.15  # chunk 包含查询的状态
  same_function_bonus:      +0.15  # chunk 属于查询的功能
  same_signal_bonus:        +0.10  # chunk 包含查询的信号
  graph_distance_bonus:     +0.10 * (1 / hop_distance)  # 图谱距离越近加分越多
  state_machine_bonus:      +0.05  # chunk 包含状态机（对理解行为有价值）
  cross_module_penalty:     -0.10  # chunk 与查询模块不匹配时扣分
  
  FinalScore = SemanticScore + Sum(RuleScores)
  
  Output: top-5 final candidates
```

#### Stage 8: Context Compression（上下文压缩）

```yaml
Compression Pipeline:
  1. Deduplication:
     - 移除重复的事实陈述（如相同的信号定义在多个 chunk 中出现）
     - 保留首次出现的完整定义
  
  2. Rule Merging:
     - 合并相同状态机的多个转移规则 → 紧凑的转移表
     - 合并相同功能的多个配置条件
  
  3. Dependency Chain Preservation:
     - 保留完整的依赖链路径
     - 保留状态迁移序列
  
  4. Signal Relationship Preservation:
     - 保留信号间的 control/output 关系
     - 保留信号值编码映射
  
  5. Evidence Package Assembly:
     输出结构见下方
```

#### Evidence Package 结构

```yaml
EvidencePackage:
  query: string                    # 原始查询
  intent: string                   # 查询意图类型
  
  # 核心信息
  primary_entity:
    type: enum                     # module | state | function | signal
    name: string
    definition: string             # 实体定义
  
  # 依赖关系
  dependencies:
    states:                        # 依赖的状态
      - name: string
        definition: string
    signals:                       # 依赖的信号
      - name: string
        can_id: string
        encoding: string
    functions:                     # 依赖的功能
      - name: string
        relationship: string
    
  # 状态机（如适用）
  state_machine:
    name: string
    states: [string]               # 状态列表
    transitions:                   # 关键转移
      - from: string
        to: string
        conditions: [string]
  
  # 条件逻辑（如适用）
  conditions:
    trigger: [string]              # 触发条件
    enable: [string]               # 使能条件
    output: [string]               # 执行输出
  
  # 配置参数
  configs:
    - name: string
      value_meaning: string
  
  # 故障信息
  faults:
    - name: string
      response: string
  
  # 文档引用
  references:
    - section: string              # "Section 3.3.4.2"
      page: int
      relevance: string            # 为什么相关
  
  # 跨模块关联
  cross_modules:
    - module: string
      reason: string
  
  # 元数据
  metadata:
    source_chunks: [string]        # 来源 chunk ID
    compression_ratio: float       # 压缩比
    confidence: float              # 置信度
```

---

## 8. 图谱设计（Neo4j）

### 8.1 图 Schema

```cypher
// === 节点标签 ===

// Module（模块）
(:Module {
  module_id: String,        // UNIQUE
  name: String,
  name_en: String,
  chapter: String,
  description: String
})

// State（状态）
(:State {
  state_id: String,         // UNIQUE
  name: String,
  module_id: String,
  state_machine: String,
  description: String
})

// Signal（信号）
(:Signal {
  signal_id: String,        // UNIQUE
  signal_type: String,      // CAN_IN | CAN_OUT | HW_IN | HW_OUT | LIN_IN | LIN_OUT
  can_id: String,
  bit_position: String,
  value_encoding: String,
  module_id: String,
  description: String
})

// Function（功能）
(:Function {
  function_id: String,      // UNIQUE
  name: String,
  name_en: String,
  module_id: String,
  section_path: String,
  description: String
})

// Parameter（参数）
(:Parameter {
  param_id: String,         // UNIQUE
  param_type: String,       // NVM | CONSTANT | CALIBRATION
  module_id: String,
  description: String
})

// Fault（故障）
(:Fault {
  fault_id: String,         // UNIQUE
  name: String,
  module_id: String,
  trigger_condition: String,
  response: String
})

// PowerMode（电源模式）
(:PowerMode {
  mode_id: String,          // UNIQUE
  name: String,
  peaps_usage_mode: String,
  description: String
})

// === 关系类型 ===

// 结构关系
(:Module)-[:CONTAINS]->(:Function)
(:Module)-[:CONTAINS]->(:State)
(:Module)-[:OWNS]->(:Signal)
(:Module)-[:OWNS]->(:Parameter)
(:Module)-[:OWNS]->(:Fault)

// 状态迁移
(:State)-[:TRANSITION_TO {
  conditions: [String],
  preconditions: [String],
  outputs: [String],
  priority: Integer
}]->(:State)

// 信号关系
(:Signal)-[:TRIGGERS]->(:Function)
(:Signal)-[:TRIGGERS]->(:State)
(:Function)-[:OUTPUTS]->(:Signal)
(:State)-[:OUTPUTS]->(:Signal)
(:Signal)-[:CONTROLS]->(:Signal)

// 功能依赖
(:Function)-[:DEPENDS_ON]->(:Function)
(:Function)-[:DEPENDS_ON]->(:State)
(:Function)-[:DEPENDS_ON]->(:Signal)
(:Function)-[:REQUIRES]->(:PowerMode)

// 配置关系
(:Parameter)-[:CONFIGURES]->(:Function)

// 故障关系
(:Function)-[:REPORTS]->(:Fault)

// 跨模块引用
(:Module)-[:REFERENCES {reason: String}]->(:Module)
```

### 8.2 索引策略

```cypher
// 节点索引
CREATE INDEX module_id_idx FOR (m:Module) ON (m.module_id);
CREATE INDEX state_id_idx FOR (s:State) ON (s.state_id);
CREATE INDEX signal_id_idx FOR (s:Signal) ON (s.signal_id);
CREATE INDEX function_id_idx FOR (f:Function) ON (f.function_id);
CREATE INDEX param_id_idx FOR (p:Parameter) ON (p.param_id);
CREATE INDEX fault_id_idx FOR (f:Fault) ON (f.fault_id);

// 复合索引（加速跨模块查询）
CREATE INDEX state_module_idx FOR (s:State) ON (s.module_id);
CREATE INDEX signal_module_idx FOR (s:Signal) ON (s.module_id);
CREATE INDEX function_module_idx FOR (f:Function) ON (f.module_id);
```

### 8.3 预期图谱规模

```
预计节点数:
  Module:     8
  State:      25-35  (VMM 4 + ATWS 7 + InteriorLight ~6 + Window ~5 + Lock ~5)
  Signal:     200-300 (每个模块 20-50 个信号)
  Function:   80-120 (每个模块 10-20 个功能)
  Parameter:  50-80
  Fault:      15-25
  PowerMode:  4
  Timer:      10-15
  ─────────────────
  Total:      约 400-600 节点

预计关系数:
  CONTAINS/OWNS:   300-400
  TRANSITION_TO:   50-80
  TRIGGERS:        100-150
  OUTPUTS:         100-150
  DEPENDS_ON:      80-120
  CONFIGURES:      50-80
  REPORTS:         15-25
  REFERENCES:      10-15
  REQUIRES:        80-100
  CONTROLS:        20-30
  ─────────────────
  Total:           约 800-1200 关系
```

### 8.4 关键图查询模板

```cypher
// 查询1: 功能的完整依赖链 (2-hop)
MATCH path = (f:Function {function_id: "GlobalClose"})-[:DEPENDS_ON|REQUIRES|TRIGGERS*1..2]->(related)
RETURN path;

// 查询2: 状态的所有可能迁移路径
MATCH (s:State {state_id: "VMM_Inactive"})-[:TRANSITION_TO]->(target:State)
RETURN s, target;

// 查询3: 信号影响的所有功能
MATCH (sig:Signal {signal_id: "PEPS_UsageMode"})-[:TRIGGERS|CONTROLS*1..2]->(affected)
RETURN sig, affected;

// 查询4: 模块间交叉引用
MATCH (m1:Module)-[r:REFERENCES]->(m2:Module)
RETURN m1.name, m2.name, r.reason;

// 查询5: 电源模式约束的所有功能
MATCH (f:Function)-[:REQUIRES]->(pm:PowerMode {name: "Driving"})
RETURN f.name, f.module_id;
```

---

## 9. 向量库设计（Qdrant）

### 9.1 Collection 设计

```yaml
Collection Name: "bcm_chunks"

Vector Config:
  dimension: 1024            # BGE-M3 / BAAI general embedding
  distance: Cosine

Index Config:
  type: HNSW
  m: 16                      # 连接数
  ef_construct: 200          # 构建时搜索范围
  ef_search: 128             # 查询时搜索范围

Quantization:
  type: scalar               # 初期使用标量量化，数据量大后可升级为 product quantization

Payload Schema:
  chunk_id: keyword           # 索引字段
  module: keyword             # 索引字段，用于过滤
  section_path: keyword       # 索引字段
  chunk_type: keyword         # 索引字段
  function: keyword
  states: [keyword]
  signals: [keyword]
  parameters: [keyword]
  graph_node_ids: [keyword]
  has_table: bool
  has_state_machine: bool
  has_condition: bool
  parent_section: keyword
```

### 9.2 检索策略

```yaml
Default Retrieval:
  method: hybrid              # 向量相似度 + 关键词匹配
  vector_weight: 0.7
  keyword_weight: 0.3
  top_k: 20
  score_threshold: 0.6

Filtered Retrieval:
  - module_filter: 仅检索指定模块的 chunks
  - type_filter: 仅检索指定类型的 chunks（如仅查状态迁移块）
  - section_filter: 仅检索指定章节的 chunks

Multi-Stage Retrieval:
  1. Broad retrieval: top_k=50, score_threshold=0.5
  2. Module-aware re-filter: 按模块优先级筛选
  3. Diversity sampling: 确保不同类型 chunk 都有覆盖
```

### 9.3 Embedding 模型选择

```yaml
Primary: BGE-M3
  - dimension: 1024
  - language: multilingual (中英混合)
  - max_tokens: 8192
  - strengths: 中文语义理解强，支持稠密+稀疏混合检索

Alternative: BAAI/bge-large-zh-v1.5
  - dimension: 1024
  - language: Chinese-optimized
  - max_tokens: 512

Embedding Strategy:
  - 对每个 chunk 生成 embedding
  - 对 chunk 中的关键字段（module, states, signals）额外生成 sparse vectors
  - 存储时同时写入 dense vector 和 sparse vector
```

---

## 10. 项目目录结构

```
bcm-rag/
│
├── CLAUDE.md                          # 架构约束文档
├── BCM-RAG-Design.md                  # 本设计文档
├── pyproject.toml                     # Python 项目配置（uv 管理）
├── uv.lock                            # 依赖锁文件
│
├── data/
│   ├── raw/
│   │   └── PA2A_中央集控器20250813(1).docx   # 原始文档
│   ├── parsed/
│   │   └── bcm_doc.md                 # Docling 解析后的 Markdown
│   └── exports/
│       ├── neo4j_export.cypher        # 图谱导出
│       └── qdrant_snapshot/           # 向量库快照
│
├── parser/
│   ├── __init__.py
│   ├── docling_parser.py              # Docling 主解析器
│   ├── mineru_parser.py               # MinerU 备用解析器
│   ├── structure_extractor.py         # 从 Markdown 提取层级结构
│   └── table_extractor.py             # 表格结构化提取
│
├── document_tree/
│   ├── __init__.py
│   ├── tree_builder.py                # 构建文档树
│   ├── tree_node.py                   # 树节点数据模型
│   ├── tree_store.py                  # 树存储（内存/JSON/数据库）
│   └── tree_query.py                  # 树查询接口
│
├── entity_extraction/
│   ├── __init__.py
│   ├── module_extractor.py            # 模块实体提取
│   ├── state_extractor.py             # 状态实体提取
│   ├── signal_extractor.py            # 信号实体提取
│   ├── function_extractor.py          # 功能实体提取
│   ├── parameter_extractor.py         # 参数实体提取
│   ├── fault_extractor.py             # 故障实体提取
│   └── relation_extractor.py          # 关系提取（规则+LLM辅助）
│
├── knowledge_graph/
│   ├── __init__.py
│   ├── graph_schema.py                # Neo4j Schema 定义
│   ├── graph_builder.py               # 图谱构建器
│   ├── graph_store.py                 # Neo4j 读写接口
│   ├── graph_query.py                 # 图谱查询模板
│   └── graph_traversal.py             # 遍历策略（1-hop/2-hop）
│
├── chunking/
│   ├── __init__.py
│   ├── chunker.py                     # 主切分器
│   ├── strategies/
│   │   ├── state_transition_chunker.py
│   │   ├── signal_table_chunker.py
│   │   ├── function_desc_chunker.py
│   │   ├── config_block_chunker.py
│   │   ├── fault_handling_chunker.py
│   │   └── state_machine_chunker.py
│   └── metadata_builder.py            # Metadata 构建器
│
├── vector_store/
│   ├── __init__.py
│   ├── embedding.py                   # Embedding 模型封装
│   ├── qdrant_client.py               # Qdrant 客户端
│   ├── indexer.py                     # 向量索引构建
│   └── retriever.py                   # 向量检索器
│
├── retrieval/
│   ├── __init__.py
│   ├── intent_analyzer.py             # Stage 1: 意图分析
│   ├── graph_retriever.py             # Stage 2: 图谱检索
│   ├── tree_localizer.py              # Stage 3: 目录树定位
│   ├── vector_retriever.py            # Stage 4: 向量检索
│   ├── candidate_merger.py            # Stage 5: 候选合并
│   └── pipeline.py                    # 检索流水线编排
│
├── rerank/
│   ├── __init__.py
│   ├── cross_encoder.py               # Stage 6: 语义重排 (BGE/Qwen)
│   ├── rule_reranker.py               # Stage 7: 规则重排
│   └── score_fusion.py                # 分数融合
│
├── context_compression/
│   ├── __init__.py
│   ├── deduplicator.py                # 去重
│   ├── rule_merger.py                 # 规则合并
│   ├── evidence_packager.py           # Evidence Package 组装
│   └── compressor.py                  # Stage 8: 压缩流水线
│
├── api/
│   ├── __init__.py
│   ├── server.py                      # FastAPI 服务
│   ├── routes/
│   │   ├── query.py                   # 查询接口
│   │   ├── admin.py                   # 管理接口（索引重建等）
│   │   └── health.py                  # 健康检查
│   └── models/
│       ├── request.py                 # 请求模型
│       └── response.py                # 响应模型
│
├── config/
│   ├── settings.py                    # 全局配置（Pydantic Settings）
│   ├── neo4j_config.py                # Neo4j 连接配置
│   ├── qdrant_config.py               # Qdrant 连接配置
│   └── model_config.py                # LLM/Embedding 模型配置
│
├── tests/
│   ├── test_parser/
│   ├── test_document_tree/
│   ├── test_entity_extraction/
│   ├── test_knowledge_graph/
│   ├── test_chunking/
│   ├── test_vector_store/
│   ├── test_retrieval/
│   ├── test_rerank/
│   ├── test_context_compression/
│   └── test_integration/
│
└── notebooks/
    ├── 01_document_exploration.ipynb   # 文档探索
    ├── 02_tree_building.ipynb          # 目录树构建验证
    ├── 03_entity_extraction.ipynb      # 实体提取验证
    ├── 04_graph_building.ipynb         # 图谱构建验证
    └── 05_retrieval_evaluation.ipynb   # 检索效果评估
```

### 模块依赖关系

```
config ──────────────────────────────────────────┐
                                                  │
parser ──► document_tree ──► entity_extraction ──► knowledge_graph
                                      │                    │
                                      ▼                    │
                                  chunking                 │
                                      │                    │
                                      ▼                    ▼
                                  vector_store ◄── knowledge_graph
                                      │                    │
                                      ▼                    ▼
                                  retrieval ◄──────────────┘
                                      │
                                      ▼
                                  rerank
                                      │
                                      ▼
                              context_compression
                                      │
                                      ▼
                                    api
```

### 技术栈选型

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| 文档解析 | Docling (主) + MinerU (备) | Docling 对 .docx 支持好，MinerU 对 PDF 扫描件支持好 |
| 文档树存储 | JSON 文件 + 内存缓存 | 规模可控，无需独立数据库 |
| 知识图谱 | Neo4j Community Edition | 图数据库首选，Cypher 查询表达力强 |
| 向量数据库 | Qdrant | 高性能，支持过滤检索，Rust 实现 |
| Embedding | BGE-M3 (1024d) | 中英混合语义理解，支持稀疏+稠密 |
| 重排序 | BGE-Reranker-v2-m3 | 中文重排效果好 |
| LLM | Claude API (Opus/Sonnet) | 复杂推理场景，上下文压缩，答案生成 |
| API 框架 | FastAPI | 异步支持，生态完善 |
| 依赖管理 | uv | 快速、可靠 |

---

## 附录 A: 实体提取规则（关键正则模式）

```
模块识别:
  - 匹配 H2 标题中的模块名: "车辆模式管理（VMM）" → VMM
  - 匹配 H2 标题中的英文名: "ExteriorLight", "InteriorLight", "Windows", "Locking", "TheftProtection", "Wiper", "RemoteControl"

状态识别:
  - 匹配模式定义表: 从 "3.3.1 模式定义" 等表格提取状态行
  - 匹配状态图/状态表标题: "ATWS 状态表", "室内灯控制状态表"
  - 匹配状态名模式: 大写开头英文单词 (Abandoned, Inactive, Driving, Disarmed, Armed, Alarm)

信号识别:
  - 匹配 CAN 信号表: 从 "CAN信号" 章节表格提取
  - 匹配信号名模式: [A-Z][a-zA-Z_]+ (PascalCase/snake_case)
  - 匹配 CAN ID 模式: 0x[0-9A-Fa-f]+
  - 匹配位位置模式: Bit\d+[-:]\d+

功能识别:
  - 匹配 H4/H5 标题中的功能名: "遥控解闭锁", "全局关窗", "碰撞解锁"
  - 匹配功能描述块: "触发条件", "使能条件", "执行输出"

参数识别:
  - 匹配参数名模式: Cfg[A-Z][a-zA-Z]+ 或 cfg[A-Z][a-zA-Z]+
  - 匹配配置参数章节: "NVM参数配置", "常数参数配置"

故障识别:
  - 匹配故障/报警标题: "未找到钥匙报警", "钥匙丢失报警", "位置灯故障报警"
  - 匹配故障描述块中的触发条件和响应

关系识别:
  - 状态迁移: 从 "状态迁移" 章节提取，识别 "迁移到X状态"
  - 信号触发: 从功能描述中提取信号名 → 功能名的对应
  - 信号输出: 从 "执行输出" 中提取 "发送CAN信号XXX=YYY"
  - 功能依赖: 从 "前置条件" 和 "使能条件" 中提取依赖关系
  - 跨模块引用: 从 "参考XXX功能规范" 中提取模块间引用
```

---

## 附录 B: 当前文档的已知限制

| 限制 | 影响 | 缓解方案 |
|------|------|---------|
| 状态图/流程图以图片形式嵌入 | 无法提取状态机可视化信息 | 依赖状态表和转移表文本补充；标记 `completeness: partial` |
| 部分表格解析格式不完整 | 信号表某些行列缺失 | MinerU 作为备选解析器；人工校验关键表格 |
| Docling 图片解析警告（VML 格式） | 部分嵌入图片丢失 | 安装 LibreOffice 增强 VML/EMF/WMF 支持 |
| CAN 矩阵在附录中引用 | 主文档只含部分信号，完整矩阵在外部附录 | 预留扩展接口支持外部 CAN 矩阵导入 |
| 中英混合术语不统一 | 实体链接歧义 | 建立术语同义词映射表 |
