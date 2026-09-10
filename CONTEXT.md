# CONTEXT — 领域词汇表

本文件是本项目的领域词汇真相源之一,随架构决策演进;新增或锐化概念时在此更新。
架构评审(`/improve-codebase-architecture`)以这些词命名缝隙与模块,不另造 "component / service / boundary"。

## 检索

- **RetrievalHit**:一次检索命中的类型化结果。字段:`source`、`parent_id`、`content`、`score`,manifest 元数据 `version`、`effective_date`、`expired_date`、`priority`,以及 chunk 身份 `chunk_id`、`span_start`/`span_end`(child 在父文本中的精确字符切片;`get_parent` 返回整父块,span 覆盖全文)与 `retrieval_channel`。span 用扁平 int 而非嵌套对象——msgpack 往返安全,API 边缘再组装 `{start, end}`。取代此前跨六模块的六种字符串编码:`CHILD_CHUNK_SEPARATOR` 拼接、`"File Name:"` 解析、`parent::` / `search::` 协议、eval 的 `(source, content)` 元组。命中现在作为类型化对象流经 `AgentState → events → eval`,不再被反复解析。

- **Retriever**:L0 的检索接口(`Protocol`)。`search(query) -> list[RetrievalHit]`(child 命中)与 `get_parent(parent_id) -> RetrievalHit`(父文档)。dense + sparse hybrid 检索与 parent store 取回都在这道缝隙之后演进。两个适配器坐实缝隙:`QdrantRetriever`(prod)、`InMemoryRetriever`(test,忠实建模父子两跳)。`tools.py` 退化为薄壳,只调 `Retriever`;`record_retrieval` 旁路删除,eval 改吃 `RetrievalHit`。`retrieval_channel` 当前是诚实常量("hybrid"/"parent_store"):单次融合调用拿不到 per-channel 归因,真归因在 M3 消融时落地。

- **受治理 fixture**:`data/fixtures/` 下项目自有的虚构政策文档(两对 active/expired 版本),独立 manifest 治理,不参与第三方语料的 SHA 同步。入库进同一 collection,使版本冲突评测在混合语料上进行;`expected_hits` 注记是 M2 qrels 素材。

## 产品术语（检索优先演示版，已实现）

范围与后续见 [PLAN](PLAN.md)，完成状态以根 [README](README.md) 为准。

- **直接检索**：无需生成模型的 search/原文回查；生成由调用者决定。不是“单图问答”的别名。
- **普通 RAG / `rag`**：固定检索→组织上下文→生成的 LangGraph 单图，不做 Agent 工具循环。
- **Agentic RAG / `agentic`**：本项目内部主图＋检索子图；一次请求内可拆解和补查，不代表跨请求会话。
- **索引快照 / `index_id`**：绑定语料、规范化/分块、模型和产物校验的固定构建；在线只读。与来源版本、编辑修订号不是同一概念。
- **证据标识 / `evidence_id`**：绑定快照与准确原文片段的公开回查标识；旧快照缺失时不能解析到同名新内容。
- **单次 decision**：`answered / clarification_required / refused`，都结束本次请求；澄清不是服务器暂停等待同 thread 恢复。
- **检索优先演示版**：受控只读资料库＋检索/问答/接入评测；知识库本体（身份、公私分区、协作写入、文档历史）明确顺延。

## 分阶段落地（M1 历史记录）

- **Stage 1**:`RetrievalHit` + `Retriever` 协议 + `InMemoryRetriever` + 路径测试(search→compress→aggregate 全链路) + chunk 缝隙修(manifest 元数据进 chunk)。先把测试面立起来。
- **Stage 2**:`QdrantRetriever` + 重接 `tools / nodes / events / eval` 吃 `RetrievalHit`,删除 `CHILD_CHUNK_SEPARATOR` 拼接、`"File Name:"` 解析、`record_retrieval` 旁路。
- **Stage 3**:证据契约收尾——`chunk_id`(child 枚举 `{parent_id}_c{j}`,provenance 主键;Qdrant point id 仍是 UUID 存储细节)、`span`(`add_start_index` 用 find 定位,`parent.content[start:end] == child.content` 精确成立)、`retrieval_channel`;受治理 fixture 落地;真实本地索引集成测试(`skipif` 无索引,验证 metadata 全链路传播与 span 精确反查);eval `retrieval_hits` 升级为含 version/chunk_id 的结构化 artifact。
