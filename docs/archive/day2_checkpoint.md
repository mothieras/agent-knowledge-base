# Day 2 检查点报告：检索服务化（HTTP + MCP 只读）

> 日期：2026-09-07 ｜ git commit：`8998bcc`（本日改动未提交，见「变更集」）
> 检查点依据：[ROADMAP](../ROADMAP.md) Day 2；本报告是 Day 2 必须留下的证据。

## 1. 检查点完成项

| Day 2 要求 | 状态 | 证据 |
|---|---|---|
| 解耦检索与可选生成 bootstrap | ✅ | `core/app_service.py`：检索资源独立就绪，`llm_configured=False` 时 /health 仍 ready |
| L2 公共接口/证据 DTO | ✅ | `core/retrieval_service.py` + `schema/dto.py`；L3 不再 import db/rag_agent 内部 |
| HTTP search/read_evidence | ✅ | 真实 Uvicorn 全链路 + pytest 64 绿 |
| MCP 只读工具 | ✅ | 官方 client 握手 → tools/list → call_tool 全通 |
| 离线文本/MD/TXT/PDF 快照与校验 | ✅ | `db/snapshot.py` + ingest 写 manifest；启动校验（index_id 重算 + parent/Qdrant 一致性） |
| 无生成模型入库 → ready → HTTP/MCP → 回查 | ✅ | `[ingest] added=18` → `/health` ready → 协议等价 4/4 → evidence roundtrip |
| 同查询有序证据等价 | ✅ | `eval/verify_protocol_equivalence.py`：4/4 逐字段一致（含版本冲突题） |
| 无效 ID/快照/凭据测试 | ✅ | pytest：非法 ID 404/410、stale evidence 410、Bearer 401/200、Qdrant 漂移 RuntimeError |

## 2. 快照与 index_id

- 构建 manifest：`qdrant_db/snapshot_manifest.json`（ingest 完成后写入，服务只读）
- `index_id = sha256:0df9fbfaeecaaf2bef1f3a78b2db71aad767935a98aafaf398792fcdc2a34325`
- 摘要输入为 canonical JSON v1（键排序、ensure_ascii=False、allow_nan=False、separators=(',', ':')）；参与字段：两个 manifest hash、source 内容 hash、normalizer/chunker 配置、dense/sparse 模型、parent/child 数量与内容 hash、retrieval 配置。排除时间/路径/状态/自身 id/Qdrant UUID。
- 本次重建 parent_chunks=46、child_chunks=354（与历史 354 一致，chunker/模型未变）。

## 3. 检索挑战集（40 题，直接检索）

| 指标 | 值 | 备注 |
|---|---:|---|
| scored_items | 40 | 0 ERROR / 0 SKIP |
| Recall@5 / Recall@7 | 0.950 / 0.950 | 未命中 2 题：ch026、ch029 |
| MRR | 0.933 | |
| source_precision@5 | 0.215 | 小语料（14 源）下的保守值 |
| nDCG@5 | 0.934 | |
| hard_negative 泄漏题数 | 2 | ch029 命中断言为 v1 的 60 次（无版本过滤），ch037/038/039 命中 v1 属预期（qrels 含 v1 grade1） |

- 回归锚点（原 20 条计分题）：**Recall@5 = 1.000**，与 Day 1 基线持平。
- 契约机械校验：83 命中全部 span/hash/evidence 通过。
- 未命中分析：
  - **ch026**（paraphrase，HyDE「假设性文档嵌入」）命中 `06_vector_embedding.md` 而非 `14_query_rewriting.md`——改写型查询在 hybrid 上的固有语义距离，非迁移回归。
  - **ch029**（hard_negative，版本冲突）命中 v1 而非 v2——**当前 retriever 无版本过滤**，这是 M3 前置信号，不计入迁移回归。

## 4. HTTP 与 MCP

- 路由：`/health`（不依赖 LLM）、`/info`（index_id/模型/modes）、`POST /search`、`GET /evidence/{id}`、`POST /invoke`/`/stream`（llm_not_configured，Day 3 接线）、`GET /history`（410 退役）、`/mcp`（Streamable HTTP 单层端点）。
- 访问边界：`DEMO_API_TOKEN` 缺省时不启用；设置后 HTTP 路由与 MCP 整个 ASGI mount 都要求 Bearer。
- MCP：SDK 2.1.1 `MCPServer`；工具 `search_knowledge(query, k)`、`get_context(evidence_id, offset, limit)`；文本部分只给短摘要，完整 DTO 走 structuredContent；工具声明带 outputSchema（Pi 结构化消费依据）。
- 关键 2.x 适配（Day 1 验证复核）：
  - `streamable_http_path='/'` 挂载 → 单层 `/mcp` 端点
  - lifespan 内 `async with mcp.session_manager.run()`（否则握手失败）
  - 工具返回 `Annotated[CallToolResult, DTO]` 才能同时发布 outputSchema 与控制文本部分（裸 CallToolResult 不发布 schema）
  - embedded Qdrant 单进程只允许一个 client：snapshot 校验复用 `VectorDbManager.client`

## 5. 24 KiB DTO 预算

- `_apply_budget`：按检索顺序取可容纳的最长完整前缀，返回 `truncated`；单条命中超预算报 `response_too_large`（413），不伪装无命中。
- 挑战集 40 题均为完整命中（truncated=0），契约校验覆盖所有返回内容。
- `include_debug_artifact=true` 可取回裁剪前完整命中（eval 用）。

## 6. 测试与验证

- pytest：**64 passed**（新增 36：snapshot/证据存储/检索服务/API/MCP；原 33 全保留，5 个旧 API 测试重写为公共 DTO 契约）。
- 真实 Uvicorn：`/health` ready（generation=disabled），HTTP search → evidence 回查全通，history 410，invoke 503（llm_not_configured）。
- MCP 官方客户端：initialize → tools/list（2 工具）→ search_knowledge（structured_content + 短摘要文本）→ get_context 窗口。
- 协议等价：HTTP vs MCP 同查询逐字段一致 4/4（含版本冲突题）。

## 7. 变更集（本日未提交，与 Day 2 报告一起提交）

- 新增：`src/db/snapshot.py`、`src/db/evidence_store.py`、`src/core/retrieval_service.py`、`src/core/app_service.py`、`src/api/auth.py`、`src/api/routes.py`、`src/api/mcp_app.py`、`src/schema/dto.py`、`eval/run_challenge.py`、`eval/verify_protocol_equivalence.py`、`tests/{test_snapshot,test_retrieval_service,test_mcp}.py`
- 重写：`src/api/main.py`、`tests/conftest.py`、`tests/test_api.py`
- 修改：`src/ingest_corpus.py`（manifest 写入 + 复用 client）、`src/db/vector_db_manager.py`（client 属性）、`src/core/document_manager.py`（TXT 支持 + 空 PDF 拒绝）、`src/schema/__init__.py`、`eval/metrics.py`（challenge 计分）
- 冻结资产：`eval/reports/challenge-2026-09-07.{md,jsonl}`（hash `6000f9e8…`/`82d95847…`）

## 8. 遗留

- `POST /invoke`、`/stream` 为 llm_not_configured 占位（Day 3 接 rag/agentic 图）。
- 旧 SSE 事件 schema（AnswerTokenEvent 等）保留未删（Gradio/client 尚引用，Day 3 迁移）。
- 真实 Pi 端到端与 Bearer 实测属 Day 5；本日 Bearer 由 pytest 覆盖。
- ch026/ch029 未命中记录在案，不得通过改 qrels 或改题集掩盖；Day 4 审计时复核。
