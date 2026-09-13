# RAG 问答评测基线 2026-09-13（mode=agentic，单次 decision 协议）

## 可复现绑定

| 项 | 值 |
|---|---|
| git commit | `9a0e389c2eb82f0a4c3f056e391ffca2b5628a53` |
| manifest hash (sha256:16) | `5c702f3c358fd1b5` |
| golden_set 行数 | 30 |
| 索引 points | 354 |
| index_id | `sha256:443c1a23da4b29733346709821a0e7728039bbc390d2cd6c9112dfbb5cabf775` |
| dense 模型 | Qwen/Qwen3-Embedding-0.6B |
| sparse 模型 | Qdrant/bm25 |
| LLM | deepseek-chat @ https://api.deepseek.com |
| judge | deepseek-chat |
| 检索参数 | k=7, threshold=0.4 |
| 执行时长 (s) | 630.8 |

- 执行 30 条 / 错误 0 条 / SKIP 0 条
- 生成层 judge: deepseek-chat，实现: ragas

## decision（单次终态）

| 指标 | 值 |
|---|---|
| refusal TP / FP / FN | 5 / 0 / 0 |
| refusal precision（TP/(TP+FP)） | 1.0 |
| refusal recall（TP/(TP+FN)） | 1.0 |
| legacy specificity（历史非误拒率） | 1.0 |
| misrefusal rate（明确可答题中 refused，n=20） | 0.0 |
| overclarify rate（明确可答题中 clarification，n=20） | 0.0 |
| clarification rate（歧义题，n=5） | 0.2 |
| ambiguous refused（歧义题被硬拒：计入 refusal FP，不计入 misrefusal） | 0 |

## 引用机械有效性（A-05）

- answered 24 条：引用全部可回查 24 条 / 存在无效引用 0 条 / 无引用 0 条

## 检索层

| 指标 | 值 |
|---|---|
| scored_items | 20 |
| Recall@5 | 1.000 |
| Recall@7 | 1.000 |
| MRR | 1.000 |
| Hit Rate | 1.000 |
| expired_hit_items | 0 |
| fixture_hit_items | 0 |

## 检索层分维度明细

### 按 题型

| 题型 | scored | Recall@5 | Recall@7 | MRR | Hit Rate |
|---|---|---|---|---|---|
| ambiguous_followup | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| concept_contrast | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| exact_term | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_hop | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| unanswerable | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| version_conflict | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
### 按 topic

| topic | scored | Recall@5 | Recall@7 | MRR | Hit Rate |
|---|---|---|---|---|---|
| data_preparation | 1 | 1.000 | 1.000 | 1.000 | 1.000 |
| evaluation | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| generation | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| graph_rag | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| indexing | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| rag_foundations | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| retrieval | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
## 生成层（answered 题）

| 指标 | 值 |
|---|---|
| faithfulness | 0.922 |
| answer_relevancy | 0.921 |
| context_precision | 0.584 |
| context_recall | 0.908 |

## 系统层（30 条执行均值）

| 指标 | 值 |
|---|---|
| latency P50/P95 (s) | 12.78 / 25.49 |
| 平均 input/output tokens | 12481 / 3018 |
| 平均 cost (¥/query) | 0.0086 |

## per-item

| id | category | topic | 得分/状态 |
|---|---|---|---|
| 1 | exact_term | rag_foundations | decision=answered, hits=2, cites=2, lat=5.24s |
| 2 | concept_contrast | rag_foundations | decision=answered, hits=7, cites=8, lat=12.78s |
| 3 | multi_hop | rag_foundations | decision=answered, hits=11, cites=10, lat=24.44s |
| 4 | ambiguous_followup | rag_foundations | decision=answered, hits=2, cites=13, lat=21.72s |
| 5 | unanswerable | data_preparation | decision=refused, hits=8, cites=0, lat=12.02s |
| 6 | unanswerable | data_preparation | decision=refused, hits=7, cites=0, lat=10.24s |
| 7 | multi_hop | data_preparation | decision=answered, hits=10, cites=20, lat=23.3s |
| 8 | ambiguous_followup | data_preparation | decision=answered, hits=14, cites=18, lat=26.52s |
| 9 | exact_term | indexing | decision=answered, hits=4, cites=10, lat=15.31s |
| 10 | concept_contrast | indexing | decision=answered, hits=2, cites=3, lat=6.7s |
| 11 | multi_hop | indexing | decision=answered, hits=6, cites=11, lat=15.71s |
| 12 | unanswerable | indexing | decision=refused, hits=12, cites=0, lat=10.5s |
| 13 | exact_term | retrieval | decision=answered, hits=3, cites=7, lat=11.57s |
| 14 | concept_contrast | retrieval | decision=answered, hits=4, cites=12, lat=8.1s |
| 15 | multi_hop | retrieval | decision=answered, hits=8, cites=14, lat=25.49s |
| 16 | ambiguous_followup | retrieval | decision=answered, hits=12, cites=28, lat=25.7s |
| 17 | unanswerable | retrieval | decision=refused, hits=6, cites=0, lat=8.49s |
| 18 | exact_term | generation | decision=answered, hits=5, cites=7, lat=13.41s |
| 19 | concept_contrast | generation | decision=answered, hits=5, cites=8, lat=16.49s |
| 20 | multi_hop | generation | decision=answered, hits=7, cites=6, lat=13.11s |
| 21 | unanswerable | generation | decision=refused, hits=5, cites=0, lat=9.52s |
| 22 | exact_term | evaluation | decision=answered, hits=5, cites=4, lat=9.22s |
| 23 | concept_contrast | evaluation | decision=answered, hits=7, cites=16, lat=14.29s |
| 24 | ambiguous_followup | evaluation | decision=answered, hits=11, cites=22, lat=20.11s |
| 25 | ambiguous_followup | evaluation | decision=clarification_required, hits=0, cites=0, lat=1.22s |
| 26 | version_conflict | graph_rag | decision=answered, hits=8, cites=3, lat=13.64s |
| 27 | version_conflict | graph_rag | decision=answered, hits=4, cites=2, lat=5.68s |
| 28 | version_conflict | graph_rag | decision=answered, hits=4, cites=2, lat=10.09s |
| 29 | version_conflict | graph_rag | decision=answered, hits=5, cites=2, lat=10.34s |
| 30 | version_conflict | graph_rag | decision=answered, hits=10, cites=3, lat=13.72s |

## 已知局限

- 单次 decision 协议：clarification_required 是终态，不再有 HITL 暂停/恢复
- legacy_specificity 为历史非误拒率口径（1 - false_refusals / answerable_n），与新 refusal precision 并列不直接比较
- 引用机械有效性只证明 span/hash/quote 可回查；语义支持性由冻结审计子集单独核查
- 0 条命中隐私排除守卫的题被 SKIP
- token/成本由回调全量采集