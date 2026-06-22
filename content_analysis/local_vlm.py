"""BCM-RAG Local VLM — CPU-based image analysis using moondream2.

moondream2 (2B params) runs on CPU with acceptable speed (~10-30s/image).
Downloads model to local cache on first use.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from content_analysis.models import Entity, EntityType, Relationship, RelType


# Simplified prompt for moondream (small model, needs concise prompts)
MOONDREAM_PROMPT = """Describe this image from a Chinese automotive BCM document.
What type of diagram is it? (state machine, flowchart, block diagram, timing diagram, table, other)
What text is visible?
What are the key elements? (states, signals, modules, functions)

Output as JSON:
{
  "image_type": "state_machine|block_diagram|flowchart|timing_diagram|table|other",
  "summary": "Brief Chinese description",
  "text_content": "Readable text from the image",
  "key_entities": ["entity names found in the image"]
}"""


class LocalVLMAnalyzer:
    """Analyze images using local moondream2 model on CPU."""

    def __init__(self, model_name: str = "vikhyatk/moondream2"):
        self.model_name = model_name
        self._model = None
        self._tokenizer = None

    def _load_model(self):
        """Lazy-load model on first use."""
        if self._model is not None:
            return

        print("  Loading moondream2 model (first time downloads ~2GB)...")
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            trust_remote_code=True,
            torch_dtype=torch.float32,
            device_map="cpu",
        )
        self._model.eval()
        print("  Model loaded.")

    def analyze_images(
        self, image_paths: list[str], section_contexts: list[dict],
    ) -> list[dict]:
        """Analyze images with section context using local model."""
        self._load_model()
        results = []
        total = len(image_paths)

        for i, (img_path, ctx) in enumerate(zip(image_paths, section_contexts)):
            print(f"  [{i+1}/{total}] {Path(img_path).name} ...", end=" ", flush=True)
            t0 = time.time()

            result = self._analyze_single(img_path, ctx)
            elapsed = time.time() - t0

            if result:
                print(f"OK ({elapsed:.1f}s)")
                results.append(result)
            else:
                print(f"FAILED ({elapsed:.1f}s)")
                results.append(self._fallback(img_path, ctx))

        return results

    def _analyze_single(self, img_path: str, ctx: dict) -> dict | None:
        """Analyze a single image with moondream2."""
        try:
            from PIL import Image

            image = Image.open(img_path).convert("RGB")

            # Build context-aware prompt
            section_info = f"Section: {ctx.get('section_title', '')}, Module: {ctx.get('module', '')}"
            full_prompt = f"{MOONDREAM_PROMPT}\nContext: {section_info}"

            # moondream uses a special encode_image + query pattern
            image_embeds = self._model.encode_image(image)

            # First: short description
            desc = self._model.answer_question(image_embeds, full_prompt, self._tokenizer)

            # Parse JSON from response
            result = self._parse_json(desc)
            if result:
                result["image_path"] = img_path
                result["module"] = ctx.get("module", "")
                result["section_number"] = ctx.get("section_number", "")
                result["section_title"] = ctx.get("section_title", "")
                return result

            # Fallback: use description as summary
            return {
                "image_path": img_path,
                "image_type": "other",
                "summary": desc[:200],
                "text_content": desc[:1000],
                "module": ctx.get("module", ""),
                "section_number": ctx.get("section_number", ""),
                "section_title": ctx.get("section_title", ""),
                "raw_response": desc,
            }

        except Exception as e:
            print(f"error: {e}", end=" ")
            return None

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def _fallback(self, img_path: str, ctx: dict) -> dict:
        return {
            "image_path": img_path, "image_type": "unknown",
            "summary": f"Image in section {ctx.get('section_number', '')}: {ctx.get('section_title', '')}",
            "module": ctx.get("module", ""),
            "section_number": ctx.get("section_number", ""),
            "section_title": ctx.get("section_title", ""),
        }

    def results_to_entities(
        self, results: list[dict],
    ) -> tuple[list[Entity], list[Relationship]]:
        """Same interface as VLMAnalyzer — delegate."""
        from content_analysis.vlm_analyzer import VLMAnalyzer
        dummy = VLMAnalyzer.__new__(VLMAnalyzer)
        return dummy.results_to_entities(results)
