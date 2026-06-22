"""Run pipeline with cached VLM results + new chunk builder (image→text merge)."""
import json, os
from content_analysis.section_tree import SectionTreeBuilder
from content_analysis.entity_extractor import EntityExtractor
from content_analysis.chunk_builder import ChunkBuilder
from content_analysis.kg_exporter import KnowledgeGraphExporter
from content_analysis.vector_exporter import VectorStoreExporter
from content_analysis.models import Entity, Relationship, RelType, EntityType

print("=" * 60)
print("Pipeline V2: Cached VLM + Merged Chunks")
print("=" * 60)

# Load content list (handle both page-wrapped and flat formats)
with open("output/bcm_mineru/content_list.json", "r", encoding="utf-8") as f:
    raw_cl = json.load(f)

is_page_wrapped = (
    isinstance(raw_cl, list) and len(raw_cl) > 0
    and isinstance(raw_cl[0], list)
)

if is_page_wrapped:
    flat_list: list[dict] = []
    for page in raw_cl:
        flat_list.extend(page)
    print(f"Loaded {len(flat_list)} items across {len(raw_cl)} pages")
else:
    flat_list = raw_cl
    print(f"Loaded {len(flat_list)} items (flat format)")

# Load cached VLM results
with open("output/vlm_cache.json", "r", encoding="utf-8") as f:
    vlm_results = json.load(f)
print(f"Loaded {len(vlm_results)} cached VLM results")

# Stage 1: Section Tree (pass page-wrapped to preserve page info)
tree_builder = SectionTreeBuilder()
tree = tree_builder.build(raw_cl if is_page_wrapped else flat_list)
print(f"Section tree: {len(tree.nodes)} nodes")
print(f"  Pages tracked: {len(tree.page_index)}")
print(f"  Tables owned: {len(tree.table_owner)}")

# Stage 2: Entity Extraction (text) — uses flat list
extractor = EntityExtractor()
entities, rels = extractor.extract(flat_list, tree)

# Add BELONGS_TO
for e in entities:
    if e.section_path:
        rels.append(Relationship(e.entity_id, f"section_{e.section_path.replace('.', '_')}", RelType.BELONGS_TO))

# Add section entities
seen = set()
for e in entities:
    if e.section_path and e.section_path not in seen:
        seen.add(e.section_path)
        entities.append(Entity(f"section_{e.section_path.replace('.', '_')}", EntityType.MODULE, f"Section {e.section_path}", e.module, e.section_path))

# Stage 3: VLM entities (from cache)
from content_analysis.vlm_analyzer import VLMAnalyzer
vlm = VLMAnalyzer.__new__(VLMAnalyzer)
vlm_ents, vlm_rels = vlm.results_to_entities(vlm_results)
entities.extend(vlm_ents)
rels.extend(vlm_rels)

print(f"Entities: {len(entities)} (text + VLM)")
print(f"Relationships: {len(rels)}")

# Stage 4: Chunk Builder (NEW — images merged into text)
chunk_builder = ChunkBuilder(storage_dir="output/storage")
chunks = chunk_builder.build(flat_list, tree, entities, "output/bcm_mineru/images", vlm_results)
print(f"Text chunks: {len(chunks.text_chunks)}")

# Stats
from collections import Counter
tc = Counter(c.chunk_type for c in chunks.text_chunks)
for t, c in sorted(tc.items()):
    print(f"  {t}: {c}")
img_chunks = sum(1 for c in chunks.text_chunks if c.has_image)
print(f"Chunks with images: {img_chunks}")

# Check image descriptions in chunks
import re
desc_count = 0
for c in chunks.text_chunks:
    descs = re.findall(r'\[描述:\s*(.+?)\]', c.text)
    desc_count += len(descs)
print(f"Total image descriptions embedded: {desc_count}")

# Check object storage
import glob
stored = glob.glob("output/storage/images/**/*", recursive=True)
stored_files = [f for f in stored if os.path.isfile(f)]
print(f"Images in object storage: {len(stored_files)}")

# Sample a chunk with images
for c in chunks.text_chunks:
    if c.has_image:
        print(f"\n=== Sample chunk with image ===")
        print(f"  module: {c.module}  section: {c.section_path}")
        print(f"  title: {c.section_title[:60]}")
        descs = re.findall(r'\[描述:\s*(.+?)\]', c.text)
        for d in descs[:2]:
            print(f"  description: {d.strip()[:200]}")
        stored_refs = re.findall(r'\[图片:\s*(.+?)\]', c.text)
        for s in stored_refs[:2]:
            print(f"  storage: {s}")
            print(f"  exists: {os.path.exists(s)}")
        break

# Export
out = "output/content_analysis"
os.makedirs(out, exist_ok=True)

# KG
kg_exp = KnowledgeGraphExporter()
(open(f"{out}/knowledge_graph.json", "w", encoding="utf-8")).write(
    json.dumps(kg_exp.export_json(entities, rels), ensure_ascii=False, indent=2))
(open(f"{out}/knowledge_graph.cypher", "w", encoding="utf-8")).write(kg_exp.export_cypher(entities, rels))

# Chunks
chunk_list = {"text_chunks": [
    {"chunk_id": c.chunk_id, "chunk_type": c.chunk_type,
     "module": c.module, "section_path": c.section_path,
     "section_title": c.section_title, "text": c.text,
     "embedding_text": c.embedding_text,
     "entities": c.entities, "signals": c.signals,
     "states": c.states, "parameters": c.parameters,
     "has_table": c.has_table, "has_image": c.has_image,
     "token_count": c.token_count}
    for c in chunks.text_chunks
]}
(open(f"{out}/chunks.json", "w", encoding="utf-8")).write(
    json.dumps(chunk_list, ensure_ascii=False, indent=2))

# Vectors
vec_exp = VectorStoreExporter()
(open(f"{out}/vector_points.json", "w", encoding="utf-8")).write(
    json.dumps(vec_exp.export_all(chunks), ensure_ascii=False, indent=2))

print(f"\nExported to {out}/")
print("DONE")
