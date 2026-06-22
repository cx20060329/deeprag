# Document Tree Schema

> BCM-RAG Layer 1 — Primary Navigation Layer
> Purpose: Preserve original document structure, hierarchy, and section ownership

---

## 1. Overview

The Document Tree is a hierarchical representation of the BCM functional specification document. It preserves the original chapter/section/subsection structure and serves as the primary navigation and localization layer in the retrieval pipeline.

```
Document (root)
 └── Chapter
      └── Section
           └── SubSection
                └── Leaf
                     ├── Table
                     └── Content
```

---

## 2. Node Types

### 2.1 Node Type Enumeration

```yaml
NodeType:
  values:
    - ROOT          # Document root
    - CHAPTER       # H2 heading (e.g. "3 车辆模式管理（VMM）")
    - SECTION       # H3 heading (e.g. "3.3 车身模式管理")
    - SUBSECTION    # H4 heading (e.g. "3.3.4 状态迁移")
    - SUBSUBSECTION # H5 heading (e.g. "3.3.4.2 Inactive模式")
    - LEAF          # H6 heading (e.g. "3.3.4.2.2 迁移到Convenience状态")
    - TABLE         # Table within any level
    - CONTENT       # Non-heading content block
```

### 2.2 Type Hierarchy Rules

```yaml
allowed_children:
  ROOT:           [CHAPTER]
  CHAPTER:        [SECTION, TABLE, CONTENT]
  SECTION:        [SUBSECTION, TABLE, CONTENT]
  SUBSECTION:     [SUBSUBSECTION, TABLE, CONTENT]
  SUBSUBSECTION:  [LEAF, TABLE, CONTENT]
  LEAF:           [TABLE, CONTENT]
  TABLE:          []                     # Terminal node
  CONTENT:        []                     # Terminal node
```

---

## 3. TreeNode Schema

```yaml
TreeNode:
  properties:
    # === Identity ===
    node_id:
      type: string
      format: "{prefix}{number}_{path}"
      description: "Unique node identifier"
      examples:
        - "ch3"                        # Chapter
        - "sec3.3"                     # Section
        - "ss3.3.4"                    # SubSection
        - "sss3.3.4.2"                 # SubSubSection
        - "leaf3.3.4.2.2"              # Leaf
        - "tbl3.2.1.1_001"             # Table
        - "cnt3.3.4.2.2_001"           # Content
      pattern: "^(ch|sec|ss|sss|leaf|tbl|cnt)[0-9.]+(_[0-9]+)?$"

    # === Type ===
    node_type:
      type: enum
      values: [ROOT, CHAPTER, SECTION, SUBSECTION, SUBSUBSECTION, LEAF, TABLE, CONTENT]
      description: "Node type from the NodeType enumeration"

    # === Structural ===
    title:
      type: string
      description: "Section heading text (original language)"
      nullable: true                   # null for CONTENT nodes

    title_en:
      type: string
      description: "English heading text (extracted or translated)"
      nullable: true

    level:
      type: integer
      range: [0, 6]
      description: "Nesting depth (0=root, 1=chapter, ..., 6=leaf)"

    path:
      type: string
      description: "Full breadcrumb path, e.g. '3 > 3.3 > 3.3.4 > 3.3.4.2 > 3.3.4.2.2'"

    section_number:
      type: string
      description: "Section number, e.g. '3.3.4.2.2'"
      nullable: true                   # null for ROOT

    parent_id:
      type: string
      description: "Parent TreeNode.node_id"
      nullable: true                   # null for ROOT

    children:
      type: string[]
      description: "Child TreeNode.node_ids (ordered)"
      default: []

    # === Position ===
    order:
      type: integer
      description: "Sibling order index (0-based)"

    page_start:
      type: integer
      description: "Start page in original document"
      nullable: true

    page_end:
      type: integer
      description: "End page in original document"
      nullable: true

    # === Content Association ===
    content_type:
      type: enum
      values: [state_machine, signal_table, function_desc, config_block, fault_block, mixed, none]
      description: "Primary content type at this node level"

    tables:
      type: string[]
      description: "Table node_ids contained in this node's subtree"
      default: []

    chunk_ids:
      type: string[]
      description: "Chunk IDs derived from this node's content"
      default: []

    graph_node_ids:
      type: string[]
      description: "Neo4j entity node IDs extracted from this node's content"
      default: []

    # === Text ===
    raw_text:
      type: string
      description: "Raw text content (for terminal CONTENT nodes)"
      nullable: true

    markdown:
      type: string
      description: "Markdown content (for terminal CONTENT/TABLE nodes)"
      nullable: true

    # === Metadata ===
    completeness:
      type: enum
      values: [complete, partial]
      description: "complete = all content extracted; partial = images missing or content incomplete"
      default: complete

    has_image:
      type: boolean
      description: "Whether original section contains images"
      default: false
```

---

## 4. Tree Storage

### 4.1 In-Memory Representation

```yaml
DocumentTree:
  properties:
    document_id: string             # Document identifier
    document_name: string           # "PA2A_中央集控器功能规范_V1.0"
    root_node_id: string            # Root TreeNode.node_id
    node_map:
      type: map[string, TreeNode]   # node_id -> TreeNode
    chapter_index:
      type: map[string, string]     # chapter_number -> Chapter node_id
    path_index:
      type: map[string, string]     # section_path -> TreeNode node_id

  methods:
    get_node(node_id: string) -> TreeNode
    get_children(node_id: string) -> TreeNode[]
    get_siblings(node_id: string) -> TreeNode[]
    get_ancestors(node_id: string) -> TreeNode[]        # Root-to-node path
    get_descendants(node_id: string) -> TreeNode[]      # All nodes in subtree
    get_node_by_path(path: string) -> TreeNode
    get_chapter_nodes() -> TreeNode[]
    get_nodes_by_content_type(content_type: string) -> TreeNode[]
    get_section_chunks(section_path: string) -> string[]  # Returns chunk_ids
    expand_section(section_path: string, radius: integer) -> string[]  # +siblings and +parent
```

### 4.2 Persistent Storage

```yaml
format: JSON
file_path: "data/parsed/document_tree.json"

schema_version: "1.0"

structure:
  document_id: string
  document_name: string
  created_at: string               # ISO 8601
  parser_version: string           # Docling version
  root_node_id: string
  nodes:
    type: array
    items: TreeNode                # Flat list with parent_id/children references
```

---

## 5. Tree Construction Rules

### 5.1 Heading Mapping

| Markdown Heading | NodeType       | Level | Section Number |
|-----------------|----------------|-------|----------------|
| `#`             | (none)         | —     | —              |
| `##`            | CHAPTER        | 1     | e.g. "3"       |
| `###`           | SECTION        | 2     | e.g. "3.3"     |
| `####`          | SUBSECTION     | 3     | e.g. "3.3.4"   |
| `#####`         | SUBSUBSECTION  | 4     | e.g. "3.3.4.2" |
| `######`        | LEAF           | 5     | e.g. "3.3.4.2.2"|

### 5.2 Content Association

```yaml
rules:
  - All content (text, tables) between two headings belongs to the first heading
  - Tables are extracted as TABLE child nodes of their containing heading
  - Non-table text blocks are extracted as CONTENT child nodes
  - A heading with no sub-headings but with content becomes a LEAF with CONTENT children
  - Empty headings (no content, no sub-headings) are preserved as structural nodes
```

### 5.3 Section Number Parsing

```yaml
patterns:
  chinese_numbered: "^(\d+(?:\.\d+)*)\s+"       # "3.3.4 状态迁移"
  pure_numbered: "^(\d+(?:\.\d+)*)\s+"           # "3.3.4 State Transition"
  
extraction:
  - Strip leading whitespace
  - Match against chinese_numbered then pure_numbered
  - If no match, use parent section_number + "." + sibling_index
```

---

## 6. Navigation Operations

### 6.1 Section Expansion (for Retrieval Stage 3)

```yaml
operation: expand_section
input:
  section_path: string        # e.g. "3.3.4.2"
  radius: integer             # 0 = self only, 1 = +siblings, 2 = +parent+siblings

output:
  chunk_ids: string[]         # All chunk_ids in expanded scope

behavior:
  radius_0:
    - Get node by section_path
    - Return all chunk_ids in node.subtree

  radius_1:
    - radius_0
    - Get node.parent
    - Return all chunk_ids in parent.subtree (all siblings)

  radius_2:
    - radius_1
    - Get node.parent.parent
    - Return all chunk_ids in grandparent.subtree
```

### 6.2 Breadcrumb Generation

```yaml
operation: get_breadcrumb
input:
  node_id: string

output:
  breadcrumb:
    type: array
    items:
      node_id: string
      title: string
      section_number: string
      level: integer

example:
  input: "leaf3.3.4.2.2"
  output:
    - { level: 1, title: "车辆模式管理（VMM）", section_number: "3" }
    - { level: 2, title: "车身模式管理", section_number: "3.3" }
    - { level: 3, title: "状态迁移", section_number: "3.3.4" }
    - { level: 4, title: "Inactive模式", section_number: "3.3.4.2" }
    - { level: 5, title: "迁移到Convenience状态", section_number: "3.3.4.2.2" }
```

---

## 7. Integration Points

| Consumer | Usage |
|----------|-------|
| Entity Extraction | Iterates tree nodes to extract entities from CONTENT and TABLE nodes |
| Chunking | Uses section boundaries as natural chunk boundaries |
| Retrieval Stage 3 | Localizes relevant sections based on graph retrieval results |
| Context Compression | References section_path in Evidence Package |
| API | Returns section tree for navigation UI |
