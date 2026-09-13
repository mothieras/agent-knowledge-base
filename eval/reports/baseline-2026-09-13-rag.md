# RAG 问答评测基线 2026-09-13（mode=rag，单次 decision 协议）

## 可复现绑定

| 项 | 值 |
|---|---|
| git commit | `3579e14bd51bfab50c3fb353204ed9b75daf5ed9` |
| manifest hash (sha256:16) | `5c702f3c358fd1b5` |
| golden_set 行数 | 30 |
| 索引 points | 354 |
| index_id | `sha256:443c1a23da4b29733346709821a0e7728039bbc390d2cd6c9112dfbb5cabf775` |
| dense 模型 | Qwen/Qwen3-Embedding-0.6B |
| sparse 模型 | Qdrant/bm25 |
| LLM | deepseek-chat @ https://api.deepseek.com |
| judge | deepseek-chat |
| 检索参数 | k=7, threshold=0.4 |
| 执行时长 (s) | 215.6 |

- 执行 30 条 / 错误 0 条 / SKIP 0 条
- 生成层 judge: deepseek-chat，实现: ragas

## decision（单次终态）

| 指标 | 值 |
|---|---|
| refusal TP / FP / FN | 5 / 4 / 0 |
| refusal precision（TP/(TP+FP)） | 0.556 |
| refusal recall（TP/(TP+FN)） | 1.0 |
| legacy specificity（历史非误拒率） | 0.84 |
| misrefusal rate（明确可答题中 refused，n=20） | 0.1 |
| overclarify rate（明确可答题中 clarification，n=20） | 0.0 |
| clarification rate（歧义题，n=5） | 0.0 |
| ambiguous refused（歧义题被硬拒：计入 refusal FP，不计入 misrefusal） | 2 |

## 引用机械有效性（A-05）

- answered 21 条：引用全部可回查 21 条 / 存在无效引用 0 条 / 无引用 0 条

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
| faithfulness | 0.959 |
| answer_relevancy | 0.898 |
| context_precision | 0.815 |
| context_recall | 0.722 |

## 系统层（30 条执行均值）

| 指标 | 值 |
|---|---|
| latency P50/P95 (s) | 1.75 / 3.49 |
| 平均 input/output tokens | 980 / 398 |
| 平均 cost (¥/query) | 0.0009 |

## per-item

| id | category | topic | 得分/状态 |
|---|---|---|---|
| 1 | exact_term | rag_foundations | decision=answered, hits=3, cites=1, lat=1.8s |
| 2 | concept_contrast | rag_foundations | decision=answered, hits=2, cites=3, lat=2.24s |
| 3 | multi_hop | rag_foundations | decision=refused, hits=2, cites=0, lat=0.89s |
| 4 | ambiguous_followup | rag_foundations | decision=answered, hits=2, cites=3, lat=2.0s |
| 5 | unanswerable | data_preparation | decision=refused, hits=2, cites=0, lat=0.97s |
| 6 | unanswerable | data_preparation | decision=refused, hits=3, cites=0, lat=1.43s |
| 7 | multi_hop | data_preparation | decision=answered, hits=3, cites=6, lat=2.82s |
| 8 | ambiguous_followup | data_preparation | decision=refused, hits=1, cites=0, lat=1.16s |
| 9 | exact_term | indexing | decision=refused, hits=1, cites=0, lat=0.98s |
| 10 | concept_contrast | indexing | decision=answered, hits=1, cites=4, lat=1.99s |
| 11 | multi_hop | indexing | decision=answered, hits=2, cites=9, lat=3.85s |
| 12 | unanswerable | indexing | decision=refused, hits=2, cites=0, lat=0.81s |
| 13 | exact_term | retrieval | decision=answered, hits=3, cites=3, lat=3.49s |
| 14 | concept_contrast | retrieval | decision=answered, hits=3, cites=10, lat=4.5s |
| 15 | multi_hop | retrieval | decision=answered, hits=3, cites=5, lat=2.76s |
| 16 | ambiguous_followup | retrieval | decision=answered, hits=1, cites=2, lat=1.59s |
| 17 | unanswerable | retrieval | decision=refused, hits=2, cites=0, lat=0.7s |
| 18 | exact_term | generation | decision=answered, hits=3, cites=4, lat=2.35s |
| 19 | concept_contrast | generation | decision=answered, hits=3, cites=5, lat=3.15s |
| 20 | multi_hop | generation | decision=answered, hits=1, cites=1, lat=1.51s |
| 21 | unanswerable | generation | decision=refused, hits=3, cites=0, lat=0.71s |
| 22 | exact_term | evaluation | decision=answered, hits=2, cites=1, lat=1.51s |
| 23 | concept_contrast | evaluation | decision=answered, hits=4, cites=4, lat=2.59s |
| 24 | ambiguous_followup | evaluation | decision=answered, hits=2, cites=2, lat=1.7s |
| 25 | ambiguous_followup | evaluation | decision=refused, hits=2, cites=0, lat=1.16s |
| 26 | version_conflict | graph_rag | decision=answered, hits=4, cites=1, lat=1.69s |
| 27 | version_conflict | graph_rag | decision=answered, hits=3, cites=7, lat=2.69s |
| 28 | version_conflict | graph_rag | decision=answered, hits=2, cites=1, lat=1.75s |
| 29 | version_conflict | graph_rag | decision=answered, hits=2, cites=1, lat=1.88s |
| 30 | version_conflict | graph_rag | decision=answered, hits=3, cites=8, lat=3.38s |

## 已知局限

- 单次 decision 协议：clarification_required 是终态，不再有 HITL 暂停/恢复
- legacy_specificity 为历史非误拒率口径（1 - false_refusals / answerable_n），与新 refusal precision 并列不直接比较
- 引用机械有效性只证明 span/hash/quote 可回查；语义支持性由冻结审计子集单独核查
- 0 条命中隐私排除守卫的题被 SKIP
- token/成本由回调全量采集