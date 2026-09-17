# Agent Knowledge Base

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Code License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Corpus License: CC BY-NC-SA 4.0](https://img.shields.io/badge/corpus-CC%20BY--NC--SA%204.0-lightgrey.svg)](data/THIRD_PARTY_NOTICES.md)

自托管、给 Agent 用的知识库服务：hybrid 检索、可精确回查的证据（span/版本/内容哈希）、HTTP 与 MCP 只读接入，以及 rag/agentic 双模式单次问答。入库与证据链路由 manifest 驱动，语料可整体替换；仓库内置一份受治理的中文 RAG 示例语料与配套评测集，本页评测结果均在该示例语料上测得。

> **状态：活跃原型，面向本地部署。** 鉴权可选（Bearer）；未内置限流与 TLS，不建议直接暴露公网。

后续产品方向与建设顺序见 [PRODUCT 总纲](PRODUCT.md) 与 [ROADMAP 路线图](ROADMAP.md)：先优化现有文档入库，再建设单用户、多 Agent 共享的 Memory / Knowledge 服务。条目写入、协作修订和无模型全文检索尚未实现，本页功能与评测描述仍对应当前只读检索演示版。

该方向与 Codex、Claude Code 等调用方的原生记忆配合使用，重点补足不同 Agent 之间的内容共享、外部资料与积累条目的关联，以及共享内容的持续修订与证据回查。调用方通过公开工具接入，无需服务接管其内部记忆文件；这些目标流程的实际收益仍待验证。

## Why

LLM Agent 的答案质量取决于它引用的事实是否可信。与其让模型在参数里猜，不如给它一个可回查的事实来源：检索命中带精确 span 与版本元数据，引用经机械校验必须命中真实证据，过期版本有明确标识。过程上下文归调用方的 agent，事实依据归本库——服务无会话状态。

三种用法：

- **Agent 接地**（重心）：外部 MCP 客户端（Pi 等）把 search/get_context 当工具，取带 span/版本的证据，用自己的模型回答
- **问答委托**：应用调 ask_knowledge 得 decision（answered/澄清/拒答）与机械校验过的引用；rag/agentic 双模式同时是接地质量与编排成本的测量仪
- **多 Agent 共享 Memory / Knowledge**（规划中）：不同 Agent 共同保存、取用和修订事实与知识，并回查资料依据；单用户共享读写，保留作者与修订历史，第一版不做私有分区，见 [PRODUCT 总纲](PRODUCT.md)

## Features

- **Hybrid 检索**：Qdrant dense + FastEmbed BM25 sparse 融合；父子分块，child 命中、parent 全文取回
- **证据可精确回查**：`evidence_id` → 有界原文窗口，`content_hash` 客户端可复算，span 是父文本的精确字符切片
- **引用机械校验**：evidence_id 必属本次实际证据、quote 必须是证据原文精确子串；预算内自动修复一次，仍失败显式报错——不伪造引用
- **单次 decision 协议**：`answered / clarification_required / refused` 皆为终态；无会话、无跨请求状态
- **双模式问答**：`rag` 单图（快、便宜）与 `agentic` 双图（改写/拆解/并行补查，共享请求级预算：3 子问题 / 8 工具调用 / 10 迭代），同 index/模型同场对照见 Evaluation
- **MCP 接入**：Streamable HTTP 端点 + Bearer 鉴权，Agent 的工具即知识库
- **共享条目服务（Memory/Knowledge）**：Agent 跨会话/跨客户端写入、修订、搜索与回查持久条目；全文搜索（SQLite FTS5 + jieba 预分词 + bm25，无模型）按全局/单项目/多项目/全部项目范围过滤；原子版本检查（409 附当前条目）、幂等写入、软生命周期（archive/delete/restore）、查询时到期判定、全量修订历史。接入指南见 [docs/ENTRY_TOOL_GUIDE.md](docs/ENTRY_TOOL_GUIDE.md)
- **manifest 语料治理**：逐文件登记来源、许可、SHA-256；同步采用镜像语义清理清单外残留，语料可整体替换
- **版本元数据全链路**：version / effective_date / expired_date / priority 从 manifest 流经 chunk → 检索 → API → eval
- **可复现评测**：30 题 golden set（6 类题型）、40 题检索挑战集、规模与并发实测；报告绑定 commit / index / 模型 / 参数
- **生成模型可选**：未配置 key 时检索照常 ready，问答返回明确的 `llm_not_configured`

## Quick Start

需要 Python 3.11+。

```bash
git clone https://github.com/mothieras/agent-knowledge-base.git
cd agent-knowledge-base
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

（可选）配置生成模型——不配置也能完整使用检索与证据回查：

```bash
cp .env.example .env
# DEEPSEEK_API_KEY=sk-...  LLM_MODEL=deepseek-chat  LLM_BASE_URL=...  JUDGE_MODEL=...
```

构建本地索引（首次运行会下载 embedding 模型）并启动 API：

```bash
python src/ingest_corpus.py
cd src && uvicorn api.main:app --host 127.0.0.1 --port 8000
```

```bash
curl http://127.0.0.1:8000/health                                   # 检索 readiness（不依赖 LLM）
curl http://127.0.0.1:8000/invoke -H 'Content-Type: application/json' \
  -d '{"message":"什么是 HyDE？","mode":"rag"}'                     # 单次问答
curl -N http://127.0.0.1:8000/stream -H 'Content-Type: application/json' \
  -d '{"message":"解释 HyDE 如何改写检索问题","mode":"agentic"}'    # SSE 流式
```

Gradio 客户端：`cd src && API_URL=http://127.0.0.1:8000 python app.py`（UI 只是 API 的薄客户端）。

### Docker

```bash
docker build -t agent-kb-demo .
docker run -d --name agent-kb -p 8000:8000 \
  -v "$PWD/qdrant_db:/app/qdrant_db" \
  -v "$PWD/parent_store:/app/parent_store" \
  -v "$HOME/.cache/huggingface:/app/.cache/huggingface" \
  -v "$PWD/.fastembed_cache:/app/.cache/fastembed" \
  -e HF_HUB_OFFLINE=1 \
  [-e DEMO_API_TOKEN=<token>] [-e DEEPSEEK_API_KEY=sk-...] \
  agent-kb-demo
```

- 入口脚本发现无索引快照时先入库再启动（需联网下载嵌入模型，建议挂载既有索引/缓存卷）；镜像约 7 GB（含 torch/CUDA 运行库）
- `DEMO_API_TOKEN` 设置后 HTTP 与 MCP 统一要求 `Authorization: Bearer <token>`（无/错凭据 401）；不设置则本地免鉴权

## MCP Integration

MCP 端点（Streamable HTTP）：`http://127.0.0.1:8000/mcp`，与 HTTP 共用 Bearer 鉴权。

| 工具 | 用途 |
|---|---|
| `search_knowledge` | 结构化证据检索，返回带 span/版本/score 的命中 |
| `get_context` | 按 `evidence_id` 回查有界原文窗口（含 `content_hash`） |
| `ask_knowledge` | 单次问答委托（配置生成模型后发布） |
| `save_entry` | 新增共享 Memory/Knowledge 条目（scope/有效期/来源/幂等键） |
| `get_entry` | 按 ID 读条目当前完整状态（含到期判定） |
| `revise_entry` | 修订条目（`expected_revision` 并发检查；`updates` 键存在=修改、null=清除） |
| `entry_lifecycle` | archive / unarchive / delete / restore 软生命周期 |
| `list_entry_revisions` | 修订历史列表 |
| `search_entries` | 条目全文搜索（范围/类型过滤；未指定范围仅查全局） |

```json
{
  "mcpServers": {
    "agent-kb": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${DEMO_API_TOKEN}" }
    }
  }
}
```

实测（Pi 0.85.1 + pi-mcp-adapter 2.32.1 ↔ 服务端 MCP SDK 2.1.1）：工具发现 → Bearer 鉴权 → search 命中 → get_context 回查 → **Pi 用自己的模型基于证据作答并引用 source**，五步全绿；`ask_knowledge` decision=answered。工具的文本通道与 structuredContent 同载荷，只渲染文本的 MCP 客户端也能拿到完整结构化结果。冒烟脚本：`python src/smoke_mcp.py --url http://127.0.0.1:8000/mcp [--token <token>]`（含条目工具 save→revise→冲突通道→lifecycle→历史全链路）。

## Evaluation

以下评测在内置示例语料（中文 RAG 教程集 + 受治理版本 fixture）上测得；更换语料需重建索引并重跑全部评测。

**当前基准：2026-09-10 单次 decision 协议基线**（rag/agentic 同 index/模型/参数同场运行；本表由脚本从同场 summary 生成）。完整报告：[agentic](eval/reports/baseline-2026-09-10-agentic.md)、[rag](eval/reports/baseline-2026-09-10-rag.md)、[对照报告](eval/reports/comparison-2026-09-10.md)（含 badcase 审计）。检索指标统计进入检索流程的 20 题，5 条歧义题由 clarification 指标单独评估。

| 指标（单次 decision 协议） | 2026-09-10 agentic | 2026-09-10 rag |
|---|---:|---:|
| 执行 / 错误 / SKIP | 30 / 0 / 0 | 30 / 0 / 0 |
| Recall@5 / Recall@7 | 1.000 / 1.000 | 1.000 / 1.000 |
| MRR | 1.000 | 1.000 |
| expired/fixture 泄漏题数 | 0 / 0 | 0 / 0 |
| Faithfulness | 0.961 | 0.968 |
| Answer relevancy | 0.931 | 0.851 |
| Context precision / recall | 0.624 / 0.917 | 0.815 / 0.722 |
| refusal precision（TP/(TP+FP)）/ recall | 1.0 / 1.0 | 0.556 / 1.0 |
| misrefusal / overclarify rate（明确可答 n=20） | 0.0 / 0.0 | 0.1 / 0.0 |
| Clarification rate（歧义题 n=5） | 0.2 | 0.0 |
| 歧义题被硬拒（计入 refusal FP，不计入 misrefusal） | 0 | 2 |
| 引用机械有效性（answered 题） | 24/24 | 21/21 |
| Latency P50 / P95 | 13.99s / 22.25s | 1.84s / 3.74s |
| 平均 cost（¥/query） | 0.0082 | 0.0009 |

两种模式的已知差异（同场对照，不预设结论）：rag 单图无改写/拆解，单次检索的 top-k 证据直接约束回答——明确可答题误拒 0.1（2/20，证据片段不足以支撑完整回答），歧义题不会澄清且 2/5 被硬拒（计入 refusal FP，故拒答 precision 0.556）；agentic 拆解/多轮工具后拒答精度 1.000，歧义题澄清 1/5。代价：agentic 延迟与成本显著更高（P50 13.99s vs 1.84s，约 7.6×；成本约 9×），检索指标两者持平（Recall@5 均 1.000），context precision 双图更低（0.624 vs 0.815）——多路证据摊薄了上下文精度，但 context recall 更高（0.917 vs 0.722）。

**检索挑战集**（40 题直接检索）：回归锚点 Recall@5=1.000 与冻结基线持平；全量 Recall@5=0.950、MRR=0.933、2 题 hard-negative 泄漏（其一为"当前有效"问题命中过期版本）。Recall 1.000 只代表这套固定语料与 golden set，不是对开放问题的泛化承诺。

**规模与并发**：100 文档 / 12,098 块合成快照，3 并发 126/126 有效查询，0 错误 0 busy；P50/P95 与峰值资源见[规模实测报告](eval/reports/scale-2026-09-10.md)。

运行评测：

```bash
cd eval
env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_eval.py --mode agentic   # 或 --mode rag
env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_challenge.py            # 检索挑战集（不调生成模型）
```

## Architecture

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

依赖方向保持单向：UI 只依赖 client；API 层只通过 L2 公共接口使用检索与问答，不 import `db/` / `rag_agent/` 内部；检索与 Agent 内部可以继续演进而不改变 HTTP 协议。

关键设计决策：

- **单次 decision 协议**：三种 decision 皆为终态，单次请求语义贯穿 HTTP / SSE / MCP，服务无会话状态
- **引用机械校验**（`rag_agent/validation.py`）：evidence_id 必属本次实际取得的证据集合，quote 必须是证据原文精确子串，span 由 quote 位置推导并绑定快照；失败在预算内修复一次，仍失败返回 `result_validation_failed`，不伪造引用、不冒充正常拒答
- **类型化 `RetrievalHit` 契约**（`db/retrieval.py`）：命中作为对象流经 AgentState → events → eval，全程无字符串解析；`Retriever` Protocol 由 `QdrantRetriever`（prod）与 `InMemoryRetriever`（test）两个适配器坐实
- **manifest 语料治理**：每篇文档登记 `source_url` / `sha256` / `license` / `topic` / 版本，同步采用镜像语义清理清单外残留

```text
src/
  api/             FastAPI 路由、SSE 序列化、MCP 工具与鉴权
  client/          HTTP/SSE 客户端
  core/            AppService composition、检索/问答服务、预算与 usage
  db/              Qdrant、parent store、快照与证据存储
  rag_agent/       rag 单图、agentic 双图、节点、引用校验
  schema/          HTTP/MCP 共用 DTO 与 SSE event schemas
  ui/              Gradio 薄客户端
data/              受治理示例语料、manifest 与版本 fixture
eval/              golden set、挑战集、metrics、runner 和 reports
tests/             API/schema/graph/validation 测试
docs/              架构：分层依赖规则与领域词汇（ARCHITECTURE.md）
```

分层规则与领域词汇详见 [ARCHITECTURE](docs/ARCHITECTURE.md)。

测试：`python -m pytest tests/`（本地实测 119 passed / 0 skipped，含 4 项真实索引集成测试；stub 隔离真实 LLM 与 Qdrant，覆盖 schemas、decision 协议、引用校验、跨请求状态隔离、检索路径与 chunker span、fixture 静态契约、ingest 样例集 Level A 管道验证与入库终态分类；无本地索引时 4 项集成测试自动跳过）。CI 在 push/PR 时执行 compile 检查 + 全量 pytest。

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | readiness：检索 + 条目库（不依赖 LLM） |
| `GET` | `/info` | 快照 index_id、模型标识、可用 modes |
| `POST` | `/search` | 结构化证据检索（无生成模型调用） |
| `GET` | `/evidence/{evidence_id}` | 按 ID 回查有界原文窗口 |
| `POST` | `/invoke` | 单次问答（`mode`=rag/agentic，默认 rag） |
| `POST` | `/stream` | 同一问答契约的 SSE 进度与结果 |
| `POST` | `/entries` | 新增共享条目（201；scope/有效期/来源/幂等键） |
| `POST` | `/entries/search` | 条目全文搜索（范围/类型/limit；恒排除非 active 与已到期） |
| `GET` | `/entries/{id}` | 条目当前完整状态（含查询时到期判定） |
| `POST` | `/entries/{id}/revisions` | 修订（`expected_revision` 并发检查，409 附当前条目） |
| `GET` | `/entries/{id}/revisions` | 修订历史列表 |
| `GET` | `/entries/{id}/revisions/{n}` | 指定修订全量快照 |
| `POST` | `/entries/{id}/lifecycle` | archive / unarchive / delete / restore |

条目写操作复用并发槽纪律（busy 不排队 → 429）；错误统一 `{code, message}`：
`invalid_request` 400 · `invalid_project` 400 · `not_found` 404 ·
`revision_conflict` 409（附 `current`）· `idempotency_conflict` 409 ·
`entry_deleted` 409 · `invalid_transition` 409 · `busy` 429。条目库为独立
SQLite 文件（`ENTRIES_DB_PATH`，默认 `entries.db`，WAL）。

问答结果统一为单次 decision 协议：

```text
request_id · index_id · mode
decision = answered | clarification_required | refused
answer · clarification_question? · limitations[]
citations[] = citation_id + evidence_id + source + chunk_id + span + quote
usage = input/output tokens + estimated cost + model calls
```

`/stream` 使用结构化 SSE 事件（`status · tool_call · tool_result · done · error`）；`done` 携带**校验后**的完整结果——答案不实时流式输出未校验文本，异常恰好一个 `error`。

## Sample Corpus

仓库内置一份受治理的示例语料，用于演示与评测：从 [`mothieras/all-in-rag`](https://github.com/mothieras/all-in-rag) 固定修订版选取的 14 篇中文 RAG 教程（七类 topic），加上项目自有的两对虚构政策文档 fixture（各含一个已过期与一个当前有效版本，入库进同一 collection，用于验证版本元数据全链路传播并为版本冲突评测提供素材）。服务本身语料无关——入库与证据链路由 [`data/manifest.json`](data/manifest.json) 驱动，可整体替换为其他语料并重跑评测。治理规则与目录说明见 [`data/README.md`](data/README.md)。

## Ingest 样例集与失败终态

`data/ingest_samples/`：10 项样例（S1–S10，独立 manifest 登记 SHA-256 与用途，不进主语料 manifest）覆盖三种支持格式与空文本、无文本层 PDF、重复提交、同名冲突、不支持格式、非 UTF-8 编码等边界情形。两级验证：Level A（无模型，进 CI）断言结构锚点保留、child==parent span 切片、内容散列可复算与分块边界质量；Level B（`eval/ingest_samples/run_level_b.py`，本地手动跑，scratch 索引与质量索引隔离）逐样例对账目标终态表。

入库结果结构化：`DocumentManager.add_documents` 逐文档返回显式终态（`ok / duplicate / conflict / unsupported_format / unsupported_encoding / empty_text / no_text_layer / parse_error`），`ingest_corpus.py` 汇总输出分类计数与失败明细——失败不冒充成功、不支持格式不静默剔除。要点：同名目标按内容比对区分重复与冲突（静默去重会掩盖登记错误）；无文本层 PDF 剥除页界标记后判空（不再假成功入库），转换全程无 OCR 调用（注意是依赖门控：装入 OCR 依赖后图像页会自动启用，升级依赖须重验）；PDF 条目 metadata 显式标注 `origin_page: "missing"`（原文件页码映射需在 chunker 合并/拆分中维护绝对偏移，属后续重写范围）；分块边界在 S1/S3/S4 上检查无缺陷（代码块/表格/标题在 parent 层完整、产物零丢失；句中切口仅见于超尺寸无结构文本，为 splitter 降级加重平衡的设计内行为）；快照 `index_id` 摘要剔除随机 point UUID，同输入同产物可复现。

## Background & Attribution

本项目脱胎于开源 LangGraph 教学项目 [agentic-rag-for-dummies](https://github.com/GiovanniPasq/agentic-rag-for-dummies)，不把"重写框架"当目标，而是模拟更常见的工程任务：接手一个可运行的开源底座，把它改造成能够测量、能够通过 API 集成的知识库服务。如今两者已是两套系统：底座是 40 文件的教学 demo，本仓库是 157 文件的服务——132 个文件为本项目新增，约 20 个沿上游模块演进的文件集中在 LangGraph 双图骨架与检索基础设施（逐项对照见下表）。

| 领域 | 上游底座 | 本项目的增量 |
|---|---|---|
| Agent 编排 | LangGraph 主图/子图、查询改写、HITL 澄清、并行子问题、上下文压缩 | DeepSeek JSON mode 适配；单次化：固定 RAG 单图 + 双图单次 decision 协议、共享请求预算、引用机械校验与一次修复 |
| 检索 | 父子分块、Qdrant dense + sparse hybrid retrieval、文件型 parent store | 示例中文 RAG 语料、固定 revision 与哈希校验、来源 metadata、检索 recorder 与分维度评测 |
| 应用入口 | Gradio 教学应用 | FastAPI `/search` `/evidence` `/invoke` `/stream`、MCP 只读+问答工具、独立 HTTP/SSE client；Gradio 改为薄客户端 |
| 评测 | — | 30 条领域 golden set、40 题检索挑战集、自动评测 runner、基线报告与 badcase 信号 |
| 可运行性 | 本地教学项目 | 环境变量驱动的 DeepSeek 配置、生成模型可选的检索服务、smoke script、API/schema/graph 测试 |

- **原作者**：[Giovanni Pasqualino](https://github.com/GiovanniPasq)
- **上游许可**：MIT；原始版权声明保留在 [`LICENSE`](LICENSE)
- **示例语料**：来自 [`mothieras/all-in-rag`](https://github.com/mothieras/all-in-rag) 固定修订版，遵循 CC BY-NC-SA 4.0，不适用本仓库代码的 MIT License；详见 [`data/THIRD_PARTY_NOTICES.md`](data/THIRD_PARTY_NOTICES.md)

### Non-goals

内置 Web/Chrome 工具、自动记忆学习、跨请求聊天历史或 HITL 恢复、管理后台、OCR 均不在本服务范围；它不是聊天机器人产品，而是 Agent 的知识层。

## License

代码沿用上游的 [MIT License](LICENSE)，原作者版权声明保持不变。`data/raw/docs/` 与 `data/interview_docs/docs/` 中的第三方语料遵循 [CC BY-NC-SA 4.0](data/THIRD_PARTY_NOTICES.md)，不适用 MIT License；使用或分发时须分别遵守对应条款。
