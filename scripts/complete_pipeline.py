"""完成 3 个数据集的剩余管线步骤：规则提取 → 状态机 → KG富化 → 向量嵌入。

跳过已完成的：MinerU 解析、内容分析（KG/Chunks/SectionTree）。
"""
import json, os, sys, time
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ---- 数据集配置 (自动探测) ----
def _discover_datasets() -> dict:
    """自动探测 output/ 下所有有 content_analysis 或 parser_output 的数据集。"""
    datasets = {}
    for d in sorted(os.listdir(ROOT / "output")):
        dpath = ROOT / "output" / d
        if not dpath.is_dir():
            continue
        # 跳过 PA2A (旧数据集，已完成)
        if d == "PA2A":
            continue
        ca = dpath / "content_analysis"
        po = dpath / "parser_output"
        if ca.exists() or po.exists():
            datasets[d] = {
                "output_dir": dpath,
            }
    return datasets

DATASETS = _discover_datasets()


def find_content_list(output_dir: Path) -> Path | None:
    """查找 MinerU 生成的 content_list.json。"""
    po = output_dir / "parser_output"
    if not po.exists():
        return None
    # 模糊搜索所有 content_list.json
    matches = list(po.rglob("*_content_list.json"))
    if matches:
        # 优先选最近修改的
        return max(matches, key=lambda p: p.stat().st_mtime)
    return None


def run_rule_extraction(dataset_name: str, content_list_path: Path, ca_dir: Path) -> dict:
    """步骤 1: 规则提取。"""
    rules_path = ca_dir / "rules.json"
    if rules_path.exists():
        with open(rules_path, encoding="utf-8") as f:
            existing = json.load(f)
        count = len(existing) if isinstance(existing, list) else len(existing.get("rules", []))
        print(f"  [skip] 规则已存在: {count} 条")
        return existing

    print(f"  [1/4] 规则提取...")
    from content_analysis.rule_extractor import RuleExtractionPipeline
    pipeline = RuleExtractionPipeline()
    rules_data = pipeline.extract_all(str(content_list_path))
    pipeline.save(rules_data, rules_path)
    stats = rules_data.get("stats", {})
    print(f"    提取 {stats.get('total', 0)} 条规则")
    if stats.get("by_type"):
        for t, c in sorted(stats["by_type"].items(), key=lambda x: -x[1])[:5]:
            print(f"      {t}: {c}")
    return rules_data


def run_state_machine(dataset_name: str, ca_dir: Path) -> dict:
    """步骤 2: 状态机构建。"""
    rules_path = ca_dir / "rules.json"
    if not rules_path.exists():
        print(f"  [skip] 无规则文件，跳过状态机构建")
        return {}

    # 检查是否已有状态机
    existing_sm = list(ca_dir.glob("state_machine_*.json"))
    if existing_sm:
        print(f"  [skip] 状态机已存在: {len(existing_sm)} 个模块")
        return {sm.stem: json.loads(sm.read_text(encoding="utf-8")) for sm in existing_sm}

    print(f"  [2/4] 状态机构建...")
    from content_analysis.state_machine import StateMachineBuilder
    builder = StateMachineBuilder()
    builder.load_rules(str(rules_path))
    machines = builder.build_all()

    for module, sm in machines.items():
        builder.save(sm, str(ca_dir))
        print(f"    {module}: {len(sm.states)} 状态, {len(sm.transitions)} 转移")

    if not machines:
        print(f"    (该文档无状态转移规则，未生成状态机)")
    return machines


def run_kg_enrich(dataset_name: str, ca_dir: Path) -> dict:
    """步骤 3: KG 关系富化。"""
    kg_path = ca_dir / "knowledge_graph.json"
    if not kg_path.exists():
        print(f"  [skip] 无 KG 文件，跳过富化")
        return {}

    # 检查是否已富化（检测是否有富化专属关系类型）
    with open(kg_path, encoding="utf-8") as f:
        kg = json.load(f)
    rels = kg.get("relationships", [])
    has_enriched = any(
        r.get("rel_type") in ("guarded_by", "signal_controls_function", "fault_detected_by", "function_triggers_state")
        for r in rels
    )
    if has_enriched:
        enriched_types = defaultdict(int)
        for r in rels:
            rt = r.get("rel_type", "")
            if rt in ("guarded_by", "signal_controls_function", "fault_detected_by", "function_triggers_state"):
                enriched_types[rt] += 1
        print(f"  [skip] KG 已富化: {dict(enriched_types)}")
        return {}

    print(f"  [3/4] KG 关系富化...")
    from content_analysis.kg_enricher import KGEnricher
    enricher = KGEnricher(
        kg_path=str(kg_path),
        sm_dir=str(ca_dir),
        rules_path=str(ca_dir / "rules.json"),
        chunks_path=str(ca_dir / "chunks.json"),
    )
    stats = enricher.enrich()
    print(f"    新增关系: {stats.get('total_added', 0)}")
    for k, v in sorted(stats.items()):
        if k != "total_added" and v > 0:
            print(f"      {k}: {v}")
    return stats


def run_embeddings(dataset_name: str, ca_dir: Path) -> Path | None:
    """步骤 4: 向量嵌入生成。"""
    chunks_path = ca_dir / "chunks.json"
    vp_path = ca_dir / "vector_points.json"

    if not chunks_path.exists():
        print(f"  [skip] 无 chunks 文件")
        return None

    # 检查是否已有嵌入
    if vp_path.exists():
        with open(vp_path, encoding="utf-8") as f:
            vp = json.load(f)
        # 新格式: {"points": [{"id":..., "vector":[...], "payload":{...}}], ...}
        # 旧格式: {"text_chunks": [{"embedding":...}], ...}
        points = vp.get("points", vp.get("text_chunks", []))
        if points:
            p0 = points[0]
            vec = p0.get("vector") or p0.get("embedding")
            has_emb = vec is not None and len(vec) > 0
            if has_emb:
                print(f"  [skip] 向量嵌入已生成: {len(points)} points, dim={len(vec)}")
                return vp_path

    print(f"  [4/4] 向量嵌入生成 (BGE-M3, 1024d)...")
    from retrieval.embedder import build_embeddings
    t0 = time.time()
    output = build_embeddings(
        chunks_path=chunks_path,
        output_path=vp_path,
    )
    elapsed = time.time() - t0
    print(f"    耗时: {elapsed:.1f}s, 输出: {output}")
    return output


def main():
    print("=" * 60)
    print("BCM-RAG 完整管线补全 (跳过已完成的 MinerU + 内容分析)")
    print("=" * 60)

    for ds_name, ds_cfg in DATASETS.items():
        output_dir = ds_cfg["output_dir"]
        ca_dir = output_dir / "content_analysis"

        if not ca_dir.exists():
            print(f"\n{'='*60}")
            print(f"  [{ds_name}] 无 content_analysis 目录，跳过")
            print(f"{'='*60}")
            continue

        # 设置数据集环境变量
        os.environ["BCM_DATASET"] = ds_name
        # 重新加载 config 以更新路径
        import importlib, config
        importlib.reload(config)

        print(f"\n{'='*60}")
        print(f"  数据集: {ds_name}")
        print(f"  输出目录: {ca_dir}")
        print(f"{'='*60}")

        # 查找 content_list
        content_list_path = find_content_list(output_dir)
        if content_list_path:
            print(f"  content_list: {content_list_path}")
        else:
            print(f"  content_list: NOT FOUND (规则提取将跳过)")

        # 步骤 1: 规则提取
        if content_list_path:
            rules_data = run_rule_extraction(ds_name, content_list_path, ca_dir)
        else:
            print(f"  [1/4] 规则提取: 无 content_list，跳过")

        # 步骤 2: 状态机构建
        machines = run_state_machine(ds_name, ca_dir)

        # 步骤 3: KG 富化
        enrich_stats = run_kg_enrich(ds_name, ca_dir)

        # 步骤 4: 向量嵌入
        emb_output = run_embeddings(ds_name, ca_dir)

    # ---- 汇总 ----
    print(f"\n{'='*60}")
    print("  全部完成！汇总:")
    print(f"{'='*60}")

    for ds_name, ds_cfg in DATASETS.items():
        ca_dir = ds_cfg["output_dir"] / "content_analysis"
        if not ca_dir.exists():
            print(f"  [{ds_name}] 无数据")
            continue

        kgf = ca_dir / "knowledge_graph.json"
        chf = ca_dir / "chunks.json"
        vpf = ca_dir / "vector_points.json"
        ruf = ca_dir / "rules.json"
        sm_count = len(list(ca_dir.glob("state_machine_*.json")))

        kg_ents = kg_rels = chunks = vecs = rules = 0
        has_emb = False

        if kgf.exists():
            with open(kgf, encoding="utf-8") as f: kg = json.load(f)
            kg_ents = len(kg.get("entities", []))
            kg_rels = len(kg.get("relationships", []))
        if chf.exists():
            with open(chf, encoding="utf-8") as f: ch = json.load(f)
            chunks = len(ch.get("text_chunks", []))
        if vpf.exists():
            with open(vpf, encoding="utf-8") as f: vp = json.load(f)
            points = vp.get("points", vp.get("text_chunks", []))
            vecs = len(points)
            if points:
                p0 = points[0]
                vec = p0.get("vector") or p0.get("embedding")
                has_emb = vec is not None and len(vec) > 0
        if ruf.exists():
            with open(ruf, encoding="utf-8") as f: r = json.load(f)
            rules = len(r) if isinstance(r, list) else len(r.get("rules", []))

        safe_name = ds_name
        print(f"  [{safe_name}] KG={kg_ents}e/{kg_rels}r  Chunks={chunks}  Rules={rules}  SM={sm_count}  Vectors={vecs}  Embed={'YES' if has_emb else 'NO'}")

    print("\nDone.")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
