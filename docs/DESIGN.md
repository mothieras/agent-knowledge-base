# 技术设计：五天检索优先演示版

> 状态：根据已确认 [PRD](PRD.md) 制定的实施设计，代码尚未按本文改造。接口名、限额和模块布局属于工程默认方案，不是现有能力声明。
> 本轮只实现 D 系列需求。后续 F 系列只保留必要的契约扩展位置，不提前建设共享文档平台。

## 1. 设计决策

| 决策 | 本轮选择 | 原因 |
|---|---|---|
| 服务入口 | 同一 FastAPI/ASGI 应用、同一端口，HTTP 与 MCP 并列 | 不新建微服务，不通过 loopback HTTP 调用自己 |
| 运行能力 | 检索运行时必选，生成运行时可选 | 不配置生成模型也必须 ready |
| 图模式 | `rag` 固定单图；`agentic` 主图＋子图 | 将普通 RAG 与 Agent 的收益、成本分开测量 |
| 数据管理 | 离线受控导入，在线只读，固定索引快照 | 明确延期协作、异步任务、热更新与文档历史生命周期 |
| 存储 | 复用 embedded Qdrant＋parent store，单进程独占 | 当前规模先验证，不为三个并发请求引入服务集群 |
| 访问控制 | 本机绑定＋部署级 Bearer，所有持有者相同权限 | 最小访问边界，不冒充每 Agent 私有区隔离 |
| 会话 | 每次独立请求，无 `/history` 产品能力 | 避免状态、工具轨迹和上下文跨请求串线 |
| 证据 | 固定快照＋结构化命中＋精确文本切片 | 复用 M1，不从展示字符串恢复来源 |
| 模型输出 | JSON mode＋手动解析/校验 | 延续 DeepSeek 兼容方式，不依赖 `json_schema` response format |
| 实验 | 不默认替换当前 hybrid | 先冻结评测，再讨论算法改善 |

## 2. 分层与应用服务

```text
L4  Pi + pi-mcp-adapter        HTTP client / Gradio
              │                         │
              └────────────┬────────────┘
                           ▼
L3                 FastAPI / ASGI
                 HTTP adapter   MCP adapter
                           │
                           ▼
L2             RAGSystem / 应用服务接口
               ├─ search / read_evidence
               ├─ invoke / stream（可选生成）
               └─ capabilities / 生命周期
                    │                │
                    │                ▼
L1                 │         rag 单图 / agentic 双图
                    │                │
                    └───────┬────────┘
                            ▼
L0             Retriever / EvidenceStore
               Qdrant + parent store + 固定快照

离线 Ingest：受控文本/文件 → 解析 → 分块 → embedding → 构建快照
Eval：直接检索、模式比较、协议/原文回查、资源与成本
```

- L0 继续拥有召回、融合、数据布局、原文回查和未来版本过滤。
- L1 负责固定 RAG 与 Agent 行为、证据约束和最终结果，不负责 HTTP/MCP。
- L2 装配依赖、管理生命周期、提供稳定调用接口并转换公共 DTO。
- L3 只做鉴权、参数校验、协议映射与错误映射，不 import `db/` 或 `rag_agent/` 内部。
- L4 只消费公开协议；Gradio 不实例化 RAGSystem。

这是对当前“L3 使用 `agent_graph + get_config`”边界的有意扩展：目标 L3 使用 L2 的公开应用方法，不再自行读取图内部状态。项目指令已同步此迁移方向，代码和测试按实施阶段迁移；文档变更本身不表示迁移已完成。

当前 `api/events.py` 仍直接依赖 `RetrievalHit` 并识别内部节点；应把证据转换放到 L2，事件按应用事件类型分类，节点名仅作可选装饰，不为每加一个节点再补一组协议规则。

建议改动位置：

| 路径 | 职责 |
|---|---|
| `src/core/rag_system.py` | composition root，分开装配检索与生成能力 |
| `src/core/retrieval_service.py`（拟新增） | search/read_evidence、快照绑定、公共结果转换 |
| `src/core/answer_service.py`（拟新增） | 单次运行、mode 选择、预算、最终结果及事件 |
| `src/schema/` | HTTP/MCP 共用的请求、证据、答案、错误 DTO |
| `src/api/` | HTTP 路由、共同访问边界、lifespan |
| `src/mcp_server/`（拟新增） | MCP 工具与 ASGI app；不使用 `mcp/` 包名遮蔽依赖 |
| `src/rag_agent/` | 固定单图、现有双图的单次化与引用校验 |
| `src/db/` | 复用 Retriever，封装证据存储和快照检查 |

不要为了上述名称创建空壳框架；每个新增接口必须有直接调用者和契约测试。

## 3. 启动、配置与关闭

### 3.1 检索先于生成

1. 新入口自己加载 `.env`，处理既有 HF/代理约定，不依赖父 shell 已导出密钥。
2. 校验索引快照及 parent store 一致性；加载一份本地 dense/sparse 模型和一个 Qdrant client。
3. 构建 Retriever 与 evidence reader，检索能力就绪。
4. 只有显式存在有效生成配置时，才构建 ChatOpenAI 与两种问答图。两个图共用模型配置，不分别要求 key。
5. FastAPI lifespan 同时管理 MCP session manager、检索资源和可选问答资源。
6. 关闭时取消/等待有界运行、清理临时图状态，关闭 MCP 和存储，释放目录锁。

缺少生成模型配置是正常的 retrieval-only 模式，不是启动错误。缺索引或索引/原文不匹配则不 ready，不能静默建一个空库冒充成功。

`/health` 的 readiness 取决于检索资源。`/info` 返回快照、能力和可选模型标识，不返回凭据、内部绝对路径或密钥相关配置。

模型上游临时失败是问答错误，不影响直接检索；显式配置但配置格式错误需清楚报错，不应悄悄切换模式掩盖配置问题。

### 3.2 本轮限额默认值

这些是待在 Day 1 配置与测试中固定的工程上限，不是延迟 SLA：

- 查询长度最多 2,000 个 Unicode 字符；`k` 默认沿用当前 7，允许 1–20。
- 最多 3 个在途应用查询；超出容量明确返回 busy，不创建无界等待队列。
- 直接检索请求总超时 30 秒，问答总超时 120 秒；模型网络调用超时不能超过剩余请求预算。
- 双图最多 3 个子问题、全请求最多 8 次工具调用、最多 10 次 Agent 迭代；父子图共享预算，不能每个分支各拿一份完整上限。
- 普通 RAG 只做一次检索；不为比较结果暗中增加改写、Agent 或生成型压缩。
- 网络瞬时失败最多重试 1 次；引用修复最多 1 次，均计入同一总预算。固定模型输出 token 上限并记录到实验配置。
- L2 公共响应 DTO 的紧凑 UTF-8 JSON 上限为 24 KiB；HTTP/MCP 使用同一预算和结果。搜索按完整命中记录裁剪并报告状态，原文按有界窗口返回。MCP 封装和简短文本摘要后以 32 KiB 为目标上限，不重复嵌入一份完整正文；问答结果超限走明确错误/有界修复，不能截成半个答案。

应用取消须传播到工具/模型；线程中的 embedding 调用不保证能立即杀死，但必须保留占用计数到实际结束，不因外层超时释放槽位后无限叠加后台任务。

## 4. 离线资料与索引快照

### 4.1 导入范围

部署者使用离线入口提交 manifest 管理的本地文本/元数据、MD、TXT 和文本 PDF。沿用现有语料路径、许可与 checksum 校验，补齐 TXT 和受控文本记录适配即可；不建设通用 loader 插件市场。

统一解析为规范化文本＋来源元数据，再复用父子分块。PDF 如未提取出可用文本，明确提示扫描件/OCR 不受支持。不得把“接受 PDF 扩展名”当作解析成功。

文件必须来自显式允许的本地目录/清单；不自动抓取 URL、执行内容或把模型生成摘要当作原始语料。文本记录类型和用户元数据不能覆盖系统生成的 ID、hash、span 或快照字段。

### 4.2 快照不是文档历史系统

拟引入构建 manifest，至少包含：

```text
index_id
corpus_manifest_hash + source content hashes
normalizer/chunker configuration and version
embedding/sparse model identifiers and revisions
parent/child content hashes and counts
retrieval configuration
build status
```

`index_id` 采用 `sha256:<64位小写hex>`，摘要输入是构建 manifest 的 canonical JSON v1：字段键排序、`separators=(',', ':')`、`ensure_ascii=False`、`allow_nan=False`，再编码为 UTF-8；集合型 source/parent/chunk 清单按稳定逻辑 ID 排序，其他有序数组保持语义顺序。参与摘要的字段包含构建输入、模型/处理版本和逻辑产物的 SHA-256/数量；排除自身 `index_id`、时间、物理路径、运行状态及 Qdrant 随机 point UUID。缺必需绑定字段或出现 NaN 时构建失败。

相同输入和逻辑产物得到相同标识；产物变更必须得到不同标识。该规范及测试在 Day 1 固定，快照在服务运行期间不可变。

原始输入或许可允许保留的副本、规范化 parent 文本、Qdrant 数据和构建 manifest 成套保存。跨平台重建若无法得到同一内容 hash，就记录不同快照，不能宣称引用可互换。

离线构建失败不得留下可被 readiness 接受的半成品。新构建在独立目录完成并校验后，于服务停止状态选择它；这不等于支持在线原子发布。退出进程、卷持久化和再次启动后，仍须校验完整快照。

本次不承诺保存全部旧快照。旧快照不存在时，回查返回明确失效；即使 `parent_id`/`chunk_id` 同名，也不能去新快照找一段文本顶替。

### 4.3 Embedded Qdrant 运行约束

- 本次一个 Uvicorn worker，一个进程持有一份 Qdrant client；HTTP 和 MCP 共用，不各自初始化模型和数据库。
- 三个并发请求可以排队进入受控检索临界区，不宣称等价于三个物理检索同时执行。必要时串行化共享 embedding/client 的访问，并实测其延迟。
- API、独立 ingest、eval、第二个容器不能同时打开同一 embedded 数据目录；CLI 要在服务停止时运行，或使用单独完整快照副本。
- Docker 原生进程与宿主 Python 服务不能同时占用同一个索引目录。
- 当后续引入独立入库 worker、多进程或在线写入时再切换到 Qdrant server；不能把该未来形态当作本轮硬依赖。

## 5. 公共证据契约

保留内部 `RetrievalHit`，在 L2 映射为公开 DTO；HTTP/MCP 不直接引用 L0 类型。

每次搜索响应包含 `request_id`、`index_id`、`requested_k`、`returned_k`、`truncated` 和 `hits`。每个命中至少包含：

| 字段 | 语义 |
|---|---|
| `evidence_id` | 服务端生成的稳定证据引用，绑定快照、parent/chunk 和准确切片；不是任意文件路径 |
| `source` / `source_uri` | 受治理来源标识和可选来源 URL，不暴露宿主绝对路径 |
| `version` | 现有来源版本元数据；未知则 null，不冒充编辑修订号 |
| `chunk_id` / `parent_id` | 原始结构化命中的身份 |
| `content` / `content_hash` | 返回的完整证据片段；hash 为 `sha256:` 加该字符串 UTF-8 字节的 SHA-256 小写 hex |
| `span.start` / `span.end` | 在规范化 parent 文本中的 Python 字符索引，左闭右开 |
| `score` / `retrieval_channel` | 原始检索分数和真实可得的通道归因；分数不是置信概率 |
| 有效期/priority 元数据 | 如实传播已知值，不凭字段存在宣称过滤已执行 |

基本不变量：

```text
parent_text[span.start:span.end] == hit.content
"sha256:" + hashlib.sha256(hit.content.encode("utf-8")).hexdigest() == hit.content_hash
resolved_evidence.index_id == response.index_id
```

文本在导入阶段规范化后即固定；计算摘要不再 trim、做 Unicode 归一化或改变换行，不能使用进程随机化的 Python `hash()`。

L2 在共同的 24 KiB DTO 预算内按检索顺序取可容纳的最长完整前缀；HTTP/MCP 都返回这个已裁剪结果，并包含同样的 `returned_k`/`truncated`。若单条命中已超限则明确报 response-too-large，不伪装成无命中。检索指标与原始 trace 使用裁剪前 artifact，响应裁剪另行报告。不得在 transport 再各自删条目，也不偷偷截短 `content` 却保留原 span/hash。`read_evidence` 提供有界父文本窗口，窗口携带自身 offset、总长度和 hash，不改变原命中的身份。

按 ID 回查必须先解析并验证快照、ID、范围；不能把 ID 当路径拼接。任意路径、越界、不存在的 parent、旧快照引用分别走明确错误路径。

此契约只证明“哪段文本被检索和引用”，不证明来源事实正确。语义支持性另由模型约束、人工 badcase 审计和生成评测验证。

## 6. HTTP 和 MCP

### 6.1 目标 HTTP 路由

下表是实施目标；根 README 的当前路由表在代码迁移前保持现状描述。

| 方法与路径 | 行为 |
|---|---|
| `GET /health` | 简单检索 readiness，不依赖 LLM，不暴露语料详情 |
| `GET /info` | 快照、模型标识或 null、可用 modes、接口版本 |
| `POST /search` | `{query, k}` → 结构化命中，无生成模型调用 |
| `GET /evidence/{evidence_id}` | 校验 ID 后返回有界原文窗口，可分页，不接受任意路径 |
| `POST /invoke` | `{message, mode}` → 单次答案，`mode` 为 `rag`/`agentic`，默认 `rag` |
| `POST /stream` | 同一问答契约的 SSE 进度/结果，支持相同 mode |
| `/mcp` | 挂载标准 MCP Streamable HTTP ASGI endpoint，不是 `/stream` 的别名 |

目标问答结果：

```text
request_id · index_id · mode
 decision = answered | clarification_required | refused
 answer · clarification_question? · limitations[]
 citations[] = citation_id + evidence_id + source + chunk_id + span + quote
 usage = input/output tokens + estimated cost + model calls
```

候选命中与真正引用分开。`citations` 只包含最终答案实际使用的证据；问答默认不返回完整 contexts，可用显式 debug/eval 模式取回诊断 artifact。

`quote` 指向规范化 parent 的准确切片，引用 span 可为命中 span 的子区间，但必须包含在本次实际获取的证据窗口内。

错误至少区分：invalid request、not authenticated、evidence not found、snapshot unavailable、retrieval failed、LLM not configured、upstream failed、budget exceeded、busy。空命中、refused、clarification 是成功的领域结果，不与基础设施错误混用。

### 6.2 MCP 工具

| 工具 | 对应应用服务 | 生成模型 |
|---|---|---|
| `search_knowledge(query, k)` | search | 不需要 |
| `get_context(evidence_id, offset, limit)` | read_evidence | 不需要 |
| `ask_knowledge(message, mode)` | invoke | 可选，仅生成能力启用时发布 |

外部 Agent 默认组合前两个工具，用自己的模型回答。高级调用者才通过 `ask_knowledge` 整体委托，不要求每个问题都经过两层 Agent。

采用官方 Python MCP SDK 的 Streamable HTTP ASGI 应用，FastAPI 宿主 lifespan 显式运行 session manager。固定与 Pi 实测兼容的 SDK/协议版本，不跟随 draft，不要求 SSE fallback、stdio 或 Sampling 全套能力。

工具返回公共 DTO 对应的 `structuredContent`，文本部分只提供短摘要、状态和证据 ID，不再复制完整正文。Pi 必须实测能消费结构化结果；如需要兼容文本序列化，应在共同响应预算内适配并重新验证，不能依赖 adapter 截断。不得将 SDK 协议错误伪装为成功的证据文本。陈旧客户端缓存若仍调用未启用的问答工具，也要返回可识别错误。

MCP session/连接 ID 不是业务身份，也不是聊天 thread。一次 MCP 连接可以执行多个彼此独立的查询；原始业务结果应与 HTTP 一致。

### 6.3 访问边界

原生服务绑定 `127.0.0.1`；容器内部可监听容器网卡，但宿主发布端口仅绑定回环地址。健康检查可公开，其余 HTTP 与 MCP 数据入口由同一部署级 Bearer 校验保护。

MCP 挂载可能绕开 FastAPI 路由依赖，鉴权必须覆盖整个 ASGI mount，包括 transport 的各种 HTTP 方法，不能只保护普通路由。校验 Host/Origin，使用明确 allowlist，不保留无边界的 CORS `*`。

部署凭据通过环境变量/受控配置提供，不是生成模型 API key。Gradio 在服务端发送凭据，不把它注入浏览器；Pi 使用环境变量引用。所有持有者可读全部演示资料，绝不将此描述为 F-01/F-02。

## 7. 图与结果语义

### 7.1 普通 RAG 单图

```text
START → retrieve → assemble_context → generate → validate_result → END
```

零命中时走确定性无证据分支；有命中不保证足够回答，生成节点必须根据证据输出 decision。生成节点也可返回澄清问题，但不会创建暂停点。

不做 LLM 改写或工具循环；整理上下文使用可复现的格式化/截断规则。引用校验失败时最多一次修复是输出校验机制，不是再次检索，必须在模式比较中计入成本。

### 7.2 Agentic 双图

```text
主图：START → rewrite/decompose → Send 子问题 → aggregate → validate_result → END
                            └→ clarification_required → END

子图：orchestrator ↔ retrieval tools → optional compression → evidence + answer
```

复用现有主图/子图和结构化命中，不另建通用 Agent 框架。去掉对历史摘要、跨请求 interrupt/resume 的依赖。子图传递 evidence artifact，不能只把摘要当作原始证据。

共享请求级预算，扇出前校验子问题数量；调用工具前预留预算，不能工具执行后才发现超限。内部临时 state 可用 request_id 隔离；如果仍使用 checkpointer，须在成功、失败、取消后清理，不能变相保留公开会话。

### 7.3 结果校验与流式

- `answered` 的知识性结论要有本次证据引用；缺失部分进入 `limitations`。
- 完全无证据为 `refused`，缺少问题限定为 `clarification_required`。不可判断的冲突不擅自裁决。
- 校验引用 ID、快照、quote/span 和本次证据集合。失败可在预算内修复一次，仍失败返回结果校验错误，不伪造引用、不冒充正常拒答。
- 服务端的校验能证明引用有效性，不能形式化保证每句话真实；生成质量仍需评测。D-06 还必须通过路线计划中的冻结语义支持审计，真实但不支持结论的引用也算失败，不能靠 span/hash 正确放行。

SSE 继续提供状态、工具调用/结果和最终结果，但本轮优先保证**最终答案校验后发布**。在校验完成前不把候选生成文本当作可信答案流给用户；可在完成后按客户端需要分片输出，必须说明这是已校验文本分片，不宣传实时 LLM token 流。

成功、拒答、澄清均恰好一个 `done`；异常恰好一个 `error`，不能再生成成功 `done`。为迁移保留的 `[DONE]` 只是传输结束标记，不是第二个领域结果。断连不承诺客户端收到终态，但服务端必须停止或有界收尾。

分别测首次状态事件、首次已校验答案内容、完整完成时间；不能拿状态事件冒充模型首 token 延迟。

### 7.4 旧协议迁移

这是活跃原型的显式破坏性迁移，目标协议版本需更新，并在 README 记录：

- `/invoke`、`/stream` 增加 mode 和新结果 schema；默认 `rag`，原默认双图不再隐式沿用。
- 新请求禁止 `thread_id`，返回明确参数错误；不能接收后悄悄忽略。
- `/history` 不再读 checkpoint，返回明确退役响应（410）；MCP 不提供历史工具。
- `interrupted` 不再表示待恢复状态，客户端改读 decision。Gradio 可以展示调用记录，但不得自动把旧消息带进下一请求。
- API/client/schema/tests 同步迁移。老报告原样保留，旧 HITL 指标不改名冒充单次澄清指标。

## 8. 评测与验收实施

完整门禁见 [ROADMAP](../ROADMAP.md)。测试分层如下：

1. 无密钥单测：应用 bootstrap、schema、模式选择、预算、引用检查、HTTP/MCP 错误和 SSE 终态。
2. 真实索引：来源 metadata、精确 span、快照失效、离线输入格式、无生成模型检索。
3. 真实模型：相同语料与 retriever 的单图/双图回答、拒答/澄清、成本和 badcase。
4. 实际客户端：Pi 工具发现、Bearer、search/get_context、可选 ask；记录版本和协议，脱敏保存证据。
5. 容量/部署：100 文档/1 万块的独立规模快照、3 并发、原生/Docker smoke 与资源记录。

既有 pytest 是每次改动门禁。检索/Agent/语料变更须按项目规则重跑 eval；服务变化须真实 Uvicorn health ready。运行 embedding 进程使用 `env -u ALL_PROXY -u all_proxy`，从 `eval/` 执行 runner；不要通过改 shell 全局环境解决本机代理问题。

真实模型和 judge 沿用现有配置，不逐轮请求费用确认。记录每题与每轮的模型、输入/输出 token、价格口径及估算成本；judge 成本单独列出，无法获取用量时标 unknown，不能记为 0。禁止在自动失败重试中反复跑完整评测。

## 9. 后续共享知识层的扩展位置（本轮不实现）

未来引入 `AccessContext`（服务端 principal）、文档 ID、编辑 revision 与当前版本指针。由 L2 把授权后的查询范围交给 L0；L0 的搜索、父文档回查和证据解析都使用同一约束，而不是在 top-k 返回后才删掉越权命中。

最小后续数据实体可为：document、revision、ingest_job、principal/credential。用户 metadata 与系统 owner/scope/revision 分开；客户端不能用 metadata 伪造身份或改变权限。

资料修改/删除采用 compare-and-swap 修订校验。任务绑定 generation，发布前复查；删除写 tombstone 并推进 generation。staging 内容不参与查询，完整发布才成为当前版本；历史读取必须检查当前文档权限与删除状态。

Qdrant 与原文/目录存储之间没有天然事务。后续必须设计可恢复的发布状态机、可见性选择和垃圾回收，并验证“搜索→回查”期间的版本一致性；不能把两次独立写操作称作原子发布，也不能仅用 collection alias 代替文档级权限/修订设计。

此时再评估 Qdrant server、事务型 catalog 与持久任务队列。不提前锁定 Postgres、Redis 或多进程 worker，也不把未实现的预留字段当作产品能力。

## 10. 兼容性依据与实施前检查

本次访谈已静态核对：Pi 0.85.1、`pi-mcp-adapter` 2.32.1；adapter 支持 Streamable HTTP、Bearer/自定义 header，协议模式有版本差异。当前尚未连接本项目的 MCP endpoint。

Day 1 用最小 ASGI 工具与真实 Pi 验证 mount 路径、lifespan、鉴权、协议协商和 structured result，再 pin 服务端 SDK。版本兼容失败先修适配，不改写 MCP 协议或承诺所有客户端兼容。

参考资料：

- [官方 Python MCP SDK：服务端与 ASGI 挂载](https://py.sdk.modelcontextprotocol.io/v1/server/)
- [官方 Python SDK 仓库](https://github.com/modelcontextprotocol/python-sdk)
- 本机 `~/.pi/agent/npm/node_modules/pi-mcp-adapter/` 的包清单、README、transport/认证实现；配置示例只使用占位符，不记录真实凭据。

Docker 原生 ARM64 依赖、模型下载与本机内存预算也在 Day 1 做可行性检查，不拖到最后一天才发现镜像无法构建。若需改限额、协议版本或存储 profile，记录技术决策；不得据此悄悄扩大或缩小 PRD 的产品范围。
