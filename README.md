# BCM-RAG — 汽车BCM功能规格文档企业级RAG系统

[![Python](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-97%20passed-brightgreen.svg)](tests/)

面向大规模汽车BCM（Body Control Module）功能规格文档的企业级RAG系统。不是简单的向量数据库聊天机器人，而是工程级知识系统。

## 架构概览

```
原始文档 (.docx)
    ↓
Docling/MinerU 解析 → 文档树 + 表格 + 图片
    ↓
实体提取 → 知识图谱 (1718节点/1726边)
    ↓
逻辑分块 → BGE-M3 嵌入 → 向量存储
    ↓
                    用户查询
                       ↓
              ┌─── 意图分析 ───┐
              ↓                ↓
         图谱检索          向量检索 (Dense+BM25+RRF)
              ↓                ↓
              └── 合并候选 ──→ CrossEncoder重排 → 规则重排
                                        ↓
                                 上下文压缩 (结构化证据)
                                        ↓
                                  LLM答案生成 (DeepSeek/GLM/豆包)
```

## 三层索引

| 层级 | 技术 | 用途 |
|------|------|------|
| **文档树** | JSON结构树 (482章节) | 保留原始文档层级、章节路径、父子关系 |
| **知识图谱** | NetworkX / Neo4j (1718实体/1726关系) | 模块、状态、信号、功能、故障间的依赖关系 |
| **向量索引** | Qdrant / Numpy + BGE-M3 (1024维) + BM25 | 语义检索 + 关键词检索 + RRF融合 |

## 核心能力

- **9阶段检索管道**: 意图分析 → 图谱检索 → 文档树定位 → 向量检索 → 候选合并 → CrossEncoder重排 → 规则重排 → 上下文压缩 → LLM答案
- **图推理引擎**: 前向链(影响分析)、后向链(条件回溯)、路径查询(nx.all_simple_paths)、冲突检测、可达性分析(入度/出度/SCC/环检测)
- **DAG模式Agent**: 6种推理模板，拓扑排序 + 层级并行执行，LLM动态模板选择，显式数据流传递
- **结构化证据包**: 依赖链 + 状态转移 + 规则匹配 + 文档片段
- **动态置信度**: 基于DAG执行统计的评分标准，非固定值

## DAG推理模板

| 模板 | 节点 | 适用场景 |
|------|------|----------|
| `factual_lookup` | intent → chunks | 信号定义/参数查询 |
| `state_transition` | intent → sm → rules → chunks | 状态转移条件推理 |
| `impact_analysis` | intent → impact → sm → rules → chunks | 故障/信号影响链 |
| `path_finding` | intent → path → sm → rules → chunks | 状态间路径查找 |
| `diagnostic` | intent → rules → impact → sm → conflicts → chunks | 故障诊断(假设检验) |
| `reachability_check` | intent → reach → sm → rules → chunks | 死锁/不可达检测 |

## 快速开始

### 环境要求

- Python >= 3.13
- BGE-M3 嵌入模型 (`models/BAAI/bge-m3/`)
- BGE-Reranker (`models/BAAI/bge-reranker-v2-m3/`)

### 安装

```bash
git clone https://github.com/cx20060329/bcmRAg.git
cd bcmRAg
pip install -e .
```

下载模型 (二选一):
```bash
python scripts/download_models_light.py     # 仅嵌入+重排
python scripts/download_models.py           # 完整模型
```

### 运行

```powershell
# 1. 设置LLM API密钥
$env:DEEPSEEK_API_KEY = "your-key"

# 2. 运行DAG Agent CLI (5个预设查询)
python -m agent.dag_agent

# 3. Python调用
from agent.dag_agent import DagAgent
agent = DagAgent(provider="deepseek")
agent.load()
result = agent.query("从Abandoned如何进入Driving？")
print(result.answer)
```

### 启动API服务

```bash
python -m api.main
# 访问 http://localhost:8000/docs 查看Swagger文档
```

## 项目结构

```
agent/                    # Agent层 (3种Agent + DAG)
  dag_agent.py            #   DAG模式Agent (6模板+拓扑执行+LLM选择)
  core.py                 #   BCMAgent (工具调用)
  agentic_rag.py          #   AgenticRAG v1 (自验证迭代)
  agentic_rag_v2.py       #   AgenticRAG v2 (5特征完整版)
  answer_synthesizer.py   #   LLM答案合成器
retrieval/                # 检索模块 (18个文件)
  pipeline.py             #   9阶段检索管道
  reasoning_engine.py     #   图推理引擎 (前向/后向链/路径/冲突/可达性)
  enhanced_reasoning.py   #   增强推理层
  llm_answer.py           #   LLM答案生成 (DeepSeek/GLM/豆包)
  context_compressor.py   #   LLM上下文压缩
  query_rewriter.py       #   HyDE查询改写
  llm_fusion.py           #   LLM候选融合
  evidence_builder.py     #   结构化证据包构建
  graph_retriever.py      #   知识图谱检索
  dense_retriever.py      #   稠密向量检索 (BGE-M3)
  vector_retriever.py     #   稀疏向量检索 (BM25)
  reranker.py             #   CrossEncoder重排
  embedder.py             #   BGE嵌入模型
content_analysis/         # 内容分析层
  entity_extractor.py     #   实体提取
  state_machine.py        #   状态机提取
  rule_extractor.py       #   规则提取
  kg_exporter.py          #   知识图谱导出
parser/                   # 文档解析层
  docling_parser.py       #   Docling解析器
  mineru_parser.py        #   MinerU解析器
api/                      # API层
  main.py                 #   FastAPI服务
tests/                    # 测试 (97个测试用例)
```

## LLM提供商

| 提供商 | 模型 | 环境变量 |
|--------|------|----------|
| DeepSeek | deepseek-v4-flash | `DEEPSEEK_API_KEY` |
| 智谱GLM | glm-4-flash | `ZHIPU_API_KEY` |
| 火山引擎(豆包) | doubao-vision-pro-32k | `ARK_API_KEY` |

## 运行测试

```bash
python -m pytest tests/ -v
```

## License

MIT
