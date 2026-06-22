# Metadata Schema

> BCM-RAG: Chunk metadata field definitions
> Purpose: Define every metadata field attached to chunks, document tree nodes, and graph entities
> Reference: CLAUDE.md Layer 3 requirements

---

## 1. Chunk Metadata

### 1.1 Core Identity Fields

```yaml
chunk_id:
  type: keyword
  required: true
  unique: true
  pattern: "chunk_{module}_{seq}"
  description: "Unique chunk identifier"
  example: "chunk_VMM_012"

chunk_type:
  type: enum
  required: true
  values:
    - StateTransitionChunk
    - StateMachineChunk
    - SignalTableChunk
    - FunctionDescChunk
    - ConfigBlockChunk
    - FaultHandlingChunk
    - OutputControlChunk
    - CrossReferenceChunk
  description: "Logical chunk type determining content structure"
```

### 1.2 Document Position Fields

```yaml
module:
  type: keyword
  required: true
  values:
    - VMM
    - ExteriorLight
    - InteriorLight
    - Window
    - Lock
    - TheftProtection
    - Wiper
    - RemoteControl
  description: "Owning BCM functional module"
  indexed: true

section_path:
  type: keyword
  required: true
  pattern: "^\\d+(\\.\\d+)*$"
  description: "Full document section path"
  example: "3.3.4.2.2"
  indexed: true

parent_section:
  type: keyword
  required: true
  pattern: "^\\d+(\\.\\d+)*$"
  description: "Immediate parent section path"
  example: "3.3.4"
  indexed: true

page_start:
  type: integer
  required: false
  min: 1
  description: "Start page number in original .docx"
  example: 28

page_end:
  type: integer
  required: false
  min: 1
  description: "End page number in original .docx"
  example: 29
```

### 1.3 Content Tag Fields

```yaml
function:
  type: keyword
  required: false
  description: "Primary function name this chunk belongs to"
  example: "遥控解锁"
  indexed: true

functions:
  type: keyword[]
  required: false
  description: "All function names referenced in this chunk"
  example: ["遥控解锁", "遥控闭锁"]
  indexed: true

states:
  type: keyword[]
  required: false
  description: "State IDs referenced in this chunk (prefixed with module)"
  example: ["VMM_Inactive", "VMM_Convenience"]
  indexed: true

signals:
  type: keyword[]
  required: false
  description: "Signal IDs referenced in this chunk"
  example: ["PEPS_UsageMode", "PEPS_PowerMode", "PEPS_IGN1RelaySts"]
  indexed: true

parameters:
  type: keyword[]
  required: false
  description: "Parameter IDs referenced in this chunk"
  example: ["CfgTCMEOLOption", "cfgDoorLatchDuration"]
  indexed: true

faults:
  type: keyword[]
  required: false
  description: "Fault IDs referenced in this chunk"
  example: ["KeyLost", "SignalTimeout"]
  indexed: true
```

### 1.4 Graph Linkage Fields

```yaml
graph_node_ids:
  type: keyword[]
  required: false
  description: >
    Neo4j entity node IDs linked to this chunk.
    Enables bidirectional navigation between graph and vector store.
  example: ["state_VMM_Inactive", "state_VMM_Convenience", "func_RemoteUnlock"]
  indexed: false
```

### 1.5 Cross-Module Fields

```yaml
cross_module_refs:
  type: keyword[]
  required: false
  description: "Other module IDs referenced by this chunk"
  example: ["PEPS", "VCU"]
  indexed: true
```

### 1.6 Quality Fields

```yaml
has_table:
  type: boolean
  required: true
  description: "Whether chunk contains a table"
  default: false
  indexed: true

has_state_machine:
  type: boolean
  required: true
  description: "Whether chunk contains state machine definitions"
  default: false
  indexed: true

has_condition:
  type: boolean
  required: true
  description: "Whether chunk contains trigger/enable conditional logic"
  default: false
  indexed: true

completeness:
  type: enum
  required: true
  values: [complete, partial]
  description: >
    complete = all content successfully extracted.
    partial = content references images that could not be extracted,
    or tables that were partially parsed.
  default: complete
  indexed: true
```

### 1.7 Temporal Fields

```yaml
created_at:
  type: datetime
  required: true
  format: ISO 8601
  description: "Chunk creation timestamp"
  example: "2026-06-15T10:30:00Z"

updated_at:
  type: datetime
  required: true
  format: ISO 8601
  description: "Chunk last modification timestamp"
  example: "2026-06-15T10:30:00Z"
```

---

## 2. Document Tree Node Metadata

### 2.1 Required Fields

```yaml
# Fields inherited from TreeNode Schema (see 03-document-tree-schema.md)
# Key metadata fields repeated here for cross-reference:

node_id:         keyword    # UNIQUE, e.g. "sec3.3"
node_type:       keyword    # ROOT|CHAPTER|SECTION|SUBSECTION|SUBSUBSECTION|LEAF|TABLE|CONTENT
title:           text       # Section heading text
level:           integer    # Nesting depth 0-6
section_number:  keyword    # e.g. "3.3.4"
parent_id:       keyword    # Parent node_id
```

### 2.2 Content Association

```yaml
content_type:
  type: enum
  values: [state_machine, signal_table, function_desc, config_block, fault_block, mixed, none]
  description: "Primary content type classification at this tree node"

tables:
  type: keyword[]
  description: "Table node_ids in this subtree"

chunk_ids:
  type: keyword[]
  description: "Chunk IDs derived from this node's content"

graph_node_ids:
  type: keyword[]
  description: "Neo4j entity node IDs extracted from this node"
```

---

## 3. Graph Entity Metadata

### 3.1 Common Entity Fields

```yaml
# All graph entities share these metadata fields:

entity_id:
  type: keyword
  required: true
  unique: true
  description: "Unique entity identifier"
  pattern: "{label}_{name}"    # e.g. "state_VMM_Driving", "func_GlobalClose"

entity_type:
  type: enum
  required: true
  values: [Module, State, Signal, Function, Parameter, Fault, PowerMode, Timer]
  description: "Entity type classification"

module_id:
  type: keyword
  required: true
  description: "Owning module (or 'SYSTEM' for cross-module entities)"
  example: "VMM"

source_section:
  type: keyword
  required: true
  description: "Document section where entity is defined"
  example: "3.3.1"

source_chunk_id:
  type: keyword
  required: false
  description: "Chunk ID where entity was extracted from"
  example: "chunk_VMM_005"

confidence:
  type: float
  required: false
  range: [0.0, 1.0]
  description: "Extraction confidence score"
  default: 1.0
```

### 3.2 Entity-Specific Metadata

```yaml
State:
  state_machine: keyword      # State machine name
  is_initial: boolean         # Whether this is the initial state
  is_terminal: boolean        # Whether this is a terminal state

Signal:
  signal_type: keyword        # CAN_IN | CAN_OUT | HW_IN | HW_OUT | LIN_IN | LIN_OUT
  can_id: keyword             # CAN message ID
  bit_position: keyword       # Bit position in CAN message

Function:
  priority: integer           # Execution priority (lower = higher priority)
  is_safety_related: boolean  # Whether function has safety implications

Parameter:
  param_type: keyword         # NVM | CONSTANT | CALIBRATION
  is_critical: boolean        # Whether parameter affects safety functions

Fault:
  alarm_type: keyword         # 仪表报警 | 灯光报警 | 声音报警 | CAN信号
  severity: keyword           # CRITICAL | MAJOR | MINOR | INFO
```

---

## 4. Retrieval Pipeline Metadata

### 4.1 Intent Analysis Output

```yaml
IntentResult:
  intent_type: enum           # STATE_QUERY | TRANSITION_QUERY | SIGNAL_QUERY | FUNCTION_QUERY | DEPENDENCY_QUERY | FAULT_QUERY | CONFIG_QUERY | CROSS_MODULE_QUERY | COMPARISON_QUERY
  target_modules: keyword[]   # Identified target module IDs
  target_entities:            # Extracted entity references
    - entity_type: enum       # Module|State|Signal|Function|Parameter|Fault
      entity_id: keyword
      confidence: float
  target_relations: keyword[] # Inferred relationship types
  original_query: text        # Raw user query
  rewritten_query: text       # Query after entity expansion
```

### 4.2 Evidence Package Metadata

```yaml
EvidencePackage:
  query: text                 # Original query
  intent: keyword             # Intent type
  compression_ratio: float    # tokens_before / tokens_after
  source_chunks: keyword[]    # Source chunk IDs
  source_graph_nodes: keyword[] # Source graph node IDs
  confidence: float           # Overall answer confidence [0, 1]
  generated_at: datetime      # Timestamp
  pipeline_duration_ms: integer  # Total pipeline latency
```

---

## 5. Metadata Validation Rules

```yaml
validation:
  chunk_id:
    - required
    - unique across all chunks
    - matches pattern "chunk_{module}_{seq}"

  module:
    - required
    - must be one of the 8 defined module IDs

  section_path:
    - required
    - must match pattern "^\d+(\.\d+)*$"
    - must correspond to an existing document tree node

  chunk_type:
    - required
    - must be one of the 8 defined chunk types

  graph_node_ids:
    - each ID must correspond to an existing Neo4j node (eventual consistency)

  completeness:
    - required
    - if "partial", chunk should have non-empty notes explaining what's missing

  cross-module validation:
    - cross_module_refs must NOT contain the chunk's own module
    - signals in cross_module_refs should match signals that cross module boundaries
```

---

## 6. Metadata Lifecycle

```yaml
creation:
  - All required fields must be populated at chunk creation time
  - graph_node_ids may be empty initially (populated after entity extraction)
  - completeness is determined by the parser

update:
  - graph_node_ids updated when new entities are extracted
  - updated_at refreshed on any metadata change
  - module, section_path, chunk_type are immutable after creation

deletion:
  - Chunk deletion cascades to Qdrant point deletion
  - Chunk deletion does NOT cascade to Neo4j (entities may be shared)
  - Orphaned graph_node_ids should be flagged for review
```
