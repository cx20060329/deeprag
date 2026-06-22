"""Download BGE-M3 and BGE Reranker v2 M3 models to project models/ dir.

Supports:
  - Direct HF Hub download
  - HF mirror (hf-mirror.com) for faster download in China
  - modelscope as alternative source

Usage:
  python scripts/download_models.py
  HF_ENDPOINT=https://hf-mirror.com python scripts/download_models.py  # China mirror
"""

import os
import sys
from pathlib import Path

# Project models directory
MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "BAAI"

MODELS = [
    {
        "name": "BAAI/bge-m3",
        "slug": "bge-m3",
        "files_required": ["pytorch_model.bin", "config.json", "tokenizer.json"],
        "size_mb": 268,
    },
    {
        "name": "BAAI/bge-reranker-v2-m3",
        "slug": "bge-reranker-v2-m3",
        "files_required": ["model.safetensors", "config.json", "tokenizer.json"],
        "size_mb": 43,
    },
]


def is_model_present(model_info: dict) -> bool:
    """Check if a model's required files exist and are non-trivial."""
    local_dir = MODELS_DIR / model_info["slug"]
    if not local_dir.is_dir():
        return False
    for fname in model_info["files_required"]:
        fpath = local_dir / fname
        if not fpath.exists():
            return False
        # Check file is non-trivial (>1MB for weights, >100 bytes for configs)
        min_size = 1_000_000 if fname.endswith((".bin", ".safetensors")) else 100
        if fpath.stat().st_size < min_size:
            return False
    return True


def download_from_hf(model_info: dict) -> bool:
    """Download model from HuggingFace Hub (or mirror via HF_ENDPOINT env)."""
    from huggingface_hub import snapshot_download

    local_dir = MODELS_DIR / model_info["slug"]
    local_dir.mkdir(parents=True, exist_ok=True)

    endpoint = os.environ.get("HF_ENDPOINT", "")
    if endpoint:
        print(f"  Using HF mirror: {endpoint}")

    try:
        snapshot_download(
            repo_id=model_info["name"],
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            resume_download=True,
            max_workers=4,
        )
        return is_model_present(model_info)
    except Exception as e:
        print(f"  HF download failed: {e}")
        return False


def download_from_modelscope(model_info: dict) -> bool:
    """Download model from modelscope (alternative for China)."""
    try:
        from modelscope import snapshot_download as ms_snapshot_download
    except ImportError:
        print("  modelscope not installed. Run: pip install modelscope")
        return False

    local_dir = MODELS_DIR / model_info["slug"]
    local_dir.mkdir(parents=True, exist_ok=True)

    # modelscope model name mapping
    ms_name_map = {
        "bge-m3": "BAAI/bge-m3",
        "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",
    }
    ms_name = ms_name_map.get(model_info["slug"], model_info["name"])

    try:
        ms_snapshot_download(
            model_id=ms_name,
            local_dir=str(local_dir),
        )
        return is_model_present(model_info)
    except Exception as e:
        print(f"  modelscope download failed: {e}")
        return False


def main():
    print("=" * 60)
    print("BCM-RAG Model Download")
    print(f"Target: {MODELS_DIR}")
    print("=" * 60)

    all_ok = True
    for model_info in MODELS:
        print(f"\n[{model_info['slug']}] ({model_info['size_mb']}MB)")

        if is_model_present(model_info):
            print(f"  Already present, skipping")
            continue

        # Try HF Hub first (may use mirror via HF_ENDPOINT env)
        print(f"  Downloading from HuggingFace Hub...")
        ok = download_from_hf(model_info)

        # Fallback to modelscope
        if not ok:
            print(f"  Trying modelscope...")
            ok = download_from_modelscope(model_info)

        if ok:
            print(f"  ✓ Downloaded successfully")
        else:
            print(f"  ✗ Download FAILED")
            print(f"  Manual: place model files in {MODELS_DIR / model_info['slug']}/")
            all_ok = False

    print()
    print("=" * 60)
    if all_ok:
        print("All models ready!")
    else:
        print("Some models failed to download. See above for manual instructions.")
    print("=" * 60)


if __name__ == "__main__":
    main()
