"""BCM-RAG DAG Agent 交互式测试工具.

运行方式:
    python scripts/interactive_dag.py

输入查询即可看到 DAG 推理结果，输入 q 退出。
"""

import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from agent.dag_agent import DagAgent


def main():
    agent = DagAgent(provider="deepseek")
    agent.load()

    print("\n" + "=" * 60)
    print("BCM DAG Agent 交互测试")
    print("输入查询开始推理，输入 q 退出")
    print("=" * 60)

    while True:
        try:
            q = input("\n>>> 查询: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出.")
            break

        if not q:
            continue
        if q.lower() == "q":
            print("退出.")
            break

        print(f"\n推理中...")
        result = agent.query(q)

        print(f"\n{'─' * 50}")
        print(f"模板: {result.template}")
        print(f"置信度: {result.confidence:.0%}")
        print(f"耗时: {result.total_duration_ms:.0f}ms")
        print(f"执行顺序: {result.execution_order}")
        print(f"{'─' * 50}")
        print(f"\n{result.answer}")


if __name__ == "__main__":
    main()
