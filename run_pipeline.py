"""Run content analysis pipeline and print stats."""
import json
from content_analysis import ContentAnalysisPipeline

p = ContentAnalysisPipeline(output_dir="output/content_analysis", enable_vlm=False)
p.run("output/bcm_mineru/content_list.json", "output/bcm_mineru/images")

with open("output/content_analysis/chunks.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)
tokens = [c["token_count"] for c in chunks["text_chunks"]]
s = sorted(tokens)
small50 = sum(1 for t in tokens if t < 50)
small30 = sum(1 for t in tokens if t < 30)

print()
print(f"Chunks: {len(chunks['text_chunks'])} (was 1714)")
print(f"Tokens: min={min(tokens)} max={max(tokens)} avg={sum(tokens)/len(tokens):.0f} median={s[len(s)//2]}")
print(f"<50: {small50}/{len(tokens)} ({small50/len(tokens)*100:.0f}%)")
print(f"<30: {small30}/{len(tokens)} ({small30/len(tokens)*100:.0f}%)")
print("DONE")
