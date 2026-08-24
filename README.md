# Agentic-RAG：中文 RAG 技术知识库服务

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Code License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Corpus License: CC BY-NC-SA 4.0](https://img.shields.io/badge/corpus-CC%20BY--NC--SA%204.0-lightgrey.svg)](data/THIRD_PARTY_NOTICES.md)

面向中文 RAG 学习与技术问答场景的 Agentic RAG 工程化实验：在开源 LangGraph 教学底座上，加入公开语料治理、DeepSeek 适配、可复现评测和 FastAPI/SSE 服务层。

> **状态：活跃原型。** 适合学习、评测和本地演示；当前没有认证、限流、TLS 或持久会话，不能直接暴露到公网。

## 这个 fork 做什么

原项目很好地展示了 Agentic RAG 的基本结构。本 fork 不把“重写框架”当目标，而是模拟更常见的工程任务：接手一个可运行的开源底座，把它改造成面向具体领域、能够测量、能够通过 API 集成的系统。

目前已交付的增量集中在四条线上：

- **模型适配**：运行时改为 OpenAI-compatible `ChatOpenAI`，默认接入 DeepSeek；查询改写使用 DeepSeek 支持的 JSON mode。
- **语料治理**：用 manifest 管理固定版本的公开中文 RAG 教程，记录逐文件来源、许可与 SHA-256，并在同步时清除清单外残留。
- **评测闭环**：维护 30 条、6 类题型的 golden set，记录检索、生成、拒答/澄清、延迟、token 与成本指标。
- **服务化**：增加 FastAPI 同步调用与 SSE 流式协议；Gradio 只作为 API 客户端，不再直接持有 RAG 运行时。

## 来源与归属

本仓库是 [GiovanniPasq/agentic-rag-for-dummies](https://github.com/GiovanniPasq/agentic-rag-for-dummies) 的二次开发版本，不是上游项目的官方延续。

- **原作者**：[Giovanni Pasqualino](https://github.com/GiovanniPasq)
- **上游许可**：MIT；原始版权声明保留在 [`LICENSE`](LICENSE)
- **上游教学文档**：[`project/README.md`](project/README.md) 与 [`notebooks/`](notebooks/)；其中部分 Ollama-first 用法不代表本 fork 的当前运行方式
- **当前检索语料**：来自 [`mothieras/all-in-rag`](https://github.com/mothieras/all-in-rag) 固定修订版，遵循 CC BY-NC-SA 4.0，不适用本仓库代码的 MIT License；详见 [`data/THIRD_PARTY_NOTICES.md`](data/THIRD_PARTY_NOTICES.md)

下表明确区分继承能力与本 fork 的工作：

| 领域 | 上游底座 | 本 fork 的增量 |
|---|---|---|
| Agent 编排 | LangGraph 主图/子图、查询改写、HITL 澄清、并行子问题、上下文压缩 | DeepSeek JSON mode 适配；checkpointer 作为可注入依赖；服务层按 `thread_id` 隔离请求 |
| 检索 | 父子分块、Qdrant dense + sparse hybrid retrieval、文件型 parent store | 公开中文 RAG 语料、固定 revision 与哈希校验、来源 metadata、检索 recorder 与分维度评测 |
| 应用入口 | Gradio 教学应用 | FastAPI `/invoke`、`/stream`、`/history`；独立 HTTP/SSE client；Gradio 改为薄客户端 |
| 评测 | 通用 evaluation notebook | 30 条领域 golden set、自动评测 runner、基线报告与 badcase 信号 |
| 可运行性 | 本地教学项目 | 环境变量驱动的 DeepSeek 配置、smoke script、API/schema/graph 测试 |

完整提交差异也可通过 GitHub 的 [fork compare](https://github.com/GiovanniPasq/agentic-rag-for-dummies/compare/main...mothieras:main) 查看。

## 已实现能力

### Agent 与检索

- LangGraph 主图负责历史摘要、查询改写、澄清和答案聚合。
- 子图负责工具调用、父子块检索、上下文压缩和 fallback。
- Qdrant 以本地模式保存 dense 与 FastEmbed BM25 sparse vectors，并使用 hybrid retrieval。
- `InMemorySaver` 保存单进程内会话；API 接受显式 `thread_id` 继续多轮对话。
- 可选 Langfuse callback 记录图节点、LLM 和工具调用。

### 语料治理

[`data/manifest.json`](data/manifest.json) 是入库清单，当前从 `all-in-rag` 选取 14 篇中文 Markdown，覆盖七类 topic：

- RAG 基础
- 数据加载与文本分块
- Embedding、向量数据库与索引优化
- 混合检索、查询构建/改写与重排压缩
- 格式化生成与 Function Calling
- RAG 评估方法与工具
- 知识图谱增强 RAG

每条语料记录固定来源 URL、许可、topic、revision 和 SHA-256；同步脚本会删除已退出清单的陈旧文件。规则见 [`data/README.md`](data/README.md)。

### API 协议

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | RAGSystem readiness |
| `GET` | `/info` | 当前模型与 collection |
| `POST` | `/invoke` | 非流式调用；返回答案、检索上下文和候选来源 |
| `POST` | `/stream` | SSE 流式调用 |
| `GET` | `/history?thread_id=...` | 查询进程内会话历史 |

SSE 使用结构化事件，而不是把所有内容压成一段文本：

```text
answer_token · tool_call · tool_result · system_status
clarification · done · error
```

`done` 事件携带最终答案、改写后的子问题、检索上下文及从上下文中解析出的候选来源文件名。

## 服务化（FastAPI + SSE）

```text
L4  Gradio UI
        |
        v
    AgentClient (HTTP / SSE)
        |
L3  FastAPI  /invoke · /stream · /history
        |
L2  RAGSystem  bootstrap · checkpointer · run config
        |
L1  LangGraph  main graph + agent subgraph
        |
L0  Qdrant hybrid retrieval + parent store + corpus
```

依赖方向保持单向：UI 只依赖 client，API 只通过 `RAGSystem` 使用 graph，检索和 Agent 内部可以继续演进而不改变 HTTP 协议。

主要代码入口：

- [`project/api/`](project/api/)：FastAPI 路由、SSE 事件映射与服务依赖
- [`project/client/`](project/client/)：同步调用与 SSE 消费客户端
- [`project/core/rag_system.py`](project/core/rag_system.py)：模型、存储、工具和 graph 的 composition root
- [`project/rag_agent/`](project/rag_agent/)：主图、子图、节点、边与工具
- [`project/schema/`](project/schema/)：请求、响应和 SSE event schemas

## 快速开始

### 1. 安装依赖

需要 Python 3.11+。

```bash
git clone https://github.com/mothieras/agentic-rag-for-dummies.git
cd agentic-rag-for-dummies

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 配置模型

```bash
cp project/.env.example project/.env
```

在 `project/.env` 中填写：

```dotenv
DEEPSEEK_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
JUDGE_MODEL=deepseek-chat
```

`project/.env` 已被 Git 忽略，不要提交真实密钥。

### 3. 构建本地索引

首次运行会下载 embedding 模型，并根据 manifest 重建本地 Qdrant 与 parent store：

```bash
# 可选：从固定 revision 重新下载并校验公开语料
python3 data/sync_sources.py

python project/ingest_corpus.py
```

### 4. 启动 API

```bash
cd project
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

检查 readiness：

```bash
curl http://127.0.0.1:8000/health
```

流式调用示例：

```bash
curl -N http://127.0.0.1:8000/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"解释 HyDE 如何改写检索问题","stream_tokens":true}'
```

### 5. 启动 Gradio 客户端

另开终端：

```bash
cd project
API_URL=http://127.0.0.1:8000 python app.py
```

## 评测

评测集位于 [`eval/golden_set.jsonl`](eval/golden_set.jsonl)，覆盖：

```text
exact_term · concept_contrast · multi_hop
version_conflict · ambiguous_followup · unanswerable
```

完成入库并配置 judge model 后运行：

```bash
cd eval
python run_eval.py
```

### 已记录基线

2026-08-23 `all-in-rag` 公开语料基线是当前基准：完整报告见 [`eval/reports/baseline-all-in-rag-2026-08-23.md`](eval/reports/baseline-all-in-rag-2026-08-23.md)，per-item 数据见 [`baseline-all-in-rag-2026-08-23.jsonl`](eval/reports/baseline-all-in-rag-2026-08-23.jsonl)。30 条全部执行，0 错误、0 SKIP；检索指标只统计实际进入检索流程的 20 题，5 条 HITL 澄清题由 clarification 指标单独评估。

| 指标 | 结果 |
|---|---:|
| Recall@5 / Recall@7 | 1.000 / 1.000 |
| MRR | 0.900 |
| Faithfulness | 0.938 |
| Answer relevancy | 0.906 |
| Context precision / recall | 0.690 / 0.908 |
| Refusal recall / precision | 0.800 / 0.840 |
| Clarification rate | 0.600 |
| Latency P50 / P95 | 14.65s / 21.89s |

保留偏低指标是刻意的：当前主要缺口是显式拒答、澄清问题覆盖度和版本过滤。Recall 1.000 也只代表这套固定语料与 golden set，不是对开放问题的泛化承诺。

## 测试

```bash
python -m pytest tests/
```

CI（`.github/workflows/ci.yml`）在 push/PR 时执行 compile 检查 + 全量 pytest，作为进入 main 的语法与回归门禁。本地等价命令：

```bash
python -m compileall -q rag_agent core api db schema client document_chunker.py config.py utils.py  # 在 project/ 下
python -m pytest tests/ -q
```

当前测试覆盖：

- Pydantic request/response/SSE schemas
- FastAPI readiness、invoke 与 stream contract
- token、工具调用、澄清和 done 事件映射
- LangGraph 默认与注入 checkpointer 的编译

这些测试通过 stub 隔离真实 LLM 与 Qdrant，属于服务契约和 graph composition 测试，不等同于端到端评测；真实模型行为由 `eval/` 单独记录。

## 仓库结构

```text
project/
  api/             FastAPI routes + SSE mapping
  client/          HTTP/SSE client
  core/            RAGSystem composition and observability
  db/              Qdrant and parent store
  rag_agent/       LangGraph nodes, edges, state and tools
  schema/          API and event contracts
  ui/              Gradio presentation

data/              governed corpus + manifest
eval/              golden set, metrics, runner and reports
tests/             API/schema/graph tests
notebooks/         upstream learning notebooks
```

## 当前边界与路线图

### 尚未作为已完成能力声明

- 中文分词 BM25、自定义 RRF 与 reranker
- 显式拒答路由
- 按版本/有效期过滤检索结果
- PostgresSaver 持久会话
- 认证、租户隔离、限流、TLS 和生产级 CORS
- 可核验的逐句引用；当前 `sources` 只是从检索上下文解析出的候选文件名

### 下一步

1. 以当前基线为守卫，依次引入中文 sparse retrieval、fusion 与 reranker。
2. 为无答案问题增加显式拒答路由，并单独优化 recall/precision。
3. 将 manifest 的版本字段接入检索过滤。
4. 用持久 checkpointer 替换 `InMemorySaver`，补充 thread 生命周期 API。
5. 完成并验证容器化启动链路，再把 Docker Compose 提升为正式 quickstart。

## License

代码沿用上游的 [MIT License](LICENSE)，原作者版权声明保持不变。`data/raw/docs/` 与 `data/interview_docs/docs/` 中的第三方语料遵循 [CC BY-NC-SA 4.0](data/THIRD_PARTY_NOTICES.md)，不适用 MIT License；使用或分发时须分别遵守对应条款。
