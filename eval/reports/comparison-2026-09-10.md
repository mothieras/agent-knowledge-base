# rag / agentic 对照实验报告（发布证据核心）

2026-09-10，最终 commit 重跑。同配置对照：同一 index、同一模型与参数、同一 30 题质量集 + 40 题检索挑战集，`mode=rag`（固定单图）与 `mode=agentic`（双图）各执行一场。数字是记录、不是门禁；负结果有效，不预设双图更好。

## 可复现绑定

| 项 | 值 |
|---|---|
| 基线评测 commit | `6abf25c`（规模实测在 `756b4d5` 执行；src/ 差异仅 config.py 数据/缓存目录的环境变量钉定——默认路径行为不变，规模实测按设计使用覆盖路径——与新增客户端脚本 smoke_mcp.py） |
| golden set / 挑战集 | 30 条（6 类题型 × 5）/ 40 条（绑定 hash 见 acceptance.yaml） |
| index | `sha256:0df9fbfa…`（354 child chunks，manifest hash `5c702f3c358fd1b5`） |
| 模型 | deepseek-chat（生成与 judge 同模型）/ dense Qwen/Qwen3-Embedding-0.6B + sparse Qdrant/bm25 |
| 检索参数 | k=7, threshold=0.4, hybrid |
| 执行 | 每场 30/30，0 ERROR / 0 SKIP（错误与跳过保留在分母） |
| 产物 | per-item `.jsonl` + `.summary.json` + 报告 `.md`，README 基线表由 make_readme_table.py 从 summary 生成 |

> 指标口径勘误（本次重跑前修正）：runner 的 misrefusal/overclarify 分母此前把歧义题计入「明确可答」（n=25），与 acceptance.yaml 冻结口径不符；已改为 n=20（排除 unanswerable 与 ambiguous_followup），歧义题被拒单独记 `ambiguous_refused` 并仍计入 refusal FP。同口径下旧场（检索服务化 commit）数字：agentic overclarify 0.040→0.000（唯一澄清在歧义题 id25，属正确行为），rag misrefusal 0.200→0.150。
>
> 提交信息勘误（2026-09-10，发布后执行）：历史提交信息做了仅消息级的规范化重写（去除内部计划编号；树、作者、日期零变化，已逐 commit 验证），本报告与全仓按哈希绑定/引用 commit 的字段同步重指至重写后的等价 commit，数值与口径零变化。

## 质量

### 生成层（answered 题，ragas judge=deepseek-chat）

| 指标 | agentic | rag | 差异 |
|---|---:|---:|---|
| Faithfulness | 0.961 | 0.968 | 持平（±0.007 属 judge 方差） |
| Answer relevancy | 0.931 | 0.851 | agentic +0.080 |
| Context precision | 0.624 | 0.815 | **rag 高 0.191** |
| Context recall | 0.917 | 0.722 | **agentic 高 0.195** |

双图的检索面更宽（拆解/多轮多取证据）：context recall 更高、context precision 更低——多路证据摊薄了上下文精度，这是「覆盖换聚焦」的直接代价。faithfulness 两者都 >0.96，引用机械有效性 24/24 与 21/21 全通过（每条引用可按 span 精确回查原文）。

### 检索层（进入检索流程的 20 题 + 40 题挑战集）

| 指标 | agentic | rag |
|---|---:|---:|
| Recall@5 / @7 / MRR（质量集 20 题） | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |
| expired/fixture 泄漏 | 0 / 0 | 0 / 0 |

挑战集（40 题直接检索，双模式共用同一检索栈）：Recall@5=0.950、Recall@7=0.950、MRR=0.933、P@5=0.215、nDCG@5=0.934、回归锚点（20 条 golden 派生）Recall@5=1.000 与冻结基线持平；2 题 hard_negative 泄漏与 2026-09-09 场持平。Recall 1.000 只是这套固定语料与 golden set 的结果，不是通用上限。

## decision（单次终态，以应拒答为正类）

| 指标 | agentic | rag |
|---|---:|---:|
| refusal TP / FP / FN | 5 / 0 / 0 | 5 / 4 / 0 |
| refusal precision / recall | 1.000 / 1.000 | 0.556 / 1.000 |
| misrefusal（明确可答 n=20） | 0/20 = 0.000 | 2/20 = 0.100 |
| overclarify（明确可答 n=20） | 0/20 = 0.000 | 0/20 = 0.000 |
| clarification（歧义题 n=5） | 1/5 = 0.200 | 0/5 = 0.000 |
| 歧义题被硬拒（计入 FP） | 0 | 2 |
| answered / 引用全部可回查 | 24 / 24 | 21 / 21 |

两模式都拒掉了全部 5 个应拒题（refusal recall 均 1.000）；差异全在 FP 侧：rag 多拒了 4 个不应拒的题，agentic 零误拒。agentic 的 1 次澄清出现在真正无指代的歧义题（id25），属协议预期行为。

## 延迟与成本（30 条执行）

| 指标 | agentic | rag | 倍数 |
|---|---:|---:|---:|
| Latency P50 / P95 | 13.99s / 22.25s | 1.84s / 3.74s | 7.6× / 6.0× |
| 平均 input tokens | 12,591 | 982 | 12.8× |
| 平均 output tokens | 2,781 | 402 | 6.9× |
| 平均模型调用数 | 6.83 | 1 | 6.8× |
| 平均 cost（¥/query） | 0.0082 | 0.0010 | ~8.6× |

agentic 的成本来自编排本身：改写/拆解/聚合/校验平均 6.83 次模型调用，token 主要是每次调用重复携带的检索上下文。若消费方只做接地（用自己的模型），双图的编排成本不发生。

## badcase 审计（轻量，不设门禁）

### rag 4 例误拒逐条归因（FP 侧）

| id | 题型 | 现象 | 归因 | agentic 如何处理 |
|---|---|---|---|---|
| 9 | exact_term | 问「什么是句子窗口检索？」，检索命中正确文件但唯一证据是代码示例里的字面分隔行 `--- 句子窗口检索结果 ---`，无定义内容 → 拒 | 单图一次检索对 chunk 级噪声无自救：该分隔行与查询词完全一致，排序天然靠前 | 重写后拿到同文件的定义性 chunk（6 次调用、16.6s），引用了「为检索精确性而索引小块，为上下文丰富性而检索大块」等原文 |
| 20 | multi_hop | 题目跨两文件（12+16），单图只检索到 12 中两个查询字符串片段，缺失「查询真实航班信息」半边 → 拒 | 单图无法跨证据域拼接；一次检索的 top-k 只覆盖了题干一半 | 拆解后取到 12+14，答出「重写 vs 自查询」的分别处理结构（11 次调用） |
| 8 | ambiguous_followup | 问「文档加载效果不好，应该怎么处理？」，检索到「垃圾进垃圾出」的重要性论述但无处理步骤 → 拒 | 单图证据不足即放弃；歧义题无澄清出口，只有拒 | 二轮检索综合 04+15 给出泛化处理思路（10 次调用、22.3s） |
| 25 | ambiguous_followup | 问「这个 RAG 系统分数高不高？」——「这个」无指代，语料内无任何可答依据 → 拒（FP） | 拒答本身诚实，但协议预期是澄清而非硬拒 | **检索前即澄清**（1 次调用、1.8s、零检索）：「请问您指的是哪个 RAG 系统？」——澄清路径不烧检索成本 |

模式很清晰：4 例 FP 全部是「单图一次检索证据不足即放弃」。双图救回 3 例的方式是改写/拆解/多轮检索（代价 6.83 次模型调用），第 4 例（真歧义）则以澄清终态正确处理。

### 歧义题 4/5 未澄清是否合理（agentic）

未澄清直接作答的 4 题：id4（选型顺序：语料有明确「提示工程→RAG→微调」）、id16（检索优化：有系统化优化章节）、id24（评估排查：有 RAG Triad 排查顺序）、id8（文档加载处理，泛化可答）。前三题语料有系统内容，直接作答合理；id8 处于边缘（澄清「哪个 loader/什么症状」同样说得过去）。结论：**4/5 未澄清合理**，但暴露 `ambiguous_followup` 题型内部歧义度不均——强歧义（必须澄清）只有 id25 一题，其余是弱歧义（可答泛化）。后续若做澄清能力专项，需把「弱歧义可答、强歧义必澄清」的期望先写进 qrels。

### 澄清计数口径核对

修正分母后口径自洽：agentic 唯一 clarification 出现在歧义题 id25（clarification_rate 1/5），明确可答题 overclarify 为 0——同一事件不再被重复计为错误信号。rag 的 FP 中歧义题 2 例（id8/25）单独记录为 `ambiguous_refused`，不再混入 misrefusal（misrefusal = id9/20 两例真实明确可答误拒 = 0.100）。

### 挑战集 2 题 hard_negative 泄漏

- **ch029**（版本过滤靶子，发布后第一优先）：问「开放平台**当前有效的**免费层分钟限额」，top-7 只命中**过期版本 v1**（api_rate_limit_policy_v1.md），qrels 认定的当前有效版 v2 未过 0.4 阈值。根因：v1/v2 是近重复文档，正文几乎只差具体数字；「当前有效」这一判别信息只存在于 manifest metadata（expired_date/version），检索通道不消费它。版本/有效期过滤上线后，此题将变成零命中并走正确拒答（不再用过期版本作答），但「v2 为何不可检」仍是开放问题——过滤不创造召回，只消除错误证据。留作 L0 过滤 + 泄漏回归的靶子（fixture、qrels 已备）。
- **ch031**（轻度精度问题）：问「查询重构通常包含哪些方法」，hard_negative `12_query_construction.md`（相邻章节、部分相关）进入 top-7，但 qrels 主命中 `14_query_rewriting.md` 仍在 top-3，Recall@5 无损。相邻章节的语义重叠是已知检索特性，不改题。

### 口径备注

- per-item jsonl 中命中 `score` 为 `null` 是设计行为：hit 契约（RetrievalHit）不携带融合分数，阈值过滤在 `similarity_search(score_threshold=0.4)` 内部对 hybrid 分数生效。
- 与检索服务化 commit 旧场（21f43a0）相比存在 LLM 方差：rag misrefusal 0.200→0.100（answered 20→21）、faithfulness 0.991→0.968；agentic 侧 decision 指标稳定（两次均为零误拒）。方差方向不改变对照结论：单图误拒源于结构（一次检索即放弃），双图修复全部 FP 的同时付出 ~8-9× 成本。
- legacy 非误拒率（历史 HITL 口径）与新 refusal precision 并列不直接比较。

## 规模与并发实测

合成规模快照（与质量语料完全隔离：语料/parent store/Qdrant 均在 eval/scale_artifacts/，确定性生成 seed=20260910），单进程 uvicorn 对 12,098 child chunks 执行 3 并发（= AppService 槽位上限，容量边界）只读压测。完整数据见 [`scale-2026-09-10.md`](scale-2026-09-10.md) / `.json`。

| 容量目标（acceptance.yaml） | 实测 |
|---|---|
| 文档约 100 | 100（4.15 MB 语料，入库 66 min，MPS 嵌入）|
| child chunks 约 1 万 | 12,098（略超目标，如实记录）|
| 并发 3 | 3 worker（3 槽位）|
| 有效只读查询 ≥100 | 126/126（105 search + 21 evidence）|

| 阶段 | n | P50 | P95 | mean | wall | 错误 | busy |
|---|---:|---:|---:|---:|---:|---:|---:|
| 串行基线 | 36 | 0.128s | 0.142s | 0.111s | 4.0s | 0 | 0 |
| 3 并发 | 126 | 0.382s | 0.411s | 0.311s | 13.1s | 0 | 0 |

- **契约有效性 126/126（100%）**：逐条客户端复算 HTTP 200 + content_hash 与内容自洽 + span 有界；**0 错误、0 busy**（槽位等待从未触及 30s 上限）。
- 排队效应：并发/串行 P50 比 2.98、P95 比 2.89——3 worker 压满 3 槽位时的近线性等待，符合设计容量边界预期，无超限排队。
- 资源：服务进程 RSS ready 后 1.6 GB → 并发峰值 1.9 GB。
- 口径：延迟为客户端测量（含回环网络与 JSON 序列化）；合成语料为模板句，只测规模/并发行为，不代表质量语料检索效果；本机实测非受控环境。

## 结论

1. **接地场景（定位重心）**：两种模式共用同一检索与证据契约（Recall@5 1.000、引用 24/24 与 21/21 全部可回查），外部 Agent 用 search/get_context 取证据、用自己的模型回答，不承担本库的编排成本。
2. **委托场景的取舍**：agentic 修复了 rag 的全部 decision FP（拒答精度 0.556→1.000，含把真歧义题从硬拒转为澄清），代价是 P50 7.6×、token 11×、成本 8.6×；质量上 answer relevancy +0.080、context recall +0.195，context precision -0.191。双图不是免费午餐，是「决策正确性/证据覆盖」换「延迟/成本/上下文聚焦」。
3. **已知短板**（发布后路线已排期）：版本/有效期过滤（ch029 是现成靶子）、chunk 级噪声对单图的伤害（id9 型，双图可救）、挑战集全量 Recall@5 0.950 的 2 题缺口。