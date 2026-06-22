# Qdrant Collection Schema

> BCM-RAG Vector Index Layer 3
> Database: Qdrant
> Purpose: Store semantic embeddings of logically-chunked BCM specification content

---

## 1. Collection Definition

### 1.1 Collection: `bcm_chunks`

```yaml
collection_name: bcm_chunks
description: >
  Primary collection storing embeddings of logically-chunked
  BCM functional specification content. Each point represents
  one semantically-complete chunk (800-2000 tokens).

vector_config:
  size: 1024
  distance: Cosine

hnsw_config:
  m: 16
  ef_construct: 200

optimizers_config:
  default_segment_number: 2

quantization_config: null       # Scalar quantization initially; upgrade to product when >100K points

wal_config:
  wal_capacity_mb: 64
```

### 1.2 Collection: `bcm_chunks_sparse`

```yaml
collection_name: bcm_chunks_sparse
description: >
  Sparse vector collection for keyword-based BM25-style retrieval.
  Complements the dense vector collection for hybrid search.

vector_config:
  size: null                    # Sparse vectors have variable dimensions
  distance: Dot                 # Inner product for sparse vectors

sparse_vector_config:
  index:
    on_disk: true
```

---

## 2. Point Schema

### 2.1 Vector

```yaml
vector:
  model: BGE-M3                  # Primary embedding model
  dimension: 1024
  normalized: true               # L2-normalized for Cosine distance
  embedding_strategy: >
    Each chunk text is embedded using BGE-M3.
    The chunk title and key metadata fields are prepended
    to the chunk content before embedding to improve semantic matching.
    
    Embedding input format:
    "[module: {module}] [section: {section_path}] [type: {chunk_type}]\n{chunk_content}"
```

### 2.2 Sparse Vector

```yaml
sparse_vector:
  model: BGE-M3 (sparse output)   # Same model supports sparse embeddings
  purpose: >
    BM25-style lexical matching for hybrid retrieval.
    Captures exact signal names, parameter names, and
    technical terms that dense vectors may miss.
```

### 2.3 Payload

```yaml
payload:
  # === Identity ===
  chunk_id:
    type: keyword
    description: "Unique chunk identifier, e.g. 'chunk_VMM_012'"
    indexed: true

  # === Document Position ===
  module:
    type: keyword
    description: "Owning module: VMM | ExteriorLight | InteriorLight | Window | Lock | TheftProtection | Wiper | RemoteControl"
    indexed: true

  section_path:
    type: keyword
    description: "Full section path, e.g. '3.3.4.2.2'"
    indexed: true

  parent_section:
    type: keyword
    description: "Parent section path, e.g. '3.3.4'"
    indexed: true

  page_start:
    type: integer
    description: "Start page number in original document"
    indexed: false

  page_end:
    type: integer
    description: "End page number in original document"
    indexed: false

  # === Chunk Type ===
  chunk_type:
    type: keyword
    description: >
      StateTransitionChunk | StateMachineChunk | SignalTableChunk |
      FunctionDescChunk | ConfigBlockChunk | FaultHandlingChunk |
      OutputControlChunk | CrossReferenceChunk
    indexed: true

  # === Content Tags ===
  function:
    type: keyword
    description: "Primary function name, e.g. '遥控解锁'"
    indexed: true

  functions:
    type: keyword[]            # Array of keywords
    description: "All referenced function names"
    indexed: true

  states:
    type: keyword[]            # Array of keywords
    description: "All referenced state IDs, e.g. ['VMM_Driving', 'VMM_Inactive']"
    indexed: true

  signals:
    type: keyword[]            # Array of keywords
    description: "All referenced signal IDs, e.g. ['PEPS_UsageMode', 'ESC_VehicleSpeed']"
    indexed: true

  parameters:
    type: keyword[]            # Array of keywords
    description: "All referenced parameter IDs, e.g. ['CfgTCMEOLOption']"
    indexed: true

  faults:
    type: keyword[]            # Array of keywords
    description: "All referenced fault IDs"
    indexed: true

  # === Graph Linkage ===
  graph_node_ids:
    type: keyword[]            # Array of keywords
    description: "Linked Neo4j entity node IDs for cross-reference"
    indexed: false

  # === Cross-Module ===
  cross_module_refs:
    type: keyword[]            # Array of keywords
    description: "Referenced other module IDs"
    indexed: true

  # === Quality ===
  has_table:
    type: bool
    description: "Whether chunk contains a table"
    indexed: true

  has_state_machine:
    type: bool
    description: "Whether chunk contains state machine definition"
    indexed: true

  has_condition:
    type: bool
    description: "Whether chunk contains conditional logic (trigger/enable)"
    indexed: true

  completeness:
    type: keyword
    description: "complete | partial (partial = image content missing)"
    indexed: true

  # === Text ===
  chunk_text:
    type: text
    description: "Full chunk text content (for retrieval, not embedded as-is)"
    indexed: false

  # === Timestamps ===
  created_at:
    type: datetime
    description: "Chunk creation timestamp"
    indexed: false

  updated_at:
    type: datetime
    description: "Chunk last update timestamp"
    indexed: false
```

---

## 3. Index Configuration

### 3.1 Payload Indexes

```yaml
payload_indexes:
  - field: chunk_id
    field_type: keyword
    
  - field: module
    field_type: keyword
    
  - field: section_path
    field_type: keyword
    
  - field: chunk_type
    field_type: keyword
    
  - field: function
    field_type: keyword
    
  - field: functions
    field_type: keyword      # Array index
    
  - field: states
    field_type: keyword      # Array index
    
  - field: signals
    field_type: keyword      # Array index
    
  - field: parameters
    field_type: keyword      # Array index
    
  - field: faults
    field_type: keyword      # Array index
    
  - field: has_table
    field_type: bool
    
  - field: has_state_machine
    field_type: bool
    
  - field: has_condition
    field_type: bool
    
  - field: completeness
    field_type: keyword
    
  - field: cross_module_refs
    field_type: keyword      # Array index
```

### 3.2 Full-Text Index

```yaml
full_text_index:
  field: chunk_text
  tokenizer: multilingual     # Supports Chinese + English mixed text
```

---

## 4. Retrieval Strategies

### 4.1 Default Hybrid Retrieval

```yaml
strategy: hybrid
description: >
  Combines dense vector similarity with sparse keyword matching.
  Primary retrieval method for all queries.

parameters:
  vector_weight: 0.7
  sparse_weight: 0.3
  top_k: 20
  score_threshold: 0.6
  with_payload: true
  with_vectors: false          # Don't return vectors to save bandwidth
```

### 4.2 Filtered Retrieval

```yaml
strategy: filtered_hybrid
description: >
  Same as hybrid but scoped to specific module(s) or chunk type(s).
  Used when Intent Analysis identifies target modules.

filter_examples:
  - module_filter:       { must: [{ key: "module", match: { value: "VMM" } }] }
  - type_filter:         { must: [{ key: "chunk_type", match: { value: "StateTransitionChunk" } }] }
  - module_type_filter:  { must: [{ key: "module", match: { value: "Lock" } },
                                  { key: "chunk_type", match: { value: "FunctionDescChunk" } }] }
  - multi_module:        { should: [{ key: "module", match: { value: "VMM" } },
                                    { key: "module", match: { value: "Lock" } }] }
  - state_filter:        { must: [{ key: "states", match: { any: ["VMM_Driving"] } }] }
```

### 4.3 Diversity-Aware Retrieval

```yaml
strategy: diversity
description: >
  Ensures retrieved chunks cover diverse chunk types.
  Useful for complex queries that need signal definitions,
  state transitions, and function descriptions.

parameters:
  top_k: 30
  group_by: chunk_type
  min_per_group: 1
  max_per_group: 5
  score_threshold: 0.5
```

### 4.4 Multi-Stage Retrieval

```yaml
strategy: multi_stage
description: >
  Broad retrieval followed by module-aware re-filtering.
  Used when graph retrieval provides entity context.

stages:
  stage_1:
    type: hybrid
    top_k: 50
    score_threshold: 0.5

  stage_2:
    type: module_relevance_filter
    description: >
      Boost chunks from modules identified by graph retrieval.
      Penalize chunks from unrelated modules.

  stage_3:
    type: diversity_sample
    top_k: 15
    description: >
      Ensure at least one chunk from each relevant chunk type.
```

---

## 5. Collection Management

### 5.1 Point Lifecycle

```yaml
operations:
  create:
    description: "Insert new chunk point with embedding and payload"
    upsert: true            # Use upsert to handle re-indexing

  update:
    description: "Update chunk payload and/or embedding"
    upsert: true

  delete:
    description: "Delete chunk by chunk_id filter"
    method: delete_points_by_filter

  batch_upsert:
    description: "Bulk insert/update chunks"
    batch_size: 100         # Max points per batch
    wait: true              # Wait for indexing to complete

  recreate:
    description: "Drop and recreate collection (full re-index)"
    method: delete_collection + create_collection
```

### 5.2 Snapshot

```yaml
snapshots:
  directory: "data/exports/qdrant_snapshot/"
  schedule: "after-index-build"
  retention: 3              # Keep last 3 snapshots
```

---

## 6. Expected Scale

| Metric | Value |
|--------|-------|
| Total chunks | ~300-500 |
| Vector dimension | 1024 |
| Points per collection | ~300-500 |
| Payload size per point | ~2-5 KB |
| Total storage (dense) | ~2-5 MB |
| Total storage (sparse) | ~1-2 MB |
