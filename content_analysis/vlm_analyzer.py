"""BCM-RAG Content Analysis — VLM Image Analyzer.

Supports multiple backends:
  1. 火山引擎 Ark API (Doubao Vision / openai-compatible)
  2. Anthropic Claude Vision API
  3. Local Ollama (llava, minicpm-v)
  4. Mock mode (no API key needed)

Auto-detects backend from available API keys.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from content_analysis.models import Entity, EntityType, Relationship, RelType


# ---------------------------------------------------------------------------
# VLM Prompts
# ---------------------------------------------------------------------------

BCM_IMAGE_ANALYSIS_PROMPT = """Analyze this image from a Chinese automotive BCM (Body Control Module) specification document.

The image is one of:
- State machine / state transition diagram
- System block diagram / architecture overview
- Signal timing diagram / sequence diagram
- Flowchart (algorithm, key learning, etc.)
- Table screenshot

Extract ALL structured information. Output ONLY valid JSON:
{
  "image_type": "state_machine|block_diagram|timing_diagram|flowchart|table|other",
  "summary": "Chinese description of what this image shows",
  "states": [{"name": "StateName", "description": "What this state means"}],
  "transitions": [{"from": "StateA", "to": "StateB", "trigger": "condition"}],
  "signals": [{"name": "SIGNAL_NAME", "description": "What this signal represents", "value": "0xNN if shown"}],
  "modules": ["Module names referenced"],
  "functions": ["Function names described"],
  "parameters": ["Cfg* parameters or config values"],
  "text_content": "All readable text extracted from the image"
}"""

BCM_IMAGE_SIMPLE_PROMPT = """Analyze this image from a Chinese BCM automotive document.
Output JSON:
{
  "image_type": "state_machine|block_diagram|timing_diagram|flowchart|table|screenshot|other",
  "summary": "Brief Chinese description",
  "text_content": "All readable text from the image",
  "key_entities": ["Entity names found"]
}"""


# ---------------------------------------------------------------------------
# Backend auto-detection
# ---------------------------------------------------------------------------

def _detect_backend(api_key: str | None = None) -> str:
    """Auto-detect which VLM backend to use."""
    # 1. 智谱 GLM
    zhipu_key = api_key or os.environ.get("ZHIPU_API_KEY", "")
    if zhipu_key:
        return "zhipu"

    # 2. 火山引擎 Ark
    ark_key = api_key or os.environ.get("ARK_API_KEY", "")
    if ark_key:
        return "ark"

    # 3. Anthropic Claude
    anthro_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if anthro_key:
        return "anthropic"

    # 4. Ollama local
    try:
        import subprocess
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return "ollama"
    except Exception:
        pass

    return "mock"


class VLMAnalyzer:
    """Analyze BCM document images using VLM (volcano ark / anthropic / ollama).

    Auto-detects available backend from API keys / local services.
    Falls back to mock mode with context-based descriptions.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        backend: str = "auto",
        mock_mode: bool = False,
    ):
        self.api_key = api_key
        self.backend = backend if backend != "auto" else _detect_backend(api_key)
        self.mock_mode = mock_mode or self.backend == "mock"

        # Set default model per backend
        if model:
            self.model = model
        elif self.backend == "zhipu":
            self.model = "glm-4v-flash"
        elif self.backend == "ark":
            self.model = "kimi-k2-instruct"
        elif self.backend == "anthropic":
            self.model = "claude-sonnet-4-20250514"
        elif self.backend == "ollama":
            self.model = "minicpm-v:latest"
        else:
            self.model = "mock"

        print(f"  VLM Backend: {self.backend} (model={self.model})")

    # ---- public API -------------------------------------------------------

    def analyze_images(
        self, image_paths: list[str], section_contexts: list[dict],
    ) -> list[dict]:
        """Analyze images with section context. Falls back to mock on failure."""
        if self.mock_mode:
            return self._mock_analyze(image_paths, section_contexts)

        results = []
        total = len(image_paths)

        for i, (img_path, ctx) in enumerate(zip(image_paths, section_contexts)):
            print(f"  [{i+1}/{total}] {Path(img_path).name} ...", end=" ")

            context_text = self._build_context(ctx)
            result = self._analyze_single(img_path, context_text, ctx)
            if result:
                print("OK")
                results.append(result)
            else:
                print("FALLBACK")
                results.append(self._make_fallback(img_path, ctx))

            if i < total - 1:
                time.sleep(0.3)

        return results

    def results_to_entities(
        self, results: list[dict],
    ) -> tuple[list[Entity], list[Relationship]]:
        """Convert VLM analysis results to entities + BELONGS_TO relationships."""
        entities = []
        relationships = []

        for r in results:
            module = r.get("module", "")
            section_num = r.get("section_number", "")
            img_path = r.get("image_path", "")

            # Image entity
            img_name = Path(img_path).stem if img_path else "unknown"
            img_eid = f"image_{module}_{img_name}" if module else f"image_{img_name}"
            entities.append(Entity(
                entity_id=img_eid,
                entity_type=EntityType.FUNCTION,
                name=f"Image: {r.get('summary', img_name)[:80]}",
                module=module,
                section_path=section_num,
                properties={
                    "image_path": img_path,
                    "image_type": r.get("image_type", "unknown"),
                    "text_content": r.get("text_content", "")[:1000],
                    "mock": r.get("mock", False),
                },
            ))

            # BELONGS_TO: image → section
            if section_num:
                section_id = f"sec_{section_num.replace('.', '_')}"
                relationships.append(Relationship(
                    source_id=img_eid, target_id=section_id,
                    rel_type=RelType.BELONGS_TO,
                ))

            # Extracted states → entities + BELONGS_TO to image
            for state in r.get("states", []):
                name = state.get("name", "")
                if name:
                    eid = f"state_{module}_{name}"
                    entities.append(Entity(
                        entity_id=eid, entity_type=EntityType.STATE,
                        name=name, module=module, section_path=section_num,
                        properties={"source": "vlm_image", "image_path": img_path},
                    ))
                    relationships.append(Relationship(
                        source_id=eid, target_id=img_eid, rel_type=RelType.BELONGS_TO,
                    ))

            # Transitions
            for t in r.get("transitions", []):
                frm, to = t.get("from", ""), t.get("to", "")
                if frm and to:
                    relationships.append(Relationship(
                        source_id=f"state_{module}_{frm}",
                        target_id=f"state_{module}_{to}",
                        rel_type=RelType.TRANSITION_TO,
                        properties={"trigger": t.get("trigger", ""), "source": "vlm_image"},
                    ))

            # Signals
            for sig in r.get("signals", []):
                name = sig.get("name", "")
                if name:
                    eid = f"signal_{module}_{name}"
                    entities.append(Entity(
                        entity_id=eid, entity_type=EntityType.SIGNAL,
                        name=name, module=module, section_path=section_num,
                        properties={"source": "vlm_image", "value": sig.get("value", "")},
                    ))
                    relationships.append(Relationship(
                        source_id=eid, target_id=img_eid, rel_type=RelType.BELONGS_TO,
                    ))

            # Functions
            for func in r.get("functions", []):
                if func:
                    eid = f"func_{module}_{func.replace(' ', '_')[:40]}"
                    entities.append(Entity(
                        entity_id=eid, entity_type=EntityType.FUNCTION,
                        name=func, module=module, section_path=section_num,
                        properties={"source": "vlm_image"},
                    ))
                    relationships.append(Relationship(
                        source_id=eid, target_id=img_eid, rel_type=RelType.BELONGS_TO,
                    ))

        return entities, relationships

    # ---- single image analysis --------------------------------------------

    def _analyze_single(self, img_path: str, context_text: str, ctx: dict) -> dict | None:
        """Dispatch to the correct backend."""
        if self.backend == "zhipu":
            return self._analyze_zhipu(img_path, context_text, ctx)
        elif self.backend == "ark":
            return self._analyze_ark(img_path, context_text, ctx)
        elif self.backend == "anthropic":
            return self._analyze_anthropic(img_path, context_text, ctx)
        elif self.backend == "ollama":
            return self._analyze_ollama(img_path, context_text, ctx)
        return None

    # ---- 智谱 GLM-4V ----------------------------------------------------

    def _analyze_zhipu(self, img_path: str, context_text: str, ctx: dict) -> dict | None:
        """Analyze via 智谱 GLM-4V API."""
        key = self.api_key or os.environ.get("ZHIPU_API_KEY", "")
        if not key:
            print("no ZHIPU_API_KEY", end=" ")
            return None

        from openai import OpenAI

        # 智谱 API 兼容 OpenAI 格式
        client = OpenAI(
            api_key=key,
            base_url="https://open.bigmodel.cn/api/paas/v4/",
        )

        img_b64 = self._encode_image(img_path)
        if not img_b64:
            return None

        prompt = self._select_prompt(ctx)
        full_prompt = f"{context_text}\n---\n{prompt}"

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": full_prompt},
                    ],
                }],
                max_tokens=1024,
                temperature=0.1,
            )
            text = response.choices[0].message.content or ""
            result = self._parse_json(text)
            if result:
                result["image_path"] = img_path
                result["module"] = ctx.get("module", "")
                result["section_number"] = ctx.get("section_number", "")
                result["section_title"] = ctx.get("section_title", "")
                return result
            # Fallback: return raw text
            return {
                "image_path": img_path, "image_type": "unknown",
                "summary": text[:200], "text_content": text[:1000],
                "module": ctx.get("module", ""),
                "section_number": ctx.get("section_number", ""),
                "section_title": ctx.get("section_title", ""),
                "raw_response": text,
            }
        except Exception as e:
            print(f"zhipu error: {str(e)[:100]}", end=" ")

        return None

    # ---- 火山引擎 Ark (OpenAI-compatible) ---------------------------------

    def _analyze_ark(self, img_path: str, context_text: str, ctx: dict) -> dict | None:
        """Analyze via 火山引擎 Ark API.

        Key format: ark-xxxx-xxxx — this is the endpoint ID.
        Uses endpoint-specific URL pattern.
        """
        key = self.api_key or os.environ.get("ARK_API_KEY", "")
        if not key:
            print("no ARK_API_KEY", end=" ")
            return None

        from openai import OpenAI

        # The key IS the endpoint ID — use endpoint-specific URL
        endpoint_id = key
        base_url = f"https://ark.cn-beijing.volces.com/api/v3"

        client = OpenAI(
            api_key=key,
            base_url=base_url,
        )

        img_b64 = self._encode_image(img_path)
        if not img_b64:
            return None

        prompt = self._select_prompt(ctx)
        full_prompt = f"{context_text}\n---\n{prompt}"

        # Try multiple URL patterns
        urls_to_try = [
            f"https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        ]
        models_to_try = [self.model, "doubao-1-5-vision-pro-32k", "ep-20250616115653-bxlm6"]

        for model in models_to_try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            {"type": "text", "text": full_prompt},
                        ],
                    }],
                    max_tokens=2000,
                    temperature=0.1,
                )
                text = response.choices[0].message.content or ""
                result = self._parse_json(text)
                if result:
                    result["image_path"] = img_path
                    result["module"] = ctx.get("module", "")
                    result["section_number"] = ctx.get("section_number", "")
                    result["section_title"] = ctx.get("section_title", "")
                    return result
                else:
                    # Return raw text as fallback
                    return {
                        "image_path": img_path,
                        "image_type": "unknown",
                        "summary": text[:200],
                        "text_content": text[:1000],
                        "module": ctx.get("module", ""),
                        "section_number": ctx.get("section_number", ""),
                        "section_title": ctx.get("section_title", ""),
                        "raw_response": text,
                    }
            except Exception as e:
                err_str = str(e)[:80]
                if "401" in err_str or "AuthenticationError" in err_str:
                    continue  # try next model
                print(f"ark({model}): {err_str}", end=" ")
                break

        return None

    # ---- Anthropic Claude Vision ------------------------------------------

    def _analyze_anthropic(self, img_path: str, context_text: str, ctx: dict) -> dict | None:
        """Analyze via Anthropic Claude Vision API."""
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return None

        from anthropic import Anthropic
        client = Anthropic(api_key=key)

        img_b64 = self._encode_image(img_path)
        if not img_b64:
            return None

        ext = Path(img_path).suffix.lower()
        media_type = "image/png" if ext == ".png" else "image/jpeg"

        prompt = self._select_prompt(ctx)
        full_prompt = f"{context_text}\n---\n{prompt}"

        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": media_type, "data": img_b64,
                        }},
                        {"type": "text", "text": full_prompt},
                    ],
                }],
            )
            text = message.content[0].text
            result = self._parse_json(text)
            if result:
                result["image_path"] = img_path
                result["module"] = ctx.get("module", "")
                result["section_number"] = ctx.get("section_number", "")
                result["section_title"] = ctx.get("section_title", "")
                return result
        except Exception as e:
            print(f"anthropic error: {e}", end=" ")

        return None

    # ---- Ollama local -----------------------------------------------------

    def _analyze_ollama(self, img_path: str, context_text: str, ctx: dict) -> dict | None:
        """Analyze via local Ollama (llava / minicpm-v)."""
        import subprocess

        img_b64 = self._encode_image(img_path)
        if not img_b64:
            return None

        prompt = self._select_prompt(ctx)
        full_prompt = f"{context_text}\n---\n{prompt}"

        try:
            result = subprocess.run(
                ["ollama", "run", self.model, full_prompt],
                input=img_b64.encode() if hasattr(self, '_ollama_image_support') else None,
                capture_output=True, text=True, timeout=120,
            )
            # Try with image as file reference
            result = subprocess.run(
                ["ollama", "run", self.model],
                input=f"{full_prompt}\n[Image: {img_path}]",
                capture_output=True, text=True, timeout=120,
            )
            text = result.stdout.strip()
            parsed = self._parse_json(text)
            if parsed:
                parsed["image_path"] = img_path
                parsed["module"] = ctx.get("module", "")
                parsed["section_number"] = ctx.get("section_number", "")
                parsed["section_title"] = ctx.get("section_title", "")
                return parsed
        except Exception as e:
            print(f"ollama error: {e}", end=" ")

        return None

    # ---- mock mode --------------------------------------------------------

    def _mock_analyze(
        self, image_paths: list[str], section_contexts: list[dict],
    ) -> list[dict]:
        """Mock VLM analysis from section context (no API call)."""
        results = []
        for img_path, ctx in zip(image_paths, section_contexts):
            fname = Path(img_path).stem
            title = ctx.get("section_title", "")
            adjacent = ctx.get("adjacent_text", "")[:300]
            module = ctx.get("module", "")
            section_num = ctx.get("section_number", "")

            # Infer image type from section title
            if any(kw in title for kw in ("状态图", "状态迁移", "State Machine")):
                image_type = "state_machine"
            elif any(kw in title for kw in ("流程图", "流程", "Flow")):
                image_type = "flowchart"
            elif any(kw in title for kw in ("框图", "架构", "系统图")):
                image_type = "block_diagram"
            elif any(kw in title for kw in ("时序", "时序图", "Timing")):
                image_type = "timing_diagram"
            else:
                image_type = "other"

            summary = f"[Mock] {title} 中的图片"
            if adjacent:
                summary += f"，上下文: {adjacent[:100]}"

            results.append({
                "image_path": img_path, "image_type": image_type,
                "summary": summary, "module": module,
                "section_number": section_num, "section_title": title,
                "text_content": adjacent[:500],
                "states": [], "transitions": [], "signals": [],
                "modules": [module] if module else [],
                "functions": [], "parameters": [], "faults": [],
                "relationships": [], "mock": True,
            })
        return results

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _build_context(ctx: dict) -> str:
        return f"""Image context:
- Module: {ctx.get('module', 'Unknown')}
- Section: {ctx.get('section_number', '')} {ctx.get('section_title', '')}
- Adjacent text: {ctx.get('adjacent_text', 'N/A')[:500]}"""

    @staticmethod
    def _select_prompt(ctx: dict) -> str:
        if ctx.get("is_state_machine") or any(
            kw in ctx.get("section_title", "")
            for kw in ("状态", "转移", "迁移", "State")
        ):
            return BCM_IMAGE_ANALYSIS_PROMPT
        return BCM_IMAGE_SIMPLE_PROMPT

    @staticmethod
    def _encode_image(img_path: str) -> str | None:
        try:
            with open(img_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return None

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def _make_fallback(self, img_path: str, ctx: dict) -> dict:
        return {
            "image_path": img_path, "image_type": "unknown",
            "summary": f"Image in section {ctx.get('section_number', '')}: {ctx.get('section_title', '')}",
            "module": ctx.get("module", ""),
            "section_number": ctx.get("section_number", ""),
            "section_title": ctx.get("section_title", ""),
            "text_content": ctx.get("adjacent_text", "")[:500],
        }
