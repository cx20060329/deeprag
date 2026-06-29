"""BCM-RAG 交互式 Chatbot Demo — 快速体验 DAG 推理引擎。

用法:
    python chatbot.py                  # 自动从 .env 加载 key，启用 LLM 模式
    python chatbot.py --api-key sk-xxx  # 手动指定 key
    python chatbot.py --dataset B70KS_SeatCtrl  # 指定数据集

交互命令:
    /help     — 帮助
    /stats    — 显示上次查询的统计信息
    /datasets — 列出可用数据集
    /dataset <name> — 切换数据集
    /exit     — 退出（自动保存对话记录为 HTML）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── 修复 Windows GBK 编码问题 ──────────────────────────────────────────
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _load_dotenv():
    """加载项目根目录 .env 文件到 os.environ（不覆盖已有值）。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


# ── 数据集发现 ──────────────────────────────────────────────────────────

def discover_datasets() -> dict[str, dict]:
    """自动发现 output/ 下所有可用数据集（有 content_analysis 目录的）。"""
    root = Path(__file__).resolve().parent
    output_dir = root / "output"
    if not output_dir.is_dir():
        return {}

    datasets = {}
    for d in sorted(output_dir.iterdir()):
        if not d.is_dir():
            continue
        ca = d / "content_analysis"
        if not ca.is_dir():
            continue
        kgf = ca / "knowledge_graph.json"
        if not kgf.exists():
            continue
        try:
            with open(kgf, encoding="utf-8") as f:
                kg = json.load(f)
            ents = len(kg.get("entities", []))
            rels = len(kg.get("relationships", []))
        except Exception:
            ents = rels = 0

        chf = ca / "chunks.json"
        chunks = 0
        if chf.exists():
            try:
                with open(chf, encoding="utf-8") as f:
                    ch = json.load(f)
                chunks = len(ch.get("text_chunks", []))
            except Exception:
                pass

        datasets[d.name] = {
            "path": str(ca),
            "entities": ents,
            "relations": rels,
            "chunks": chunks,
        }
    return datasets


# ── 对话记录 ────────────────────────────────────────────────────────────

@dataclass
class ChatRecord:
    question: str
    answer: str
    template: str
    confidence: float
    elapsed_ms: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    dataset: str = ""


# ── HTML 导出 ───────────────────────────────────────────────────────────

def export_html(history: list[ChatRecord], dataset_name: str = "", output_dir: str = "") -> str:
    """将对话记录导出为 HTML 文件，返回文件路径。"""
    if not history:
        return ""

    root = Path(__file__).resolve().parent
    if output_dir:
        out = Path(output_dir)
    else:
        out = root / "output"
    out.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = out / f"chat_history_{ts}.html"

    total_ms = sum(r.elapsed_ms for r in history)
    avg_conf = sum(r.confidence for r in history) / len(history) if history else 0

    # 生成对话卡片
    cards = []
    for i, r in enumerate(history, 1):
        # Markdown 简单转 HTML
        answer_html = _markdown_to_html(r.answer)
        conf_pct = f"{r.confidence:.0%}"
        conf_color = "#22c55e" if r.confidence >= 0.7 else ("#f59e0b" if r.confidence >= 0.4 else "#ef4444")
        cards.append(f"""
        <div class="round">
            <div class="round-header">
                <span class="round-num">#{i}</span>
                <span class="round-meta">
                    <span>模板: {r.template}</span>
                    <span style="color:{conf_color}">置信度: {conf_pct}</span>
                    <span>耗时: {r.elapsed_ms:.0f}ms</span>
                    <span>{r.timestamp}</span>
                </span>
            </div>
            <div class="question">
                <div class="q-label">Q</div>
                <div class="q-text">{_escape_html(r.question)}</div>
            </div>
            <div class="answer">
                <div class="a-label">A</div>
                <div class="a-text">{answer_html}</div>
            </div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCM-RAG 对话记录 — {ts}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f0f2f5;
    color: #1a1a2e;
    line-height: 1.6;
}}
.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #fff;
    padding: 32px 40px;
}}
.header h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 8px; }}
.header .subtitle {{ font-size: 14px; opacity: 0.7; }}
.summary {{
    display: flex;
    gap: 24px;
    margin: 20px 40px;
    flex-wrap: wrap;
}}
.summary-card {{
    background: #fff;
    border-radius: 10px;
    padding: 16px 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
    min-width: 120px;
}}
.summary-card .num {{ font-size: 28px; font-weight: 700; color: #0f3460; }}
.summary-card .label {{ font-size: 13px; color: #64748b; margin-top: 2px; }}
.container {{ max-width: 960px; margin: 0 auto; padding: 0 20px 40px; }}
.round {{
    background: #fff;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
    overflow: hidden;
}}
.round-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 20px;
    background: #f8fafc;
    border-bottom: 1px solid #e2e8f0;
    font-size: 12px;
    color: #64748b;
    gap: 12px;
}}
.round-num {{ font-weight: 700; color: #0f3460; font-size: 14px; }}
.round-meta {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.question, .answer {{
    display: flex;
    gap: 14px;
    padding: 18px 20px;
}}
.question {{ border-bottom: 1px solid #f1f5f9; }}
.q-label, .a-label {{
    width: 32px; height: 32px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 14px;
    flex-shrink: 0;
}}
.q-label {{ background: #dbeafe; color: #1d4ed8; }}
.a-label {{ background: #dcfce7; color: #16a34a; }}
.q-text {{ font-weight: 500; padding-top: 4px; }}
.a-text {{ padding-top: 4px; }}
.a-text h2 {{ font-size: 18px; margin: 14px 0 6px; color: #1a1a2e; }}
.a-text h3 {{ font-size: 15px; margin: 12px 0 4px; color: #334155; }}
.a-text p {{ margin: 6px 0; }}
.a-text ul, .a-text ol {{ margin: 6px 0 6px 20px; }}
.a-text li {{ margin: 2px 0; }}
.a-text strong {{ color: #0f3460; }}
.a-text code {{ background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 13px; }}
.a-text table {{ border-collapse: collapse; margin: 8px 0; width: 100%; }}
.a-text th, .a-text td {{ border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; font-size: 13px; }}
.a-text th {{ background: #f8fafc; }}
.footer {{
    text-align: center;
    padding: 24px;
    color: #94a3b8;
    font-size: 13px;
}}
</style>
</head>
<body>
<div class="header">
    <h1>BCM-RAG 对话记录</h1>
    <div class="subtitle">数据集: {_escape_html(dataset_name)} &nbsp;|&nbsp; 导出时间: {ts}</div>
</div>
<div class="summary">
    <div class="summary-card">
        <div class="num">{len(history)}</div>
        <div class="label">对话轮数</div>
    </div>
    <div class="summary-card">
        <div class="num">{total_ms/1000:.1f}s</div>
        <div class="label">总耗时</div>
    </div>
    <div class="summary-card">
        <div class="num">{avg_conf:.0%}</div>
        <div class="label">平均置信度</div>
    </div>
</div>
<div class="container">
{''.join(cards)}
</div>
<div class="footer">BCM-RAG · DAG Agent · 自动生成于 {ts}</div>
</body>
</html>"""

    html_path.write_text(html, encoding="utf-8")
    return str(html_path)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markdown_to_html(text: str) -> str:
    """简单的 Markdown → HTML 转换（处理标题、列表、加粗、代码块）。"""
    lines = text.split("\n")
    result = []
    in_code_block = False
    in_list = False
    list_tag = ""

    for line in lines:
        # 代码块
        if line.strip().startswith("```"):
            if in_code_block:
                result.append("</code></pre>")
                in_code_block = False
            else:
                result.append("<pre><code>")
                in_code_block = True
            continue
        if in_code_block:
            result.append(_escape_html(line))
            continue

        # 空行结束列表
        if not line.strip():
            if in_list:
                result.append(f"</{list_tag}>")
                in_list = False
            result.append("")
            continue

        # 标题
        if line.startswith("#### "):
            result.append(f"<h4>{_inline_md(line[5:])}</h4>")
            continue
        if line.startswith("### "):
            result.append(f"<h3>{_inline_md(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            result.append(f"<h2>{_inline_md(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            result.append(f"<h1>{_inline_md(line[2:])}</h1>")
            continue

        # 无序列表
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            if not in_list or list_tag != "ul":
                if in_list:
                    result.append(f"</{list_tag}>")
                result.append("<ul>")
                in_list = True
                list_tag = "ul"
            content = line.strip()[2:]
            result.append(f"<li>{_inline_md(content)}</li>")
            continue

        # 有序列表
        m = __import__("re").match(r"^(\d+)\.\s+(.*)", line.strip())
        if m:
            if not in_list or list_tag != "ol":
                if in_list:
                    result.append(f"</{list_tag}>")
                result.append("<ol>")
                in_list = True
                list_tag = "ol"
            result.append(f"<li>{_inline_md(m.group(2))}</li>")
            continue

        # 普通段落
        if in_list:
            result.append(f"</{list_tag}>")
            in_list = False
        result.append(f"<p>{_inline_md(line)}</p>")

    if in_list:
        result.append(f"</{list_tag}>")
    if in_code_block:
        result.append("</code></pre>")

    return "\n".join(result)


def _inline_md(text: str) -> str:
    """处理行内 Markdown：加粗、代码。"""
    import re
    text = _escape_html(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


# ── UI ──────────────────────────────────────────────────────────────────

def print_banner():
    print()
    print("=" * 55)
    print("       BCM-RAG 工程知识问答系统")
    print("       基于 DAG 推理引擎 · 完整推理链 · 可审计")
    print("=" * 55)
    print()


def print_help():
    print()
    print("-" * 55)
    print("  示例查询:")
    print("    IGN1 信号的定义是什么？")
    print("    如何进入 Driving 模式？")
    print("    KeyLost 会影响哪些功能？")
    print("    从 Abandoned 如何到达 Driving？")
    print("    为什么车辆无法从 Inactive 进入 Driving？")
    print("    VMM 状态机是否存在不可达状态？")
    print()
    print("  命令: /help  /stats  /datasets  /dataset <name>  /exit")
    print("-" * 55)
    print()


def print_stats(result):
    """显示上次查询的详细统计信息。"""
    print()
    print("-" * 55)
    print(f"  模板:       {result.template}")
    print(f"  置信度:     {result.confidence:.0%}")
    print(f"  总耗时:     {result.total_duration_ms:.0f}ms")
    print(f"  执行层级:   {len(result.execution_order)}")
    print()

    for i, level in enumerate(result.execution_order):
        print(f"  Level {i}: {' -> '.join(level)}")

    print()
    print("  节点详情:")
    for nid, no in result.node_outputs.items():
        icon = "[OK]" if no.status == "success" else "[ERR]"
        print(f"    {icon} {nid:10s} ({no.node_type:20s}) {no.duration_ms:6.0f}ms", end="")
        if no.error:
            print(f"  ERR: {no.error[:60]}")
        elif no.node_type == "completeness_eval" and no.output:
            score = no.output.get("overall_score", 0)
            suf = "信息充分" if no.output.get("is_sufficient") else "存在缺口"
            print(f"  完整性: {score:.0%} {suf}")
        elif no.node_type == "state_machine" and no.output:
            n = len(no.output.get("transitions", []))
            print(f"  {n} 条转移边")
        elif no.node_type == "rule_lookup" and no.output:
            n = len(no.output.get("matched_rules", []))
            print(f"  {n} 条匹配规则")
        elif no.node_type == "chunk_search" and no.output:
            n = len(no.output.get("chunks", []))
            print(f"  {n} 条文档片段")
        else:
            print()

    print("-" * 55)
    print()


def print_datasets(datasets: dict, current: str):
    """列出可用数据集。"""
    print()
    print("-" * 55)
    print(f"  可用数据集 (当前: {current}):")
    print()
    for name, info in datasets.items():
        marker = " ←" if name == current else ""
        print(f"    {name}{marker}")
        print(f"      KG: {info['entities']}实体/{info['relations']}关系  Chunks: {info['chunks']}")
    print("-" * 55)
    print()


def run_query(agent, question: str, history: list[ChatRecord], dataset_name: str) -> str | None:
    """执行查询并返回格式化的答案。同时将本轮对话追加到 history。"""
    t0 = time.time()
    try:
        result = agent.query(question)
    except Exception as e:
        print(f"\n  [ERR] 查询失败: {e}")
        return None

    elapsed = (time.time() - t0) * 1000

    # 打印答案
    print()
    print("-" * 55)
    answer = result.answer.strip()
    if answer:
        if len(answer) > 5000:
            answer = answer[:5000] + "\n... (答案过长已截断，用 /stats 查看完整信息)"
        print(answer)
    else:
        print("  (未生成答案)")

    # 摘要行
    print()
    bar_len = 10
    filled = int(result.confidence * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"  模板: {result.template}  |  置信度: {bar} {result.confidence:.0%}  |  耗时: {elapsed:.0f}ms")

    # 完整性评估摘要
    eval_out = result.node_outputs.get("eval")
    if eval_out and eval_out.status == "success" and eval_out.output:
        score = eval_out.output.get("overall_score", 0)
        suf = "信息充分" if eval_out.output.get("is_sufficient") else "存在信息缺口"
        gaps = eval_out.output.get("gap_queries", [])
        print(f"  完整性评估: {score:.0%} — {suf}", end="")
        if gaps:
            print(f"  (建议跟进: {gaps[0][:50]}...)")
        else:
            print()

    print("-" * 55)

    # 保存本轮对话
    record = ChatRecord(
        question=question,
        answer=answer,
        template=result.template,
        confidence=result.confidence,
        elapsed_ms=elapsed,
        dataset=dataset_name,
    )
    history.append(record)

    # 保存结果供 /stats 查看
    run_query._last_result = result
    return answer


def main():
    _load_dotenv()

    parser = argparse.ArgumentParser(description="BCM-RAG Chatbot Demo")
    parser.add_argument("--api-key", type=str, default="", help="直接传入 API key")
    parser.add_argument("--provider", default="deepseek", choices=["ark", "deepseek", "zhipu"],
                        help="LLM 提供商 (默认: deepseek)")
    parser.add_argument("--dataset", type=str, default="",
                        help="数据集名称 (默认: PA2A)")
    args = parser.parse_args()

    if args.api_key:
        os.environ["DEEPSEEK_API_KEY"] = args.api_key

    # 发现可用数据集
    datasets = discover_datasets()
    if not datasets:
        print("错误: 未发现任何数据集 (output/*/content_analysis/knowledge_graph.json)")
        sys.exit(1)

    # 确定当前数据集
    current_dataset = args.dataset or os.getenv("BCM_DATASET", "PA2A")
    if current_dataset not in datasets:
        # 选第一个可用的
        current_dataset = next(iter(datasets.keys()))
        print(f"⚠ 数据集 '{args.dataset}' 未找到，使用: {current_dataset}")

    os.environ["BCM_DATASET"] = current_dataset

    print_banner()

    # 加载 Agent
    from agent.dag_agent import DagAgent

    has_key = any(
        os.getenv(k) for k in
        ["ARK_API_KEY", "DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY"]
    )

    if not has_key:
        print("⚠  未检测到 API Key，将使用无 LLM 模式（关键词匹配 + 回退合成）。")
        print("   设置方式: python chatbot.py --api-key sk-xxx")
        print()
    else:
        print(f"✓  LLM 模式 (provider={args.provider})")
        print()

    print(f"  数据集: {current_dataset} ({datasets[current_dataset]['entities']}实体, {datasets[current_dataset]['chunks']}chunks)")
    print(f"  可用数据集: {', '.join(datasets.keys())}")
    print()
    print("  正在加载知识库...", end=" ", flush=True)
    agent = DagAgent(api_key=args.api_key or None, provider=args.provider)
    agent.load()
    print("就绪！")
    print()
    print("  输入问题开始查询，输入 /help 查看示例，输入 /exit 退出。")
    print()

    # 对话历史
    history: list[ChatRecord] = []

    # 交互循环
    while True:
        try:
            question = input("  Query> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  再见！")
            break

        if not question:
            continue

        if question == "/exit":
            if history:
                print(f"\n  正在导出对话记录...")
                html_path = export_html(history, current_dataset)
                print(f"  对话记录已保存: {html_path}")
                try:
                    webbrowser.open(html_path)
                except Exception:
                    pass
            print("  再见！")
            break
        elif question == "/help":
            print_help()
            continue
        elif question == "/stats":
            result = getattr(run_query, "_last_result", None)
            if result is None:
                print("\n  还没有执行过查询，请先提问。\n")
            else:
                print_stats(result)
            continue
        elif question == "/datasets":
            print_datasets(datasets, current_dataset)
            continue
        elif question.startswith("/dataset"):
            parts = question.split(maxsplit=1)
            if len(parts) < 2:
                print_datasets(datasets, current_dataset)
                continue
            name = parts[1].strip()
            if name not in datasets:
                print(f"\n  数据集 '{name}' 不存在。可用: {', '.join(datasets.keys())}\n")
                continue
            current_dataset = name
            os.environ["BCM_DATASET"] = name
            print(f"\n  切换数据集: {current_dataset}")
            print(f"  ({datasets[current_dataset]['entities']}实体, {datasets[current_dataset]['chunks']}chunks)")
            print("  重新加载知识库...", end=" ", flush=True)
            agent = DagAgent(api_key=args.api_key or None, provider=args.provider)
            agent.load()
            print("就绪！\n")
            continue

        run_query(agent, question, history, current_dataset)


if __name__ == "__main__":
    main()
