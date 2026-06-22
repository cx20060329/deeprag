---
name: retrieval-optimization-2026-06-18
description: Retrieval pipeline optimizations based on evaluation analysis
metadata:
  type: project
---

# Retrieval Optimization — 2026-06-18

## Changes Made

### 1. Intent Analysis Enhancement (`retrieval/pipeline.py`)

**Module aliases**: Added `_MODULE_ALIASES` dict mapping common Chinese/English terms to canonical module names:
- "BCM" → "VMM" (fixes "BCM休眠条件" routing)
- "灯光/车灯" → "ExteriorLight"
- "车窗" → "Window", "门锁" → "Lock", etc.

**Query type hints**: New regex patterns detect what kind of information the user wants:
- `_SIGNAL_DEF_PATTERNS`: "取值/定义/编码/CAN ID" → `hint_signal_def=True`
- `_TRANSITION_PATTERNS`: "迁移/转移/进入条件" → `hint_transition=True`

**Signal→module routing**: When a signal is identified, its owning module (from entity metadata) is automatically added to intent modules.

**Exact name matching**: English identifiers (like PEPS_UsageMode) are tried with exact `get_by_name` before substring `search_entities`, preventing short keywords from matching wrong entities.

### 2. Stage 5 Merge Enhancement

- Signal→module routing: chunks from signal's owning module get **1.6x boost** (2.5x when also signal_table type)
- Signal definition hint: signal_table chunks get **1.35x boost**
- Anti-boost: general_text chunks from non-owning modules get **0.85x penalty** when query asks for signal definition
- Transition hint: state_transition/state_machine chunks get **1.3x boost**
- Tree section depth: deeper section matches get higher tree_support score

### 3. Stage 7 Rule Rerank Enhancement

- Signal definition hint → signal_table chunks get +0.2 rule bonus
- Transition hint → state_transition/state_machine chunks get +0.2 rule bonus

### 4. Tree Localization Enhancement

- Now matches parent sections too (entity at 2.3.4.1 also matches 2.3.4, 2.3)
- Returns deeper/exact matches first
- Section depth tracking for scoring

### 5. Golden Query Expansion

Expanded from 25 to 35 queries, adding:
- More diagnostic type queries
- Signal definition disambiguation queries
- State transition queries
- ATWS/TheftProtection module coverage

## Results

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| VMM "BCM休眠条件" | ❌ ExteriorLight | ✓ VMM | Fixed |
| VMM "BCM唤醒条件" | ❌ ExteriorLight | ✓ VMM | Fixed |
| VMM "PEPS_UsageMode取值" | ❌ ExteriorLight | ❌ ExteriorLight* | Data issue |
| Hit@1 (estimate) | 84% | ~92% | +8% |

*PEPS_UsageMode query failure is due to chunk data quality: the signal definition table in VMM section 2.2.1.1 did not produce a chunk containing "PEPS_UsageMode" text. The retrieval correctly identifies VMM as target and boosts signal_table chunks, but none exist with that text. Fix requires re-running content analysis pipeline.

## Remaining Work

1. Download BGE-M3 + BGE Reranker v2 M3 models (use `scripts/download_models_light.py`)
2. Rebuild vector_points.json with BGE-M3 embeddings
3. Re-run content analysis to fix PEPS_UsageMode signal_table chunk
4. Full evaluation with dense+reranker enabled
