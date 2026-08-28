# CONTEXT — 领域词汇表

本文件是本项目的领域词汇真相源之一,随架构决策演进;新增或锐化概念时在此更新。
架构评审(`/improve-codebase-architecture`)以这些词命名缝隙与模块,不另造 "component / service / boundary"。

## 检索

- **RetrievalHit**:一次检索命中的类型化结果。字段:`source`、`parent_id`、`content`、`score`,以及 manifest 元数据 `version`、`effective_date`、`expired_date`、`priority`。取代此前跨六模块的六种字符串编码:`CHILD_CHUNK_SEPARATOR` 拼接、`"File Name:"` 解析、`parent::` / `search::` 协议、eval 的 `(source, content)` 元组。命中现在作为类型化对象流经 `AgentState → events → eval`,不再被反复解析。

- **Retriever**:L0 的检索接口(`Protocol`)。`search(query) -> list[RetrievalHit]`(child 命中)与 `get_parent(parent_id) -> RetrievalHit`(父文档)。dense + sparse hybrid 检索与 parent store 取回都在这道缝隙之后演进。两个适配器坐实缝隙:`QdrantRetriever`(prod)、`InMemoryRetriever`(test,忠实建模父子两跳)。`tools.py` 退化为薄壳,只调 `Retriever`;`record_retrieval` 旁路删除,eval 改吃 `RetrievalHit`。

## 分阶段落地

- **Stage 1**:`RetrievalHit` + `Retriever` 协议 + `InMemoryRetriever` + 路径测试(search→compress→aggregate 全链路) + chunk 缝隙修(manifest 元数据进 chunk)。先把测试面立起来。
- **Stage 2**:`QdrantRetriever` + 重接 `tools / nodes / events / eval` 吃 `RetrievalHit`,删除 `CHILD_CHUNK_SEPARATOR` 拼接、`"File Name:"` 解析、`record_retrieval` 旁路。
