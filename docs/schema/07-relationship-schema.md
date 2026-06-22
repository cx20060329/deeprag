# Relationship Schema

> BCM-RAG: Relationship type definitions for knowledge graph
> Reference: CLAUDE.md Layer 2 — Relationship Types

---

## 1. Relationship Registry

| # | Relationship | Direction | Source → Target | Count (Est.) | Priority |
|---|-------------|-----------|-----------------|-------------|----------|
| 1 | CONTAINS | → | Module → Function/State | 100-150 | CRITICAL |
| 2 | OWNS | → | Module → Signal/Parameter/Fault | 250-350 | CRITICAL |
| 3 | TRANSITION_TO | → | State → State | 50-80 | CRITICAL |
| 4 | TRIGGERS | → | Signal → Function/State | 100-150 | CRITICAL |
| 5 | OUTPUTS | → | Function/State → Signal | 100-150 | CRITICAL |
| 6 | DEPENDS_ON | → | Function → Function/State/Signal | 80-120 | CRITICAL |
| 7 | REQUIRES | → | Function → PowerMode | 80-100 | HIGH |
| 8 | CONTROLS | → | Signal → Signal | 20-30 | HIGH |
| 9 | CONFIGURES | → | Parameter → Function | 50-80 | HIGH |
| 10 | REPORTS | → | Function → Fault | 15-25 | MEDIUM |
| 11 | REFERENCES | → | Module → Module | 10-15 | MEDIUM |

---

## 2. Relationship Definitions

### 2.1 CONTAINS

```yaml
relationship: CONTAINS
category: structural
description: >
  A Module contains a Function or State (state machine).
  This is the primary structural relationship that organizes
  entities under their owning module.

direction: (Module)-[:CONTAINS]->(Function|State)

source:
  label: Module
  cardinality: one

target:
  labels: [Function, State]
  cardinality: many

properties: none

semantics:
  - A Function MUST be contained by exactly one Module
  - A State MUST be contained by exactly one Module
  - Deleting a Module cascades to its contained Functions and States

examples:
  - (mod_Window)-[:CONTAINS]->(func_GlobalClose)
  - (mod_VMM)-[:CONTAINS]->(state_VMM_Driving)
  - (mod_TheftProtection)-[:CONTAINS]->(state_ATWS_Armed)

query_template: |
  MATCH (m:Module {module_id: $module_id})-[:CONTAINS]->(contained)
  RETURN labels(contained) AS type, contained
```

### 2.2 OWNS

```yaml
relationship: OWNS
category: structural
description: >
  A Module owns a Signal, Parameter, or Fault.
  This is a weaker structural relationship than CONTAINS —
  signals may cross module boundaries, but each has a primary owner.

direction: (Module)-[:OWNS]->(Signal|Parameter|Fault)

source:
  label: Module
  cardinality: one

target:
  labels: [Signal, Parameter, Fault]
  cardinality: many

properties: none

semantics:
  - A Signal MUST have exactly one owning Module (the module that defines it)
  - A Parameter MUST have exactly one owning Module
  - A Fault MUST have exactly one owning Module
  - The owner is the module where the entity is defined in the spec

examples:
  - (mod_VMM)-[:OWNS]->(sig_PEPS_UsageMode)
  - (mod_ExteriorLight)-[:OWNS]->(param_CfgTCMEOLOption)
  - (mod_VMM)-[:OWNS]->(fault_KeyLost)
```

### 2.3 TRANSITION_TO

```yaml
relationship: TRANSITION_TO
category: behavioral
description: >
  A state transition in a state machine.
  Defines how the system moves from one state to another
  under specific conditions.

direction: (State)-[:TRANSITION_TO]->(State)

source:
  label: State
  cardinality: one (source may have many outgoing transitions)

target:
  label: State
  cardinality: one (target may have many incoming transitions)

properties:
  conditions:
    type: STRING[]
    required: true
    description: "Transition conditions (OR logic between array items)"
    example: ["主驾侧门打开且车辆处于解防状态", "制动踏板被踩下"]

  preconditions:
    type: STRING[]
    required: false
    description: "Preconditions that must ALL be true (AND logic)"
    example: ["处于Inactive状态"]

  outputs:
    type: STRING[]
    required: false
    description: "Actions executed when transition occurs"
    example:
      - "迁移到Convenience状态"
      - "IGN1继电器驱动，输出高电平打开继电器"
      - "发送CAN信号PEPS_UsageMode=0x1:Convenience"

  priority:
    type: INTEGER
    required: false
    description: "Priority among competing transitions (lower = higher priority)"

semantics:
  - Source and target states MUST belong to the same state machine
  - Conditions represent OR logic (any condition can trigger)
  - Preconditions represent AND logic (all must be satisfied)

examples:
  - source: state_VMM_Inactive
    target: state_VMM_Convenience
    conditions: ["车辆处于解防状态", "车辆处于非解防状态且钥匙有效"]
    preconditions: ["处于Inactive状态", "主驾侧门打开且车辆处于解防状态", "制动踏板被踩下"]
    outputs: ["迁移到Convenience状态", "IGN1继电器驱动高电平", "发送PEPS_UsageMode=0x1:Convenience"]

  - source: state_ATWS_Prearmed
    target: state_ATWS_Armed
    conditions: ["预设防计时器时长超时"]

query_template: |
  MATCH (s:State {state_id: $state_id})-[r:TRANSITION_TO]->(t:State)
  RETURN s.name AS from_state, t.name AS to_state, r.conditions, r.preconditions, r.outputs
  ORDER BY r.priority;
```

### 2.4 TRIGGERS

```yaml
relationship: TRIGGERS
category: causal
description: >
  A Signal triggers (activates/influences) a Function or State change.
  This captures how external signals drive system behavior.

direction: (Signal)-[:TRIGGERS]->(Function|State)

source:
  label: Signal
  cardinality: many (one signal may trigger many functions/states)

target:
  labels: [Function, State]
  cardinality: many (one function may be triggered by many signals)

properties:
  condition:
    type: STRING
    required: false
    description: "Specific signal value or condition that triggers"
    example: "PEPS_UsageMode == 0x1 (Convenience)"

semantics:
  - TRIGGERS implies causality: signal change → function activation or state change
  - If a signal with a specific value triggers, capture that in condition
  - Different from DEPENDS_ON: TRIGGERS is about activation, DEPENDS_ON is about prerequisites

examples:
  - (sig_PEPS_UsageMode)-[:TRIGGERS {condition: "value == 0x2"}]->(func_GlobalClose)
  - (sig_ESC_VehicleSpeed)-[:TRIGGERS {condition: "speed > threshold"}]->(func_AutoLock)
  - (sig_VCU_ePTReady)-[:TRIGGERS {condition: "value == 0x1"}]->(state_VMM_Driving)
```

### 2.5 OUTPUTS

```yaml
relationship: OUTPUTS
category: causal
description: >
  A Function or State outputs (sends/sets) a Signal.
  This captures how system behavior produces observable outputs.

direction: (Function|State)-[:OUTPUTS]->(Signal)

source:
  labels: [Function, State]
  cardinality: many

target:
  label: Signal
  cardinality: many (one signal may be output by multiple functions)

properties:
  value:
    type: STRING
    required: false
    description: "Output signal value"
    example: "0x1:Convenience"

  description:
    type: STRING
    required: false
    description: "Output context description"

semantics:
  - OUTPUTS implies the function/state actively sets the signal value
  - Often paired with TRIGGERS: Signal A TRIGGERS Function, Function OUTPUTS Signal B
  - Value field captures the specific value set (when applicable)

examples:
  - (func_RemoteUnlock)-[:OUTPUTS {description: "驱动门锁电机执行解锁"}]->(sig_DoorLockMotor)
  - (state_VMM_Driving)-[:OUTPUTS {value: "0x2"}]->(sig_PEPS_UsageMode)
  - (state_ATWS_Alarm)-[:OUTPUTS {value: "0x4"}]->(sig_BCM_ATWS_St)
```

### 2.6 DEPENDS_ON

```yaml
relationship: DEPENDS_ON
category: dependency
description: >
  A Function depends on another Function, State, or Signal.
  This captures prerequisite relationships — the function
  cannot execute unless the dependency is satisfied.

direction: (Function)-[:DEPENDS_ON]->(Function|State|Signal)

source:
  label: Function
  cardinality: many

target:
  labels: [Function, State, Signal]
  cardinality: many

properties:
  dependency_type:
    type: STRING
    required: true
    values: [precondition, trigger, enable]
    description: >
      precondition = must be true before function can start
      trigger = causes function to execute
      enable = allows function to execute (but doesn't trigger it)

  is_critical:
    type: BOOLEAN
    required: false
    default: true
    description: "Whether function fails without this dependency"

semantics:
  - DEPENDS_ON is a directed dependency edge
  - Used in graph traversal for dependency chain discovery
  - Different from TRIGGERS: TRIGGERS is Signal→Function, DEPENDS_ON is Function→anything

examples:
  - (func_GlobalClose)-[:DEPENDS_ON {dependency_type: "precondition"}]->(func_WindowEnable)
  - (func_GlobalClose)-[:DEPENDS_ON {dependency_type: "precondition"}]->(state_VMM_Driving)
  - (func_AutoLock)-[:DEPENDS_ON {dependency_type: "trigger"}]->(sig_ESC_VehicleSpeed)
  - (func_RemoteUnlock)-[:DEPENDS_ON {dependency_type: "enable"}]->(func_MotorThermalProtection)

query_template: |
  MATCH path = (f:Function {function_id: $function_id})
               -[:DEPENDS_ON|REQUIRES*1..2]->(dep)
  RETURN path;
```

### 2.7 REQUIRES

```yaml
relationship: REQUIRES
category: constraint
description: >
  A Function requires a specific PowerMode to be active.
  This captures power mode constraints that gate function availability.

direction: (Function)-[:REQUIRES]->(PowerMode)

source:
  label: Function
  cardinality: many (one function may require multiple power modes, OR logic)

target:
  label: PowerMode
  cardinality: many

properties:
  condition:
    type: STRING
    required: false
    description: "Additional condition beyond power mode"
    example: "车速小于X公里/时"

semantics:
  - Multiple REQUIRES from same function = OR logic (any of these modes)
  - If a function needs multiple power modes simultaneously, use condition property instead
  - This is a specialized form of DEPENDS_ON for PowerMode entities

examples:
  - (func_RemoteUnlock)-[:REQUIRES]->(pm_Inactive)
  - (func_RemoteUnlock)-[:REQUIRES {condition: "远程控制模式激活"}]->(pm_Inactive)
  - (func_GlobalClose)-[:REQUIRES]->(pm_Convenience)
  - (func_GlobalClose)-[:REQUIRES]->(pm_Driving)
  - (func_CrashUnlock)-[:REQUIRES]->(pm_Abandoned)
  - (func_CrashUnlock)-[:REQUIRES]->(pm_Inactive)
  - (func_CrashUnlock)-[:REQUIRES]->(pm_Convenience)
  - (func_CrashUnlock)-[:REQUIRES]->(pm_Driving)

query_template: |
  MATCH (f:Function)-[:REQUIRES]->(pm:PowerMode)
  RETURN f.name AS function, collect(pm.name) AS required_modes;
```

### 2.8 CONTROLS

```yaml
relationship: CONTROLS
category: causal
description: >
  A Signal controls (determines the value of) another Signal.
  Used primarily for integrated/composite signals whose value
  is derived from multiple source signals.

direction: (Signal)-[:CONTROLS]->(Signal)

source:
  label: Signal
  cardinality: many

target:
  label: Signal
  cardinality: many

properties:
  logic:
    type: STRING
    required: true
    description: "Control logic description"
    example: "AllWindowClosedSts = AND(L_Drv_Wdw_PositionSts==0x2, L_Psa_Wdw_PositionSts==0x2, ...)"

semantics:
  - CONTROLS implies the source signal's value directly determines the target signal's value
  - Used for integrated signal decomposition
  - Enables tracing: "which raw signals feed into this integrated signal?"

examples:
  - (sig_L_Drv_Wdw_PositionSts)-[:CONTROLS {logic: "==0x2 (Completely close)"}]->(sig_AllWindowClosedSts)
  - (sig_L_Psa_Wdw_PositionSts)-[:CONTROLS {logic: "==0x2 (Completely close)"}]->(sig_AllWindowClosedSts)
  - (sig_L_Drv_Wdw_OD_Sts)-[:CONTROLS {logic: "==0x1 (In Anti-pinch)"}]->(sig_WindowAntiPinchSts)
```

### 2.9 CONFIGURES

```yaml
relationship: CONFIGURES
category: dependency
description: >
  A Parameter configures (affects the behavior of) a Function.
  Changing the parameter changes how the function behaves.

direction: (Parameter)-[:CONFIGURES]->(Function)

source:
  label: Parameter
  cardinality: many (one parameter may configure many functions)

target:
  label: Function
  cardinality: many (one function may be configured by many parameters)

properties:
  effect:
    type: STRING
    required: true
    description: "How the parameter affects the function"
    example: "Determines switch input type (hardwire self-locking vs CAN TCM)"

semantics:
  - CONFIGURES is different from DEPENDS_ON: CONFIGURES is about behavioral variation, DEPENDS_ON is about prerequisites
  - A parameter change does not prevent the function from executing — it changes HOW it executes

examples:
  - (param_CfgTCMEOLOption)-[:CONFIGURES {effect: "Determines switch type: 0x1=硬线自锁, 0x2=CAN TCM"}]->(func_PositionLight)
  - (param_cfgUnlockType)-[:CONFIGURES {effect: "Determines unlock mode: 舒适模式 vs 安全模式"}]->(func_RemoteUnlock)
  - (param_cfgAPWLOption)-[:CONFIGURES {effect: "0x1=LIN无防夹, 0x3=LIN四门防夹"}]->(func_GlobalClose)
  - (param_cfgCentralLockSWOption)-[:CONFIGURES {effect: "10=自锁, 11=非自锁"}]->(func_CentralUnlockSwitch)
```

### 2.10 REPORTS

```yaml
relationship: REPORTS
category: fault
description: >
  A Function detects and reports a Fault condition.
  This captures the fault detection and reporting chain.

direction: (Function)-[:REPORTS]->(Fault)

source:
  label: Function
  cardinality: many

target:
  label: Fault
  cardinality: many (one fault may be reported by multiple functions)

properties:
  detection_method:
    type: STRING
    required: true
    description: "How the function detects this fault"
    example: "IGN1继电器驱动输出与反馈不一致超过阈值时间"

semantics:
  - REPORTS links functions to the faults they detect
  - A function may report multiple faults
  - A fault may be reported by multiple functions (different detection contexts)

examples:
  - (func_IGN1Control)-[:REPORTS {detection_method: "驱动输出与反馈不一致"}]->(fault_IGN1Failure)
  - (func_KeyAuthentication)-[:REPORTS {detection_method: "LF搜索+Transponder搜索均未找到钥匙"}]->(fault_KeyNotFound)
  - (func_WindowOperation)-[:REPORTS {detection_method: "车窗运行中检测到阻力超过阈值"}]->(fault_WindowJam)
```

### 2.11 REFERENCES

```yaml
relationship: REFERENCES
category: cross_module
description: >
  A Module explicitly references another Module in its specification.
  Captures documented cross-module dependencies and interactions.

direction: (Module)-[:REFERENCES]->(Module)

source:
  label: Module
  cardinality: many

target:
  label: Module
  cardinality: many

properties:
  reason:
    type: STRING
    required: true
    description: "Why the cross-module reference exists"

  signal_ids:
    type: STRING[]
    required: false
    description: "Signals involved in the cross-module interaction"

  section_path:
    type: STRING
    required: false
    description: "Section where the reference is documented"

semantics:
  - REFERENCES is a documented, intentional cross-module link
  - Different from implicit dependencies captured by other relationships
  - Used to discover module interaction topology

examples:
  - (mod_InteriorLight)-[:REFERENCES {
      reason: "参考车窗功能规范的中控车窗开关采集章节",
      section_path: "5.5.4"
    }]->(mod_Window)

  - (mod_Window)-[:REFERENCES {
      reason: "防夹报警需灯光模块闪烁提醒",
      signal_ids: ["WindowAntiPinchWarningReq"]
    }]->(mod_ExteriorLight)

  - (mod_Lock)-[:REFERENCES {
      reason: "电源模式判断依赖VMM的PEPS_UsageMode信号",
      signal_ids: ["PEPS_UsageMode"]
    }]->(mod_VMM)
```

---

## 3. Relationship Cardinality Summary

```yaml
cardinality_matrix:
  CONTAINS:
    source: { min: 1, max: 1 }       # Function/State has exactly 1 owning Module
    target: { min: 0, max: n }       # Module may have 0+ Functions/States

  OWNS:
    source: { min: 1, max: 1 }       # Signal/Param/Fault has exactly 1 owning Module
    target: { min: 0, max: n }

  TRANSITION_TO:
    source: { min: 0, max: n }       # State may have 0+ outgoing transitions
    target: { min: 0, max: n }       # State may have 0+ incoming transitions

  TRIGGERS:
    source: { min: 0, max: n }       # Signal may trigger 0+ functions/states
    target: { min: 0, max: n }       # Function may be triggered by 0+ signals

  OUTPUTS:
    source: { min: 0, max: n }       # Function may output 0+ signals
    target: { min: 0, max: n }       # Signal may be output by 0+ functions

  DEPENDS_ON:
    source: { min: 0, max: n }       # Function may depend on 0+ entities
    target: { min: 0, max: n }       # Entity may be depended on by 0+ functions

  REQUIRES:
    source: { min: 0, max: n }       # Function may require 0+ power modes
    target: { min: 0, max: n }       # PowerMode may be required by 0+ functions

  CONTROLS:
    source: { min: 0, max: n }       # Signal may control 0+ signals
    target: { min: 0, max: n }       # Signal may be controlled by 0+ signals

  CONFIGURES:
    source: { min: 0, max: n }       # Parameter may configure 0+ functions
    target: { min: 0, max: n }       # Function may be configured by 0+ parameters

  REPORTS:
    source: { min: 0, max: n }       # Function may report 0+ faults
    target: { min: 0, max: n }       # Fault may be reported by 0+ functions

  REFERENCES:
    source: { min: 0, max: n }       # Module may reference 0+ modules
    target: { min: 0, max: n }       # Module may be referenced by 0+ modules
```

---

## 4. Relationship Extraction Rules

### 4.1 Structural (CONTAINS, OWNS)

```yaml
extraction:
  CONTAINS:
    - Every Function and State entity → CONTAINS from its module_id Module
    - Automatic after entity extraction
    - No text pattern matching needed

  OWNS:
    - Every Signal, Parameter, Fault entity → OWNS from its module_id Module
    - Automatic after entity extraction
    - No text pattern matching needed
```

### 4.2 Behavioral (TRANSITION_TO)

```yaml
extraction:
  source_sections:
    - "状态迁移" sub-sections (e.g. 3.3.4, 8.4.4)
    - State transition tables
  
  patterns:
    - "迁移到{target_state}状态" → TRANSITION_TO
    - Transition table rows: (from_state, to_state, conditions)
  
  condition_parsing:
    - Lines under "前置条件（&&）：" → preconditions (AND)
    - Lines under "触发条件（||）：" → conditions (OR)
    - Lines under "执行输出：" → outputs
```

### 4.3 Causal (TRIGGERS, OUTPUTS, CONTROLS)

```yaml
extraction:
  TRIGGERS:
    patterns:
      - "接收到CAN信号{signal_id}" → TRIGGERS
      - "{signal_id}=0x{value}" → TRIGGERS with condition
      - "收到{signal_id}" → TRIGGERS
    source: Function description "触发条件" sections

  OUTPUTS:
    patterns:
      - "发送CAN信号{signal_id}={value}" → OUTPUTS
      - "BCM发送CAN信号{signal_id}" → OUTPUTS
      - "驱动{output_name}" → OUTPUTS
    source: Function description "执行输出" sections

  CONTROLS:
    patterns:
      - Integrated signal definitions (e.g. "AllWindowClosedSts = AND(...)")
      - Explicit "controls/determines" language
    source: Integrated signal definition sections (e.g. 6.4.2)
```

### 4.4 Dependency (DEPENDS_ON, REQUIRES, CONFIGURES)

```yaml
extraction:
  DEPENDS_ON:
    patterns:
      - Function references another function in preconditions → DEPENDS_ON (precondition)
      - Function references a state in preconditions → DEPENDS_ON (precondition)
      - "电源模式为{inactive/convenience/driving}" → REQUIRES (not DEPENDS_ON)
    source: Function "使能条件" and "前置条件" sections

  REQUIRES:
    patterns:
      - "电源模式为{inactive/convenience/driving}" → REQUIRES
      - "处于远控模式" → REQUIRES (Inactive with condition)
      - "整车熄火（电源模式为Inactive）" → REQUIRES
    source: Function "触发条件" and "使能条件" sections

  CONFIGURES:
    patterns:
      - "配置{Cfg*/cfg*}" in function descriptions → CONFIGURES
      - "配置字{param_name}为{value}" → CONFIGURES
    source: Function descriptions referencing parameters
```

### 4.5 Fault (REPORTS)

```yaml
extraction:
  patterns:
    - Function description section → alarm/fault detection → REPORTS
    - "报警" headings under function sections
    - Fault name referenced in function's trigger_conditions or outputs
  source: "内部状态判断及报警" and fault diagnosis sections
```

### 4.6 Cross-Module (REFERENCES)

```yaml
extraction:
  patterns:
    - "参考{module}功能规范的{section}" → REFERENCES
    - "{module}模块" in another module's section → REFERENCES
    - Signal output from one module consumed by another → REFERENCES with signal_ids
  source: Cross-module reference sections, signal source_module/target_module fields
```

---

## 5. Graph Traversal Patterns

### 5.1 Dependency Chain Discovery

```yaml
pattern: dependency_chain
description: "Discover all entities a function depends on (2-hop)"

cypher: |
  MATCH path = (f:Function {function_id: $function_id})
               -[:DEPENDS_ON|REQUIRES|TRIGGERS*1..2]->(related)
  RETURN path;

use_case: "What does GlobalClose depend on?"
```

### 5.2 Impact Analysis

```yaml
pattern: impact_analysis
description: "Discover all functions affected by a signal change (reverse 2-hop)"

cypher: |
  MATCH path = (sig:Signal {signal_id: $signal_id})
               -[:TRIGGERS|CONTROLS*1..2]->(affected)
  RETURN path;

use_case: "What happens if PEPS_UsageMode changes?"
```

### 5.3 State Reachability

```yaml
pattern: state_reachability
description: "Find all states reachable from a given state"

cypher: |
  MATCH path = (s:State {state_id: $state_id})
               -[:TRANSITION_TO*1..3]->(reachable:State)
  RETURN path;

use_case: "From Inactive, what states can we reach?"
```

### 5.4 Module Interaction Map

```yaml
pattern: module_interactions
description: "Discover all interactions between two modules"

cypher: |
  MATCH (m1:Module {module_id: $module_1})-[r:REFERENCES]->(m2:Module {module_id: $module_2})
  OPTIONAL MATCH (sig1:Signal {module_id: $module_1})-[:TRIGGERS]->(f2:Function {module_id: $module_2})
  OPTIONAL MATCH (f1:Function {module_id: $module_1})-[:OUTPUTS]->(sig2:Signal {module_id: $module_2})
  RETURN m1, m2, r, collect(DISTINCT sig1) AS outgoing_signals, collect(DISTINCT sig2) AS incoming_signals;

use_case: "How does VMM interact with Lock?"
```

### 5.5 Full Entity Context

```yaml
pattern: entity_context
description: "Get all relationships for any entity"

cypher: |
  MATCH (e {entity_id: $entity_id})-[r]-(related)
  RETURN e, type(r) AS relationship_type, r, related;

use_case: "Show me everything about CrashUnlock"
```
