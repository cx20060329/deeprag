"""BCM-RAG VLM Enhancement — Vision Language Model for image-aware retrieval.

Uses Zhipu GLM-4V (or any OpenAI-compatible VLM) to:
  1. Extract state transitions from flowcharts
  2. Convert table-images to structured JSON
  3. Generate searchable captions for all document images
  4. Extract timing logic from sequence/timing diagrams
  5. Cross-modal retrieval: answer queries from visual evidence

Architecture:
  Document Images → VLM Analysis → Structured Captions + Rules
  → Stored as additional chunks in vector DB
  → Queries can match visual content via text descriptions

Usage:
  enhancer = VLMEnhancer(api_key="...")
  enhancer.enhance_chunks("output/content_analysis/chunks.json")
  # Or analyze a single image:
  result = enhancer.analyze_image("path/to/image.png", context="State machine flowchart")
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Optional


class VLMEnhancer:
    """VLM-based image analysis for BCM document enhancement.

    Uses OpenAI-compatible Vision API (Zhipu GLM-4V, Doubao Vision, etc.)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
        model: str = "glm-4v-flash",
        cache_path: str = "output/vlm_cache.json",
    ):
        self.api_key = api_key or os.getenv("ZHIPU_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.cache_path = Path(cache_path)
        self._cache: dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self):
        if self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    # ---- Core: Analyze Image ----

    def analyze_image(
        self,
        image_path: str | Path,
        context: str = "",
        analysis_type: str = "auto",  # auto | flowchart | table | timing | circuit | caption
    ) -> dict:
        """Analyze a single image with VLM.

        Args:
            image_path: Path to image file (PNG/JPG)
            context: Optional surrounding text for better analysis
            analysis_type: What kind of analysis to perform

        Returns:
            {
                "image_type": "flowchart" | "table" | "timing_diagram" | "circuit" | "other",
                "summary": "1-2 sentence description",
                "extracted_text": "all visible text in the image",
                "structured_data": {...},  # depends on image_type
                "searchable_caption": "dense description for retrieval",
            }
        """
        image_path = Path(image_path)
        cache_key = f"{image_path.name}:{analysis_type}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # Encode image
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        ext = image_path.suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"

        # Build prompt
        prompt = self._build_prompt(context, analysis_type)
        messages = [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                {"type": "text", "text": prompt},
            ]},
        ]

        # Call VLM
        try:
            result = self._call_vlm(messages)
            result["image_path"] = str(image_path)
            self._cache[cache_key] = result
            self._save_cache()
            return result
        except Exception as e:
            return {"error": str(e), "image_path": str(image_path)}

    def _build_prompt(self, context: str, analysis_type: str) -> str:
        """Build VLM prompt based on analysis type."""
        base = f"""你是汽车BCM（车身控制模块）文档分析专家。

分析这张来自汽车BCM功能规范文档的图片。

上下文（图片周围的文字）：
{context if context else '无'}

请输出严格JSON格式（不要markdown代码块，不要解释）：
{{"image_type": "图片类型", "summary": "1-2句中文描述", "extracted_text": "图片中所有可见文字", "searchable_caption": "用于检索的详细描述（含关键信号名/状态名/模块名）"}}"""

        if analysis_type == "flowchart":
            base += """
额外提取（添加到JSON中）：
"states": ["识别到的状态名列表"],
"transitions": [{"from": "源状态", "to": "目标状态", "condition": "转移条件"}]"""
        elif analysis_type == "table":
            base += """
额外提取：
"headers": ["表头列名"],
"rows": [["单元格内容数组"]]"""
        elif analysis_type == "timing":
            base += """
额外提取：
"signals": ["信号名列表"],
"timing": {"period_ms": 周期毫秒数, "duty_cycle_pct": 占空比百分比, "duration_ms": 持续毫秒数}"""
        elif analysis_type == "circuit":
            base += """
额外提取：
"components": ["硬件组件列表"],
"connections": [{"from": "源", "to": "目标", "type": "HSD|LSD|CAN|LIN|硬线"}]"""

        return base

    def _call_vlm(self, messages: list[dict]) -> dict:
        """Call VLM API and parse JSON response."""
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1024,
            temperature=0.1,
        )
        text = response.choices[0].message.content

        # Parse JSON from response (may have markdown fences)
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return {"raw_response": text, "image_type": "unknown", "summary": text[:200]}

    # ---- Batch Enhance ----

    def enhance_chunks(
        self,
        chunks_path: str | Path = "output/content_analysis/chunks.json",
        mineru_images_dir: str = "output/bcm_mineru/images",
        limit: int = 0,
    ) -> list[dict]:
        """Enhance all chunks that contain images with VLM descriptions.

        Adds 'vlm_captions' to each image_ref in the chunk.

        Returns:
            List of {chunk_id, image_path, vlm_result} for each analyzed image
        """
        chunks_data = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
        chunks = chunks_data.get("text_chunks", [])
        results = []

        img_chunks = [c for c in chunks if c.get("has_image")]
        if limit:
            img_chunks = img_chunks[:limit]

        print(f"VLM Enhancer: {len(img_chunks)} chunks with images to analyze")

        for ci, chunk in enumerate(img_chunks):
            image_refs = chunk.get("image_refs", [])
            section_title = chunk.get("section_title", "")
            section_path = chunk.get("section_path", "")

            for ri, ref in enumerate(image_refs):
                storage_path = ref.get("storage_path", "")
                if not storage_path:
                    continue

                # Try to resolve the image path
                img_path = self._resolve_image_path(storage_path, mineru_images_dir)
                if not img_path:
                    continue

                # Determine analysis type from context
                atype = self._infer_type(section_title, chunk.get("text", ""))

                context = f"章节: {section_path} {section_title}\n周围文本: {chunk.get('text', '')[:300]}"

                print(f"  [{ci+1}/{len(img_chunks)}] {section_path}: {Path(img_path).name} ({atype})")
                result = self.analyze_image(img_path, context=context, analysis_type=atype)

                # Add to image_ref
                ref["vlm_caption"] = result.get("searchable_caption", "")
                ref["vlm_summary"] = result.get("summary", "")
                ref["vlm_type"] = result.get("image_type", "unknown")
                if "states" in result:
                    ref["vlm_states"] = result["states"]
                if "transitions" in result:
                    ref["vlm_transitions"] = result["transitions"]

                results.append({
                    "chunk_id": chunk.get("chunk_id", ""),
                    "section_path": section_path,
                    "image_path": storage_path,
                    "vlm_result": result,
                })

        # Save enhanced chunks
        chunks_data["text_chunks"] = chunks
        enhanced_path = Path(chunks_path).with_suffix(".vlm_enhanced.json")
        enhanced_path.write_text(json.dumps(chunks_data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"Enhanced chunks saved: {enhanced_path}")

        return results

    def _resolve_image_path(self, storage_path: str, mineru_dir: str) -> str | None:
        """Try to resolve a storage path to an actual file."""
        # Try direct path
        if Path(storage_path).exists():
            return storage_path

        # Try relative to project
        for prefix in [".", "output", ".."]:
            candidate = Path(prefix) / storage_path
            if candidate.exists():
                return str(candidate)

        # Try to find by hash in mineru images
        hash_name = Path(storage_path).stem
        mineru_path = Path(mineru_dir)
        for ext in [".png", ".jpg", ".jpeg"]:
            candidate = mineru_path / f"{hash_name}{ext}"
            if candidate.exists():
                return str(candidate)

        # Try matching just the hash (mineru uses full hash names)
        if mineru_path.exists():
            for f in mineru_path.iterdir():
                if hash_name in f.name:
                    return str(f)

        return None

    def _infer_type(self, section_title: str, chunk_text: str) -> str:
        """Infer the image type from context."""
        combined = (section_title + " " + chunk_text).lower()
        if any(w in combined for w in ["流程图", "flowchart", "流程", "flow"]):
            return "flowchart"
        if any(w in combined for w in ["时序", "timing", "波形", "闪烁"]):
            return "timing"
        if any(w in combined for w in ["电路", "circuit", "硬件", "hardware", "框图"]):
            return "circuit"
        if any(w in combined for w in ["表", "table", "参数", "配置"]):
            return "table"
        return "auto"

    # ---- VLM-Augmented Retrieval ----

    def augment_search(
        self,
        query: str,
        search_result: dict,
        chunks_data: dict | None = None,
    ) -> dict:
        """Augment a search result with VLM image analysis.

        If any top chunks have images, include VLM captions in the evidence.
        Optionally, call VLM to answer query directly from images.
        """
        merged = search_result.get("merged", [])
        vlm_snippets = []

        for item in merged[:5]:
            chunk = item.get("chunk", {})
            image_refs = chunk.get("image_refs", [])
            for ref in image_refs:
                caption = ref.get("vlm_caption", "") or ref.get("vlm_summary", "")
                if caption:
                    vlm_snippets.append(f"[图片描述: {caption}] (section {chunk.get('section_path','')})")

        if vlm_snippets:
            vlm_text = "\n".join(vlm_snippets)
            search_result["evidence"] = search_result.get("evidence", "") + "\n\n## VLM Image Analysis\n" + vlm_text
            search_result["vlm_snippets"] = vlm_snippets

        return search_result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    enhancer = VLMEnhancer()

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Test with a single image
        mineru_dir = "output/bcm_mineru/images"
        imgs = list(Path(mineru_dir).glob("*.png")) + list(Path(mineru_dir).glob("*.jpg"))
        if imgs:
            test_img = str(imgs[0])
            print(f"Testing with: {test_img}")
            result = enhancer.analyze_image(test_img, context="BCM系统流程图", analysis_type="flowchart")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"No images found in {mineru_dir}")
    else:
        # Batch enhance all chunks
        results = enhancer.enhance_chunks(limit=5)  # Limit to 5 for quick test
        print(f"\nAnalyzed {len(results)} images")
