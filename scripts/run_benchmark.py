"""Run 10 benchmark questions against specified dataset and save as HTML."""
import os, sys, json, time, webbrowser, re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

os.environ["HF_HUB_OFFLINE"] = "1"

DATASET = sys.argv[1] if len(sys.argv) > 1 else "B70KS_SeatCtrl"
os.environ["BCM_DATASET"] = DATASET

QUESTIONS = [
    "首次交样时间，如果距离评审今天，不足半年，要在报告中给出警示；",
    "项目软件交样计划，尤其是首次交样时间，要关注并输出到报告中；",
    "软件开发分工，需要找到并输出到报告中，全部埃泰克，部分埃泰克（具体客户做什么），如果SOR里没有提，报告中显示不明确；",
    "交付方式是白盒（具体哪些模块要求是白盒交付），还是黑盒，如果SOR里没有提具体黑盒白盒，报告中显示不明确；；",
    "如客户要求Autosar BSW配置工具白盒交付，需要重点提醒，无法满足；",
    "是否涉及软件调试工具，或者编译器，或者测试盒等的采购交付，并给出涉及的具体章节名称；",
    "功能安全最高要求等级，如果没有功能安全要求，则显示无；",
    "是否有以太网开发内容，报告需明确，显示有 或 无即可，并给出涉及章节名称；",
    "如果要求乙方交付CICD系统，需要重点提醒，如果没有则忽略",
    "如果要求乙方把代码或者模型等上传到甲方的服务器等要求，需要重点提醒，如果没有则忽略；",
]

from agent.dag_agent import DagAgent
agent = DagAgent(provider="deepseek")
agent.load()

records = []
t_start = time.time()
for i, q in enumerate(QUESTIONS):
    t0 = time.time()
    print(f"\n[Round #{i+1}] {q[:60]}...")
    result = agent.query(q)
    elapsed = (time.time() - t0) * 1000
    records.append({
        "num": i + 1, "question": q, "template": result.template,
        "confidence": result.confidence, "answer": result.answer,
        "elapsed_ms": elapsed, "timestamp": datetime.now().isoformat(),
    })
    print(f"  {result.template} | conf={result.confidence:.0%} | {elapsed:.0f}ms | crit={result.critique[:60] if result.critique else 'N/A'}")

total_ms = (time.time() - t_start) * 1000

# ── Build HTML ──
def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def md_to_html(text: str) -> str:
    lines = text.split("\n"); result = []; in_list = False
    for line in lines:
        if line.startswith("#### "): result.append(f"<h4>{escape_html(line[5:])}</h4>")
        elif line.startswith("### "): result.append(f"<h3>{escape_html(line[4:])}</h3>")
        elif line.startswith("## "): result.append(f"<h2>{escape_html(line[3:])}</h2>")
        elif line.startswith("# "): result.append(f"<h1>{escape_html(line[2:])}</h1>")
        elif "**" in line:
            line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            result.append(f"<p>{line}</p>")
        elif line.strip().startswith(("- ", "* ")):
            if not in_list: result.append("<ul>"); in_list = True
            result.append(f"<li>{escape_html(line.strip()[2:])}</li>")
        elif re.match(r'^\d+[\.\)]\s', line.strip()):
            if not in_list: result.append("<ol>"); in_list = True
            result.append(f"<li>{escape_html(re.sub(r'^\d+[\.\)]\s','',line.strip()))}</li>")
        else:
            if in_list: result.append("</ul>" if "<ul>" in result[-1] else "</ol>"); in_list = False
            if line.strip(): result.append(f"<p>{escape_html(line)}</p>")
    if in_list: result.append("</ul>")
    return "\n".join(result)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
html_path = ROOT / "output" / f"{DATASET}_v2_{ts}.html"

html_parts = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>BCM-RAG — {DATASET} — {ts}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.6}}
.header{{background:linear-gradient(135deg,#0f3460,#16213e);color:#fff;padding:32px 40px}}
.header h1{{font-size:24px;margin-bottom:8px}}
.header .subtitle{{font-size:14px;opacity:.7}}
.summary{{display:flex;gap:24px;margin:20px 40px;flex-wrap:wrap}}
.summary-card{{background:#fff;border-radius:10px;padding:16px 24px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:120px}}
.summary-card .num{{font-size:28px;font-weight:700;color:#0f3460}}
.summary-card .label{{font-size:13px;color:#64748b;margin-top:2px}}
.container{{max-width:960px;margin:0 auto;padding:0 20px 40px}}
.round{{background:#fff;border-radius:12px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden}}
.round-header{{display:flex;justify-content:space-between;align-items:center;padding:10px 20px;background:#f8fafc;border-bottom:1px solid #e2e8f0;font-size:12px;color:#64748b;gap:12px}}
.round-num{{font-weight:700;color:#0f3460;font-size:14px}}
.round-meta{{display:flex;gap:16px;flex-wrap:wrap}}
.question,.answer{{display:flex;gap:14px;padding:18px 20px}}
.question{{border-bottom:1px solid #f1f5f9}}
.q-label,.a-label{{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;flex-shrink:0}}
.q-label{{background:#dbeafe;color:#1d4ed8}}
.a-label{{background:#dcfce7;color:#16a34a}}
.q-text{{font-weight:500;padding-top:4px}}
.a-text{{padding-top:4px}}
.a-text h2{{font-size:18px;margin:14px 0 6px}}
.a-text h3{{font-size:15px;margin:12px 0 4px;color:#334155}}
.a-text p{{margin:6px 0}}
.a-text ul,.a-text ol{{margin:6px 0 6px 20px}}
.a-text li{{margin:2px 0}}
.a-text strong{{color:#0f3460}}
.a-text table{{border-collapse:collapse;margin:8px 0;width:100%}}
.a-text th,.a-text td{{border:1px solid #e2e8f0;padding:6px 10px;text-align:left;font-size:13px}}
.a-text th{{background:#f8fafc}}
.footer{{text-align:center;padding:24px;color:#94a3b8;font-size:13px}}
</style></head><body>
<div class="header"><h1>BCM-RAG 对话记录 — {DATASET}</h1><div class="subtitle">数据集: {DATASET} &nbsp;|&nbsp; 导出: {ts}</div></div>
<div class="summary">
<div class="summary-card"><div class="num">{len(records)}</div><div class="label">对话轮数</div></div>
<div class="summary-card"><div class="num">{total_ms/1000:.1f}s</div><div class="label">总耗时</div></div>
<div class="summary-card"><div class="num">{sum(r['confidence'] for r in records)/len(records):.0%}</div><div class="label">平均置信度</div></div>
</div><div class="container">
"""]

for r in records:
    conf = r["confidence"]; color = "#22c55e" if conf>=.7 else ("#f59e0b" if conf>=.5 else "#ef4444")
    html_parts.append(f"""
<div class="round"><div class="round-header">
<span class="round-num">#{r['num']}</span><span class="round-meta">
<span>{r['template']}</span><span style="color:{color}">置信度: {conf:.0%}</span>
<span>{r['elapsed_ms']:.0f}ms</span><span>{r['timestamp'][:19]}</span></span></div>
<div class="question"><div class="q-label">Q</div><div class="q-text">{escape_html(r['question'])}</div></div>
<div class="answer"><div class="a-label">A</div><div class="a-text">{md_to_html(r['answer'])}</div></div>
</div>""")

html_parts.append(f'</div><div class="footer">BCM-RAG · {DATASET} · {ts}</div></body></html>')
html_path.write_text("\n".join(html_parts), encoding="utf-8")
print(f"\nHTML: {html_path}")
try: webbrowser.open(str(html_path))
except: pass
