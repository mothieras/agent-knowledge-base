# 条目服务工具说明（Agent 接入指南）

> 面向接入共享条目库的 Agent 客户端。契约细节见服务 HTTP/MCP 接口；本文回答
> 「什么时候用、怎么选参数、出错怎么办」。分类定义见 PRODUCT.md §3。

## 1. 这个库记什么：共享服务 vs 原生记忆

共享条目库是**跨会话、跨 Agent** 的持久 Memory/Knowledge 存储。判断标准是
「其他会话或其他 Agent 的后续任务会不会用到」：

| 记到共享服务 | 留在原生记忆 |
|---|---|
| 用户/项目的长期事实、偏好、约定、决策 | 本次任务的临时状态、工具原始输出、scratchpad |
| 可复用的方法、概念、排障经验 | 未成型的猜测（或记录时在正文明确标注置信与依据） |
| 多个 Agent 都会撞上的环境约束 | 不应共享的内容——**v1 所有条目对全部获准 Agent 可读**，无私有分区 |

服务端不做自动语义提炼、去重、遗忘；「值得记什么」完全由调用方决定。记错
了随时修订（见 §4），成本很低。

## 2. 类型选择：Memory / Knowledge

按**记录的主要用途**分，不按作者、长度、保存时间或可信度分：

- **memory**：回答「某个主体实际是什么状态、经历过什么、偏好什么、约定或决定
  了什么」。项目配置、用户偏好、一次具体的排障经历。
- **knowledge**：回答「某个概念是什么、为什么如此、在什么条件下怎么做」。
  方法、概念解释、带适用条件的操作知识；可以只关联一个项目。

边界样例（含模糊案例；服务只校验枚举，不代为分类）：

| 内容 | 建议类型 | 理由 |
|---|---|---|
| 「项目 A 选择 Python 3.12。」 | memory | 保存该项目的实际决策 |
| 「Python 生成器的用法与适用场景。」 | knowledge | 可复用的方法知识 |
| 「用户希望所有项目默认中文回复。」 | memory（全局） | 用户偏好；scope 用 global |
| 「项目 A 的发布流程、各步骤原因与排障。」 | knowledge（关联 A） | 操作知识；单项目关联不改变类型 |
| 「上次发布失败，因为评测进程占用索引目录。」 | memory | 一次经历的事实 |
| 「从那次排障总结出：重建索引前必须停服务。」 | knowledge | 从经历提炼的可复用方法 |
| 「embedded Qdrant 同目录只能一个进程持有。」 | knowledge | 环境约束的通用化表述；若只记「这台机器当前的状态」则是 memory |
| 「项目 B 用 Rust，因为延迟要求 <5ms。」 | **拆两条** | 决策事实（memory）与选型理由/适用条件（knowledge）分开记 |

- 混合内容由调用方自行拆分，服务不拆不拒。
- 类型选错 = 正常修订（`revise` 改 `type`），条目 ID 不变、不含类型信息。

## 3. 新增（save_entry / POST /entries）

必填：`type`、`body`（非空，≤64 KiB，唯一正文字段，无 title）、`scope`、`author`。
可选：`expires_at`、`source`、`idempotency_key`。

**scope 选择**（无默认值，全局须显式声明）：

- `{kind: global}`：与任何项目无关的通用内容（用户全局偏好、通用方法）。
- `{kind: projects, projects: [标签]}`：1–16 个项目标签；标签 trim+小写后须匹配
  `^[a-z0-9][a-z0-9_-]{0,63}$`，非法返回 `invalid_project`（不静默改写）。
  项目标签无需注册，「不存在的项目」不是错误。
- 全局条目自动参与任何项目搜索（含未来新项目）；项目条目通过关联被搜到。
  scope 是组织与检索维度，**不是权限边界**。
- 修改关联项目 = 修订 scope，受同一并发检查约束。

**source**：`{url?/note?}` 或 `{url?/note?, document?}` 或 `null`（明确未知，不伪造引用）。
用户口述、Agent 推断都记在 note 里说明来源性质。

**document**（文档派生条目）：`{doc_id, version, span_start?, span_end?}` 指向文档注册
表的版本化规范化文本；span 为全文字符区间，缺省 = 全文。写入时服务机械校验引用
存在且 span 合法（不存在/越界 → `invalid_source`），所以引用顺序是：先
`resolve_document(source)` 拿 doc_id/最新版本，需要片段时 `read_document` 读窗口
并用窗口 offset 算 span。文档更新后旧引用仍指向原修订；`list_document_versions`
可对照最新版本判断来源是否需要复核。

**expires_at**：RFC3339（带时区）。到期由服务在查询时判定：退出默认搜索，
按 ID/修订仍可回查；访问不续期；到期不是删除，条目仍可修订。

## 4. 修订与并发（revise_entry / POST /entries/{id}/revisions）

- 请求携带 `expected_revision`（你手里读到的修订号）。服务在单事务内
  「读当前 → 比较 → 应用 → 追加历史」，一切变更（含生命周期）revision+1，
  历史为全量快照，可回查任意时点。
- **冲突处理（服务不自动合并）**：`409 revision_conflict` 错误体携带
  `current`（对方已提交的最新条目）。正确做法：读 `current` → 语义整合
  （重写你的变更或放弃）→ 以 `current.revision` 作为新的 expected_revision
  重试。
- 变更字段语义：字段/键**出现** = 修改；`expires_at`/`source` 值为 **null** =
  清除；**缺省** = 不变。MCP 侧通过 `updates` 对象表达（键存在性即语义）。
- 生命周期同样受并发纪律约束：archive/unarchive/delete/restore 各自产生修订。

## 5. 生命周期（entry_lifecycle）

三态软转换：`archive`（退出默认搜索，正文历史全保留，仍可修订/恢复）、
`delete`（软删，仅 restore 与显式读取可及）、`unarchive`/`restore` 恢复。
非法转换返回 `invalid_transition`；无永久清除。

## 6. 何时检索、何时回查

- 任务开始、遇到相关决策点时：`search_entries` 按 scope 语义搜索——未指定
  范围=仅全局；指定项目=全局+关联项目；恒排除 archived/deleted/expired。
- 搜索结果里的 `matched` 是命中片段（长条目按分块返回最佳匹配片段与 span，
  短条目 = 全文；同一条目只返回一次），body 始终完整。
- 拿到条目要改：先 `get_entry` 读当前修订号再修订；搜索结果里的 revision 就是
  命中时的修订号。
- 看历史演变、对比「当时怎么记的」：`list_entry_revisions` + 按修订读（HTTP）。
  回查不改变条目，也不受默认过滤影响。
- 条目带 `source.document` 要核原文：`read_document(doc_id, version)` 取回该版本
  全文窗口，窗口 offset/total_length 与 content_hash 可复算。

### 从文档提取条目（D6 流程）

1. `search_knowledge` 检索语料拿证据（source/span）
2. `resolve_document(source)` → doc_id + 最新版本
3. 需要片段时 `read_document(doc_id, version, offset, limit)` 读原文窗口，用窗口
   offset + 片段位置算出全文字符 span
4. `save_entry` 携带 `source.document = {doc_id, version, span_start, span_end}`

## 7. 幂等（idempotency_key）

网络超时/重试安全网：同键 + 同请求内容 → 重放原始成功结果（不重复修改）；
同键 + 不同内容 → `409 idempotency_conflict`。键全局唯一（跨条目、跨作者），
记录持久保留。**建议所有写操作都带键**。

## 8. author 与身份

`author` 必填自报字符串，约定格式「客户端/版本」（如 `pi/0.85.1`）；记入创建
与每次修订（modifier）。凭据是部署级单 Bearer token，与 author **不绑定**：
author 是记录用途，**不是强身份保证**，不要据此做安全决策。

## 9. 接口对照

| 操作 | HTTP | MCP 工具 |
|---|---|---|
| 新增 | `POST /entries` | `save_entry` |
| 当前状态 | `GET /entries/{id}` | `get_entry` |
| 修订 | `POST /entries/{id}/revisions` | `revise_entry` |
| 生命周期 | `POST /entries/{id}/lifecycle` | `entry_lifecycle` |
| 历史列表 | `GET /entries/{id}/revisions` | `list_entry_revisions` |
| 指定修订快照 | `GET /entries/{id}/revisions/{n}` | （HTTP） |
| 搜索 | `POST /entries/search` | `search_entries` |
| 来源解析 | `GET /documents/resolve?source=` | `resolve_document` |
| 文档版本列表 | `GET /documents/{doc_id}/versions` | `list_document_versions` |
| 文档窗口回查 | `GET /documents/{doc_id}/versions/{version}` | `read_document` |

错误统一为 `{code, message}`（冲突另含 `current`）；MCP 侧错误载荷为可解析
JSON（文本通道）。错误码：`invalid_request` 400 · `invalid_project` 400 ·
`invalid_source` 400 · `not_found` 404 · `revision_conflict` 409 ·
`idempotency_conflict` 409 · `entry_deleted` 409 · `invalid_transition` 409 ·
`busy` 429。
