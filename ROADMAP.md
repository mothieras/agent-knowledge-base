# Agentic-RAG 最终形态与路线计划

## 最终定位

本项目最终形态为：

> **可复现、可评测、可私有部署的中文 Agentic RAG 参考服务。**

项目不以通用 SaaS 或微服务平台为目标。整体保持**模块化单体 + 独立存储 + 离线入库任务**，以检索调优、Agent 决策和评测闭环为主线；私有部署只是可运行边界，不扩展成平台工程。核心价值集中在四个方面：

1. 中文检索质量能够在有区分度的挑战集上量化。
2. 回答有证据，无证据时能够明确拒答。
3. 澄清、拒答和正常回答都能通过真实多轮脚本验证。
4. 从空环境能够以最小依赖稳定部署、复现和回归。

当前项目已经具备双图 Agent、HITL 澄清、受治理语料、30 题评测集和 FastAPI/SSE 服务层。后续重点是建立更难的检索评测、补齐显式拒答和可核验引用；持久化与访问控制只做到私有部署所需的最小闭环。

根 `README.md` 继续作为项目已完成功能、验证证据和当前边界的活档真相源；本文只描述目标形态与未来路线，不代表对应能力已经完成。

## 最终架构

```text
Web / SDK / Gradio
        │
        ▼
FastAPI v1
  invoke · stream · history · health
  deployment bearer token · exact CORS
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
  │    ├─ Dense candidate
  │    ├─ jieba sparse candidate
  │    ├─ RRF fusion
  │    ├─ version / effective-date filter
  │    └─ optional reranker
  │
  ├─ Qdrant（local / server profile）
  ├─ Parent Document Store
  └─ PostgresSaver

独立 Ingest Job
  manifest → 校验 → 分块 → metadata → 索引版本 → 发布

Eval / Observability
  golden set · retrieval trace · LLM trace · latency · cost
```

系统继续保持现有 L0–L4 单向依赖：

- L0 负责语料、索引、检索、融合、重排和版本策略。
- L1 负责 Agent 编排、澄清、拒答和引用决策。
- L2 负责依赖装配、checkpointer 和运行配置。
- L3 负责稳定的 HTTP/SSE 协议、最小访问控制和线程续接。
- L4 只负责消费协议和展示结果。

FastAPI 不感知 jieba、RRF、reranker 或图节点内部实现。

### 结构化检索结果

当前检索工具返回拼接字符串，API 再从 `File Name:` 文本中解析来源。最终应改为结构化命中：

```text
RetrievalHit
  chunk_id
  parent_id
  source
  version
  effective_date
  expired_date
  score
  retrieval_channel
  content
  span
```

Agent 可以消费格式化文本，但 API、评测和引用必须使用结构化 artifact，避免依靠字符串解析实现拒答、引用和检索诊断。

## 路线计划

按单人全职估算约 **5–7 周**。进度以阶段退出条件为准，不以开发时间作为完成声明。

| 阶段 | 目标 | 主要工作 | 退出条件 |
|---|---|---|---|
| **M0 可重复运行** | 让当前原型形成可靠运行闭环 | 修复 Docker build context；统一 sync、ingest、test、eval、up 命令；增加真实组件 smoke；拆分 liveness/readiness | `docker compose build` 成功；空环境完成 sync→ingest→API；`/health=ready`；pytest 全绿；容器 smoke 可在本地或发布前手动复现 |
| **M1 检索契约与 metadata** | 为后续检索升级建立稳定接口 | 增加 `Retriever`/`RetrievalHit`；manifest metadata 全链路入库；记录索引版本；ToolFactory 不再依赖 Qdrant 字符串格式 | chunk 可反查 source/version/date/span；metadata 单测及真实索引测试通过；API 层无 L0 内部依赖 |
| **M2 检索挑战集与中文检索升级** | 让检索改造的收益能够被证明 | 先扩展并冻结检索挑战集与改造前基线，再实现 dense + jieba sparse、RRF、版本/有效期过滤，并通过消融决定是否启用 reranker | 检索计分题由 20 条扩展到至少 40 条，覆盖真实新旧版本冲突、困难多跳和 hard negative；原 30 题 Recall@5/MRR 只作回归守卫；挑战集的排序敏感指标与 Context Precision 均优于冻结基线；困难分组无退化；形成消融报告 |
| **M3 多轮决策与可信回答** | 验证完整的澄清、拒答和引用链路 | 为澄清题补多轮回复脚本；runner 使用同一 `thread_id` 恢复执行；实现显式拒答路由、chunk/span 引用和结构化 decision；默认不暴露完整 context | 所有澄清题完成两轮执行且 0 ERROR/SKIP；分别报告澄清召回、过度澄清率和澄清后解决率；正常回答、澄清、拒答三条路径均有测试；citation 全部可反查实际命中块 |
| **M4 最小私有部署** | 证明 checkpointer seam 和最小访问控制可用 | PostgresSaver；单一部署级 Bearer token；精确 CORS；Compose 启动说明；备份/恢复文档与脚本 | 重启后会话恢复；无凭据或错误凭据请求被拒绝；并发 thread 不串线；一条文档化命令启动；备份/恢复脚本有最小 smoke，不要求生产级演练 |
| **M5 轻量发布门禁** | 在不增加持续维护负担的前提下形成可发布版本 | 固定 API v1 契约；PR CI 保持 compile + pytest；容器 smoke 作为本地/发布前检查；带密钥 eval 走手动或可选 nightly；维护发布清单 | 默认 CI 全绿；发布记录附容器 smoke 与 eval 证据；报告绑定 commit、manifest hash、索引版本和模型配置 |

## 各阶段关键取舍

### M0：优先解决确定性阻塞

当前 `docker-compose.yml` 使用 `./project` 作为 build context，但 `project/Dockerfile` 执行：

```dockerfile
COPY requirements.txt .
```

`requirements.txt` 位于仓库根目录，因此当前 Compose 配置无法完成构建。容器文件存在不等于容器化链路已经完成。

测试需要分成两个层次：

- 单元与契约测试：继续通过 stub 隔离 LLM 和 Qdrant。
- 真实 smoke：覆盖临时索引、API 生命周期及 SSE 完整结束事件。

### M1：先稳定数据契约，再升级算法

manifest 已经包含 `version`、`effective_date`、`expired_date` 和 `priority`，但当前入库只传播 `source`。如果直接实现 RRF 或 reranker，后续版本过滤和引用仍需再次调整数据链路。

建议固定以下实施顺序：

```text
结构化命中 → metadata 完整传播 → filter → fusion → rerank
```

### M2：先建立能证明提升的检索挑战集

当前 Recall@5 已为 1.000，只能继续作为回归守卫，不能证明检索升级有效。M2 必须先扩展评测，再修改检索实现：

1. 在算法改造前增加困难多跳、真实新旧版本冲突、精确术语和 hard-negative 问题。
2. 为检索题补充 qrels 或等价的相关性标注，使 `source_precision@5`、`nDCG@5` 等排序敏感指标可计算。
3. 使用当前 retriever 在新增挑战集上记录并冻结基线，同时预先固定验收阈值。
4. 保持新增题目和阈值在检索改造期间不变，避免针对结果调整题集。

Context Precision 当前为 0.6895，应作为 M2 的核心生成侧结果；Recall@5/MRR 只负责保护原有能力。比较必须固定模型、prompt、语料、索引参数和 judge 配置。

reranker 会增加模型体积、资源消耗和 P95 延迟，不应仅因为路线图中存在该能力就默认启用。至少维护四组消融实验：

1. dense only
2. dense + jieba sparse
3. dense + sparse + RRF
4. dense + sparse + RRF + reranker

只有第四组在挑战集的排序指标和 Context Precision 上产生稳定收益，且延迟与资源开销可接受时，才进入默认配置。

### M3：多轮决策、拒答与引用

#### 多轮澄清评测

当前基线的澄清题只有首轮输入，没有用户澄清回复脚本。M3 应为每条 `ambiguous_followup` 增加至少以下信息：

```text
clarification_reply
expected_first_turn_decision
expected_final_decision
expected_sources
```

评测 runner 必须先验证首轮是否正确暂停并提出有效问题，再使用同一 `thread_id` 提交澄清回复，验证图是否恢复、检索和最终回答是否正确。指标至少区分：

- clarification recall：该澄清时是否澄清。
- over-clarification rate：明确问题是否被多余地打断。
- resolution success：得到补充信息后是否完成正确回答或拒答。

单轮 `clarification_rate` 只保留为历史对比，不再作为 M3 验收依据。

#### 结构化引用契约

最终响应至少应包含：

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

`decision` 建议定义为：

```text
answered · clarification_required · refused
```

客户端不应通过空答案或 `interrupted` 字段猜测当前状态。

### M4：部署是边界，不是主线

M4 只验证现有 checkpointer seam 和最小私有部署能力，不建设通用平台：

- 使用 PostgresSaver 证明会话可以跨进程重启恢复。
- 使用单一部署级 Bearer token 拒绝未认证请求；该模式不声明用户身份或 thread ownership。
- CORS 使用明确来源配置，不实现完整身份系统。
- 提供 Compose、配置检查以及备份/恢复文档和脚本。

不在该阶段实现 OIDC、RBAC、租户后台、配额、限流、审计平台、自动迁移体系或生产级备份演练。省下的投入优先用于 M2 的检索挑战集和 M3 的拒答、多轮评测。

### M5：CI 保持轻量

push/PR 默认门禁继续采用 compile 语法检查和全量 pytest。真实模型 eval 需要密钥且运行较慢，容器 smoke 也会增加 CI 时间和维护成本，因此二者作为发布前可复现检查；有稳定运行环境后，再选择性加入 nightly，而不是阻塞每个 PR。

## 发布质量门槛

### 检索

- 原 30 题继续保持 0 ERROR、0 SKIP；Recall@5 1.000 和 MRR 0.900 仅作为回归守卫。
- M2 开发前冻结扩展挑战集及当前 retriever 基线。
- 扩展集至少包含 40 条实际进入检索计分的问题，并覆盖困难多跳、真实版本冲突和 hard negative。
- 排序敏感指标与 Context Precision 必须相对冻结基线提升，具体阈值在检索实现改动前写入评测配置。
- 版本过期文档默认不会进入检索结果，每个命中包含完整 provenance。

### 生成与决策

- Faithfulness 不低于当前基线 0.938。
- 引用 100% 可解析到实际检索结果，未检索到证据时不得生成伪引用。
- 所有澄清题具有第二轮用户回复，并完成同一 thread 上的恢复执行。
- 分别报告 clarification recall、over-clarification rate 和 resolution success；单轮 clarification rate 不作为验收门禁。
- 拒答指标在扩展集上优于当前基线，同时报告正常问题的误拒率。

### 服务与发布

- 分别记录流式首 token 和完整响应的 P50/P95，作为回归证据而非独立平台建设目标。
- API 重启后会话可恢复，无凭据或错误凭据不能读取 history。
- 原始 contexts 默认只在 debug/eval 模式返回。
- push/PR 只强制 compile + pytest；容器 smoke 和带密钥 eval 在发布前运行并保存证据。

## 暂不纳入范围

以下能力在核心质量闭环完成前不进入路线：

- Kubernetes 和微服务拆分。
- 更多 Agent 或 GraphRAG。
- 通用拖拽式工作流。
- 多模型自动路由。
- 文档管理后台。
- 大规模多租户 SaaS。
- per-user token、thread ownership、OIDC、RBAC、配额、限流和审计平台。
- 自动迁移体系、生产级高可用和备份演练。
- 完整日志/指标基础设施和每个 PR 的容器 smoke。
- 在没有消融证据时默认启用高成本 reranker。

近期优先保证系统能够解释检索到了什么、为什么回答、证据在哪里，以及什么时候应该拒答。

## 许可风险

当前检索语料遵循 **CC BY-NC-SA 4.0**，因此项目默认面向学习、评测、非商业演示和私有研究场景。

如果目标调整为商业产品，必须建立独立的语料迁移流程：

1. 替换为自有语料或获得商业授权的语料。
2. 重新生成 manifest、golden set 和评测基线。
3. 不随商业镜像分发由当前非商业语料构建的索引。

该限制属于产品与数据许可边界，不能通过部署方式规避。
