# Chunk Schema

> BCM-RAG: Logical chunking schema for vector storage
> Principle: Chunk by logical unit, NOT by fixed token count
> Target size: 800-2000 tokens per chunk

---

## 1. Chunk Type Definitions

### 1.1 StateTransitionChunk

```yaml
chunk_type: StateTransitionChunk
description: >
  A single state transition rule within a state machine.
  Captures: source state, target state, preconditions (AND),
  trigger conditions (OR), and execution outputs.

target_size: 300-800 tokens

structure:
  state_machine: string         # State machine name, e.g. "车身模式管理"
  source_state: string          # Source state ID, e.g. "VMM_Inactive"
  target_state: string          # Target state ID, e.g. "VMM_Convenience"
  preconditions: string[]       # AND conditions
  trigger_conditions: string[]  # OR conditions
  execution_outputs: string[]   # Actions on transition
  notes: string[]               # Additional notes

source_example: "Section 3.3.4.2.2: Inactive → Convenience"
```

### 1.2 StateMachineChunk

```yaml
chunk_type: StateMachineChunk
description: >
  Complete state machine definition including all states,
  state table, and transition table. Used when the entire
  state machine is compact enough for a single chunk.

target_size: 1000-2000 tokens

structure:
  state_machine: string         # State machine name, e.g. "ATWS"
  states:                        # Array of state definitions
    - state_id: string
      name: string
      description: string
      entry_actions: string[]
      exit_actions: string[]
      internal_actions: string[]
  transitions:                   # Transition table
    - from_state: string
      to_state: string
      event: string
      conditions: string[]
  timers:                        # Associated timers
    - timer_id: string
      duration: string
      purpose: string

source_example: "Section 8.4.3-8.4.4: ATWS State Table + Transition Table"
```

### 1.3 SignalTableChunk

```yaml
chunk_type: SignalTableChunk
description: >
  A signal definition table. Contains signal names, CAN IDs,
  bit positions, value encodings, and descriptions.
  One chunk per signal table (input or output).

target_size: 500-1500 tokens

structure:
  signal_type: enum             # CAN_IN | CAN_OUT | HW_IN | HW_OUT | LIN_IN | LIN_OUT
  signals:
    - signal_id: string         # Signal name
      can_id: string            # CAN ID (nullable)
      bit_position: string      # Bit position (nullable)
      value_encoding: string    # Value meaning mapping
      description: string

source_example: "Section 3.2.2.1: VMM CAN Input Signals"
```

### 1.4 FunctionDescChunk

```yaml
chunk_type: FunctionDescChunk
description: >
  Complete description of a single function including
  trigger conditions, enable conditions, execution outputs,
  and configuration dependencies.

target_size: 400-1200 tokens

structure:
  function_name: string         # Chinese function name
  function_name_en: string      # English function name
  trigger_conditions:           # OR-connected triggers
    - condition: string
  enable_conditions:            # AND-connected enablers
    - condition: string
  execution_outputs: string[]   # Actions when function executes
  related_signals: string[]     # Signals involved
  related_configs: string[]     # Configuration parameters affecting behavior
  priority: string              # Priority relative to competing functions
  notes: string[]

source_example: "Section 7.4.2.1: Remote Unlock"
```

### 1.5 ConfigBlockChunk

```yaml
chunk_type: ConfigBlockChunk
description: >
  Configuration parameter definitions. Groups related parameters
  (NVM or Constant) that belong to the same module section.

target_size: 200-600 tokens

structure:
  config_type: enum             # NVM | CONSTANT
  parameters:
    - param_id: string          # Parameter name
      description: string
      default_value: string
      value_range: string
      affects: string[]         # Functions affected

source_example: "Section 4.2.4: ExteriorLight Configuration Parameters"
```

### 1.6 FaultHandlingChunk

```yaml
chunk_type: FaultHandlingChunk
description: >
  Fault detection and handling logic. Covers trigger conditions,
  alarm behavior, and system response.

target_size: 200-600 tokens

structure:
  fault_name: string            # Fault name
  fault_id: string              # Fault identifier
  trigger_condition: string     # When fault is triggered
  alarm_type: enum              # 仪表报警 | 灯光报警 | 声音报警 | CAN信号
  alarm_behavior: string        # How alarm manifests
  system_response: string       # What system does in response
  recovery_condition: string    # How fault clears

source_example: "Section 3.3.3.5: Key Not Found Alarm"
```

### 1.7 OutputControlChunk

```yaml
chunk_type: OutputControlChunk
description: >
  Hardware output control logic. Defines how physical outputs
  are driven based on logical conditions and priority management.

target_size: 300-800 tokens

structure:
  output_name: string           # Output signal name
  control_logic: string         # When/how output is activated
  priority_rules:               # Priority management
    - rule: string
  timing: string                # Timing requirements
  related_outputs: string[]     # Related output signals

source_example: "Section 4.5.9: Turn Signal Output Control (16 flash modes with priority)"
```

### 1.8 CrossReferenceChunk

```yaml
chunk_type: CrossReferenceChunk
description: >
  Explicit cross-module references found in the document.
  Captures which module references which other module and why.

target_size: 100-300 tokens

structure:
  source_module: string         # Module making the reference
  target_module: string         # Module being referenced
  reference_reason: string      # Why the reference exists
  reference_section: string     # Target section path
  signals_involved: string[]    # Signals crossing module boundaries

source_example: "Section 5.5.4: InteriorLight references Window's center console switch collection"
```

---

## 2. Chunk Identity

```yaml
ChunkId:
  pattern: "chunk_{module}_{seq}"
  examples:
    - "chunk_VMM_001"
    - "chunk_Lock_012"
    - "chunk_ATWS_003"

  generation:
    module: "{module abbreviation}"     # VMM, ExtLight, IntLight, Window, Lock, ATWS, Wiper, Remote
    seq: "{zero-padded 3-digit sequential number per module}"
```

---

## 3. Chunk Content

```yaml
ChunkContent:
  # Text representation for embedding
  embedding_text:
    format: >
      "[{module}] [{section_path}] [{chunk_type}]\n
      {title}\n\n
      {structured_content}"
    description: >
      Prepends module, section path, and chunk type to
      improve semantic matching. Uses structured format
      appropriate to each chunk type.

  # Raw content
  raw_text:
    type: string
    description: "Original text from document without prepended metadata"

  # Markdown content
  markdown:
    type: string
    description: "Markdown representation preserving tables and formatting"

  # Token count
  token_count:
    type: integer
    description: "Estimated token count (using cl100k_base tokenizer)"
```

---

## 4. Chunking Rules

### 4.1 Boundary Detection

```yaml
chunk_boundaries:
  primary:
    - H5 heading (SUBSUBSECTION)    # Strong boundary
    - H6 heading (LEAF)             # Strong boundary
    - Table start/end               # Table = own chunk

  secondary:
    - State transition entry (new source→target pair)
    - Function description entry (new trigger/enable/output triplet)
    - Configuration parameter group
    - Fault handling entry

  merge_triggers:
    description: >
      Adjacent small chunks (under 200 tokens each)
      of the same type within the same section are merged.
```

### 4.2 Size Constraints

```yaml
size_constraints:
  min_tokens: 200            # Below this: merge with adjacent same-type chunk
  target_min: 800            # Ideal minimum
  target_max: 2000           # Ideal maximum
  hard_max: 3000             # Above this: split at next logical boundary

  splitting_strategy:
    description: >
      When a section exceeds hard_max tokens, split at the
      next available heading level or table boundary.
      Never split mid-table or mid-transition.
```

### 4.3 Overlap Policy

```yaml
overlap:
  default: 0                 # No overlap between chunks
  exception: >
    StateMachineChunks may overlap with individual
    StateTransitionChunks. In this case, the StateMachineChunk
    provides the overview, and individual StateTransitionChunks
    provide detailed retrieval targets.
```

---

## 5. Chunk Type Distribution (Estimated)

| Chunk Type | Count (Estimated) | Avg Tokens |
|------------|-------------------|------------|
| StateTransitionChunk | 40-60 | 500 |
| StateMachineChunk | 4-6 | 1500 |
| SignalTableChunk | 30-40 | 800 |
| FunctionDescChunk | 60-80 | 700 |
| ConfigBlockChunk | 15-20 | 400 |
| FaultHandlingChunk | 15-25 | 350 |
| OutputControlChunk | 20-30 | 500 |
| CrossReferenceChunk | 5-10 | 200 |
| **Total** | **~200-270** | — |
