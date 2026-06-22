# BCM-RAG System Architecture

## Role

You are a senior AI Architect and Principal Engineer.

Your task is to design and implement an enterprise-grade RAG system for large-scale automotive BCM (Body Control Module) functional specification documents.

The target document is not a normal text document.

It contains:

* hierarchical specifications
* state machines
* signal definitions
* CAN messages
* configuration parameters
* fault diagnosis logic
* feature dependency chains
* cross-module references

The final system must support:

* accurate retrieval
* dependency tracing
* state transition reasoning
* signal relationship analysis
* cross-chapter reasoning
* long-context compression

Do NOT build a simple vector database chatbot.

The goal is an engineering-grade knowledge system.

---

# Core Design Principles

The system MUST contain three independent indexing layers:

## Layer 1 — Document Tree

Purpose:

Preserve original document structure.

Example:

Root
├── VMM
│   ├── State Management
│   ├── Voltage Management
│
├── Exterior Light
│
├── Interior Light
│
├── Window
│
├── Lock
│
└── Wiper

Requirements:

* preserve chapter hierarchy
* preserve parent-child relations
* preserve section path
* preserve page references
* preserve table ownership

The document tree is the primary navigation layer.

---

## Layer 2 — Knowledge Graph

Purpose:

Represent logical relationships.

Extract entities:

### Module

Examples:

* VMM
* Window
* Lock
* ExteriorLight
* Wiper

### State

Examples:

* Inactive
* Convenience
* Driving
* Abandoned

### Signal

Examples:

* PEPS_UsageMode
* ESC_VehicleSpeed
* VCU_StartActive

### Function

Examples:

* GlobalClose
* AutoLock
* CrashUnlock
* FollowMeHome

### Parameter

Examples:

* CfgTCMEOLOption
* cfgDoorLatchDuration

### Fault

Examples:

* KeyLost
* WindowJam
* SignalTimeout

Relationship Types:

* belongs_to
* transition_to
* triggered_by
* depends_on
* controls
* outputs
* requires
* configures
* reports
* references

Graph database:

Neo4j preferred.

The graph should store only high-value entities and relationships.

Do NOT create graphs for every sentence.

---

## Layer 3 — Vector Index

Purpose:

Store semantic content.

Storage:

Qdrant preferred.

Chunking Strategy:

DO NOT use fixed token chunking.

Chunk by logical unit.

Examples:

* State transition block
* Signal table
* Function description
* Configuration block
* Fault handling block

Every chunk must contain metadata:

{
"module": "",
"section_path": "",
"function": "",
"states": [],
"signals": [],
"parameters": [],
"graph_node_ids": [],
"parent_section": ""
}

Chunk size target:

800-2000 tokens.

Preserve semantic completeness.

---

# Parsing Layer

Primary parser:

Docling

Fallback parser:

MinerU

Requirements:

* preserve hierarchy
* preserve tables
* preserve lists
* preserve captions
* preserve references

Output:

Structured Document Model

Never directly chunk raw text.

---

# Retrieval Pipeline

The retrieval process MUST follow:

User Query
↓
Intent Analysis
↓
Graph Retrieval
↓
Document Tree Localization
↓
Vector Retrieval
↓
Merge Candidates
↓
Cross Encoder Rerank
↓
Rule-Based Rerank
↓
Context Compression
↓
LLM Answer

Do not skip any stage.

---

# Graph Retrieval

Purpose:

Discover dependency chains.

Example:

GlobalClose
↓
WindowEnable
↓
Driving
↓
PEPS_UsageMode

Graph traversal should support:

* 1-hop
* 2-hop
* configurable depth

Avoid unrestricted traversal.

---

# Reranking

Mandatory.

Stage 1:

Semantic Rerank

Recommended:

* BGE Reranker
* Qwen Reranker

Stage 2:

Rule Rerank

Additional scoring:

* same module
* same state
* same function
* same signal
* graph distance

Final Score:

FinalScore =
SemanticScore +
RuleScore

---

# Context Compression

Required.

The system must never send dozens of chunks directly to the LLM.

Compression objectives:

* remove duplicate facts
* merge equivalent rules
* keep dependency chains
* keep state transitions
* keep signal relationships

Output:

Evidence Package

Example:

State:
Driving

Dependencies:
PEPS_UsageMode
VehicleSpeed

Related Functions:
GlobalClose
AutoLock

Referenced Rules:
Section 2.3.4
Section 5.4.7

This compressed package is passed to the LLM.

---

# Data Flow

Raw Document
↓
Docling
↓
Document Tree
↓
Entity Extraction
↓
Knowledge Graph
↓
Logical Chunking
↓
Embeddings
↓
Vector Store

Query
↓
Graph Recall
↓
Vector Recall
↓
Rerank
↓
Compression
↓
LLM

---

# Engineering Requirements

Code Structure:

/parser
/document_tree
/entity_extraction
/knowledge_graph
/chunking
/vector_store
/retrieval
/rerank
/context_compression
/api

Every module must be independently testable.

Follow SOLID principles.

Avoid monolithic implementations.

Prefer extensible pipelines.

All architecture decisions should prioritize maintainability, observability, and future support for multi-document GraphRAG systems.
