"""BCM-RAG 全局配置 — 数据集隔离。

通过 BCM_DATASET 环境变量切换数据集，所有输出自动路由到 output/{dataset}/ 下。

用法:
    # 默认数据集 (PA2A)
    from config import CONTENT_ANALYSIS_DIR, PARSER_OUTPUT_DIR, STORAGE_DIR

    # 切换到其他数据集
    $env:BCM_DATASET = "B70KS"
    python ...

输出结构:
    output/
    ├── PA2A/                     # BCM_DATASET=PA2A (默认)
    │   ├── parser_output/        # 解析器产出
    │   ├── content_analysis/     # KG, chunks, SM, rules, vectors
    │   └── storage/              # 图片对象存储
    └── B70KS/                    # BCM_DATASET=B70KS
        ├── parser_output/
        ├── content_analysis/
        └── storage/
"""

import os
from pathlib import Path

# ---- 数据集标识 ----
# Primary env var (new name). Falls back to BCM_DATASET for backward compat.
DATASET = os.getenv("DEEPRAG_DATASET") or os.getenv("BCM_DATASET", "PA2A")
if os.getenv("BCM_DATASET") and not os.getenv("DEEPRAG_DATASET"):
    import warnings
    warnings.warn(
        "BCM_DATASET is deprecated, use DEEPRAG_DATASET instead.",
        DeprecationWarning, stacklevel=2,
    )

# ---- 输出根目录 ----
OUTPUT_ROOT = Path("output") / DATASET

# ---- 子目录 ----
PARSER_OUTPUT_DIR = OUTPUT_ROOT / "parser_output"
CONTENT_ANALYSIS_DIR = OUTPUT_ROOT / "content_analysis"
STORAGE_DIR = OUTPUT_ROOT / "storage"

# ---- 确保目录存在 ----
for _d in (OUTPUT_ROOT, PARSER_OUTPUT_DIR, CONTENT_ANALYSIS_DIR, STORAGE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def get_path(*parts: str) -> Path:
    """获取数据集下的文件路径。

    示例:
        get_path("content_analysis", "knowledge_graph.json")
        # → output/PA2A/content_analysis/knowledge_graph.json
    """
    return Path(OUTPUT_ROOT, *parts)
