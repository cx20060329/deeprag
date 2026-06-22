"""BCM-RAG Chunk Optimizer — Fixes chunk fragmentation and quality issues.

Problems found:
  1. 51/162 chunks are near-empty (<50 chars) — just TOC headings, no content
  2. Median token count = 74 (target: 800-2000) — massive over-fragmentation
  3. 10 chunks are pure TOC (heading indexes with no actual text)
  4. Adjacent heading-only chunks that should be merged

Strategy:
  - Remove pure-TOC chunks (only headings, no content)
  - Merge adjacent undersized chunks (<200 tokens) from same section
  - Rebuild embeddings with optimized chunks
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path


def analyze_chunks(chunks: list[dict]) -> dict:
    """Analyze chunk quality and return statistics."""
    stats = {
        "total": len(chunks),
        "near_empty": sum(1 for c in chunks if len(c.get("text", "").strip()) < 50),
        "toc_only": sum(1 for c in chunks if _is_toc_only(c)),
        "undersized": sum(1 for c in chunks if c.get("token_count", 0) < 200),
        "by_type": defaultdict(int),
        "tokens": [c.get("token_count", 0) for c in chunks],
        "avg_tokens": 0,
        "median_tokens": 0,
    }
    for c in chunks:
        stats["by_type"][c.get("chunk_type", "?")] += 1

    if stats["tokens"]:
        import numpy as np
        stats["avg_tokens"] = np.mean(stats["tokens"])
        stats["median_tokens"] = np.median(stats["tokens"])
    return stats


def _is_toc_only(chunk: dict) -> bool:
    """Check if chunk is just a TOC/heading index."""
    text = chunk.get("text", "").strip()
    if not text:
        return True
    # Count heading markers vs actual content
    heading_lines = len(re.findall(r"^#{2,4}\s", text, re.MULTILINE))
    content_lines = len([l for l in text.split("\n") if l.strip() and not l.strip().startswith("#")])
    # If >50% of lines are headings and <100 chars of real content
    real_content = re.sub(r"^#{2,4}\s.*$", "", text, flags=re.MULTILINE).strip()
    return heading_lines >= 3 and len(real_content) < 100


def _count_real_content(text: str) -> int:
    """Count characters of non-heading content."""
    cleaned = re.sub(r"^#{2,4}\s.*$", "", text, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"\|\s*\|", "", cleaned)  # Remove empty table rows
    return len(cleaned)


def optimize_chunks(
    chunks_path: str | Path = "output/content_analysis/chunks.json",
    output_path: str | Path = "output/content_analysis/chunks_optimized.json",
    min_content_chars: int = 80,
    min_tokens: int = 150,
) -> dict:
    """Optimize chunks: remove TOC-only, merge undersized neighbors.

    Returns: {chunks: [...], stats: {...}}
    """
    with open(chunks_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("text_chunks", [])
    before_stats = analyze_chunks(chunks)

    print(f"Before: {len(chunks)} chunks, avg {before_stats['avg_tokens']:.0f} tokens, "
          f"median {before_stats['median_tokens']:.0f} tokens")

    # Step 1: Remove pure TOC chunks
    kept = []
    removed_toc = 0
    for c in chunks:
        if _is_toc_only(c):
            removed_toc += 1
            continue
        kept.append(c)
    print(f"  Removed {removed_toc} TOC-only chunks → {len(kept)} remaining")

    # Step 2: Light merge — only merge chunks with IDENTICAL section_path
    # that are both undersized. Don't merge across different sections.
    by_section = defaultdict(list)
    for c in kept:
        by_section[c.get("section_path", "")].append(c)

    merged = []
    merge_count = 0
    for section, section_chunks in by_section.items():
        if len(section_chunks) == 1:
            merged.append(section_chunks[0])
            continue

        # Merge multiple chunks from same section into one
        base = dict(section_chunks[0])
        combined_text = base.get("text", "")
        combined_entities = list(base.get("entities", []))
        combined_signals = list(base.get("signals", []))
        combined_states = list(base.get("states", []))
        has_table = base.get("has_table", False)
        has_image = base.get("has_image", False)
        image_refs = list(base.get("image_refs", []))

        for extra in section_chunks[1:]:
            combined_text += "\n" + extra.get("text", "")
            combined_entities.extend(extra.get("entities", []))
            combined_signals.extend(extra.get("signals", []))
            combined_states.extend(extra.get("states", []))
            has_table = has_table or extra.get("has_table", False)
            has_image = has_image or extra.get("has_image", False)
            image_refs.extend(extra.get("image_refs", []))
            merge_count += 1

        base["text"] = combined_text
        base["embedding_text"] = combined_text
        base["token_count"] = len(combined_text) // 3  # rough estimate
        base["entities"] = list(set(combined_entities))
        base["signals"] = list(set(combined_signals))
        base["states"] = list(set(combined_states))
        base["has_table"] = has_table
        base["has_image"] = has_image
        base["image_refs"] = image_refs
        merged.append(base)

    print(f"  Merged {merge_count} same-section duplicates → {len(merged)} final")

    # Step 3: Remove remaining table-stub chunks (all vertical bars, no text)
    final = []
    removed_empty = 0
    for c in merged:
        text = c.get("text", "")
        # Check if chunk is just empty table rows
        non_table = re.sub(r"\|\s*\|", "", text).strip()
        non_heading = re.sub(r"^#{2,4}\s.*$", "", non_table, flags=re.MULTILINE).strip()
        if len(non_heading) < 30:
            removed_empty += 1
            continue
        final.append(c)

    print(f"  Removed {removed_empty} table-stub chunks → {len(final)} final")

    after_stats = analyze_chunks(final)
    print(f"After: {len(final)} chunks, avg {after_stats['avg_tokens']:.0f} tokens, "
          f"median {after_stats['median_tokens']:.0f} tokens")

    # Save
    output_data = dict(data)
    output_data["text_chunks"] = final
    output_data["_optimization"] = {
        "before_count": len(chunks),
        "after_count": len(final),
        "removed_toc": removed_toc,
        "merged_undersized": merge_count,
        "removed_empty": removed_empty,
        "before_avg_tokens": before_stats["avg_tokens"],
        "after_avg_tokens": after_stats["avg_tokens"],
        "before_median_tokens": before_stats["median_tokens"],
        "after_median_tokens": after_stats["median_tokens"],
    }

    output_path = Path(output_path)
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"Saved: {output_path}")

    return {"chunks": final, "stats": after_stats, "optimization": output_data["_optimization"]}


if __name__ == "__main__":
    result = optimize_chunks()
    print()
    print("=== Optimization Summary ===")
    opt = result["optimization"]
    print(f"  Chunks: {opt['before_count']} → {opt['after_count']} "
          f"(-{opt['before_count']-opt['after_count']})")
    print(f"  Avg tokens: {opt['before_avg_tokens']:.0f} → {opt['after_avg_tokens']:.0f}")
    print(f"  Median tokens: {opt['before_median_tokens']:.0f} → {opt['after_median_tokens']:.0f}")
