"""Run full pipeline WITH VLM + image object storage + merged chunks."""
import os, json
from content_analysis import ContentAnalysisPipeline

key = "b6334912ba274d6dbedc42e9a4dc3181.ztmCrKVoGy8hOxGd"

p = ContentAnalysisPipeline(
    output_dir="output/content_analysis",
    vlm_api_key=key,
    vlm_model="glm-4v-flash",
    enable_vlm=True,
    vlm_backend="zhipu",
)
p.run("output/bcm_mineru/content_list.json", "output/bcm_mineru/images")

# Quick stats
with open("output/content_analysis/chunks.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)
tokens = [c["token_count"] for c in chunks["text_chunks"]]
s = sorted(tokens)
img_chunks = sum(1 for c in chunks["text_chunks"] if c.get("has_image"))
print(f"\n=== Final ===")
print(f"Text chunks: {len(chunks['text_chunks'])} (with images: {img_chunks})")
print(f"Tokens: avg={sum(tokens)/len(tokens):.0f} median={s[len(s)//2]} min={min(tokens)} max={max(tokens)}")
print(f"<50: {sum(1 for t in tokens if t<50)}/{len(tokens)} ({sum(1 for t in tokens if t<50)/len(tokens)*100:.0f}%)")

# Check image storage
import glob
stored = glob.glob("output/storage/images/**/*", recursive=True)
print(f"Images in storage: {len([f for f in stored if os.path.isfile(f)])}")
