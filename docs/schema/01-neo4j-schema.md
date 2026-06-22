# Neo4j Graph Schema

> BCM-RAG Knowledge Graph Layer 2
> Database: Neo4j Community Edition
> Purpose: Represent logical relationships between automotive BCM entities

---

## 1. Node Labels

### 1.1 Module

```yaml
Label: Module
Description: BCM functional module
Properties:
  module_id:    STRING    # UNIQUE, e.g. "VMM", "ExteriorLight", "Lock"
  name:         STRING    # Chinese name, e.g. "车辆模式管理"
  name_en:      STRING    # English name, e.g. "Vehicle Mode Management"
  chapter:      STRING    # Chapter number, e.g. "3"
  description:  STRING    # Module overview
  page_start:   INTEGER   # Start page
  page_end:     INTEGER   # End page
Constraints:
  - UNIQUE (module_id)
  - EXISTS (module_id)
```

### 1.2 State

```yaml
Label: State
Description: State machine state
Properties:
  state_id:         STRING    # UNIQUE, e.g. "VMM_Driving", "ATWS_Armed"
  name:             STRING    # State name, e.g. "Driving"
  module_id:        STRING    # Owning module
  state_machine:    STRING    # State machine name, e.g. "车身模式管理", "ATWS"
  description:      STRING    # State description
  entry_actions:    LIST<STRING>   # Actions on entry
  exit_actions:     LIST<STRING>   # Actions on exit
  internal_actions: LIST<STRING>   # Internal actions
  timers:           LIST<STRING>   # Associated timers
Constraints:
  - UNIQUE (state_id)
  - EXISTS (state_id)
  - EXISTS (module_id)
```

### 1.3 Signal

```yaml
Label: Signal
Description: CAN/Hardware/LIN signal
Properties:
  signal_id:       STRING    # UNIQUE, e.g. "PEPS_UsageMode", "ESC_VehicleSpeed"
  signal_type:     STRING    # ENUM: CAN_IN | CAN_OUT | HW_IN | HW_OUT | LIN_IN | LIN_OUT
  can_id:          STRING    # CAN ID, e.g. "0x1E2" (nullable for non-CAN signals)
  bit_position:    STRING    # Bit position, e.g. "Bit34-32" (nullable)
  value_encoding:  STRING    # Value encoding, e.g. "0x0:Inactive 0x1:Convenience 0x2:Driving"
  module_id:       STRING    # Owning module
  source_module:   STRING    # Signal source module (for input signals)
  target_module:   STRING    # Signal target module (for output signals)
  description:     STRING    # Signal description
Constraints:
  - UNIQUE (signal_id)
  - EXISTS (signal_id)
  - EXISTS (signal_type)
```

### 1.4 Function

```yaml
Label: Function
Description: BCM feature/function
Properties:
  function_id:        STRING    # UNIQUE, e.g. "GlobalClose", "CrashUnlock"
  name:               STRING    # Chinese name, e.g. "全局关窗"
  name_en:            STRING    # English name, e.g. "Global Window Close"
  module_id:          STRING    # Owning module
  section_path:       STRING    # Document section path, e.g. "6.4.5"
  description:        STRING    # Function description
  trigger_conditions: LIST<STRING>   # Trigger conditions (OR logic)
  enable_conditions:  LIST<STRING>   # Enable conditions (AND logic)
  outputs:            LIST<STRING>   # Execution outputs
  priority:           INTEGER   # Priority among competing functions
Constraints:
  - UNIQUE (function_id)
  - EXISTS (function_id)
  - EXISTS (module_id)
```

### 1.5 Parameter

```yaml
Label: Parameter
Description: Configuration/calibration parameter
Properties:
  param_id:           STRING    # UNIQUE, e.g. "CfgTCMEOLOption", "cfgDoorLatchDuration"
  param_type:         STRING    # ENUM: NVM | CONSTANT | CALIBRATION
  module_id:          STRING    # Owning module
  description:        STRING    # Parameter description
  default_value:      STRING    # Default value
  value_range:        STRING    # Value range
  affects_functions:  LIST<STRING>   # Functions affected by this parameter
Constraints:
  - UNIQUE (param_id)
  - EXISTS (param_id)
```

### 1.6 Fault

```yaml
Label: Fault
Description: Fault/diagnosis entity
Properties:
  fault_id:           STRING    # UNIQUE, e.g. "KeyLost", "WindowJam"
  name:               STRING    # Chinese name, e.g. "钥匙丢失"
  module_id:          STRING    # Owning module
  trigger_condition:  STRING    # Trigger condition
  response:           STRING    # System response
  alarm_type:         STRING    # ENUM: 仪表报警 | 灯光报警 | 声音报警 | CAN信号
Constraints:
  - UNIQUE (fault_id)
  - EXISTS (fault_id)
```

### 1.7 PowerMode

```yaml
Label: PowerMode
Description: Vehicle power mode (cross-module concept)
Properties:
  mode_id:            STRING    # UNIQUE, e.g. "Abandoned", "Inactive", "Convenience", "Driving"
  name:               STRING    # Display name
  peaps_usage_mode:   STRING    # Corresponding PEPS_UsageMode signal value
  description:        STRING    # Mode description
  available_functions: LIST<STRING>   # Functions available in this mode
Constraints:
  - UNIQUE (mode_id)
```

### 1.8 Timer

```yaml
Label: Timer
Description: Timer used in state machines and functions
Properties:
  timer_id:    STRING    # UNIQUE, e.g. "ATWSPrearmingTimer", "KeyDetectionTimer"
  module_id:   STRING    # Owning module
  duration:    STRING    # Duration description
  purpose:     STRING    # Timer purpose
Constraints:
  - UNIQUE (timer_id)
```

---

## 2. Relationship Types

### 2.1 CONTAINS

```yaml
Type: CONTAINS
Direction: (Module)-[:CONTAINS]->(Function)
           (Module)-[:CONTAINS]->(State)
Description: Module contains a function or state machine
```

### 2.2 OWNS

```yaml
Type: OWNS
Direction: (Module)-[:OWNS]->(Signal)
           (Module)-[:OWNS]->(Parameter)
           (Module)-[:OWNS]->(Fault)
Description: Module owns a signal/parameter/fault
```

### 2.3 TRANSITION_TO

```yaml
Type: TRANSITION_TO
Direction: (State)-[:TRANSITION_TO]->(State)
Properties:
  conditions:     LIST<STRING>   # Transition conditions (OR logic)
  preconditions:  LIST<STRING>   # Preconditions (AND logic)
  outputs:        LIST<STRING>   # Actions executed on transition
  priority:       INTEGER        # Priority level
Description: State machine transition between states
```

### 2.4 TRIGGERS

```yaml
Type: TRIGGERS
Direction: (Signal)-[:TRIGGERS]->(Function)
           (Signal)-[:TRIGGERS]->(State)
Properties:
  condition: STRING    # Trigger condition description
Description: Signal triggers a function or state change
```

### 2.5 OUTPUTS

```yaml
Type: OUTPUTS
Direction: (Function)-[:OUTPUTS]->(Signal)
           (State)-[:OUTPUTS]->(Signal)
Properties:
  value: STRING    # Output signal value
Description: Function or state outputs a signal
```

### 2.6 CONTROLS

```yaml
Type: CONTROLS
Direction: (Signal)-[:CONTROLS]->(Signal)
Properties:
  logic: STRING    # Control logic description
Description: Signal controls another signal (e.g. integrated signals)
```

### 2.7 DEPENDS_ON

```yaml
Type: DEPENDS_ON
Direction: (Function)-[:DEPENDS_ON]->(Function)
           (Function)-[:DEPENDS_ON]->(State)
           (Function)-[:DEPENDS_ON]->(Signal)
Properties:
  dependency_type: STRING    # ENUM: precondition | trigger | enable
  is_critical:     BOOLEAN   # Whether dependency is critical
Description: Function depends on another entity
```

### 2.8 REQUIRES

```yaml
Type: REQUIRES
Direction: (Function)-[:REQUIRES]->(PowerMode)
Properties:
  condition: STRING    # Additional condition (nullable)
Description: Function requires a specific power mode
```

### 2.9 CONFIGURES

```yaml
Type: CONFIGURES
Direction: (Parameter)-[:CONFIGURES]->(Function)
Properties:
  effect: STRING    # How the parameter affects the function
Description: Parameter configures function behavior
```

### 2.10 REPORTS

```yaml
Type: REPORTS
Direction: (Function)-[:REPORTS]->(Fault)
Properties:
  detection_method: STRING    # How the fault is detected
Description: Function detects and reports a fault
```

### 2.11 REFERENCES

```yaml
Type: REFERENCES
Direction: (Module)-[:REFERENCES]->(Module)
Properties:
  reason:      STRING    # Reason for cross-module reference
  signal_ids:  LIST<STRING>   # Signals involved in the reference
Description: Cross-module reference
```

---

## 3. Constraints

```cypher
// Uniqueness constraints
CREATE CONSTRAINT module_id_unique     IF NOT EXISTS FOR (m:Module)     REQUIRE m.module_id IS UNIQUE;
CREATE CONSTRAINT state_id_unique      IF NOT EXISTS FOR (s:State)      REQUIRE s.state_id IS UNIQUE;
CREATE CONSTRAINT signal_id_unique     IF NOT EXISTS FOR (s:Signal)     REQUIRE s.signal_id IS UNIQUE;
CREATE CONSTRAINT function_id_unique   IF NOT EXISTS FOR (f:Function)   REQUIRE f.function_id IS UNIQUE;
CREATE CONSTRAINT param_id_unique      IF NOT EXISTS FOR (p:Parameter)  REQUIRE p.param_id IS UNIQUE;
CREATE CONSTRAINT fault_id_unique      IF NOT EXISTS FOR (f:Fault)      REQUIRE f.fault_id IS UNIQUE;
CREATE CONSTRAINT powermode_id_unique  IF NOT EXISTS FOR (p:PowerMode)  REQUIRE p.mode_id IS UNIQUE;
CREATE CONSTRAINT timer_id_unique      IF NOT EXISTS FOR (t:Timer)      REQUIRE t.timer_id IS UNIQUE;

// Existence constraints
CREATE CONSTRAINT module_id_exists     IF NOT EXISTS FOR (m:Module)     REQUIRE m.module_id IS NOT NULL;
CREATE CONSTRAINT state_id_exists      IF NOT EXISTS FOR (s:State)      REQUIRE s.state_id IS NOT NULL;
CREATE CONSTRAINT signal_id_exists     IF NOT EXISTS FOR (s:Signal)     REQUIRE s.signal_id IS NOT NULL;
CREATE CONSTRAINT function_id_exists   IF NOT EXISTS FOR (f:Function)   REQUIRE f.function_id IS NOT NULL;
CREATE CONSTRAINT param_id_exists      IF NOT EXISTS FOR (p:Parameter)  REQUIRE p.param_id IS NOT NULL;
CREATE CONSTRAINT fault_id_exists      IF NOT EXISTS FOR (f:Fault)      REQUIRE f.fault_id IS NOT NULL;
```

---

## 4. Indexes

```cypher
// Single-property indexes for frequent lookup fields
CREATE INDEX module_chapter_idx        IF NOT EXISTS FOR (m:Module)     ON (m.chapter);
CREATE INDEX state_module_idx          IF NOT EXISTS FOR (s:State)      ON (s.module_id);
CREATE INDEX state_machine_idx         IF NOT EXISTS FOR (s:State)      ON (s.state_machine);
CREATE INDEX signal_module_idx         IF NOT EXISTS FOR (s:Signal)     ON (s.module_id);
CREATE INDEX signal_type_idx           IF NOT EXISTS FOR (s:Signal)     ON (s.signal_type);
CREATE INDEX signal_can_id_idx         IF NOT EXISTS FOR (s:Signal)     ON (s.can_id);
CREATE INDEX function_module_idx       IF NOT EXISTS FOR (f:Function)   ON (f.module_id);
CREATE INDEX param_module_idx          IF NOT EXISTS FOR (p:Parameter)  ON (p.module_id);
CREATE INDEX param_type_idx            IF NOT EXISTS FOR (p:Parameter)  ON (p.param_type);
CREATE INDEX fault_module_idx          IF NOT EXISTS FOR (f:Fault)      ON (f.module_id);

// Composite indexes
CREATE INDEX state_module_machine_idx  IF NOT EXISTS FOR (s:State)      ON (s.module_id, s.state_machine);
CREATE INDEX signal_module_type_idx    IF NOT EXISTS FOR (s:Signal)     ON (s.module_id, s.signal_type);

// Text indexes for search
CREATE TEXT INDEX module_name_text     IF NOT EXISTS FOR (m:Module)     ON (m.name, m.name_en);
CREATE TEXT INDEX signal_desc_text     IF NOT EXISTS FOR (s:Signal)     ON (s.description);
CREATE TEXT INDEX function_name_text   IF NOT EXISTS FOR (f:Function)   ON (f.name, f.name_en);
```

---

## 5. Query Templates

### 5.1 Dependency Chain (2-hop)

```cypher
// Find all dependencies of a function up to 2 hops
MATCH path = (f:Function {function_id: $function_id})
             -[:DEPENDS_ON|REQUIRES|TRIGGERS*1..2]->(related)
RETURN path;
```

### 5.2 State Transitions

```cypher
// Find all possible transitions from a state
MATCH (s:State {state_id: $state_id})-[r:TRANSITION_TO]->(target:State)
RETURN s, r, target
ORDER BY r.priority;
```

### 5.3 Signal Impact Analysis

```cypher
// Find all entities affected by a signal (2-hop)
MATCH (sig:Signal {signal_id: $signal_id})
      -[:TRIGGERS|CONTROLS*1..2]->(affected)
RETURN DISTINCT labels(affected) AS entity_type, affected;
```

### 5.4 Module Cross-References

```cypher
// Find all cross-module references
MATCH (m1:Module)-[r:REFERENCES]->(m2:Module)
RETURN m1.name AS source_module, m2.name AS target_module, r.reason;
```

### 5.5 Functions by PowerMode

```cypher
// Find all functions requiring a specific power mode
MATCH (f:Function)-[:REQUIRES]->(pm:PowerMode {mode_id: $mode_id})
RETURN f.name, f.module_id;
```

### 5.6 Full State Machine

```cypher
// Retrieve a complete state machine with all transitions
MATCH (s:State {state_machine: $machine_name})-[r:TRANSITION_TO]->(t:State)
RETURN s, r, t;
```

### 5.7 Function Full Context

```cypher
// Get complete context for a function (dependencies, signals, parameters)
MATCH (f:Function {function_id: $function_id})
OPTIONAL MATCH (f)-[:DEPENDS_ON]->(dep)
OPTIONAL MATCH (f)-[:REQUIRES]->(pm:PowerMode)
OPTIONAL MATCH (sig:Signal)-[:TRIGGERS]->(f)
OPTIONAL MATCH (f)-[:OUTPUTS]->(out_sig:Signal)
OPTIONAL MATCH (param:Parameter)-[:CONFIGURES]->(f)
OPTIONAL MATCH (f)-[:REPORTS]->(fault:Fault)
RETURN f, collect(DISTINCT dep) AS dependencies,
          collect(DISTINCT pm) AS power_modes,
          collect(DISTINCT sig) AS trigger_signals,
          collect(DISTINCT out_sig) AS output_signals,
          collect(DISTINCT param) AS parameters,
          collect(DISTINCT fault) AS faults;
```

---

## 6. Graph Statistics (Estimated)

| Node Label | Estimated Count |
|------------|----------------|
| Module     | 8              |
| State      | 30-40          |
| Signal     | 200-300        |
| Function   | 80-120         |
| Parameter  | 50-80          |
| Fault      | 15-25          |
| PowerMode  | 4              |
| Timer      | 10-15          |
| **Total**  | **~400-600**   |

| Relationship Type | Estimated Count |
|-------------------|----------------|
| CONTAINS          | 100-150         |
| OWNS              | 250-350         |
| TRANSITION_TO     | 50-80           |
| TRIGGERS          | 100-150         |
| OUTPUTS           | 100-150         |
| CONTROLS          | 20-30           |
| DEPENDS_ON        | 80-120          |
| REQUIRES          | 80-100          |
| CONFIGURES        | 50-80           |
| REPORTS           | 15-25           |
| REFERENCES        | 10-15           |
| **Total**         | **~850-1250**   |
