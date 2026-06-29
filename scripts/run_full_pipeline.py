"""BCM-RAG 完整管线：MinerU 解析 → 内容分析 (KG + Chunks + Vectors + VLM)

修复版：每个 PDF 独立 dataset 目录，避免互相覆盖。
"""
import json, os, sys, time
from pathlib import Path

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    DATA_DIR = ROOT / "data"
    pdf_files = sorted(DATA_DIR.glob("*.pdf"))
    print(f"找到 {len(pdf_files)} 个 PDF 文件")

    # ---- 数据集名映射（短标识，避免超长路径）----
    DATASET_NAMES = {
        "B70KS项目-其他控制器信息安全SOR(1)": "B70KS_InfoSec",
        "汽车零部件产品开发要求说明（SOR）-B70KS_电子电气_座椅控制器_20251218": "B70KS_SeatCtrl",
        "附件2：RFQ – CH事业部B70KS项目V2.0-251216(1)": "B70KS_RFQ",
    }

    # =========================================================================
    # 阶段 1：MinerU 解析（跳过已完成的）
    # =========================================================================
    for src in pdf_files:
        stem = src.stem
        dataset_name = DATASET_NAMES.get(stem, stem[:20])
        os.environ["BCM_DATASET"] = dataset_name

        # reload config so PARSER_OUTPUT_DIR reflects the new dataset
        import importlib, config
        importlib.reload(config)
        out_dir = config.PARSER_OUTPUT_DIR / stem
        mineru_md = out_dir / "mineru" / stem / "auto" / f"{stem}.md"

        if mineru_md.exists():
            print(f"  [skip] MinerU 已存在: {mineru_md}")
            continue

        print(f"\n{'='*60}")
        print(f"  MinerU 解析 [{dataset_name}]: {stem}")
        print(f"{'='*60}")
        t0 = time.time()
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = src.read_bytes()

        from mineru.cli.common import do_parse
        from mineru.utils.enum_class import MakeMode

        do_parse(
            output_dir=str(out_dir / "mineru"),
            pdf_file_names=[stem],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=["ch"],
            backend="pipeline", parse_method="auto",
            formula_enable=True, table_enable=True,
            f_draw_layout_bbox=False, f_draw_span_bbox=False,
            f_dump_md=True, f_dump_middle_json=True,
            f_dump_model_output=False, f_dump_orig_pdf=False,
            f_dump_content_list=True,
            f_make_md_mode=MakeMode.MM_MD,
        )
        print(f"  耗时: {time.time()-t0:.1f}s")

    # =========================================================================
    # 阶段 2：内容分析 (KG + Chunks + Vectors + VLM)
    # =========================================================================
    print(f"\n{'='*60}")
    print("  阶段 2: 内容分析管线")
    print(f"{'='*60}")

    def adapt_mineru_to_docling(cl_items: list) -> list:
        """MinerU flat content_list → Docling page-wrapped format."""
        pages: dict[int, list] = {}
        for item in cl_items:
            t = item.get("type", "")
            page_idx = item.get("page_idx", 0)
            converted = None
            if t == "text":
                level = item.get("text_level", 0)
                text = item.get("text", "")
                if not text.strip():
                    continue
                if level >= 1:
                    converted = {
                        "type": "title",
                        "content": {
                            "level": min(level, 6),
                            "title_content": [{"type": "text", "content": text}],
                        },
                    }
                else:
                    converted = {
                        "type": "paragraph",
                        "content": {
                            "paragraph_content": [{"type": "text", "content": text}],
                        },
                    }
            elif t == "table":
                html = item.get("table_body", "")
                if not html:
                    continue
                converted = {"type": "table", "content": {"html": html}}
            elif t in ("footer", "page_number"):
                continue
            if converted:
                pages.setdefault(page_idx, []).append(converted)
        return [pages[p] for p in sorted(pages.keys())]

    from content_analysis.pipeline import ContentAnalysisPipeline

    for src in pdf_files:
        stem = src.stem
        dataset_name = DATASET_NAMES.get(stem, stem[:20])
        os.environ["BCM_DATASET"] = dataset_name

        import importlib, config
        importlib.reload(config)

        out_dir = config.PARSER_OUTPUT_DIR / stem
        ca_dir = config.CONTENT_ANALYSIS_DIR

        cl_path = out_dir / "mineru" / stem / "auto" / f"{stem}_content_list.json"
        if not cl_path.exists():
            print(f"  [skip] 无 content_list: {cl_path}")
            continue

        kg_path = ca_dir / "knowledge_graph.json"
        if kg_path.exists():
            print(f"  [skip] KG 已存在: {kg_path}")
            continue

        print(f"\n{'='*60}")
        print(f"  数据集: {dataset_name}")
        print(f"  文件:   {stem}")
        print(f"{'='*60}")

        with open(cl_path, encoding="utf-8") as f:
            raw_cl = json.load(f)
        print(f"  MinerU items: {len(raw_cl)} (flat)")

        cl_page_wrapped = adapt_mineru_to_docling(raw_cl)
        total_items = sum(len(p) for p in cl_page_wrapped)
        print(f"  适配后: {total_items} items / {len(cl_page_wrapped)} pages")

        if total_items == 0:
            print("  [skip] 无可转换内容")
            continue

        class FakeParseResult:
            pass
        fake = FakeParseResult()
        fake.source_file = str(src)
        fake.parser_name = "mineru"
        fake.warnings = []
        fake.parse_time_seconds = 0.0
        fake.content_list = cl_page_wrapped
        fake.images_dir = str(out_dir / "mineru" / stem / "auto" / "images")

        pipeline = ContentAnalysisPipeline(
            output_dir=str(ca_dir),
            vlm_api_key=os.environ.get("ZHIPU_API_KEY", ""),
            vlm_backend="zhipu",
            enable_vlm=bool(os.environ.get("ZHIPU_API_KEY")),
        )
        output = pipeline.run_from_result(fake)
        print(f"\n  输出目录: {ca_dir}")

    # =========================================================================
    # 汇总
    # =========================================================================
    print(f"\n{'='*60}")
    print("  管线汇总")
    print(f"{'='*60}")
    for d in sorted(os.listdir(ROOT / "output")):
        dpath = ROOT / "output" / d
        if not dpath.is_dir():
            continue
        ca = dpath / "content_analysis"
        if not ca.exists():
            continue
        kg_file = ca / "knowledge_graph.json"
        vec_file = ca / "vector_points.json"
        chunks_file = ca / "chunks.json"
        if kg_file.exists():
            kg_data = json.loads(kg_file.read_text(encoding="utf-8"))
            ents = len(kg_data.get("entities", []))
            rels = len(kg_data.get("relationships", []))
            vecs = json.loads(vec_file.read_text(encoding="utf-8")) if vec_file.exists() else {}
            vec_count = vecs.get("count", 0) if isinstance(vecs, dict) else 0
            chunks = json.loads(chunks_file.read_text(encoding="utf-8")) if chunks_file.exists() else {}
            chunk_count = len(chunks.get("text_chunks", []))
            print(f"  {d}: {ents} entities, {rels} relations, {chunk_count} chunks, {vec_count} vectors")

    print("\n全部完成！")
