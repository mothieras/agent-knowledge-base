# Agentic-RAG：中文 RAG 技术知识库服务

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Code License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Corpus License: CC BY-NC-SA 4.0](https://img.shields.io/badge/corpus-CC%20BY--NC--SA%204.0-lightgrey.svg)](data/THIRD_PARTY_NOTICES.md)

面向中文 RAG 学习与技术问答场景的 Agentic RAG 工程化实验：在开源 LangGraph 教学底座上，加入公开语料治理、DeepSeek 适配、可复现评测和 FastAPI/SSE 服务层。

> **状态：活跃原型。** 适合学习、评测和本地演示；当前没有认证、限流、TLS 或持久会话，不能直接暴露到公网。

## 产品方向与本次交付范围（已确认，尚未实施）

新定位是**自托管知识检索服务**：以 RAG 质量和可追溯证据为核心，同一个 FastAPI 应用提供 HTTP 与 MCP；生成模型可选，普通 RAG 单图与 Agentic RAG 双图共用模型配置。

用户已选择 **A：五天检索优先演示版**，主要用于学习与求职展示：

- **本次目标**：受控资料离线导入；无生成模型的 HTTP/MCP 检索与证据回查；单次普通 RAG/双图 Agent 问答及效果、延迟、成本比较；Pi 实际接入；Python/Docker 与现有 Gradio 演示。
- **明确顺延**：每 Agent 身份与私有分区、公共协作写入、异步入库 API、文档历史和在线版本生命周期。五天版所有获准访问者看到同一固定资料库，不宣传完整共享知识服务。
- **不纳入新产品**：内置 Web/Chrome 工具、自动记忆学习、跨请求聊天历史或 HITL 恢复、管理后台、OCR。当前已有的会话能力在代码迁移前仍是现状，不代表新产品继续承诺。

文档分工：

- [作战计划](PLAN.md)：定位（最终形态为给 Agent 用的自托管知识库）、收尾两步、评测纪律与范围变更原则。原 PRD / DESIGN / ROADMAP 已归档至 [docs/archive/](docs/archive/)，仅作追溯。

**以下“已实现能力”、当前接口和历史报告描述代码现状与验证证据；未声明的能力与后续范围以 [作战计划](PLAN.md) 为准。**

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
- **当前检索语料**：来自 [`mothieras/all-in-rag`](https://github.com/mothieras/all-in-rag) 固定修订版，遵循 CC BY-NC-SA 4.0，不适用本仓库代码的 MIT License；详见 [`data/THIRD_PARTY_NOTICES.md`](data/THIRD_PARTY_NOTICES.md)

下表明确区分继承能力与本 fork 的工作：

| 领域 | 上游底座 | 本 fork 的增量 |
|---|---|---|
| Agent 编排 | LangGraph 主图/子图、查询改写、HITL 澄清、并行子问题、上下文压缩 | DeepSeek JSON mode 适配；单次化：固定 RAG 单图 + 双图单次 decision 协议、共享请求预算、引用机械校验与一次修复 |
| 检索 | 父子分块、Qdrant dense + sparse hybrid retrieval、文件型 parent store | 公开中文 RAG 语料、固定 revision 与哈希校验、来源 metadata、检索 recorder 与分维度评测 |
| 应用入口 | Gradio 教学应用 | FastAPI `/search` `/evidence` `/invoke` `/stream`、MCP 只读+问答工具、独立 HTTP/SSE client；Gradio 改为薄客户端 |
| 评测 | — | 30 条领域 golden set、40 题检索挑战集、自动评测 runner、基线报告与 badcase 信号 |
| 可运行性 | 本地教学项目 | 环境变量驱动的 DeepSeek 配置、生成模型可选的检索服务、smoke script、API/schema/graph 测试 |

完整提交差异也可通过 GitHub 的 [fork compare](https://github.com/GiovanniPasq/agentic-rag-for-dummies/compare/main...mothieras:main) 查看。

## 已实现能力

### Agent 与检索

- 两种问答图共用一套模型配置：`rag` 固定单图（retrieve → assemble_context → generate → validate_result，零命中确定性拒答，不做改写或工具循环）；`agentic` 双图（主图改写/拆解 → 并行子图检索 → 聚合 → 校验）。
- 主图/子图共享请求级预算（最多 3 子问题、8 次工具调用、10 次迭代）；调用工具前预留预算。
- 单次 decision 协议：`answered` / `clarification_required` / `refused`，`clarification_required` 是终态而非暂停点；`/history` 与 `thread_id` 已退役。
- 引用机械校验（`rag_agent/validation.py`）：evidence_id 必须属于本次实际取得的证据集合，quote 必须是证据原文精确子串，span 由 quote 位置推导并绑定快照；失败在预算内修复一次，仍失败返回 `result_validation_failed`，不伪造引用、不冒充正常拒答。
- Qdrant 以本地模式保存 dense 与 FastEmbed BM25 sparse vectors，并使用 hybrid retrieval。
- 检索证据走类型化 `RetrievalHit` 契约（`db/retrieval.py`）：`source`/`version`/有效期/`priority` 等 manifest 元数据 + `chunk_id` + 父文本精确 `span` 切片 + `retrieval_channel`，流经图 state、SSE 事件与 eval，全程无字符串解析。`Retriever` 协议由 `QdrantRetriever`（prod）与 `InMemoryRetriever`（test）两个适配器坐实。
- 生成模型可选：未配置时检索照常 ready，问答明确返回 `llm_not_configured`。

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

另有项目自有的受治理版本冲突 fixture（[`data/fixtures/`](data/fixtures/)，MIT 许可）：两对虚构政策文档各含一个已过期与一个当前有效版本，入库进同一 collection，用于验证 manifest 元数据（version/有效期/priority）全链路传播，并为版本冲突评测提供素材。

### API 协议

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 检索 readiness（不依赖 LLM） |
| `GET` | `/info` | 快照 index_id、模型标识、可用 modes |
| `POST` | `/search` | 结构化证据检索（无生成模型调用） |
| `GET` | `/evidence/{evidence_id}` | 按 ID 回查有界原文窗口 |
| `POST` | `/invoke` | 单次问答（`mode`=rag/agentic，默认 rag） |
| `POST` | `/stream` | 同一问答契约的 SSE 进度与结果 |
| `GET` | `/history` | 已退役（410）：单次请求语义，不保留会话 |

问答结果统一为单次 decision 协议：

```text
request_id · index_id · mode
decision = answered | clarification_required | refused
answer · clarification_question? · limitations[]
citations[] = citation_id + evidence_id + source + chunk_id + span + quote
usage = input/output tokens + estimated cost + model calls
```

`thread_id` 等旧协议字段被明确拒绝（参数错误），不静默忽略。`/invoke` 与 `/stream` 的 SSE 使用结构化事件：

```text
status · tool_call · tool_result · done · error
```

`done` 事件携带**校验后**的完整 `AnswerResponse`（引用经机械校验可精确回查，答案不再实时流式输出未校验文本）；异常恰好一个 `error`。

## 服务化（FastAPI + SSE）

```text
L4  Gradio UI
        |
        v
    AgentClient (HTTP / SSE)
        |
L3  FastAPI  /health /info /search /evidence /invoke /stream · /mcp
        |
L2  AppService  search/read_evidence + AnswerService  rag/agentic 单次问答
        |
L1  LangGraph  rag 单图 · agentic 主图 + 检索子图
        |
L0  Qdrant hybrid retrieval + parent store + corpus
```

依赖方向保持单向：UI 只依赖 client；API 只通过 L2 公共接口使用检索与问答，不 import `db/` / `rag_agent/` 内部；检索和 Agent 内部可以继续演进而不改变 HTTP 协议。生成模型可选：未配置 `DEEPSEEK_API_KEY` 时检索照常 ready，问答返回明确的 `llm_not_configured`。

主要代码入口：

- [`src/api/`](src/api/)：FastAPI 路由、SSE 序列化、MCP 工具与访问边界
- [`src/client/`](src/client/)：HTTP/SSE 客户端
- [`src/core/app_service.py`](src/core/app_service.py)：检索先于生成的 composition root
- [`src/core/retrieval_service.py`](src/core/retrieval_service.py)：search/read_evidence 与公共证据 DTO
- [`src/core/answer_service.py`](src/core/answer_service.py)：单次问答、预算、usage 与事件
- [`src/rag_agent/`](src/rag_agent/)：rag 单图、agentic 主图/子图、引用校验
- [`src/schema/dto.py`](src/schema/dto.py)：HTTP/MCP 共用请求、证据、答案与 SSE event schemas

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
cp .env.example .env
```

在 `.env` 中填写：

```dotenv
DEEPSEEK_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
JUDGE_MODEL=deepseek-chat
```

`.env` 已被 Git 忽略，不要提交真实密钥。

### 3. 构建本地索引

首次运行会下载 embedding 模型，并根据 manifest 重建本地 Qdrant 与 parent store：

```bash
# 可选：从固定 revision 重新下载并校验公开语料
python3 data/sync_sources.py

python src/ingest_corpus.py
```

### 4. 启动 API

```bash
cd src
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
  -d '{"message":"解释 HyDE 如何改写检索问题","mode":"agentic"}'
```

非流式单次问答（`mode` 默认 `rag`）：

```bash
curl http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -d '{"message":"什么是 HyDE？","mode":"rag"}'
```

未配置 `DEEPSEEK_API_KEY` 时服务照常 ready：`/search`、`/evidence`、MCP 检索工具可用，问答返回 `llm_not_configured`。

### 5. 启动 Gradio 客户端

另开终端：

```bash
cd src
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
env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_eval.py --mode agentic  # 或 --mode rag
```

检索挑战集（40 题，不调用生成模型）：

```bash
cd eval
env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_challenge.py
```

### 已记录基线

**当前基准是 2026-09-10 单次 decision 协议基线（mode=agentic 与 mode=rag 分别建立首轮基线）**：完整报告见 [`eval/reports/baseline-2026-09-10-agentic.md`](eval/reports/baseline-2026-09-10-agentic.md) 与 [`baseline-2026-09-10-rag.md`](eval/reports/baseline-2026-09-10-rag.md)，per-item 数据见同名 `.jsonl`。检索指标统计进入检索流程的 20 题，5 条歧义题由 clarification 指标单独评估。

| 指标（单次 decision 协议） | 2026-09-10 agentic | 2026-09-10 rag |
|---|---:|---:|
| 执行 / 错误 / SKIP | 30 / 0 / 0 | 30 / 0 / 0 |
| Recall@5 / Recall@7 | 1.000 / 1.000 | 1.000 / 1.000 |
| MRR | 1.000 | 1.000 |
| expired/fixture 泄漏题数 | 1 / 1 | 0 / 0 |
| Faithfulness | 0.957 | 0.991 |
| Answer relevancy | 0.918 | 0.879 |
| Context precision / recall | 0.674 / 0.900 | 0.863 / 0.794 |
| refusal precision（TP/(TP+FP)）/ recall | 1.000 / 1.000 | 0.500 / 1.000 |
| misrefusal / overclarify rate | 0.000 / 0.040 | 0.200 / 0.000 |
| Clarification rate（歧义题 n=5） | 0.200 | 0.000 |
| 引用机械有效性（answered 题） | 23/23 | 20/20 |
| Latency P50 / P95 | 10.56s / 17.85s | 1.65s / 3.74s |
| 平均 cost（¥/query） | 0.0075 | 0.0027 |

两种模式首轮基线的已知差异（Day 4 对比审计输入，不预设结论）：rag 单图无改写/拆解，单次检索的 top-k 证据直接约束回答，歧义题不会返回澄清（clarification 0.000）且误拒率偏高（0.200，部分多跳题证据片段不足以支撑完整回答）；agentic 拆解/多轮工具后拒答精度更高（refusal precision 1.000，含 rewrite JSON 解析失败重试）。agentic 延迟与成本显著更高（P50 10.56s vs 1.65s，成本约 2.8×），检索指标两者持平（Recall@5 均 1.000）。

**历史基准（旧 HITL 协议，不回写、不直接同比）**：

| 指标 | 2026-09-01 | 2026-08-28 |
|---|---:|---:|
| Recall@5 / Recall@7 | 1.000 / 1.000 | 1.000 / 1.000 |
| MRR | 0.975 | 0.975 |
| Faithfulness | 0.895 | 0.943 |
| Answer relevancy | 0.909 | 0.882 |
| Context precision / recall | 0.679 / 0.908 | 0.761 / 1.000 |
| Refusal recall / legacy specificity（历史非误拒率） | 1.000 / 0.840 | 1.000 / 0.880 |
| Clarification rate（HITL 暂停口径） | 0.800 | 0.600 |
| Latency P50 / P95 | 15.11s / 24.85s | 14.99s / 27.03s |

完整历史报告：[2026-09-01](eval/reports/baseline-2026-09-01.md)、[2026-08-28](eval/reports/baseline-2026-08-28.md)、[2026-08-23](eval/reports/baseline-all-in-rag-2026-08-23.md)（M0 冻结）。

**历史指标口径提醒**：旧 runner/报告中的 `refusal.precision` 实际计算 `1 - false_refusals / answerable_n`，不是标准拒答 precision；新 decision 协议使用标准 TP/(TP+FP)，二者不能直接比较。旧 HITL 的澄清暂停率与新单次澄清率也是不同语义，并列报告不直接同比。

检索挑战集（40 题直接检索）回归锚点 Recall@5=1.000 与 Day 1 基线持平（见 [`eval/reports/challenge-2026-09-09.md`](eval/reports/challenge-2026-09-09.md)），全量指标 Recall@5=0.950、MRR=0.933、2 题 hard-negative 泄漏与 Day 2 持平。Recall 1.000 只代表这套固定语料与 golden set，不是对开放问题的泛化承诺。

## 测试

```bash
python -m pytest tests/
```

CI（`.github/workflows/ci.yml`）在 push/PR 时执行 compile 检查 + 全量 pytest，作为进入 main 的语法与回归门禁。本地等价命令：

```bash
python -m compileall -q rag_agent core api db schema client document_chunker.py config.py utils.py  # 在 src/ 下
python -m pytest tests/ -q
```

当前测试覆盖：

- Pydantic 请求/响应/SSE schemas（含 `thread_id` 拒绝与 mode 校验）
- FastAPI readiness、search/evidence 契约、invoke/stream decision 协议与终态
- 固定 RAG 单图与单次化双图的编译、三种 decision、引用修复与错误路径
- 引用机械校验：伪造 evidence_id、quote 非原文子串、span 推导与精确回查
- 跨请求状态隔离（无 checkpointer、无会话历史）
- 检索路径（search→compress→collect）与 chunker metadata/chunk_id/span
- 受治理 fixture 的静态契约（active/expired 配对语义）
- 真实本地索引集成测试：`skipif` 本地未入库，验证 metadata 全链路传播与 `parent.content[span_start:span_end] == hit.content` 精确反查（fresh clone 自动跳过）

除最后两类外，测试通过 stub 隔离真实 LLM 与 Qdrant，属于服务契约和 graph composition 测试，不等同于端到端评测；真实模型行为由 `eval/` 单独记录。

## 仓库结构

```text
src/
  api/             FastAPI routes、SSE 序列化、MCP 工具与鉴权
  client/          HTTP/SSE client
  core/            AppService composition、检索/问答服务、预算与 usage
  db/              Qdrant、parent store、快照与证据存储
  rag_agent/       rag 单图、agentic 双图、节点、引用校验
  schema/          HTTP/MCP 共用 DTO 与 SSE event schemas
  ui/              Gradio presentation

data/              governed corpus + manifest
eval/              golden set、挑战集、metrics、runner 和 reports
tests/             API/schema/graph/validation tests
docs/              归档：原 PRD/DESIGN/ROADMAP、检查点与旧路线
```

## 当前边界与路线图

### 尚未作为已完成能力声明

- 中文分词 BM25、自定义 RRF、reranker、按版本/有效期过滤（已有元数据不等于已执行过滤）
- Docker 交付及计划中的 Pi 端到端兼容性验收（MCP Streamable HTTP 已按 SDK 2.x 实现，Pi 实测在 Day 5）
- 每 Agent 身份、公私分区、协作编辑、异步入库和文档历史生命周期（已顺延）
- 限流、TLS、生产级部署；新产品以单次请求为语义，不再提供跨请求会话

### 下一步

1. 第一步，对照实验定稿：最终 commit 重跑双基线并修正基线表、badcase 审计、规模与并发实测，产出对照报告。
2. 第二步，发布：Docker 原生流程、Pi 实测端到端、空数据复现、README 收尾与发布清单。
3. 发布后第一优先是版本/有效期过滤；知识库本体与检索消融另行排期。收尾细节与评测纪律见 [作战计划](PLAN.md)，旧 M0–M6 计划保留在[历史归档](docs/archive/roadmap-before-retrieval-demo.md)。

## License

代码沿用上游的 [MIT License](LICENSE)，原作者版权声明保持不变。`data/raw/docs/` 与 `data/interview_docs/docs/` 中的第三方语料遵循 [CC BY-NC-SA 4.0](data/THIRD_PARTY_NOTICES.md)，不适用 MIT License；使用或分发时须分别遵守对应条款。
