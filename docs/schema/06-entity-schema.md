# Entity Schema

> BCM-RAG: Entity type definitions for knowledge graph construction
> Reference: CLAUDE.md Layer 2 — Knowledge Graph entity types

---

## 1. Entity Registry

| # | Entity | Label | Graph Node | Count (Est.) | Priority |
|---|--------|-------|------------|-------------|----------|
| 1 | Module | 模块 | `(:Module)` | 8 | CRITICAL |
| 2 | State | 状态 | `(:State)` | 30-40 | CRITICAL |
| 3 | Signal | 信号 | `(:Signal)` | 200-300 | CRITICAL |
| 4 | Function | 功能 | `(:Function)` | 80-120 | CRITICAL |
| 5 | Parameter | 参数 | `(:Parameter)` | 50-80 | HIGH |
| 6 | Fault | 故障 | `(:Fault)` | 15-25 | HIGH |
| 7 | PowerMode | 电源模式 | `(:PowerMode)` | 4 | HIGH |
| 8 | Timer | 计时器 | `(:Timer)` | 10-15 | MEDIUM |

---

## 2. Entity Definitions

### 2.1 Module

```yaml
entity: Module
label: 模块
description: >
  A BCM functional module. Modules are the top-level
  organizational units of the specification document.
  Each module owns states, signals, functions, parameters, and faults.

graph_label: Module

fields:
  entity_id:      { type: keyword,  required: true, unique: true,  pattern: "mod_{module_id}" }
  module_id:      { type: keyword,  required: true, unique: true,  description: "Short module identifier" }
  name:           { type: text,     required: true,                description: "Chinese display name" }
  name_en:        { type: text,     required: true,                description: "English display name" }
  chapter:        { type: keyword,  required: true,                description: "Chapter number in spec" }
  description:    { type: text,     required: true,                description: "Module functional overview" }
  page_start:     { type: integer,  required: false,               description: "Start page" }
  page_end:       { type: integer,  required: false,               description: "End page" }
  source_section: { type: keyword,  required: true,                description: "Defining section" }

instances:
  - module_id: "VMM"
    name: "车辆模式管理"
    name_en: "Vehicle Mode Management"
    chapter: "3"
  - module_id: "ExteriorLight"
    name: "外灯"
    name_en: "Exterior Light"
    chapter: "4"
  - module_id: "InteriorLight"
    name: "内灯"
    name_en: "Interior Light"
    chapter: "5"
  - module_id: "Window"
    name: "车窗"
    name_en: "Windows"
    chapter: "6"
  - module_id: "Lock"
    name: "锁"
    name_en: "Locking"
    chapter: "7"
  - module_id: "TheftProtection"
    name: "防盗"
    name_en: "Theft Protection"
    chapter: "8"
  - module_id: "Wiper"
    name: "雨刮"
    name_en: "Wiper"
    chapter: "9"
  - module_id: "RemoteControl"
    name: "远程控制"
    name_en: "Remote Control"
    chapter: "10"

extraction_source:
  - H2 headings containing module names
  - Section 1.2 (Table of Contents) for chapter mapping
```

### 2.2 State

```yaml
entity: State
label: 状态
description: >
  A state within a state machine. States represent discrete
  operational modes of a module or subsystem. Each state
  has entry/exit/internal actions and belongs to exactly one
  state machine within one module.

graph_label: State

fields:
  entity_id:         { type: keyword,  required: true, unique: true,  pattern: "state_{module_id}_{name}" }
  state_id:          { type: keyword,  required: true, unique: true,  description: "Unique state identifier" }
  name:              { type: text,     required: true,                description: "State name" }
  module_id:         { type: keyword,  required: true,                description: "Owning module" }
  state_machine:     { type: keyword,  required: true,                description: "State machine name" }
  description:       { type: text,     required: true,                description: "State description" }
  entry_actions:     { type: text[],   required: false,               description: "Actions on state entry" }
  exit_actions:      { type: text[],   required: false,               description: "Actions on state exit" }
  internal_actions:  { type: text[],   required: false,               description: "Actions while in state" }
  timers:            { type: keyword[],required: false,               description: "Associated timer IDs" }
  is_initial:        { type: boolean,  required: false, default: false }
  is_terminal:       { type: boolean,  required: false, default: false }
  source_section:    { type: keyword,  required: true }

instances:
  # VMM States
  - state_id: "VMM_Abandoned"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "最终休眠模式，最低系统功耗"
  - state_id: "VMM_Inactive"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "典型休息模式，有限功能可用"
  - state_id: "VMM_Convenience"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "便利功能可用，需有效钥匙"
  - state_id: "VMM_Driving"
    module_id: "VMM"
    state_machine: "车身模式管理"
    description: "动力系统启动，全功能可用"

  # ATWS States
  - state_id: "ATWS_Disarmed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "解防状态"
  - state_id: "ATWS_Prearmed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "预设防状态（等待计时器超时）"
  - state_id: "ATWS_Armed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "设防状态"
  - state_id: "ATWS_Alarm"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "报警状态"
  - state_id: "ATWS_PartiallyArmed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "部分设防（后备箱释放后）"
  - state_id: "ATWS_Remind"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "提醒状态（门未全关）"
  - state_id: "ATWS_Predisarmed"
    module_id: "TheftProtection"
    state_machine: "ATWS"
    description: "预解除设防状态"

  # InteriorLight States
  - state_id: "InteriorLight_Off"
    module_id: "InteriorLight"
    state_machine: "室内灯控制"
  - state_id: "InteriorLight_On"
    module_id: "InteriorLight"
    state_machine: "室内灯控制"
  - state_id: "InteriorLight_FadeIn"
    module_id: "InteriorLight"
    state_machine: "室内灯控制"
  - state_id: "InteriorLight_FadeOut"
    module_id: "InteriorLight"
    state_machine: "室内灯控制"

extraction_source:
  - Mode definition tables (e.g. Section 3.3.1)
  - State machine state tables (e.g. Section 8.4.3 ATWS)
  - State machine state diagrams (from tables, not images)
  - H4/H5 headings containing state names
```

### 2.3 Signal

```yaml
entity: Signal
label: 信号
description: >
  A CAN bus signal, hardware input/output signal, or LIN bus signal.
  Signals carry data between modules or between the BCM and vehicle hardware.
  Each signal has a defined encoding mapping raw values to semantic meanings.

graph_label: Signal

fields:
  entity_id:       { type: keyword,  required: true, unique: true,  pattern: "sig_{signal_id}" }
  signal_id:       { type: keyword,  required: true, unique: true,  description: "Signal name" }
  signal_type:     { type: keyword,  required: true,                description: "CAN_IN|CAN_OUT|HW_IN|HW_OUT|LIN_IN|LIN_OUT" }
  can_id:          { type: keyword,  required: false,               description: "CAN message ID, e.g. 0x1E2" }
  bit_position:    { type: keyword,  required: false,               description: "Bit position, e.g. Bit34-32" }
  value_encoding:  { type: text,     required: false,               description: "Value encoding mapping" }
  module_id:       { type: keyword,  required: true,                description: "Owning/receiving module" }
  source_module:   { type: keyword,  required: false,               description: "Signal source module (for inputs)" }
  target_module:   { type: keyword,  required: false,               description: "Signal target module (for outputs)" }
  description:     { type: text,     required: true,                description: "Signal description" }
  source_section:  { type: keyword,  required: true }

signal_type_rules:
  CAN_IN:   { can_id: required, bit_position: required, source_module: required }
  CAN_OUT:  { can_id: required, bit_position: required, target_module: required }
  HW_IN:    { can_id: null,     bit_position: null,     description: "Hardware input pin signal" }
  HW_OUT:   { can_id: null,     bit_position: null,     description: "Hardware output pin signal" }
  LIN_IN:   { can_id: null,     bit_position: null,     source_module: "LIN slave device" }
  LIN_OUT:  { can_id: null,     bit_position: null,     target_module: "LIN slave device" }

instances:
  - signal_id: "PEPS_UsageMode"
    signal_type: "CAN_OUT"
    can_id: "0x1E2"
    bit_position: "Bit34-32"
    value_encoding: "0x0:Inactive 0x1:Convenience 0x2:Driving"
    module_id: "VMM"
    description: "车辆使用模式状态"

  - signal_id: "VCU_ePTReady"
    signal_type: "CAN_IN"
    can_id: "0x165"
    value_encoding: "0x0:Inactive 0x1:Active"
    module_id: "VMM"
    source_module: "VCU"
    description: "动力使能成功状态"

  - signal_id: "BCM_ATWS_St"
    signal_type: "CAN_OUT"
    can_id: "0x284"
    value_encoding: "0x0:Armed 0x1:Prearmed 0x2:Disarmed 0x3:Remind 0x4:Alarm 0x5:PartiallyArmed 0x6:Predisarmed"
    module_id: "TheftProtection"
    description: "防盗系统状态"

  - signal_id: "ESC_VehicleSpeed"
    signal_type: "CAN_IN"
    can_id: "(from ESC module)"
    module_id: "Lock"
    source_module: "ESC"
    description: "车速信号，用于车速自动闭锁"

extraction_source:
  - Signal definition tables in each module's "系统信号" section
  - CAN input/output signal tables
  - Hardware input/output signal tables
  - LIN input/output signal tables
  - Inline signal references in function descriptions (e.g. "发送CAN信号PEPS_UsageMode=0x1")

naming_patterns:
  CAN_signals: "^[A-Z][a-zA-Z0-9_]*$"        # PascalCase with underscores
  HW_signals:  "^(HW_)?[A-Z][a-zA-Z0-9_]*$"  # Often prefixed with HW_
  integrated:  "^(All|Window|L_)[A-Z][a-zA-Z]*$"  # Integrated signal names
```

### 2.4 Function

```yaml
entity: Function
label: 功能
description: >
  A BCM feature or function. Functions encapsulate a specific
  vehicle behavior, defined by trigger conditions, enable conditions,
  execution outputs, and configuration dependencies.

graph_label: Function

fields:
  entity_id:           { type: keyword,  required: true, unique: true,  pattern: "func_{function_id}" }
  function_id:         { type: keyword,  required: true, unique: true,  description: "Short function identifier" }
  name:                { type: text,     required: true,                description: "Chinese function name" }
  name_en:             { type: text,     required: true,                description: "English function name" }
  module_id:           { type: keyword,  required: true,                description: "Owning module" }
  section_path:        { type: keyword,  required: true,                description: "Document section" }
  description:         { type: text,     required: true,                description: "Function description" }
  trigger_conditions:  { type: text[],   required: false,               description: "OR-connected triggers" }
  enable_conditions:   { type: text[],   required: false,               description: "AND-connected enablers" }
  outputs:             { type: text[],   required: false,               description: "Execution outputs" }
  priority:            { type: integer,  required: false,               description: "Priority among competing functions" }
  is_safety_related:   { type: boolean,  required: false, default: false }
  source_section:      { type: keyword,  required: true }

instances:
  # Window functions
  - function_id: "GlobalClose"
    name: "全局关窗"
    name_en: "Global Window Close"
    module_id: "Window"
    section_path: "6.4.5"
    description: "通过遥控钥匙或中控触发全部车窗关闭"

  - function_id: "GlobalOpen"
    name: "全局开窗"
    name_en: "Global Window Open"
    module_id: "Window"
    section_path: "6.4.6"

  # Lock functions
  - function_id: "RemoteUnlock"
    name: "遥控解锁"
    name_en: "Remote Unlock"
    module_id: "Lock"
    section_path: "7.4.2.1"

  - function_id: "RemoteLock"
    name: "遥控闭锁"
    name_en: "Remote Lock"
    module_id: "Lock"
    section_path: "7.4.2.3"

  - function_id: "CrashUnlock"
    name: "碰撞解锁"
    name_en: "Crash Unlock"
    module_id: "Lock"
    section_path: "7.4.7"
    is_safety_related: true

  - function_id: "AutoLock"
    name: "车速自动闭锁"
    name_en: "Speed Auto Lock"
    module_id: "Lock"
    section_path: "7.4.8"

  - function_id: "AutoRelock"
    name: "自动重新上锁"
    name_en: "Automatic Re-lock"
    module_id: "Lock"
    section_path: "7.4.9"

  - function_id: "ParkingUnlock"
    name: "驻车自动解锁"
    name_en: "Parking Auto Unlock"
    module_id: "Lock"
    section_path: "7.4.6"

  # ExteriorLight functions
  - function_id: "FollowMeHome"
    name: "伴我回家灯"
    name_en: "Follow Me Home"
    module_id: "ExteriorLight"
    section_path: "4.3.8"

  - function_id: "PositionLight"
    name: "位置灯"
    name_en: "Position Light"
    module_id: "ExteriorLight"
    section_path: "4.3.2"

  - function_id: "LowBeam"
    name: "近光灯"
    name_en: "Low Beam"
    module_id: "ExteriorLight"
    section_path: "4.3.3"

  - function_id: "DRL"
    name: "日间行车灯"
    name_en: "Daytime Running Light"
    module_id: "ExteriorLight"
    section_path: "4.3.7"

  # Wiper functions
  - function_id: "FrontWiperLowSpeed"
    name: "前雨刮低速"
    name_en: "Front Wiper Low Speed"
    module_id: "Wiper"
    section_path: "9.4.3.2"

extraction_source:
  - H4/H5 headings in "功能描述" sections
  - Trigger condition / Enable condition / Execution output triples
  - Function overview tables
```

### 2.5 Parameter

```yaml
entity: Parameter
label: 参数
description: >
  A configuration or calibration parameter. Parameters control
  function behavior variants (e.g. switch type, timer durations,
  feature enable/disable flags).

graph_label: Parameter

fields:
  entity_id:          { type: keyword,  required: true, unique: true,  pattern: "param_{param_id}" }
  param_id:           { type: keyword,  required: true, unique: true,  description: "Parameter name" }
  param_type:         { type: keyword,  required: true,                description: "NVM|CONSTANT|CALIBRATION" }
  module_id:          { type: keyword,  required: true,                description: "Owning module" }
  description:        { type: text,     required: true,                description: "Parameter description" }
  default_value:      { type: text,     required: false,               description: "Default value" }
  value_range:        { type: text,     required: false,               description: "Valid value range" }
  affects_functions:  { type: keyword[],required: false,               description: "Functions affected" }
  is_critical:        { type: boolean,  required: false, default: false }
  source_section:     { type: keyword,  required: true }

param_type_definitions:
  NVM:         "Non-Volatile Memory — persisted across power cycles"
  CONSTANT:    "Compile-time constant — fixed per vehicle configuration"
  CALIBRATION: "Runtime calibration — adjustable within defined range"

instances:
  - param_id: "CfgTCMEOLOption"
    param_type: "CONSTANT"
    module_id: "ExteriorLight"
    description: "组合开关EOL配置（0x1=硬线自锁, 0x2=CAN TCM报文）"
    affects_functions: ["外灯开关输入", "位置灯", "近光灯", "后雾灯"]

  - param_id: "cfgUnlockType"
    param_type: "CONSTANT"
    module_id: "Lock"
    description: "解锁类型（舒适模式/安全模式）"
    affects_functions: ["遥控解锁"]

  - param_id: "cfgDoorLatchDuration"
    param_type: "CALIBRATION"
    module_id: "Lock"
    description: "门锁电机驱动持续时间"
    affects_functions: ["遥控解锁", "遥控闭锁", "中控解锁", "中控闭锁"]

  - param_id: "cfgAPWLOption"
    param_type: "CONSTANT"
    module_id: "Window"
    description: "车窗类型配置（0x0=不使用, 0x1=LIN无防夹, 0x3=LIN四门防夹）"
    affects_functions: ["全局关窗", "全局开窗"]

  - param_id: "cfgCentralLockSWOption"
    param_type: "CONSTANT"
    module_id: "Lock"
    description: "中控锁开关选项（10=自锁, 11=非自锁）"
    affects_functions: ["中控开关解锁", "中控开关闭锁"]

extraction_source:
  - "NVM参数配置" sub-sections
  - "常数参数配置" sub-sections
  - Inline parameter references in function conditions (Cfg* and cfg* patterns)

naming_pattern:
  NVM_params:  "^(Cfg|cfg)[A-Z][a-zA-Z0-9]*$"     # PascalCase with Cfg/cfg prefix
  calibration: "^(cfg)[a-z][a-zA-Z0-9]*$"          # camelCase with cfg prefix
```

### 2.6 Fault

```yaml
entity: Fault
label: 故障
description: >
  A fault condition that the BCM can detect and respond to.
  Faults include sensor failures, signal timeouts, mechanical
  issues, and security violations.

graph_label: Fault

fields:
  entity_id:          { type: keyword,  required: true, unique: true,  pattern: "fault_{fault_id}" }
  fault_id:           { type: keyword,  required: true, unique: true,  description: "Fault identifier" }
  name:               { type: text,     required: true,                description: "Chinese fault name" }
  module_id:          { type: keyword,  required: true,                description: "Owning module" }
  trigger_condition:  { type: text,     required: true,                description: "Fault trigger condition" }
  response:           { type: text,     required: true,                description: "System response" }
  alarm_type:         { type: keyword,  required: true,                description: "仪表报警|灯光报警|声音报警|CAN信号" }
  severity:           { type: keyword,  required: false,               description: "CRITICAL|MAJOR|MINOR|INFO" }
  recovery_condition: { type: text,     required: false,               description: "Fault recovery condition" }
  source_section:     { type: keyword,  required: true }

instances:
  - fault_id: "KeyNotFound"
    name: "未找到钥匙"
    module_id: "VMM"
    trigger_condition: "Inactive模式非解防踩刹车或Convenience踩制动，10秒内未检测到钥匙"
    alarm_type: "仪表报警"
    severity: "MAJOR"

  - fault_id: "KeyLost"
    name: "钥匙丢失"
    module_id: "VMM"
    trigger_condition: "行驶中有效钥匙信号丢失"
    alarm_type: "仪表报警"
    severity: "CRITICAL"

  - fault_id: "IGN1Failure"
    name: "IGN1失效报警"
    module_id: "VMM"
    trigger_condition: "IGN1继电器驱动输出与反馈不一致"
    alarm_type: "仪表报警"

  - fault_id: "WindowJam"
    name: "车窗防夹"
    module_id: "Window"
    trigger_condition: "车窗运行中检测到阻力超过阈值"
    response: "车窗反转"
    alarm_type: "灯光报警"
    severity: "MINOR"

  - fault_id: "PositionLampFault"
    name: "位置灯故障"
    module_id: "ExteriorLight"
    trigger_condition: "位置灯输出开路/短路"
    alarm_type: "仪表报警"

  - fault_id: "EngineImmobilizerFail"
    name: "发动机防盗认证失败"
    module_id: "TheftProtection"
    trigger_condition: "认证失败信号"
    alarm_type: "CAN信号"
    severity: "CRITICAL"

extraction_source:
  - "内部状态判断及报警" sub-sections
  - Fault diagnosis sections
  - "故障报警" headings
  - Function descriptions containing fault/error handling
```

### 2.7 PowerMode

```yaml
entity: PowerMode
label: 电源模式
description: >
  Vehicle power/usage mode. A cross-module concept that defines
  which functions are available at each operational level.
  Corresponds to the PEPS_UsageMode CAN signal values.

graph_label: PowerMode

fields:
  entity_id:              { type: keyword,  required: true, unique: true,  pattern: "pm_{mode_id}" }
  mode_id:                { type: keyword,  required: true, unique: true,  description: "Mode identifier" }
  name:                   { type: text,     required: true,                description: "Mode display name" }
  peaps_usage_mode:       { type: keyword,  required: true,                description: "PEPS_UsageMode signal value" }
  description:            { type: text,     required: true,                description: "Mode description" }
  available_functions:    { type: keyword[],required: false,               description: "Functions available in this mode" }
  source_section:         { type: keyword,  required: true }

instances:
  - mode_id: "Abandoned"
    name: "Abandoned"
    peaps_usage_mode: "(not transmitted — bus asleep)"
    description: "最终休眠模式，网络休眠，最低系统功耗"

  - mode_id: "Inactive"
    name: "Inactive"
    peaps_usage_mode: "0x0"
    description: "典型休息模式（OFF），有限功能可用，无需有效钥匙"

  - mode_id: "Convenience"
    name: "Convenience"
    peaps_usage_mode: "0x1"
    description: "便利模式（Crank/ON），便利功能可用，必须检测并批准钥匙"

  - mode_id: "Driving"
    name: "Driving"
    peaps_usage_mode: "0x2"
    description: "行驶模式（ON），动力系统启动，全功能可用"

extraction_source:
  - Section 3.3.1: Mode Definition table
```

### 2.8 Timer

```yaml
entity: Timer
label: 计时器
description: >
  A timer used in state machines and function logic.
  Timers control durations for pre-arming, alarming,
  key detection, and other time-dependent behaviors.

graph_label: Timer

fields:
  entity_id:       { type: keyword,  required: true, unique: true,  pattern: "tmr_{timer_id}" }
  timer_id:        { type: keyword,  required: true, unique: true,  description: "Timer identifier" }
  module_id:       { type: keyword,  required: true,                description: "Owning module" }
  duration:        { type: text,     required: false,               description: "Timer duration" }
  purpose:         { type: text,     required: true,                description: "Timer purpose" }
  source_section:  { type: keyword,  required: true }

instances:
  - timer_id: "ATWSPrearmingTimer"
    module_id: "TheftProtection"
    purpose: "预设防计时器（Disarmed→Prearmed后等待超时→Armed）"

  - timer_id: "ATWSAlarmingTimer"
    module_id: "TheftProtection"
    purpose: "报警持续时间计时器"

  - timer_id: "CfgATWSRemindingTimer"
    module_id: "TheftProtection"
    purpose: "提醒计时器（闭锁成功但门未全关时）"

  - timer_id: "KeyDetectionTimer"
    module_id: "TheftProtection"
    purpose: "钥匙检测超时计时器"

  - timer_id: "PredisarmedTimer"
    module_id: "TheftProtection"
    purpose: "预解除设防计时器"

  - timer_id: "DoorkeepingTimer"
    module_id: "TheftProtection"
    purpose: "门状态保持计时器"

  - timer_id: "ConvenienceTimeout"
    module_id: "VMM"
    duration: "30分钟"
    purpose: "Convenience模式超时（默认30分钟无操作→Inactive）"

  - timer_id: "StartRequestTimeout"
    module_id: "VMM"
    duration: "15秒"
    purpose: "启动请求超时"

  - timer_id: "KeyValidTimeout"
    module_id: "VMM"
    duration: "6秒"
    purpose: "非Driving模式下钥匙有效保持时间"

extraction_source:
  - State transition descriptions (timers referenced in conditions/outputs)
  - State table entry/exit actions (timer start/stop)
  - Function timeout descriptions
```

---

## 3. Entity Identification Rules

### 3.1 Priority Order

```
1. Module     — Extract first (structure)
2. State      — Extract from mode definitions and state tables
3. Signal     — Extract from signal tables
4. Parameter  — Extract from config sections
5. Function   — Extract from function description sections
6. Fault      — Extract from alarm/fault sections
7. PowerMode  — Hardcoded (fixed 4 modes)
8. Timer      — Extract from state transitions and function descriptions
```

### 3.2 Cross-Entity Resolution

```yaml
resolution_rules:
  - After extraction, resolve entity references:
    - Signal names in function trigger_conditions → link to Signal entities
    - Parameter names in function enable_conditions → link to Parameter entities
    - State names in transition descriptions → link to State entities
    - Module names in cross-module references → link to Module entities

  - Unresolved references (e.g. signals from external ECUs):
    - Create stub entities with source_module="EXTERNAL"
    - Mark confidence < 1.0
    - Flag for manual review
```

### 3.3 Entity ID Generation

```yaml
id_patterns:
  Module:     "mod_{module_id}"                    # mod_VMM
  State:      "state_{module_id}_{name}"           # state_VMM_Driving
  Signal:     "sig_{signal_id}"                    # sig_PEPS_UsageMode
  Function:   "func_{function_id}"                 # func_GlobalClose
  Parameter:  "param_{param_id}"                   # param_CfgTCMEOLOption
  Fault:      "fault_{fault_id}"                   # fault_KeyLost
  PowerMode:  "pm_{mode_id}"                       # pm_Driving
  Timer:      "tmr_{timer_id}"                     # tmr_ATWSPrearmingTimer
```
