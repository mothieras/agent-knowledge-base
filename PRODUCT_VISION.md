# 产品构想 vs 代码现状对照（原型讨论底稿）

> **历史讨论，已被新的需求决策替代。** 当前定位与计划见 [PLAN.md](PLAN.md)；原 PRD/DESIGN/ROADMAP 已归档至 [docs/archive/](docs/archive/)。下文原样保留供追溯，不再作为实现指令；其中企业级、内置 Web/Chrome 工具与多轮恢复不属于新产品范围。

> 状态：讨论底稿，非路线图承诺。基于 `main` @ `100bed05`（2026-09-06）快照核对。
> 本文不改变根 README / PLAN 的「已完成 vs 计划」表述；仅当构想落地后才更新 README。

## 产品构想（一句话）

多图 LangGraph + agentic RAG 的企业级知识库：**多类型输入**（Markdown / PDF / Web Search / Web Fetch / Chrome MCP）→ **统一归一化**（中间格式）→ embedding → 向量库；**使用时**检索增强生成。

## 现状对照

| 构想模块 | 现状 | 代码证据 | 差距 |
|---|---|---|---|
| 多图 LangGraph agent | ✅ 已实现 | 主图 5 节点 + 子图 6 节点，Send 并行扇出、上下文压缩、HITL 澄清 interrupt（`rag_agent/graph.py`） | 无 |
| 统一中间格式 | 🔶 雏形，无独立抽象 | 所有输入归一为 Markdown 落盘 `markdown_docs/`（`document_manager.py` 只收 `.md/.pdf`，PDF 经 `utils.pdf_to_markdown`）；父子分块 `document_chunker.py` | 无「loader 注册表 + 独立归一化层」概念；html/docx/web 无 loader |
| embedding + 向量库 | ✅ 已实现 | Qdrant 本地，dense（Qwen3-Embedding-0.6B）+ sparse（BM25）HYBRID（`db/vector_db_manager.py`） | 无 |
| 检索证据契约 | ✅ 已实现（M1 收尾） | `RetrievalHit`：source / version / effective_date / expired_date / priority / chunk_id / span / retrieval_channel（`db/retrieval.py`，M1 stage 1–3） | 无 |
| 企业级语料治理 | ✅ 已有（超出原型预期） | manifest 逐文档 SHA-256 / license / version / expiry / priority；受治理 fixture（`data/manifest.json`、`data/fixtures/`） | 无 |
| 版本过滤 | 🔶 元数据全链路，无过滤逻辑 | `RetrievalHit` 携带 version/expiry，但 `QdrantRetriever.search` 不过滤 expired | 归 M3 |
| Web Search / Web Fetch / Chrome MCP | ❌ 零痕迹 | `tools.py` 仅 `search_child_chunks` + `retrieve_parent_chunks` 两个检索工具；requirements 无 web/mcp 依赖 | **本构想新增项**（见下） |
| 企业级另一半（auth / 多租户 / 审计 / PostgresSaver） | ❌ 未实现 | Docker/Compose 未提供；CORS `*`；InMemorySaver | 归 M6 |

## 已定决策（2026-09-06 讨论）

- **Web Search / Web Fetch / Chrome MCP 定位 = 检索时的 agent 工具**，与知识库检索并存；**不是**入库语料来源。语料治理链（版本/来源/许可证）不为其服务。
- 推论 → 三个设计问题（列在下方待讨论）。

## 里程碑归属建议

- M0–M4（评测门槛 → 中文检索 → 可信回答）与 web 工具**正交**，不必互相阻塞；web 工具在 M1 类型化契约之上做最顺。
- web 工具若落 `RetrievalHit` 契约，天然获得来源卡片 / citation / 评测复用——但 `retrieval_channel` 语义需扩展，且 M3 消融实验的检索基线必须隔离 web 通道。

## 待讨论问题

1. **通道语义**：web 命中是否进同一 `RetrievalHit` 契约（扩展 `retrieval_channel` ∈ {knowledge_base, web, chrome}）？倾向扩展而非旁路——否则 M1 刚统一的证据契约又裂成两套。
2. **防污染与抢答**：agent 有 web 工具后，同一问题既查库又上网——谁来裁决？是否需要显式路由（如 rewrite_query 阶段判别「库内题 vs 实时题」）？
3. **归一化层是否提为显式抽象**：把「多源 → 中间格式（md）→ chunk → embedding」做成 loader 注册表（md/pdf 现在有，html/web 归档后加）？还是保持「能转 md 就能入库」的现状哲学？
4. **Chrome MCP 的确切语义**：agent「能实时浏览网页」（与 Web Fetch 重叠）还是「读取用户当前浏览器会话」（查登录态内容、与用户同屏）？二者架构位置不同。
5. **实时结果是否需要落地缓存**：每次查询现场抓取 vs 抓取结果回流语料治理链（会与「非入库来源」决策冲突，需定缓存策略）。
6. **优先级排序**：web 工具 vs M2 挑战集 vs 企业级（auth/租户），哪个先做？
