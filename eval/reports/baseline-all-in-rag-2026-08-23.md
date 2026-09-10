# Agentic-RAG 评测基线 2026-08-23

## 可复现绑定

| 项 | 值 |
|---|---|
| git commit | `1b3bfdabd00fd2a47bb216ad10d74e02f2459013` |
| manifest hash (sha256:16) | `6c61ae6e110a622d` |
| golden_set 行数 | 30 |
| 索引 points | 339 |
| dense 模型 | Qwen/Qwen3-Embedding-0.6B |
| sparse 模型 | Qdrant/bm25 |
| LLM | deepseek-chat @ https://api.deepseek.com |
| judge | deepseek-chat |
| 检索参数 | k=7, threshold=0.4 |
| 执行时长 (s) | 674.9 |

- 执行 30 条 / 错误 0 条 / SKIP 0 条
- 生成层 judge: deepseek-chat，实现: ragas

## 检索层

| 指标 | 值 |
|---|---|
| scored_items | 20 |
| Recall@5 | 1.000 |
| Recall@7 | 1.000 |
| MRR | 0.900 |
| Hit Rate | 1.000 |
| section_hit_rate | n/a |
| expired_hit_items | 0 |


## 检索层分维度明细

### 按 题型

| 题型 | scored | Recall@5 | Recall@7 | MRR | Hit Rate |
|---|---|---|---|---|---|
| ambiguous_followup | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| concept_contrast | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| exact_term | 5 | 1.000 | 1.000 | 0.900 | 1.000 |
| multi_hop | 5 | 1.000 | 1.000 | 0.800 | 1.000 |
| unanswerable | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| version_conflict | 5 | 1.000 | 1.000 | 0.900 | 1.000 |
### 按 topic

| topic | scored | Recall@5 | Recall@7 | MRR | Hit Rate |
|---|---|---|---|---|---|
| data_preparation | 1 | 1.000 | 1.000 | 1.000 | 1.000 |
| evaluation | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| generation | 3 | 1.000 | 1.000 | 0.833 | 1.000 |
| graph_rag | 5 | 1.000 | 1.000 | 0.900 | 1.000 |
| indexing | 3 | 1.000 | 1.000 | 0.833 | 1.000 |
| rag_foundations | 3 | 1.000 | 1.000 | 0.833 | 1.000 |
| retrieval | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
## 生成层（排除拒答/澄清题）

| 指标 | 值 |
|---|---|
| faithfulness | 0.9383790263151319 |
| answer_relevancy | 0.9055151498743642 |
| context_precision | 0.6895059523498063 |
| context_recall | 0.9083333333333332 |

## 拒答 / 澄清

| 指标 | 值 |
|---|---|
| refusal_recall | 0.800 |
| refusal_precision | 0.840 |
| clarification_rate | 0.600 |

## 系统层（30 条执行均值）

| 指标 | 值 |
|---|---|
| latency P50/P95 (s) | 14.65 / 21.89 |
| 平均 input/output tokens | 9719 / 1777 |
| 平均 cost (¥/query) | 0.0057 |
| 平均 tool_calls | 3.8 |

## per-item

| id | category | topic | 得分/状态 |
|---|---|---|---|
| 1 | exact_term | rag_foundations | clarified=False, hits=1, lat=4.72s |
| 2 | concept_contrast | rag_foundations | clarified=False, hits=7, lat=16.28s |
| 3 | multi_hop | rag_foundations | clarified=False, hits=8, lat=31.14s |
| 4 | ambiguous_followup | rag_foundations | clarified=False, hits=12, lat=21.89s |
| 5 | unanswerable | data_preparation | clarified=False, hits=11, lat=14.65s |
| 6 | unanswerable | data_preparation | clarified=False, hits=17, lat=14.16s |
| 7 | multi_hop | data_preparation | clarified=False, hits=8, lat=16.63s |
| 8 | ambiguous_followup | data_preparation | clarified=True, hits=0, lat=0.88s |
| 9 | exact_term | indexing | clarified=False, hits=6, lat=15.04s |
| 10 | concept_contrast | indexing | clarified=False, hits=2, lat=12.32s |
| 11 | multi_hop | indexing | clarified=False, hits=7, lat=19.43s |
| 12 | unanswerable | indexing | clarified=False, hits=10, lat=13.33s |
| 13 | exact_term | retrieval | clarified=False, hits=2, lat=14.13s |
| 14 | concept_contrast | retrieval | clarified=False, hits=3, lat=16.91s |
| 15 | multi_hop | retrieval | clarified=False, hits=7, lat=18.11s |
| 16 | ambiguous_followup | retrieval | clarified=True, hits=0, lat=1.01s |
| 17 | unanswerable | retrieval | clarified=False, hits=10, lat=13.85s |
| 18 | exact_term | generation | clarified=False, hits=8, lat=18.83s |
| 19 | concept_contrast | generation | clarified=False, hits=9, lat=21.59s |
| 20 | multi_hop | generation | clarified=False, hits=26, lat=23.93s |
| 21 | unanswerable | generation | clarified=False, hits=15, lat=19.22s |
| 22 | exact_term | evaluation | clarified=False, hits=2, lat=12.31s |
| 23 | concept_contrast | evaluation | clarified=False, hits=9, lat=15.78s |
| 24 | ambiguous_followup | evaluation | clarified=True, hits=0, lat=1.26s |
| 25 | ambiguous_followup | evaluation | clarified=True, hits=0, lat=1.03s |
| 26 | version_conflict | graph_rag | clarified=False, hits=4, lat=16.86s |
| 27 | version_conflict | graph_rag | clarified=False, hits=6, lat=8.14s |
| 28 | version_conflict | graph_rag | clarified=False, hits=1, lat=11.78s |
| 29 | version_conflict | graph_rag | clarified=False, hits=2, lat=13.9s |
| 30 | version_conflict | graph_rag | clarified=False, hits=5, lat=18.28s |

## 已知局限

- 拒答未显式实现（T6 规划中）；基座 agent 在检索无命中时自然拒答（实测 refusal_recall=0.800 / precision=0.840，语料覆盖缺口的误拒留 T5 badcase 复核）
- 澄清题为单轮口径（golden set 无澄清回复脚本）
- 0 条命中隐私排除守卫的题被 SKIP
- manifest 版本字段尚未接入检索过滤；当前公开语料通过单一 Git revision 固定版本
- token/成本由回调全量采集（含子图内部调用）