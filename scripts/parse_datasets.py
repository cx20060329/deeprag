"""使用 MinerU 3.4.0 pipeline 后端解析 data/ 下 3 个 PDF。

调用 mineru.cli.common.do_parse 直接解析，无需 HTTP API 层。
解析完成后，用智谱 GLM-4V 分析提取的图片。
"""
import os, sys, time
from pathlib import Path


def main():
    # 确保项目根在 sys.path
    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))

    for sn in ("stdout", "stderr"):
        s = getattr(sys, sn, None)
        if s and hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    # 加载 .env
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    # ---- 数据集映射 ----
    DATASETS = [
        ("B70KS_InfoSec",  "B70KS项目-其他控制器信息安全SOR(1).pdf"),
        ("B70KS_SeatCtrl", "汽车零部件产品开发要求说明（SOR）-B70KS_电子电气_座椅控制器_20251218.pdf"),
        ("B70KS_RFQ",      "附件2：RFQ – CH事业部B70KS项目V2.0-251216(1).pdf"),
    ]

    for dataset, fname in DATASETS:
        src = ROOT / "data" / fname
        if not src.exists():
            print(f"[SKIP] {fname}")
            continue

        os.environ["BCM_DATASET"] = dataset
        t0 = time.time()

        print(f"\n{'='*60}")
        print(f"  {dataset}: {fname}")
        print(f"  Size: {src.stat().st_size:,} bytes")
        print(f"{'='*60}")

        # ---- 阶段 1: MinerU pipeline 解析 ----
        from config import PARSER_OUTPUT_DIR
        out_dir = PARSER_OUTPUT_DIR / src.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        pdf_bytes = src.read_bytes()

        from mineru.cli.common import do_parse
        from mineru.utils.enum_class import MakeMode

        print(f"  [pipeline] 解析中...")
        do_parse(
            output_dir=str(out_dir / "mineru"),
            pdf_file_names=[src.stem],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=["ch"],
            backend="pipeline",
            parse_method="auto",
            formula_enable=True,
            table_enable=True,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=True,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
            f_make_md_mode=MakeMode.MM_MD,
        )

        elapsed = time.time() - t0
        print(f"  [pipeline] 完成 ({elapsed:.1f}s)")

        # ---- 阶段 2: 智谱 GLM-4V 分析图片 ----
        from content_analysis.vlm_analyzer import VLMAnalyzer
        from content_analysis.models import Entity, Relationship

        images_dir = out_dir / "mineru" / src.stem / "images"
        if images_dir.exists():
            image_files = list(images_dir.glob("*"))
            if image_files:
                print(f"  [vlm] 分析 {len(image_files)} 张图片 (智谱 GLM-4V)...")
                vlm = VLMAnalyzer(
                    api_key=os.environ.get("ZHIPU_API_KEY", ""),
                    backend="zhipu",
                )
                # 构造简单的 section context
                contexts = [{
                    "module": dataset,
                    "section_number": "",
                    "section_title": src.stem,
                    "adjacent_text": "",
                }] * len(image_files)

                results = vlm.analyze_images(
                    [str(p) for p in image_files], contexts
                )
                entities, rels = vlm.results_to_entities(results)
                print(f"  [vlm] 提取 {len(entities)} entities, {len(rels)} relations")

                # 保存 VLM 结果
                import json
                vlm_out = out_dir / "vlm_results.json"
                with open(vlm_out, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                print(f"  [vlm] 结果保存到: {vlm_out}")
            else:
                print(f"  [vlm] 无图片，跳过")
        else:
            print(f"  [vlm] 无 images 目录，跳过")

        total_elapsed = time.time() - t0
        print(f"  [done] 总耗时: {total_elapsed:.1f}s")

    print("\n" + "=" * 60)
    print("全部完成！")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
