# ARCHITECTURE — 分层与领域词汇

本文件是本项目的架构与领域词汇真相源,随架构决策演进;新增或锐化概念时在此更新。架构讨论以这些词命名模块与缝隙,不另造 "component / service / boundary"。

## 分层

上层只依赖下层的稳定接口,依赖方向保持单向:

- **L0** `db/` + 检索层:Qdrant dense+sparse hybrid 检索、parent store、快照;SQLite 条目库(FTS5 + jieba 空格预分词)与文档注册表(版本化规范化文本)也在这层。版本过滤、检索消融等能力在检索层内演进
- **L1** `rag_agent/`:普通 RAG 单图与双图的编排、decision 与证据约束
- **L2** `core/`:bootstrap、资源/运行生命周期、检索/问答/条目/文档服务的公开应用接口
- **L3** `api/`:HTTP(FastAPI/SSE)与 MCP 的稳定薄前——只调用 L2 公共接口,不 import `db/` / `rag_agent/` 内部
- **L4** `client/` + `ui/`:HTTP/SSE 客户端与 Gradio 薄映射;外部 MCP 客户端同样只消费协议

## 检索

- **RetrievalHit**:一次检索命中的类型化结果。字段:`source`、`parent_id`、`content`、`score`,manifest 元数据 `version`、`effective_date`、`expired_date`、`priority`,以及 chunk 身份 `chunk_id`、`span_start`/`span_end`(child 在父文本中的精确字符切片;`get_parent` 返回整父块,span 覆盖全文)与 `retrieval_channel`。span 用扁平 int 而非嵌套对象——msgpack 往返安全,API 边缘再组装 `{start, end}`。取代此前跨六模块的六种字符串编码:`CHILD_CHUNK_SEPARATOR` 拼接、`"File Name:"` 解析、`parent::` / `search::` 协议、eval 的 `(source, content)` 元组。命中现在作为类型化对象流经 `AgentState → events → eval`,不再被反复解析。

- **Retriever**:L0 的检索接口(`Protocol`)。`search(query) -> list[RetrievalHit]`(child 命中)与 `get_parent(parent_id) -> RetrievalHit`(父文档)。dense + sparse hybrid 检索与 parent store 取回都在这道缝隙之后演进。两个适配器坐实缝隙:`QdrantRetriever`(prod)、`InMemoryRetriever`(test,忠实建模父子两跳)。`tools.py` 退化为薄壳,只调 `Retriever`;`record_retrieval` 旁路删除,eval 改吃 `RetrievalHit`。`retrieval_channel` 当前是诚实常量("hybrid"/"parent_store"):单次融合调用拿不到 per-channel 归因,真归因在检索消融时落地。

- **受治理 fixture**:`data/fixtures/` 下项目自有的虚构政策文档(两对 active/expired 版本),独立 manifest 治理,不参与第三方语料的 SHA 同步。入库进同一 collection,使版本冲突评测在混合语料上进行;`expected_hits` 注记是检索挑战集 qrels 素材。

## 产品术语（已实现能力）

范围与完成状态以根 [README](../README.md) 为准。

### 检索优先演示版

- **直接检索**：无需生成模型的 search/原文回查；生成由调用者决定。不是“单图问答”的别名。
- **普通 RAG / `rag`**：固定检索→组织上下文→生成的 LangGraph 单图，不做 Agent 工具循环。
- **Agentic RAG / `agentic`**：本项目内部主图＋检索子图；一次请求内可拆解和补查，不代表跨请求会话。
- **索引快照 / `index_id`**：绑定语料、规范化/分块、模型和产物校验的固定构建；在线只读。与来源版本、编辑修订号不是同一概念。
- **证据标识 / `evidence_id`**：绑定快照与准确原文片段的公开回查标识；旧快照缺失时不能解析到同名新内容。
- **单次 decision**：`answered / clarification_required / refused`，都结束本次请求；澄清不是服务器暂停等待同 thread 恢复。
- **检索优先演示版**：受控只读资料库＋检索/问答/接入评测；知识库本体中，协作写入与修订历史已由共享条目服务交付，用户身份/公私分区与文档历史仍顺延。

### 共享条目服务（阶段 2 已交付）

- **共享条目 / `entry`**：Memory/Knowledge 类型的持久记录。稳定 ID `entry_`+ULID（类型不编码进 ID）；`body` 唯一正文（≤64 KiB）；`scope`、`author`、`revision`、`status`、`expires_at`、`source`。写入、修订、搜索、生命周期经 HTTP/MCP 公开工具操作。
- **项目标签 / scope**：`{kind: global}` 或 `{kind: projects, projects:[1..16 个规范化标签]}`。组织与检索维度，不是权限边界；标签无需注册。
- **修订 / revision**：一切变更（含生命周期）revision+1，历史为全量快照；变更携带 `expected_revision`，单事务内比较并应用，不匹配 409 附当前条目。
- **生命周期**：`active / archived / deleted` 三态软转换（archive/unarchive/delete/restore）；正文与历史全保留，无永久清除；退出默认搜索，显式回查可见。
- **到期 / `expires_at`**：查询时判定（服务端 UTC，无后台任务）；到期退出默认搜索，按 ID/修订回查保留到期信息；访问不续期。
- **幂等键 / `idempotency_key`**：全局唯一、持久保留；同键同内容重放原结果，同键异内容 409。
- **无模型全文搜索**：SQLite FTS5 + 空格预分词（jieba）+ bm25；独立于 Qdrant/embedding/生成模型，写入与索引同事务提交。长条目（>4000 字符）按分块检索：chunks + FTS，命中返回最佳匹配片段与 span，条目身份不变。
- **author**：必填自报字符串（约定「客户端/版本」），与部署级单 Bearer 凭据不绑定，非强身份。

### 文档派生条目与来源（D6，阶段 2 T7 已交付）

- **文档注册表 / `doc_store`**：独立 SQLite（`DOCS_DB_PATH`）。离线 ingest 注册版本化规范化文本：`doc_id`（manifest source 字符串）+ 单调递增 `version`（内容 hash 变化才 +1，同内容幂等跳过）+ 全文 + 原文件/快照归属。旧版本永久保留——「文档更新后旧引用仍指向原修订」由版本保留直接满足。
- **来源引用 / `source.document`**：`{doc_id, version, span_start?, span_end?}`，span 为规范化全文字符区间（缺省 = 全文）。写入/修订时机械校验引用存在与 span 合法（400 `invalid_source`）；不校验正文主张是否都在 span 内（语义复核属调用方）。
- **来源回查**：`resolve_document`（检索命中 source → doc_id/最新版本）、`list_document_versions`、`read_document`（有界窗口，offset/limit 语义同 EvidenceWindow，content_hash 为全文 sha256）。
