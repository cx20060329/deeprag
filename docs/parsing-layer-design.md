# Parsing Layer Design

> BCM-RAG: DOCX → Docling → Document Tree
> Principle: Never directly chunk raw text — always produce a Structured Document Model first
> Reference: CLAUDE.md Parsing Layer + Data Flow + Engineering Requirements

---

## 1. Parser Design

### 1.1 Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                   Parser Layer                        │
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐ │
│  │ Docling  │   │ MinerU   │   │ Structure         │ │
│  │ Parser   │   │ Parser   │   │ Extractor         │ │
│  │ (Primary)│   │ (Fallback)│   │ (Post-processor) │ │
│  └────┬─────┘   └────┬─────┘   └────────┬─────────┘ │
│       │               │                  │           │
│       └───────┬───────┘                  │           │
│               │                          │           │
│               ▼                          ▼           │
│       ┌──────────────┐    ┌──────────────────────┐  │
│       │ ParsedDocument│◄───│ StructuredDocument   │  │
│       │ (Raw)         │    │ Model (Final Output) │  │
│       └──────────────┘    └──────────────────────┘  │
│                                                      │
│  Input:  .docx file                                  │
│  Output: StructuredDocumentModel                     │
└──────────────────────────────────────────────────────┘
```

### 1.2 Parser Interface (Abstract Base)

```yaml
Interface: BaseParser

  # Contract: Every parser MUST implement these methods

  parse(file_path: str) -> ParsedDocument:
    description: >
      Parse a document file into a ParsedDocument intermediate object.
      This is the primary entry point. All parser implementations
      produce the SAME intermediate format regardless of backend.
    raises:
      - ParserNotFoundError: when file format is unsupported
      - ParserTimeoutError: when parsing exceeds timeout threshold
      - ParserIntegrityError: when output fails validation

  supported_formats() -> list[str]:
    description: >
      Return list of file extensions this parser supports.
      Docling: ['.docx', '.pdf', '.pptx', '.html', '.md']
      MinerU:  ['.pdf', '.docx']

  version() -> str:
    description: "Return parser library version for provenance tracking"

  capabilities() -> ParserCapabilities:
    description: >
      Declare what this parser can/cannot do.
      Used by the factory to decide routing and fallback behavior.
```

### 1.3 ParserCapabilities

```yaml
ParserCapabilities:
  properties:
    preserves_hierarchy: boolean      # Can reconstruct heading levels
    preserves_tables: boolean         # Can extract table structure
    preserves_lists: boolean          # Can preserve bullet/numbered lists
    preserves_images: boolean         # Can extract image references
    preserves_page_numbers: boolean   # Can map content to page numbers
    preserves_footnotes: boolean      # Can extract footnote references
    max_file_size_mb: integer         # Maximum supported file size
    supported_encodings: [string]     # Character encodings

  # Docling capabilities (current version 2.102.1):
  #   preserves_hierarchy: true
  #   preserves_tables: true          (87 tables extracted from BCM doc)
  #   preserves_lists: true
  #   preserves_images: partial       (VML images not supported — needs LibreOffice)
  #   preserves_page_numbers: true    (via doc.pages)
  #   preserves_footnotes: false
  #   max_file_size_mb: unlimited
  #   supported_encodings: ['utf-8', 'gbk', 'gb2312']

  # MinerU capabilities (magic-pdf 1.3.12):
  #   preserves_hierarchy: true
  #   preserves_tables: true
  #   preserves_lists: true
  #   preserves_images: true          (stronger image support than Docling)
  #   preserves_page_numbers: true
  #   preserves_footnotes: false
  #   max_file_size_mb: unlimited
  #   supported_encodings: ['utf-8', 'gbk', 'gb2312']
```

### 1.4 DoclingParser (Primary)

```yaml
DoclingParser:
  implements: BaseParser

  configuration:
    pipeline_options:
      # Docling pipeline configuration
      do_ocr: false                    # BCM doc is digital-native, no OCR needed
      do_table_structure: true         # CRITICAL: 87 tables in BCM doc
      do_formula_enrichment: false     # No formulas in BCM spec
      do_picture_description: false    # State machine diagrams can't be text-described reliably
      do_code_enrichment: false        # No code blocks
      image_export_as: "placeholder"   # Export placeholder refs, not actual images
      table_mode: "accurate"           # Use accurate table mode for signal matrices

    export_options:
      format: "markdown"               # Primary export format
      strict_headers: true             # Preserve heading hierarchy
      table_style: "pipe"              # GFM pipe tables for reliable parsing
      image_placeholder: "[Image: {caption}]"  # Explicit placeholder format

  parse_flow:
    step_1_convert:
      description: >
        Call docling.DocumentConverter with pipeline_options.
        Produces a DoclingDocument object.
      input: ".docx file path"
      output: "DoclingDocument (in-memory)"

    step_2_export:
      description: >
        Call doc.export_to_markdown() to get full markdown text.
        Also iterate doc.tables, doc.texts, doc.pages for structured access.
      input: "DoclingDocument"
      output: "Markdown string + structured item lists"

    step_3_assemble:
      description: >
        Package everything into a ParsedDocument.
        - markdown_text: full markdown string
        - tables: list of TableNode from doc.tables iteration
        - pages: list of PageReference from doc.pages (if available)
        - images: list of ImageReference extracted from markdown placeholders
        - warnings: list of ParsingWarning (e.g. VML image warnings)
      input: "Markdown + structured items"
      output: "ParsedDocument"

  error_handling:
    VML_image_warning:
      condition: "VML image cannot be loaded" in output
      action: >
        Add ParsingWarning(type=IMAGE_VML_UNSUPPORTED, severity=WARNING).
        Does NOT block parsing. Mark affected sections with completeness=partial.
        Recommend: install LibreOffice for VML/EMF/WMF support.

    table_parse_error:
      condition: "Table structure extraction failed for a specific table"
      action: >
        Add ParsingWarning(type=TABLE_PARSE_FAILED, severity=WARNING).
        Capture raw text of the table area as fallback.
        Mark affected table node with completeness=partial.

    empty_output:
      condition: "markdown_text is empty or under 100 chars"
      action: >
        Raise ParserIntegrityError.
        Trigger automatic fallback to MinerUParser.

  performance_targets:
    docx_parsing: "< 30 seconds for 4.2MB BCM document"
    memory_peak: "< 2GB RAM"

  provenance:
    # Stored in ParsedDocument.parser_metadata
    parser_name: "docling"
    parser_version: "2.102.1"          # From uv pip list
    parse_timestamp: "ISO 8601"
    parse_duration_ms: integer
    document_md5: string               # MD5 hash of original .docx
```

### 1.5 MinerUParser (Fallback)

```yaml
MinerUParser:
  implements: BaseParser

  activation_conditions:
    # MinerU is invoked when:
    - condition: "DoclingParser raises ParserIntegrityError"
      action: "Automatic fallback"
    - condition: "DoclingParser produces >50% TABLE_PARSE_FAILED warnings"
      action: "Automatic fallback"
    - condition: "User explicitly requests MinerU via config"
      action: "Manual override"
    - condition: "Input is a scanned PDF (detected via metadata)"
      action: "Automatic routing to MinerU (better OCR support)"

  configuration:
    magic_pdf_config:
      parse_method: "auto"             # auto | txt | ocr
      lang: "ch"                       # Chinese document
      output_format: "markdown"

  parse_flow:
    step_1_convert:
      description: >
        Call magic_pdf.DocConverter or CLI interface.
        MinerU produces a directory of outputs including .md and .json.
      input: ".docx or .pdf file path"
      output: "Output directory with markdown + structured JSON"

    step_2_normalize:
      description: >
        Read MinerU's output and normalize to ParsedDocument format.
        MinerU's output structure differs from Docling's — normalization
        is required to maintain a single downstream pipeline.
      input: "MinerU output directory"
      output: "ParsedDocument (same schema as DoclingParser output)"

    step_3_reconcile:
      description: >
        If MinerU was used as fallback after Docling failure, compare
        outputs and merge where possible. Docling may have succeeded on
        some parts while failing on others.
      input: "Optional partial ParsedDocument from Docling + MinerU ParsedDocument"
      output: "Merged ParsedDocument"

  error_handling:
    complete_failure:
      condition: "Both Docling and MinerU fail"
      action: >
        Raise CriticalParsingError.
        Log full error details for manual investigation.
        Do NOT proceed with empty/incomplete document — downstream
        layers depend on valid structure.

  provenance:
    parser_name: "mineru"
    parser_version: "1.3.12"
```

### 1.6 ParserFactory (Strategy Selection)

```yaml
ParserFactory:

  create(file_path: str, config: ParserConfig) -> BaseParser:
    logic:
      1. Detect file extension
      2. Check config.preferred_parser:
         - "auto" (default): route .docx → Docling, scanned PDF → MinerU
         - "docling": force Docling
         - "mineru": force MinerU
      3. Validate parser supports the format
      4. Instantiate with config
      5. Return parser instance

  create_with_fallback(file_path: str, config: ParserConfig) -> FallbackChain:
    logic:
      1. Create primary parser via create()
      2. Create fallback parser (the OTHER one)
      3. Return FallbackChain([primary, fallback])

FallbackChain:
  parsers: [BaseParser]              # Ordered list, tried in sequence
  execute() -> ParsedDocument:
    for parser in parsers:
      try:
        result = parser.parse(file_path)
        if result.is_valid():
          return result
      except ParserIntegrityError:
        continue  # Try next parser
    raise CriticalParsingError("All parsers failed")
```

### 1.7 StructureExtractor (Post-Processor)

```yaml
StructureExtractor:
  description: >
    Operates on ParsedDocument.markdown_text to extract hierarchical
    structure, section boundaries, table positions, and content blocks.
    This is parser-agnostic — it works on any ParsedDocument regardless
    of which parser produced it.

  extraction_pipeline:
    step_1_heading_extraction:
      input: "markdown_text (string)"
      method: >
        Regex scan for markdown headings (## through ######).
        Extract: heading_level, heading_text, line_number, section_number.
        # headings are ignored (document title only, not part of BCM hierarchy).
      output: "list of HeadingNode"

    step_2_section_boundary_detection:
      input: "list of HeadingNode"
      method: >
        Each heading owns all content from its line to the next heading
        of equal or higher level. Build a SectionNode tree:
        - root = virtual ROOT node
        - each heading becomes a SectionNode
        - content between heading H at level L and next heading at level ≤ L
          belongs to H's section
      output: "SectionNode tree (intermediate)"

    step_3_table_localization:
      input: "markdown_text + SectionNode tree"
      method: >
        Detect markdown tables (pipe tables from Docling, or HTML tables).
        Assign each table to its containing SectionNode.
        Extract table caption from the line immediately preceding the table.
      output: "SectionNode tree with TableNode children attached"

    step_4_content_classification:
      input: "SectionNode tree"
      method: >
        For each SectionNode, classify its content_type:
        - "state_machine": heading or content contains "状态图|状态表|状态迁移|转移表"
        - "signal_table": heading contains "CAN信号|硬件信号|LIN信号|信号列表"
        - "function_desc": heading contains "功能描述" or is under function section
        - "config_block": heading contains "配置参数|NVM|常数"
        - "fault_block": heading contains "故障|报警|诊断"
        - "mixed": contains multiple types
        - "none": structural node with no direct content
      output: "SectionNode tree with content_type annotations"

    step_5_page_reference_mapping:
      input: "SectionNode tree + ParsedDocument.pages"
      method: >
        If page information is available, map each SectionNode to its
        approximate page range. This is a best-effort mapping:
        - Use heading positions in markdown
        - Correlate with page boundary information from parser
        - If unavailable, leave page_start/page_end as null
      output: "SectionNode tree with page references"

    step_6_image_reference_extraction:
      input: "markdown_text"
      method: >
        Extract image placeholders: "[Image: ...]" or "![](...)" patterns.
        Record: section_path, image_caption, image_index.
        Flag sections containing images with has_image=true.
      output: "list of ImageReference"

    step_7_parsing_warning_collection:
      input: "ParsedDocument.warnings + extraction results"
      method: >
        Consolidate warnings from parser and extraction stages.
        Categorize by severity: INFO, WARNING, ERROR.
        Attach to relevant SectionNode where possible.
      output: "list of ParsingWarning with section associations"
```

---

## 2. Tree Builder Design

### 2.1 Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                  Tree Builder Layer                   │
│                                                      │
│  StructuredDocumentModel                             │
│  (from Parser Layer)                                 │
│           │                                          │
│           ▼                                          │
│  ┌────────────────────────────┐                      │
│  │ SectionNode → TreeNode     │  Node Type Mapping   │
│  │ Converter                  │                      │
│  └────────────┬───────────────┘                      │
│               │                                      │
│               ▼                                      │
│  ┌────────────────────────────┐                      │
│  │ TreeValidator              │  Structural Rules    │
│  └────────────┬───────────────┘                      │
│               │                                      │
│               ▼                                      │
│  ┌────────────────────────────┐                      │
│  │ TreeIndexBuilder           │  Lookup Indexes      │
│  └────────────┬───────────────┘                      │
│               │                                      │
│               ▼                                      │
│  ┌────────────────────────────┐                      │
│  │ DocumentTree               │  Final Output        │
│  └────────────────────────────┘                      │
│                                                      │
│  Output: DocumentTree (see 03-document-tree-schema)  │
└──────────────────────────────────────────────────────┘
```

### 2.2 TreeBuilder Interface

```yaml
Interface: BaseTreeBuilder

  build(structured_doc: StructuredDocumentModel) -> DocumentTree:
    description: >
      Transform a StructuredDocumentModel into a fully validated
      DocumentTree with all navigation indexes.

  rebuild_subtree(tree: DocumentTree, section_path: str,
                  updated_model: StructuredDocumentModel) -> DocumentTree:
    description: >
      Incremental rebuild: replace only the subtree rooted at section_path.
      Used for document updates without full re-parsing.
```

### 2.3 SectionNode → TreeNode Conversion

```yaml
NodeTypeMapping:
  description: >
    Maps StructureExtractor's SectionNode types to DocumentTree TreeNode types.
    The mapping follows the heading level directly.

  rules:
    # Heading Level → TreeNode.node_type
    level_0:  ROOT              # Virtual root, no corresponding heading
    level_1:  CHAPTER           # H2 (##) — top-level module chapters
    level_2:  SECTION           # H3 (###) — major sections within a module
    level_3:  SUBSECTION        # H4 (####) — subsections
    level_4:  SUBSUBSECTION     # H5 (#####) — detailed breakdowns
    level_5:  LEAF              # H6 (######) — leaf-level content entries
    level_N:  CONTENT           # Non-heading content blocks (terminal)
    level_T:  TABLE             # Tables (terminal)

  content_assignment:
    description: >
      Content between two headings is assigned to CONTENT child nodes
      of the higher-level heading. Tables are assigned to TABLE child nodes.

    algorithm: |
      For each SectionNode:
        1. Create a TreeNode with node_type from heading level
        2. Split the section's raw content into:
           a. Table blocks → each becomes a TABLE TreeNode child
           b. Text blocks (between tables) → each becomes a CONTENT TreeNode child
        3. Set parent-child relationships
        4. Propagate content_type, page references, image flags
        5. Order children by appearance in source document

  node_id_generation:
    patterns:
      ROOT:       "root"
      CHAPTER:    "ch{section_number}"                       # ch3
      SECTION:    "sec{section_number}"                      # sec3.3
      SUBSECTION: "ss{section_number}"                       # ss3.3.4
      SUBSUBSECTION: "sss{section_number}"                   # sss3.3.4.2
      LEAF:       "leaf{section_number}"                     # leaf3.3.4.2.2
      TABLE:      "tbl{parent_section}_{seq:03d}"            # tbl3.2.1_001
      CONTENT:    "cnt{parent_section}_{seq:03d}"            # cnt3.3.4.2_001

  example_transformation:
    input_section:
      level: 5
      section_number: "3.3.4.2"
      title: "Inactive模式"
      content: >
        Inactive模式说明文字...
        [table: 信号定义表]
        [content: 模式详细描述...]
        [table: 输出信号表]

    output_tree_nodes:
      - node_id: "sss3.3.4.2"
        node_type: SUBSUBSECTION
        title: "Inactive模式"
        level: 4
        section_number: "3.3.4.2"
        children: ["cnt3.3.4.2_001", "tbl3.3.4.2_001", "cnt3.3.4.2_002", "tbl3.3.4.2_002"]

      - node_id: "cnt3.3.4.2_001"
        node_type: CONTENT
        level: 5
        parent_id: "sss3.3.4.2"
        raw_text: "Inactive模式说明文字..."

      - node_id: "tbl3.3.4.2_001"
        node_type: TABLE
        level: 5
        parent_id: "sss3.3.4.2"
        markdown: "[pipe table content]"

      - node_id: "cnt3.3.4.2_002"
        node_type: CONTENT
        level: 5
        parent_id: "sss3.3.4.2"
        raw_text: "模式详细描述..."

      - node_id: "tbl3.3.4.2_002"
        node_type: TABLE
        level: 5
        parent_id: "sss3.3.4.2"
        markdown: "[pipe table content]"
```

### 2.4 TreeValidator

```yaml
TreeValidator:
  description: >
    Validates the constructed DocumentTree against structural rules
    before it is accepted for downstream use.

  validation_rules:

    rule_1_root_singleton:
      check: "Exactly one ROOT node exists"
      on_failure: "FATAL — tree construction aborted"

    rule_2_no_orphans:
      check: "Every non-ROOT node has a valid parent_id that exists in node_map"
      on_failure: "ERROR — orphan nodes attached to nearest valid ancestor"

    rule_3_hierarchy_consistency:
      check: "Child level > Parent level, except for TABLE/CONTENT which are terminal"
      on_failure: "WARNING — levels adjusted to maintain consistency"

    rule_4_child_type_rules:
      check: "Parent-child type combinations match allowed_children rules"
      reference: "See 03-document-tree-schema.md Section 2.2"
      on_failure: "WARNING — invalid child moved to nearest valid parent"

    rule_5_section_number_continuity:
      check: >
        Section numbers follow document order.
        A section's children have section numbers that are
        sub-numbers of the parent.
        e.g. "3.3" children must start with "3.3."
      on_failure: "WARNING — section number corrected"

    rule_6_table_ownership:
      check: "Every TABLE node has a non-TABLE, non-CONTENT parent"
      on_failure: "ERROR — table reassigned to containing section"

    rule_7_content_non_empty:
      check: "CONTENT nodes have non-empty raw_text or markdown"
      on_failure: "INFO — empty CONTENT nodes removed"

    rule_8_heading_number_correspondence:
      check: >
        Node with level=N and node_type=CHAPTER/SECTION/etc.
        must have a section_number matching the heading's number prefix.
      on_failure: "WARNING — section_number extracted from parent context"

    rule_9_bcm_module_coverage:
      check: >
        At minimum, CHAPTER nodes for these modules must exist:
        VMM, ExteriorLight, InteriorLight, Window, Lock,
        TheftProtection, Wiper, RemoteControl
      on_failure: "WARNING — missing module flagged, tree still accepted"

  validation_result:
    is_valid: boolean              # True if no FATAL errors
    errors: [ValidationError]      # FATAL issues
    warnings: [ValidationWarning]  # Non-fatal issues
    corrected_nodes: integer       # Count of auto-corrected nodes

  ValidationError:
    rule_id: string                # Which rule failed
    node_id: string                # Affected node
    severity: enum                 # FATAL | ERROR | WARNING | INFO
    message: string                # Human-readable description
    auto_corrected: boolean        # Whether auto-fix was applied
    original_value: string         # Value before correction
    corrected_value: string        # Value after correction
```

### 2.5 TreeIndexBuilder

```yaml
TreeIndexBuilder:
  description: >
    Builds auxiliary indexes on the DocumentTree for fast lookup.
    These indexes are stored on the DocumentTree object.

  indexes:

    node_map:
      type: "dict[str, TreeNode]"
      key: "node_id"
      description: "O(1) node lookup by ID"
      build: "Direct hash map from node list"

    chapter_index:
      type: "dict[str, str]"
      key: "chapter_number"         # e.g. "3"
      value: "node_id"              # e.g. "ch3"
      description: "O(1) chapter lookup by number"

    path_index:
      type: "dict[str, str]"
      key: "section_path"           # e.g. "3.3.4.2"
      value: "node_id"              # e.g. "sss3.3.4.2"
      description: "O(1) node lookup by section path"

    content_type_index:
      type: "dict[str, list[str]]"
      key: "content_type"           # e.g. "state_machine"
      value: "[node_id, ...]"       # All nodes of this content type
      description: "O(1) lookup of all nodes by content type"

    table_index:
      type: "dict[str, list[str]]"
      key: "parent_section_path"    # e.g. "3.2.2"
      value: "[table_node_id, ...]"
      description: "O(1) lookup of tables by containing section"

    module_index:
      type: "dict[str, str]"
      key: "module_id"              # e.g. "VMM"
      value: "chapter_node_id"      # e.g. "ch3"
      description: "O(1) chapter lookup by module ID"

    page_index:
      type: "dict[int, str]"
      key: "page_number"
      value: "node_id"
      description: >
        Approximate mapping from page number to nearest section node.
        Built only if page references are available from parser.
```

### 2.6 DocumentTree (Final Output)

```yaml
DocumentTree:
  description: >
    The complete, validated document tree ready for downstream consumption.
    This is the output of the Tree Builder layer and the input to
    Entity Extraction, Chunking, and Retrieval (Stage 3: Tree Localization).

  properties:
    # === Identity ===
    document_id: string             # Unique document identifier
    document_name: string           # "PA2A_中央集控器功能规范_V1.0"
    schema_version: string          # "1.0"

    # === Structure ===
    root_node_id: string            # "root"
    node_map: dict[str, TreeNode]   # ALL nodes by node_id

    # === Indexes (built by TreeIndexBuilder) ===
    chapter_index: dict[str, str]
    path_index: dict[str, str]
    content_type_index: dict[str, list[str]]
    table_index: dict[str, list[str]]
    module_index: dict[str, str]
    page_index: dict[int, str]

    # === Metadata ===
    created_at: datetime            # ISO 8601
    parser_name: string             # "docling" or "mineru"
    parser_version: string          # "2.102.1" or "1.3.12"
    source_file_md5: string         # Original .docx MD5
    total_nodes: integer            # Total node count
    validation_result: ValidationResult  # From TreeValidator

  methods:
    # See 03-document-tree-schema.md Section 4.1 for full interface
    get_node(node_id: str) -> TreeNode
    get_children(node_id: str) -> list[TreeNode]
    get_siblings(node_id: str) -> list[TreeNode]
    get_ancestors(node_id: str) -> list[TreeNode]
    get_descendants(node_id: str) -> list[TreeNode]
    get_node_by_path(path: str) -> TreeNode
    get_chapter_nodes() -> list[TreeNode]
    get_nodes_by_content_type(content_type: str) -> list[TreeNode]
    get_section_chunks(section_path: str) -> list[str]
    expand_section(section_path: str, radius: int) -> list[str]
    get_breadcrumb(node_id: str) -> list[BreadcrumbItem]
```

---

## 3. Data Flow Design

### 3.1 End-to-End Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    END-TO-END DATA FLOW                         │
│                                                                 │
│  [1]                    [2]                    [3]              │
│  Raw .docx ──────────► ParsedDocument ──────► Structured       │
│  (4.2MB)     Parser      (intermediate)         DocumentModel  │
│              Layer                              (final parser   │
│                                                 output)         │
│                                                                 │
│  [4]                    [5]                    [6]              │
│  Structured ──────────► DocumentTree ────────► Persist to      │
│  DocumentModel  Tree     (in-memory)           data/parsed/    │
│                Builder                         document_tree   │
│                                                .json            │
│                                                                 │
│  [7]                    [8]                    [9]              │
│  DocumentTree ────────► Entity Extraction ───► Knowledge Graph │
│  (Layer 1)                                               (Layer 2) │
│                                                                 │
│  [10]                   [11]                                   │
│  DocumentTree ────────► Chunking ─────────────► Vector Store   │
│  + Entities            (logical chunks)          (Layer 3)     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Stage Ownership:
  [1]-[3]: PARSER LAYER         (this document)
  [4]-[6]: TREE BUILDER LAYER   (this document)
  [7]-[8]: ENTITY EXTRACTION    (separate design)
  [9]:     KNOWLEDGE GRAPH      (separate design)
  [10]-[11]: CHUNKING + VECTOR  (separate design)
```

### 3.2 Detailed Stage Flow

```yaml
Stage 1: Raw Document Input
  input:
    file_path: "data/raw/PA2A_中央集控器20250813(1).docx"
    file_size: "~4.2 MB"
    file_format: "docx"
  validation:
    - File exists and is readable
    - File extension is supported (.docx)
    - File size is within limits
    - MD5 checksum computed for provenance
  output: "Validated file path + metadata"

Stage 2: Parser Execution
  entry: "ParserFactory.create_with_fallback(file_path, config)"
  primary_path:
    DoclingParser.parse(file_path)
      → DoclingDocument
      → export_to_markdown()
      → ParsedDocument
  fallback_path (if primary fails):
    MinerUParser.parse(file_path)
      → MinerU output directory
      → normalize to ParsedDocument
  output: "ParsedDocument (validated)"

Stage 3: Structure Extraction
  entry: "StructureExtractor.extract(parsed_doc)"
  substages:
    3a: Heading extraction → list of HeadingNode
    3b: Section boundary detection → SectionNode tree
    3c: Table localization → SectionNode tree + TableNode children
    3d: Content classification → annotated SectionNode tree
    3e: Page reference mapping → SectionNode tree + page refs
    3f: Image reference extraction → list of ImageReference
    3g: Warning consolidation → list of ParsingWarning
  output: "StructuredDocumentModel"

Stage 4: Tree Construction
  entry: "TreeBuilder.build(structured_doc)"
  substages:
    4a: SectionNode → TreeNode conversion (NodeTypeMapping)
    4b: node_id generation
    4c: Parent-child relationship wiring
    4d: Content assignment (CONTENT + TABLE children)
  output: "Unvalidated DocumentTree"

Stage 5: Tree Validation
  entry: "TreeValidator.validate(tree)"
  rules: "9 validation rules (see Section 2.4)"
  on_failure: "Auto-correct where possible, flag uncorrectable as errors"
  output: "Validated DocumentTree + ValidationResult"

Stage 6: Index Building
  entry: "TreeIndexBuilder.build_indexes(tree)"
  indexes: "6 lookup indexes (see Section 2.5)"
  output: "DocumentTree with indexes (final in-memory)"

Stage 7: Persistence
  entry: "TreeStore.save(tree, 'data/parsed/document_tree.json')"
  format: "JSON (flat node array + metadata)"
  schema: "See 03-document-tree-schema.md Section 4.2"
  output: "JSON file on disk"
```

### 3.3 Error Handling & Fallback Paths

```yaml
ErrorFlow:

  scenario_1_docling_succeeds:
    DoclingParser → ParsedDocument (complete) → StructureExtractor → TreeBuilder
    # Normal path. 95%+ expected success rate for .docx files.

  scenario_2_docling_partial_failure:
    DoclingParser → ParsedDocument (with warnings)
      → StructureExtractor handles warnings (marks affected sections partial)
      → TreeBuilder builds tree with completeness=partial flags
    # Tree is usable but some sections may be incomplete.
    # Downstream systems check completeness flag before relying on data.

  scenario_3_docling_fails_mineru_succeeds:
    DoclingParser → ParserIntegrityError
      → FallbackChain triggers MinerUParser
      → MinerUParser produces ParsedDocument
      → Normal flow continues
    # Transparent to downstream. Provenance metadata records which parser was used.

  scenario_4_both_parsers_fail:
    DoclingParser → ParserIntegrityError
      → MinerUParser → also fails
      → CriticalParsingError raised
      → Pipeline aborts. Human intervention required.

  scenario_5_structure_extraction_fails:
    ParsedDocument is valid but StructureExtractor cannot find any headings
      → Check if document is actually a flat text (no hierarchy)
      → If so, create a single CHAPTER node with all content as CONTENT children
      → Flag with ValidationWarning(type=FLAT_DOCUMENT)
      → Tree is minimal but usable

  scenario_6_tree_validation_fails:
    TreeValidator finds FATAL errors
      → Attempt auto-correction
      → If uncorrectable, abort tree construction
      → Log full validation report for debugging
      → Return partial tree with error markers if possible
```

### 3.4 Caching Strategy

```yaml
CacheDesign:

  cache_key: "md5 of original .docx file + parser name + parser version"

  cache_locations:

    parsed_document_cache:
      path: "data/parsed/.cache/parsed_document_{md5}.pkl"
      format: "Python pickle (or msgpack)"
      content: "ParsedDocument object"
      invalidation: "Delete when .docx MD5 changes or parser version changes"
      ttl: "Permanent (until source changes)"

    structured_model_cache:
      path: "data/parsed/.cache/structured_model_{md5}.json"
      format: "JSON"
      content: "StructuredDocumentModel (serialized)"
      invalidation: "Delete when ParsedDocument changes or extraction logic version changes"
      ttl: "Permanent (until upstream changes)"

    document_tree_cache:
      path: "data/parsed/.cache/document_tree_{md5}.pkl"
      format: "Python pickle"
      content: "DocumentTree object (with indexes)"
      invalidation: "Delete when StructuredDocumentModel changes or tree builder version changes"
      ttl: "Permanent (until upstream changes)"

  cache_hit_flow:
    1. Check parsed_document_cache → if hit, skip Stage 2
    2. Check structured_model_cache → if hit, skip Stage 3
    3. Check document_tree_cache → if hit, skip Stages 4-6
    4. On any cache miss, recompute from that stage forward

  cache_benefit:
    # During development, only re-parse when document or parser changes
    # Structure extraction and tree building are fast (seconds),
    # so caching is most valuable for the parsing stage (30s+ for 4.2MB docx)
```

### 3.5 Incremental Update Flow

```yaml
IncrementalUpdate:
  description: >
    When the source document is updated (new version), avoid full re-processing.
    Only re-process changed sections.

  trigger: "New .docx with different MD5 detected"

  flow:
    step_1_diff:
      method: >
        Parse both old and new documents.
        Compare ParsedDocument trees at the section level.
        Identify: added sections, removed sections, modified sections.
      output: "SectionDiff"

    step_2_selective_rebuild:
      method: >
        For modified sections only:
        - Re-run StructureExtractor on affected markdown ranges
        - Re-build affected subtree nodes
        - Graft updated subtrees into existing DocumentTree
      output: "Partially updated DocumentTree"

    step_3_downstream_invalidation:
      method: >
        Identify which entities, chunks, and vector points are affected
        by the changed sections. Flag them for re-extraction/re-indexing.
        Downstream layers handle their own incremental updates.
      output: "InvalidationReport"

  limitation: >
    Full re-processing is simpler and recommended for now.
    Incremental update is a future optimization.
    Document versions are infrequent (months apart).
```

---

## 4. Intermediate Object Design

### 4.1 Object Hierarchy

```
ParsedDocument                    # Raw parser output
├── ParserMetadata                # Parser provenance
├── list of ParsingWarning        # Issues encountered
├── list of PageReference         # Page boundaries
├── list of ImageReference        # Image placeholders
└── markdown_text (str)           # Full markdown

StructuredDocumentModel           # StructureExtractor output
├── DocumentMeta                  # Document identity
├── list of SectionNode           # Hierarchical section tree
├── list of TableNode             # All tables
├── list of ImageReference        # All images
├── list of ParsingWarning        # All warnings
└── SectionDiff (optional)        # For incremental updates

DocumentTree                      # TreeBuilder output
├── (see 03-document-tree-schema.md for full schema)
└── list of TreeNode              # Final tree nodes
```

### 4.2 ParsedDocument

```yaml
ParsedDocument:
  description: >
    Raw, unprocessed output from the document parser (Docling or MinerU).
    Contains the full markdown text plus structured items extracted by the parser.
    This is the boundary between parser implementations and the rest of the pipeline.

  fields:
    # === Identity ===
    document_id:
      type: string
      description: "Unique ID derived from source file"
      generation: "sha256 of file path + file size (first 8 chars)"
      example: "a3f2b91c"

    source_file_path:
      type: string
      description: "Absolute path to source .docx file"
      example: "data/raw/PA2A_中央集控器20250813(1).docx"

    source_file_md5:
      type: string
      description: "MD5 hash of source file for change detection"
      example: "d41d8cd98f00b204e9800998ecf8427e"

    # === Content ===
    markdown_text:
      type: string
      description: >
        Full document content as markdown. This is the primary content
        that StructureExtractor operates on. Contains headings, paragraphs,
        pipe tables, lists, and image placeholders.
      constraints:
        min_length: 100           # Reject if shorter (likely parse failure)
      example_length: "166,337 chars (BCM document)"

    # === Structured Items ===
    raw_tables:
      type: list[RawTable]
      description: >
        Tables extracted by the parser as structured objects.
        From Docling: doc.tables iterator.
        From MinerU: JSON table output.
        These are pre-normalization — TableExtractor converts them to TableNode.

    raw_texts:
      type: list[RawText]
      description: >
        Text items extracted by the parser with position metadata.
        From Docling: doc.texts iterator.
        Used for page-level position mapping.

    raw_pages:
      type: list[RawPage]
      description: >
        Page metadata from the parser.
        From Docling: doc.pages.
        May be empty if parser cannot extract page information.

    # === Metadata ===
    parser_metadata:
      type: ParserMetadata
      description: "Provenance information about the parsing process"

    warnings:
      type: list[ParsingWarning]
      description: "Non-fatal issues encountered during parsing"

    # === Quality ===
    is_valid:
      type: boolean
      description: "Whether the parsed document passes basic validation"
      computed: >
        True if markdown_text is non-empty AND no FATAL warnings exist.
```

### 4.3 ParserMetadata

```yaml
ParserMetadata:
  description: >
    Immutable record of how this document was parsed.
    Stored with the document for full provenance tracking.

  fields:
    parser_name:
      type: enum
      values: ["docling", "mineru"]
      description: "Which parser produced this output"

    parser_version:
      type: string
      description: "Parser library version"
      example: "2.102.1"

    parse_timestamp:
      type: datetime
      format: "ISO 8601"
      description: "When parsing started"
      example: "2026-06-15T14:30:00+08:00"

    parse_duration_ms:
      type: integer
      description: "Total parse time in milliseconds"
      example: 28500

    source_file_md5:
      type: string
      description: "MD5 of source file at parse time"

    source_file_size_bytes:
      type: integer
      description: "Source file size in bytes"
      example: 4400000

    configuration_snapshot:
      type: dict
      description: "Parser configuration used (for reproducibility)"
      example:
        do_ocr: false
        do_table_structure: true
        table_mode: "accurate"

    fallback_used:
      type: boolean
      description: "Whether fallback parser was invoked"
      default: false

    fallback_from:
      type: string
      description: "Which parser failed, triggering fallback"
      nullable: true

    environment:
      type: dict
      description: "Runtime environment info"
      fields:
        python_version: string      # "3.11.9"
        platform: string            # "win32"
        libreoffice_available: boolean  # Whether LibreOffice is installed
```

### 4.4 SectionNode (Intermediate)

```yaml
SectionNode:
  description: >
    INTERMEDIATE representation of a document section.
    NOT the final TreeNode. This is the output of StructureExtractor
    before conversion to DocumentTree TreeNode format.
    It retains raw content references that are split into separate
    TreeNode children during tree building.

  fields:
    # === Identity ===
    section_id:
      type: string
      description: "Temporary ID for cross-referencing during extraction"
      example: "sec_0037"

    # === Structural ===
    heading_level:
      type: integer
      range: [0, 6]
      description: >
        Markdown heading level.
        0 = virtual root (no heading)
        1 = H1 (#) — document title (usually ignored)
        2 = H2 (##) — chapter
        3 = H3 (###) — section
        4 = H4 (####) — subsection
        5 = H5 (#####) — subsubsection
        6 = H6 (######) — leaf

    section_number:
      type: string
      description: "Parsed section number from heading"
      nullable: true
      example: "3.3.4.2"

    title:
      type: string
      description: "Heading text (Chinese)"
      nullable: true
      example: "车身模式管理"

    title_en:
      type: string
      description: "English heading text extracted from parenthetical"
      nullable: true
      example: "Vehicle Mode Management"

    # === Hierarchy ===
    parent:
      type: SectionNode | null
      description: "Parent section (null for root)"

    children:
      type: list[SectionNode]
      description: "Child sections (ordered by document appearance)"
      default: []

    # === Content ===
    raw_content:
      type: string
      description: >
        All markdown content between this heading and the next heading
        of equal or higher level. Includes tables, paragraphs, lists, images.
        This is the raw text that will be split into CONTENT and TABLE children.

    content_start_line:
      type: integer
      description: "Line number in markdown_text where content begins"

    content_end_line:
      type: integer
      description: "Line number in markdown_text where content ends"

    # === Tables ===
    tables:
      type: list[TableNode]
      description: "Tables found within this section's content"
      default: []

    # === Classification ===
    content_type:
      type: enum
      values: [state_machine, signal_table, function_desc, config_block, fault_block, mixed, none]
      description: "Classified content type (see Section 2.4 of tree builder)"

    # === Position ===
    page_start:
      type: integer | null
      description: "Approximate start page"

    page_end:
      type: integer | null
      description: "Approximate end page"

    # === Flags ===
    has_images:
      type: boolean
      description: "Whether section contains image references"
      default: false

    is_empty:
      type: boolean
      description: "True if section has no content and no children"
      default: false
```

### 4.5 TableNode

```yaml
TableNode:
  description: >
    Structured representation of a single table extracted from the document.
    Preserves both markdown and structured (row/column) formats.
    This is the normalized output from TableExtractor.

  fields:
    # === Identity ===
    table_id:
      type: string
      description: "Unique table identifier"
      pattern: "tbl_{section_number}_{seq:03d}"
      example: "tbl_3.2.1_001"

    # === Position ===
    section_path:
      type: string
      description: "Section where this table appears"
      example: "3.2.1"

    line_start:
      type: integer
      description: "Starting line number in markdown_text"

    line_end:
      type: integer
      description: "Ending line number in markdown_text"

    page_number:
      type: integer | null
      description: "Page number where table appears"

    # === Content ===
    caption:
      type: string
      description: "Table caption (line immediately preceding the table)"
      nullable: true
      example: "表 3-1: CAN 输入信号"

    caption_line:
      type: integer
      description: "Line number of caption text"

    markdown:
      type: string
      description: "GFM pipe table format (from Docling)"
      example: "| 信号名 | CAN ID | 位位置 | 值编码 | 说明 |\n|--------|--------|--------|--------|------|"

    # === Structure ===
    headers:
      type: list[string]
      description: "Column headers"
      example: ["信号名", "CAN ID", "位位置", "值编码", "说明"]

    rows:
      type: list[list[string]]
      description: "Table rows as list of cell value lists"

    row_count:
      type: integer
      description: "Number of data rows (excluding header)"

    column_count:
      type: integer
      description: "Number of columns"

    # === Classification ===
    table_type:
      type: enum
      values:
        - SIGNAL_MATRIX          # Signal definition table (CAN/IO/LIN signals)
        - STATE_DEFINITION       # State definition table
        - STATE_TRANSITION       # State transition table
        - CONFIG_PARAMETER       # Configuration parameter table
        - FAULT_DEFINITION       # Fault/alarm definition table
        - OUTPUT_CONTROL         # Output control logic table
        - GENERAL                # Uncategorized table
      description: "Classified table purpose"

    # === Quality ===
    completeness:
      type: enum
      values: [complete, partial]
      description: "Whether table was fully extracted"
      default: complete

    extraction_method:
      type: enum
      values: [structured, fallback_text]
      description: >
        structured = parser extracted row/column structure.
        fallback_text = only raw text captured (table parse failed).

    warnings:
      type: list[ParsingWarning]
      description: "Issues specific to this table"
      default: []
```

### 4.6 ContentBlock

```yaml
ContentBlock:
  description: >
    A non-table text block within a section. Represents paragraphs,
    lists, or other text content between tables or headings.

  fields:
    block_id:
      type: string
      description: "Unique block identifier"
      pattern: "blk_{section_path}_{seq:03d}"
      example: "blk_3.3.4.2_003"

    section_path:
      type: string
      description: "Containing section"

    block_type:
      type: enum
      values: [paragraph, list, code, image_placeholder, note, warning]
      description: "Type of content block"

    text:
      type: string
      description: "Raw text content"

    line_start:
      type: integer

    line_end:
      type: integer

    contains_conditions:
      type: boolean
      description: >
        True if block contains conditional logic patterns:
        - "如果...则..." (if...then...)
        - "当...时" (when...)
        - "条件:" (condition:)
        - "前置条件:" (precondition:)
        - "触发条件:" (trigger condition:)
      default: false

    contains_references:
      type: boolean
      description: >
        True if block references other sections:
        - "参考..." (refer to...)
        - "参见..." (see...)
        - "详见..." (see details in...)
      default: false

    entity_hints:
      type: list[EntityHint]
      description: "Tentative entity references found in text"
      default: []

EntityHint:
  description: >
    Lightweight entity reference detected during parsing.
    These are HINTS for the Entity Extraction layer, not final entities.

  fields:
    text: string                  # Matched text
    hint_type: enum               # STATE | SIGNAL | FUNCTION | PARAMETER | FAULT | MODULE
    confidence: float             # 0.0 - 1.0
    line_number: integer
```

### 4.7 PageReference

```yaml
PageReference:
  description: >
    Maps a page number to a position in the markdown text.
    Used for source citation and navigation.

  fields:
    page_number:
      type: integer
      min: 1
      description: "1-based page number"

    markdown_line_start:
      type: integer
      description: "First line of this page in markdown_text"

    markdown_line_end:
      type: integer
      description: "Last line of this page in markdown_text"

    section_path:
      type: string
      description: "Primary section on this page"
      nullable: true
```

### 4.8 ImageReference

```yaml
ImageReference:
  description: >
    Records an image found in the document. Images are NOT extracted
    as content (state machine diagrams, flowcharts), but their existence
    is recorded for completeness tracking.

  fields:
    image_index:
      type: integer
      description: "Sequential image number in document"

    section_path:
      type: string
      description: "Section where image appears"

    caption:
      type: string
      description: "Image caption or alt text"
      nullable: true
      example: "图 3-1: 车身模式管理状态图"

    markdown_line:
      type: integer
      description: "Line number where image placeholder appears"

    placeholder_text:
      type: string
      description: "Actual placeholder text in markdown"
      example: "[Image: 车身模式管理状态图]"

    extraction_possible:
      type: boolean
      description: >
        True if image could potentially be extracted (raster formats).
        False for VML/EMF/WMF formats that Docling cannot process.
      default: false

    recommended_action:
      type: enum
      values: [none, install_libreoffice, manual_review]
      description: "Action to improve image extraction"
```

### 4.9 ParsingWarning

```yaml
ParsingWarning:
  description: >
    Non-fatal issue encountered during parsing or structure extraction.
    Accumulated throughout the parsing pipeline and attached to
    affected nodes for downstream awareness.

  fields:
    warning_id:
      type: string
      pattern: "warn_{seq:04d}"
      example: "warn_0003"

    warning_type:
      type: enum
      values:
        - IMAGE_VML_UNSUPPORTED       # VML image not extractable
        - TABLE_PARSE_FAILED          # Table structure extraction failed
        - TABLE_TRUNCATED             # Table appears incomplete
        - HEADING_LEVEL_SKIP          # Heading level jump (e.g. H2 → H4)
        - SECTION_NUMBER_MISSING      # Heading has no parseable section number
        - PAGE_MAPPING_FAILED         # Cannot map content to pages
        - ENCODING_ISSUE              # Character encoding problem
        - CONTENT_TRUNCATED           # Content appears cut off
        - DUPLICATE_SECTION_NUMBER    # Same section number appears twice
        - CROSS_REFERENCE_UNRESOLVED  # Reference to non-existent section

    severity:
      type: enum
      values: [INFO, WARNING, ERROR]
      description: >
        INFO: Noted for provenance, no action needed.
        WARNING: May affect quality, downstream should check completeness flag.
        ERROR: Definite quality issue, but does not block parsing.

    message:
      type: string
      description: "Human-readable description"

    section_path:
      type: string | null
      description: "Affected section, if known"

    table_id:
      type: string | null
      description: "Affected table, if applicable"

    line_number:
      type: integer | null
      description: "Line number in markdown_text where issue was detected"

    suggestion:
      type: string
      description: "Recommended fix or mitigation"
      example: "Install LibreOffice to enable VML/EMF/WMF image extraction"
```

### 4.10 StructuredDocumentModel (Final Parser Output)

```yaml
StructuredDocumentModel:
  description: >
    The FINAL output of the Parser Layer. This is the input to the
    Tree Builder. It contains the fully extracted and classified
    document structure, ready for tree construction.

  fields:
    # === Identity ===
    document_id: string
    document_name: string
    source_file_path: string
    parser_metadata: ParserMetadata

    # === Structure ===
    root_section: SectionNode
      description: "Root of the section tree"

    all_sections:
      type: list[SectionNode]
      description: "Flat list of ALL SectionNodes (for iteration)"

    all_tables:
      type: list[TableNode]
      description: "Flat list of ALL TableNodes"

    all_images:
      type: list[ImageReference]
      description: "Flat list of ALL ImageReferences"

    all_warnings:
      type: list[ParsingWarning]
      description: "All warnings from all stages"

    # === Statistics ===
    statistics:
      type: DocumentStatistics
      fields:
        total_chars: integer           # 166,337 for BCM doc
        total_lines: integer           # Markdown line count
        total_sections: integer        # Section count
        total_tables: integer          # 87 for BCM doc
        total_images: integer          # Image placeholder count
        max_depth: integer             # 6 for BCM doc
        warning_count: integer
        parse_duration_ms: integer

    # === Diff (optional, for incremental updates) ===
    diff:
      type: SectionDiff | null
      description: "Section-level diff from previous version"

SectionDiff:
  added_sections: list[SectionNode]
  removed_sections: list[SectionNode]
  modified_sections: list[SectionNode]    # Content changed, structure same
  unchanged_sections: list[SectionNode]
```

### 4.11 Object Lifecycle Summary

```yaml
ObjectLifecycle:
  description: "Which objects are created/destroyed at each stage"

  Stage 1-2 (Parser):
    CREATED:
      - ParsedDocument (with markdown_text, raw_tables, raw_texts, raw_pages)
      - ParserMetadata
      - RawTable, RawText, RawPage (parser-specific, normalized away)
      - ParsingWarning (from parser)
    LIFETIME: "ParsedDocument is cached; raw items are discarded after normalization"

  Stage 3 (Structure Extraction):
    CONSUMES: "ParsedDocument"
    CREATED:
      - SectionNode tree
      - TableNode (normalized from RawTable)
      - ContentBlock
      - ImageReference
      - EntityHint
      - PageReference
      - ParsingWarning (from extraction)
      - DocumentStatistics
    PRODUCES: "StructuredDocumentModel"
    LIFETIME: "StructuredDocumentModel is cached; ContentBlock/EntityHint are intermediate"

  Stage 4-6 (Tree Building):
    CONSUMES: "StructuredDocumentModel"
    CREATED:
      - TreeNode (final, from SectionNode + TableNode + ContentBlock)
      - DocumentTree
      - ValidationResult
      - Index maps
    PRODUCES: "DocumentTree"
    LIFETIME: "DocumentTree is persisted to JSON and held in memory"

  Garbage Collection:
    - SectionNode, ContentBlock, EntityHint: discarded after tree building
    - TableNode: may be retained for entity extraction (signal tables)
    - ParsedDocument: retained in cache for incremental updates
    - StructuredDocumentModel: retained in cache for tree rebuilds
```

---

## Appendix A: BCM Document Parsing Estimates

```yaml
BCM_Specific_Estimates:
  source: "PA2A_中央集控器20250813(1).docx"

  parsing:
    docling_time: "~20-35 seconds"
    mineru_time: "~30-60 seconds"
    markdown_output: "~166,000 chars"
    tables_extracted: 87

  structure_extraction:
    extraction_time: "~1-3 seconds"
    sections_expected: "~300-500"
    max_depth: 6
    tables_classified:
      SIGNAL_MATRIX: "~30-40"
      STATE_DEFINITION: "~5-10"
      STATE_TRANSITION: "~5-10"
      CONFIG_PARAMETER: "~10-15"
      FAULT_DEFINITION: "~3-5"
      OUTPUT_CONTROL: "~5-10"
      GENERAL: "~5-10"

  tree_building:
    build_time: "~0.5-1 second"
    tree_nodes_expected: "~800-1200"
      CHAPTER: 9
      SECTION: "~50"
      SUBSECTION: "~150"
      SUBSUBSECTION: "~200"
      LEAF: "~300"
      TABLE: 87
      CONTENT: "~300-500"

  total_pipeline:
    end_to_end_time: "~25-40 seconds (first run)"
    cached_time: "~2-5 seconds (warm cache, skip parsing)"
```

---

## Appendix B: Integration Contracts

```yaml
Downstream Contracts:

  To Entity Extraction:
    input: "DocumentTree"
    consumes:
      - TreeNode.raw_text (CONTENT nodes) for text-based extraction
      - TreeNode.markdown (TABLE nodes) for table-based extraction
      - TreeNode.content_type for routing to appropriate extractor
      - TreeNode.section_path for source provenance
    does_not_consume:
      - ParsedDocument (entity extraction works on tree, not raw markdown)

  To Chunking:
    input: "DocumentTree"
    consumes:
      - TreeNode hierarchy for section boundary detection
      - TreeNode.content_type for chunk type selection
      - TreeNode.raw_text / markdown for chunk content
      - TableNode for signal/state/transition table chunking
    does_not_consume:
      - SectionNode (tree building is complete)

  To Retrieval (Stage 3: Tree Localization):
    input: "DocumentTree"
    consumes:
      - expand_section() for section localization
      - get_breadcrumb() for source citation
      - path_index for section path → chunk_id mapping
```

---

## Appendix C: Configuration

```yaml
ParserConfig:
  # All configurable parameters for the parsing layer

  parser:
    preferred_parser: "auto"         # auto | docling | mineru
    fallback_enabled: true           # Whether to try fallback on failure
    parse_timeout_seconds: 120       # Max time for parsing
    cache_enabled: true              # Whether to use parse cache
    cache_dir: "data/parsed/.cache"

  docling:
    do_ocr: false                    # BCM doc is digital-native
    do_table_structure: true
    table_mode: "accurate"           # accurate | fast
    image_export_as: "placeholder"
    export_format: "markdown"
    strict_headers: true

  mineru:
    parse_method: "auto"             # auto | txt | ocr
    lang: "ch"
    output_format: "markdown"

  structure_extraction:
    heading_pattern: >
      Chinese numbered: ^(\d+(?:\.\d+)*)\s+   (e.g. "3.3.4 状态迁移")
      Pure numbered:   ^(\d+(?:\.\d+)*)\s+    (e.g. "3.3.4 State Transition")
    min_section_content_chars: 10    # Sections with less content are flagged
    table_caption_max_distance: 3    # Max lines between caption and table

  tree_building:
    validate_on_build: true          # Run TreeValidator after building
    auto_correct: true               # Auto-correct validation issues
    build_indexes: true              # Build lookup indexes
    persist_on_build: true           # Save to JSON after building
    persist_path: "data/parsed/document_tree.json"
```
