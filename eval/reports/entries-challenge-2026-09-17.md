# 条目检索挑战集报告（PHASE2 T3）

- 日期：2026-09-17（数据集与门槛见 entries_challenge_contract.yaml，运行前 sha256 校验通过）
- 实现：SQLite FTS5 + jieba cut_for_search 空格预分词 + bm25；无 embedding/rerank/生成
- 分母：26 题（独立报告，不与 30 题 golden / 40 题检索挑战集混算）

| 指标 | 实测 | 门槛 | 判定 |
|---|---|---|---|
| recall_at_5 | 1.0000 | ≥ 0.85 | PASS |
| mrr_at_10 | 1.0000 | ≥ 0.65 | PASS |
| exact_term_recall_at_5 | 1.0000 | ≥ 1.00 | PASS |

## 分类别 recall@5

| 类别 | 题数 | recall@5 |
|---|---|---|
| all_projects | 3 | 1.0000 |
| distractor_dense | 2 | 1.0000 |
| exact_term | 5 | 1.0000 |
| mixed_lang | 5 | 1.0000 |
| paraphrase | 6 | 1.0000 |
| project_scope | 4 | 1.0000 |
| type_filter | 1 | 1.0000 |

未命中题（recall@5 < 1）：
- 无

逐题明细：`eval/reports/entries-challenge-2026-09-17.jsonl`

**结论：PASS（三门槛全过）**
