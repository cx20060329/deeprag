"""Download essential BGE model weight files via ModelScope (no ONNX).

ONNX files (~2GB) excluded — not needed for CPU sentence-transformers inference.
ModelScope is used as primary source (faster in China).

Usage:
  python scripts/download_models_light.py
"""

import os
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "BAAI"

# Essential files only (pytorch_model.bin / model.safetensors)
# Format: {model_slug: {repo_id: str, files: [str]}}
MODELS = {
    "bge-m3": {
        "repo": "BAAI/bge-m3",
        "files": [
            "pytorch_model.bin",        # ~268MB
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
            "modules.json",
            "config_sentence_transformers.json",
            "sentence_bert_config.json",
            "1_Pooling/config.json",
        ],
    },
    "bge-reranker-v2-m3": {
        "repo": "BAAI/bge-reranker-v2-m3",
        "files": [
            "model.safetensors",        # ~43MB
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
        ],
    },
}


def download_from_modelscope(repo_id: str, filename: str, local_dir: Path) -> bool:
    """Download a single file from ModelScope."""
    try:
        from modelscope.hub.file_download import model_file_download
        target = model_file_download(
            model_id=repo_id,
            file_path=filename,
            local_dir=str(local_dir),
        )
        return Path(target).exists()
    except ImportError:
        print("ERROR: modelscope not installed. Run: pip install modelscope")
        return False
    except Exception as e:
        print(f"    FAIL {filename}: {e}")
        return False


def download_from_hf(repo_id: str, filename: str, local_dir: Path) -> bool:
    """Download a single file from HuggingFace Hub."""
    try:
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
        )
        return True
    except Exception:
        return False


def is_file_valid(filepath: Path, min_size: int = 1000) -> bool:
    """Check if a downloaded file is valid (exists + non-trivial size)."""
    if not filepath.exists():
        return False
    # Weight files should be >1MB
    if filepath.suffix in ('.bin', '.safetensors'):
        return filepath.stat().st_size > 1_000_000
    return filepath.stat().st_size > min_size


def main():
    # Set UTF-8 for console
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    print("=" * 60)
    print("BCM-RAG Lightweight Model Download")
    print("(essential files only, no ONNX/images)")
    print("=" * 60)

    all_ok = True
    for slug, info in MODELS.items():
        repo = info["repo"]
        files = info["files"]
        local_dir = MODELS_DIR / slug
        local_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[{slug}] {len(files)} files from {repo}")

        ok_count = 0
        for fname in files:
            dest = local_dir / fname
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Skip if already valid
            if is_file_valid(dest):
                print(f"    SKIP {fname} (already exists)")
                ok_count += 1
                continue

            # Try ModelScope first, then HF Hub
            print(f"    DOWNLOAD {fname} ...", end=" ", flush=True)
            success = download_from_modelscope(repo, fname, local_dir)
            if not success:
                success = download_from_hf(repo, fname, local_dir)

            if success and is_file_valid(dest):
                size_mb = dest.stat().st_size / (1024 * 1024)
                print(f"OK ({size_mb:.1f}MB)")
                ok_count += 1
            else:
                print("FAIL")
                # Clean up partial download
                if dest.exists():
                    dest.unlink()
                all_ok = False

        status = "OK" if ok_count == len(files) else f"{ok_count}/{len(files)}"
        print(f"  [{status}]")

    print("\n" + "=" * 60)
    if all_ok:
        print("All models ready.")
        print("Run: python -m retrieval.embedder")
    else:
        print("Some files failed. Check errors above.")
        print(f"Manual: place model files in {MODELS_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
