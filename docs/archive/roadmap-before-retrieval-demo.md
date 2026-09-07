# Agentic-RAG 最终形态与路线计划（历史归档）

> 本文保留检索优先演示版需求访谈前的路线计划，不再作为执行要求。原有 6–8 周计划、强制多轮恢复/PostgresSaver 及相应退出条件均需按新范围理解；保留不代表完成或继续承诺。
> 当前需求见 [PRD](../PRD.md)，技术设计见 [DESIGN](../DESIGN.md)，执行顺序见 [根 ROADMAP](../../ROADMAP.md)，已完成能力见 [根 README](../../README.md)。下文按历史原文保留。

## 最终定位

本项目最终形态为：

> **检索质量可量化、回答证据可核验，并可在核心能力稳定后私有部署的中文 Agentic RAG 参考实现。**

项目不以通用 SaaS、微服务平台或基础设施模板为目标。核心价值按优先级排列：

1. 能用有区分度的评测判断中文检索是否真的改善。
2. 能解释检索到了什么、为什么回答，以及引用来自哪里。
3. 无证据时拒答，有歧义时澄清，三种决策都能被真实脚本验证。
4. 核心行为稳定后，再提供最小、可复现的私有部署闭环。

当前项目已经具备双图 Agent、HITL 澄清、受治理语料、30 题评测集、Qdrant hybrid 检索和 FastAPI/SSE 原型。现有指标可以作为回归基线，但题集区分度、结构化 provenance、显式拒答、可核验引用和真实多轮评测仍未完成。

根 `README.md` 是已完成功能、验证证据和当前边界的活档真相源。本文描述目标、依赖顺序和退出条件，不把计划中的能力写成已经完成。

## 第一性原则

### 1. 项目的价值不是“能启动”，而是“结果可信”

容器成功启动只能证明打包和进程编排有效，不能证明：

- 检索结果相关；
- 新旧版本处理正确；
- 回答受证据支持；
- 无答案时会拒答；
- 引用能够回查原文。

因此，Docker、Compose、持久化和访问控制不能成为检索与回答质量开发的前置门槛。

### 2. 先建立判定标准，再修改算法

如果挑战集不能区分方案优劣，新增 sparse、RRF 或 reranker 只是在堆功能。算法改造前必须冻结问题、qrels、指标、模型配置和旧方案基线，避免看到结果后再调整验收标准。

### 3. 先稳定证据契约，再实现过滤、拒答和引用

版本过滤、检索诊断、拒答阈值和 citation 都依赖结构化命中。如果继续从拼接字符串中解析 `File Name:`，后续每层都会重复修补同一缺口。

固定依赖顺序：

```text
评测基线
  → 结构化 RetrievalHit 与 metadata
  → 有区分度的挑战集
  → 检索消融与策略选择
  → 回答 / 澄清 / 拒答 / 引用
  → 稳定服务协议
  → 私有部署与发布
```

### 4. 延后高返工、低信息量的工作

镜像分层、Compose 编排、Postgres、认证、备份和发布脚本依赖最终的运行进程、配置项、存储边界和 API 契约。过早完成这些工作会随核心设计变化反复重做，却不能降低当前最大的质量风险。

容器化与编排在最终部署阶段前不构成开发门禁，也不阻塞 M0–M5。

## 目标架构

```text
本地应用与协议层（M5 稳定）
Web / SDK / Gradio
        │
        ▼
FastAPI v1
  invoke · stream · history
        │
        ▼
RAGSystem（composition root）
  ├─ LangGraph 主图 / 子图
  │    ├─ 澄清
  │    ├─ 显式拒答
  │    ├─ 并行子问题
  │    └─ 带引用回答
  │
  ├─ Retriever
  │    ├─ dense candidate
  │    ├─ jieba sparse candidate
  │    ├─ RRF fusion
  │    ├─ version / effective-date filter
  │    └─ optional reranker
  │
  ├─ Qdrant
  ├─ Parent Document Store
  └─ injectable checkpointer

独立 Ingest
  manifest → 校验 → 分块 → metadata → 索引版本 → 发布

Eval
  golden set · qrels · retrieval trace · decision · citation · latency · cost

最终部署边界（M6 才固化）
  Docker / Compose · Qdrant server · PostgresSaver
  Bearer token · exact CORS · health · backup / restore
```

系统继续遵守 L0–L4 单向依赖：

- L0：语料、索引、检索、融合、重排和版本策略。
- L1：Agent 编排、澄清、拒答和引用决策。
- L2：依赖装配、checkpointer 和运行配置。
- L3：HTTP/SSE 协议、线程生命周期和最小访问边界。
- L4：客户端与展示。

FastAPI 不感知 jieba、RRF、reranker 或图节点内部实现。Docker 和 Compose 只负责最终进程装配，不反向塑造 L0–L4 的内部契约。

### 结构化检索结果

当前检索工具返回拼接字符串，API 再从 `File Name:` 文本中解析候选来源。目标契约为：

```text
RetrievalHit
  chunk_id
  parent_id
  source
  version
  effective_date
  expired_date
  priority
  score
  retrieval_channel
  content
  span
```

Agent 可以消费由 `RetrievalHit` 格式化的上下文，但 API、评测、过滤和引用必须使用结构化 artifact，不能依赖文本解析。

## 路线计划

按单人全职估算约 **6–8 周**。阶段由依赖关系和退出证据推进，不按日期宣布完成。

| 阶段 | 要解决的根本问题 | 主要工作 | 严格退出条件 |
|---|---|---|---|
| **M0 评测契约与冻结基线** | 我们如何知道后续改动是改善而不是波动？ | 审计现有 golden set 和指标；固定语料 revision、manifest hash、索引参数、模型与 prompt；冻结 top-5 检索 trace；报告绑定代码版本 | 30 题基线 0 ERROR/0 SKIP；报告记录 commit、manifest、模型和索引配置；每题 top-5 的有序 source/content hash 可机器比对；指标定义与计分范围明确；只声明回归基线，不宣称检索已充分优化 |
| **M1 检索证据契约** | 每个命中能否被机器稳定追踪和验证？ | 引入 `Retriever` / `RetrievalHit`；manifest metadata 全链路传播；chunk/span 与索引版本；增加受治理的多版本/过期 fixture；移除来源字符串解析 | 每个命中可反查 source/version/date/chunk/span；API 与评测不再解析 `File Name:`；metadata 单测和真实本地索引集成测试通过；原 30 题指标及 top-5 来源/内容哈希顺序与 M0 冻结 trace 完全一致；L3 不依赖 L0 内部实现 |
| **M2 检索挑战集** | 当前题集能否区分检索策略优劣？ | 在改算法前扩展困难题；补 qrels；纳入受治理的版本 fixture；冻结旧 retriever 基线和数值验收阈值 | 至少 40 条实际进入检索计分的问题；覆盖中文精确术语、hard negative、困难多跳和 active/expired 版本冲突；可计算 Precision/nDCG 等排序指标；题集、qrels、`eval/acceptance.yaml` 和旧基线在 M3 前提交且冻结 |
| **M3 中文检索实证升级** | 哪种检索组合在质量与成本之间最好？ | dense、jieba sparse、RRF、有效期过滤与可选 reranker；分组评测和消融 | 所有预设组合完成同配置消融；expired fixture 默认不返回；报告包含质量、P95 延迟和峰值内存；只有满足 M2 晋升阈值的方案才能替换基线，否则保留原方案并以负结果完成阶段 |
| **M4 可信回答与多轮决策** | 系统何时回答、澄清或拒答，依据能否核验？ | 显式 decision；拒答路由；结构化 citation；真实两轮澄清脚本；同 thread 恢复；实现前冻结 decision 阈值 | `answered / clarification_required / refused` 三路径均有测试；0 ERROR/SKIP；citation validity 与澄清后解决率均为 100%；拒答 recall/precision 均 ≥0.90；正常题误拒率与过度澄清率均 ≤5% |
| **M5 本地服务契约** | 已证明的核心能力能否通过稳定协议被正确消费？ | 收敛 API v1 schema；统一 invoke/stream/history；线程生命周期；SSE terminal contract；context debug 边界 | 本地 Uvicorn 真实链路覆盖回答、拒答、澄清恢复和 SSE `done`；API/client/schema 契约测试全绿；默认响应不暴露完整 context；本阶段不要求 Docker、Postgres 或部署认证 |
| **M6 私有部署与发布** | 如何把稳定系统以最小风险交付和复现？ | Docker/Compose；Qdrant server profile；PostgresSaver；Bearer token；精确 CORS；health；原子入库；备份恢复；发布门禁 | 空环境用一条文档化命令完成 sync→ingest→up→真实 smoke；重启后会话恢复；并发 thread 不串线；未认证请求被拒绝；备份恢复 smoke 通过；发布记录附容器与 eval 证据 |

## 阶段说明与关键取舍

### M0：评测契约先于工程扩张

现有 30 题和公开报告是有价值的回归资产，但 Recall@5 已为 1.000，且实际进入检索计分的题目只有 20 条。它们不能证明更复杂的检索策略具有增量价值。

M0 只回答三个问题：

1. 当前系统在固定输入和配置下表现如何？
2. 每项指标具体统计哪些题、如何处理 ERROR/SKIP？
3. 后续报告能否精确绑定代码、语料、索引和模型？

M0 使用本地开发环境完成，不要求构建镜像。默认 PR 门禁继续保持 compile + pytest；带密钥 eval 可以手动执行，不必阻塞每次提交。

### M1：先消除字符串型证据债务

manifest 已包含 `version`、`effective_date`、`expired_date` 和 `priority`，但当前链路主要传播 `source`。版本过滤、拒答证据和 citation 都依赖同一份结构化 metadata，因此必须一次打通：

```text
manifest
  → document metadata
  → parent / child chunks
  → Qdrant payload
  → RetrievalHit
  → graph state / artifact
  → API 与 eval
```

索引版本属于数据与检索契约，不属于 Docker。无论本地脚本还是未来容器入库，都必须产生相同的版本信息。

当前 14 篇公开语料没有 active/expired 多版本对，无法验证版本传播和过滤。M1 增加项目自有、可公开、内容最小的受治理 fixture：同一文档至少包含一个已过期版本和一个当前有效版本，并具有明确预期命中。它只用于 metadata、过滤和冲突评测，不改写第三方语料。

M0 额外冻结每道检索题 top-5 的有序 `source + content_hash` trace。M1 完成结构重构后必须机器比对：原 30 题指标完全相等，且上述有序 trace 逐项相等。M1 不允许顺便调整召回或排序；任何行为优化都推迟到 M3，因此不存在用“预期变化”放行回归的口子。

### M2：先冻结挑战，再看算法结果

M2 在任何新检索实现进入默认路径前完成：

1. 增加中文精确术语、hard negative、困难多跳和真实新旧版本冲突题。
2. 为检索题补 qrels 或等价的分级相关性标注。
3. 使用当前 Qdrant hybrid retriever 记录冻结基线。
4. 在看到新算法结果前写入分组指标和验收阈值。
5. 将 M1 的 active/expired fixture 纳入版本冲突题和 qrels。
6. 在 `eval/acceptance.yaml` 写入并提交数值门槛，然后才开始 M3。
7. 评测期间保持问题、qrels、阈值、模型、prompt、语料和索引参数不变。

`eval/acceptance.yaml` 至少固定：

- nDCG@5 和 source precision@5 相对基线的最小绝对提升均为 `0.05`；
- 困难多跳、版本冲突和 hard-negative 分组指标不得下降；
- 原 30 题 Recall@5 与 MRR 不下降；
- Context Precision 在 3 次独立生成评测中的均值至少提升 `0.03`，且任一次不得低于基线 `0.02` 以上；
- 默认检索方案的 P95 延迟和峰值内存均不得超过基线的 `2x`。

如果 M2 的冻结基线证明某个阈值因指标天花板不可计算，必须在任何 M3 实现前通过单独提交调整题集或指标，不能在看到新方案结果后修改。这一步的产物是“尺子”，不是新的检索功能。

### M3：检索能力由消融决定，不由路线图指定

当前已经存在 Qdrant dense + FastEmbed sparse hybrid。M3 评估的是中文场景下，jieba sparse、自定义融合、版本过滤和 reranker 是否带来可证明的净收益，而不是从零增加“hybrid”标签。

至少比较：

1. dense only；
2. 当前 dense + FastEmbed sparse hybrid 基线；
3. dense + jieba sparse；
4. dense + jieba sparse + RRF；
5. 上述组合 + reranker。

`eval/acceptance.yaml` 是新方案替换当前基线的晋升门槛，不是强迫实验必须得到正结果。若所有候选都未达标，M3 以“保留当前方案”的负结果正常完成；版本有效期过滤仍作为独立 correctness 门禁落地。

reranker 会增加模型体积、P95 延迟和运行资源。只有第五组满足全部质量与资源门槛时才进入默认配置；否则保留为关闭的实验选项或直接删除。这里没有“感觉更好”的通过方式。

### M4：回答可信依赖检索证据已经稳定

M4 不用 prompt 掩盖检索问题。只有 M1 的结构化命中和 M3 的检索策略通过验收后，才固定回答决策。

目标响应至少包含：

```json
{
  "thread_id": "...",
  "answer": "...",
  "decision": "answered",
  "citations": [
    {
      "citation_id": "c1",
      "source": "...",
      "chunk_id": "...",
      "span": {"start": 120, "end": 248},
      "quote": "..."
    }
  ]
}
```

`decision` 固定为：

```text
answered · clarification_required · refused
```

客户端不再通过空答案或 `interrupted` 猜测状态。

每条 `ambiguous_followup` 至少增加：

```text
clarification_reply
expected_first_turn_decision
expected_final_decision
expected_sources
```

runner 先验证首轮暂停与澄清问题，再使用同一 `thread_id` 提交回复并验证恢复结果。单轮 `clarification_rate` 只作历史对比，不作为最终门禁。

M4 实现开始前先冻结 decision 标注集和阈值：拒答 recall 与 precision 均不低于 `0.90`，正常可回答题误拒率不高于 `5%`，明确问题过度澄清率不高于 `5%`，澄清后解决率与 citation validity 均为 `100%`。整个评测必须 0 ERROR、0 SKIP；未执行的题不能从分母中消失。

### M5：先稳定产品语义，再冻结部署形态

FastAPI/SSE 原型已经存在，但 API v1 应在 decision、citation 和 thread 行为确定后再冻结。否则部署层会围绕变化中的 schema、状态机和存储需求反复调整。

M5 的真实 smoke 直接在本地启动 Uvicorn，验证：

- 正常回答；
- 无证据拒答；
- 首轮澄清和同 thread 恢复；
- SSE 恰好一个 terminal `done`；
- client 与 Gradio 只消费公开协议。

这一阶段只验证应用协议，不以 Docker build、Compose、Postgres、认证或备份为退出条件。

### M6：部署是最终边界，不是研发起点

到 M6 时，核心依赖、索引契约、API schema、会话语义和进程角色已经稳定，才有足够信息设计最终镜像与编排。

M6 包含：

- runtime、ingest、eval 和 UI 的最终镜像/进程边界；
- Compose 本地 Qdrant server 与 PostgresSaver；
- liveness、readiness 和入库完成状态；
- 单一部署级 Bearer token 与精确 CORS；
- 配置检查、资源限制、备份和恢复脚本；
- CPU 环境从空数据卷完成全链路复现；
- 发布前容器 smoke 与带密钥 eval。

不建设 OIDC、RBAC、多租户后台、配额、限流平台、Kubernetes、高可用或生产级灾备。部署只证明这个参考服务可以被最小、可信地交付。

Dockerfile 和 Compose 在 M6 按最终进程边界重新设计；其当前形态不约束 M1–M5 的设计。

## 质量门槛

### M0：基线证据

- 30 题运行保持 0 ERROR、0 SKIP。
- 报告绑定 commit、manifest hash、语料 revision、索引参数、模型和 prompt 版本。
- 每道检索题保存 top-5 有序 `source + content_hash`，供 M1 做逐项机器比对。
- Recall@5 1.000、MRR 0.900 等当前结果只作为回归守卫，不宣传为检索上限。

### M1：证据契约

- public corpus 与 active/expired fixture 的 metadata 均能从 manifest 传播到 `RetrievalHit`。
- M0 的 30 题指标完全相等；每题 top-5 的有序 `source + content_hash` trace 逐项相等。
- API、评测和后续引用只消费结构化 artifact，不解析上下文展示文本。

### M2–M3：检索

- 扩展集至少 40 条实际进入检索计分的问题。
- qrels 支持 source precision@5、nDCG@5 和分组统计。
- `eval/acceptance.yaml` 在实现前冻结质量、P95 延迟和峰值内存门槛，M3 期间不得修改。
- 候选方案只有在 nDCG@5、source precision@5、Context Precision、困难分组和原 30 题守卫全部达到冻结门槛时才可晋升；没有候选达标时保留基线。
- active/expired fixture 能产生反例，过期版本默认不会进入结果；每个命中包含完整 provenance。

### M4：生成与决策

- Faithfulness 不低于当前基线 0.938。
- citation validity 与澄清后解决率均为 100%，未检索到证据时不得生成伪引用。
- 拒答 recall/precision 均不低于 0.90；正常题误拒率与明确题过度澄清率均不高于 5%。
- 所有澄清题具有第二轮回复，并完成同一 thread 上的恢复执行。
- 全集 0 ERROR、0 SKIP，所有比例使用冻结标注集的完整分母。

### M5：服务协议

- invoke、stream、history 对 decision 和 citation 的表达一致。
- SSE terminal contract、错误事件和中断恢复有自动化测试。
- 原始 contexts 默认只在 debug/eval 模式返回。
- 本地真实 API smoke 可重复通过。

### M6：部署与发布

- 空环境完成 sync→ingest→API→真实 SSE smoke。
- API 重启后会话可恢复，并发 thread 不串线。
- 无凭据或错误凭据不能调用受保护接口或读取 history。
- 分别记录流式首 token 和完整响应的 P50/P95。
- 发布记录附 commit、manifest、索引版本、模型配置、容器 smoke、eval 和备份恢复证据。

## 不纳入范围

核心质量闭环完成前不进入以下工作：

- Kubernetes 和微服务拆分；
- 更多 Agent 或 GraphRAG；
- 通用拖拽式工作流；
- 多模型自动路由；
- 文档管理后台；
- 大规模多租户 SaaS；
- per-user token、thread ownership、OIDC、RBAC、配额、限流和审计平台；
- 自动迁移体系、生产级高可用和灾备演练；
- 完整可观测性平台和每个 PR 的容器 smoke；
- 没有消融证据时默认启用高成本 reranker。

近期优先级始终是：**衡量检索 → 稳定证据 → 改善检索 → 约束回答**。

## 许可边界

当前检索语料遵循 **CC BY-NC-SA 4.0**，项目默认面向学习、评测、非商业演示和私有研究场景。

如果目标调整为商业产品，必须建立独立的语料迁移流程：

1. 替换为自有语料或获得商业授权的语料；
2. 重新生成 manifest、golden set 和评测基线；
3. 不随商业镜像分发由当前非商业语料构建的索引。

该限制属于产品与数据许可边界，不能通过部署方式规避。
