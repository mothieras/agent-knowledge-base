# 检索挑战集评测 2026-09-10

## 可复现绑定

| 项 | 值 |
|---|---|
| git commit | `ca3cee45bb03104ca597cfafc1f3fa2659e529d6` |
| index_id | `sha256:0df9fbfaeecaaf2bef1f3a78b2db71aad767935a98aafaf398792fcdc2a34325` |
| parent/child chunks | 46 / 354 |
| dense / sparse | Qwen/Qwen3-Embedding-0.6B / Qdrant/bm25 |
| k / threshold | 7 / 0.4 |
| 执行 | 40 条 / 错误 0 条 |

## 总指标

| 指标 | 值 |
|---|---|
| scored_items | 40 |
| Recall@5 | 0.950 |
| Recall@7 | 0.950 |
| MRR | 0.933 |
| Hit Rate | 0.950 |
| source_precision@5 | 0.215 |
| nDCG@5 | 0.934 |
| hard_negative 泄漏题数 | 2 |

## 回归锚点（20 条 golden 计分题）

| 指标 | Day 1 基线 | 本次 |
|---|---|---|
| Recall@5 | 1.000 | 1.000 |
| anchor 题数 | 20 | 20 |

## 分题型

| category | scored | Recall@5 | Recall@7 | MRR | leak_items |
|---|---|---|---|---|---|
| concept_contrast | 5 | 1.000 | 1.000 | 1.000 | 0 |
| exact_term | 9 | 1.000 | 1.000 | 1.000 | 0 |
| hard_negative | 4 | 0.750 | 0.750 | 0.583 | 2 |
| multi_hop | 9 | 1.000 | 1.000 | 1.000 | 0 |
| paraphrase | 4 | 0.750 | 0.750 | 0.750 | 0 |
| version_conflict | 9 | 1.000 | 1.000 | 1.000 | 0 |

## 契约机械校验

| 项 | 值 |
|---|---|
| 命中数 | 83 |
| span/hash/evidence 全部通过 | 83 |
| 契约失败数 | 0 |

## per-item

| id | category | origin | Recall@5 | 状态 |
|---|---|---|---|---|
| ch001 | exact_term | golden-1 | ✓ | ok |
| ch002 | concept_contrast | golden-2 | ✓ | ok |
| ch003 | multi_hop | golden-3 | ✓ | ok |
| ch004 | multi_hop | golden-7 | ✓ | ok |
| ch005 | exact_term | golden-9 | ✓ | ok |
| ch006 | concept_contrast | golden-10 | ✓ | ok |
| ch007 | multi_hop | golden-11 | ✓ | ok |
| ch008 | exact_term | golden-13 | ✓ | ok |
| ch009 | concept_contrast | golden-14 | ✓ | ok |
| ch010 | multi_hop | golden-15 | ✓ | ok |
| ch011 | exact_term | golden-18 | ✓ | ok |
| ch012 | concept_contrast | golden-19 | ✓ | ok |
| ch013 | multi_hop | golden-20 | ✓ | ok |
| ch014 | exact_term | golden-22 | ✓ | ok |
| ch015 | concept_contrast | golden-23 | ✓ | ok |
| ch016 | version_conflict | golden-26 | ✓ | ok |
| ch017 | version_conflict | golden-27 | ✓ | ok |
| ch018 | version_conflict | golden-28 | ✓ | ok |
| ch019 | version_conflict | golden-29 | ✓ | ok |
| ch020 | version_conflict | golden-30 | ✓ | ok |
| ch021 | exact_term | new | ✓ | ok |
| ch022 | exact_term | new | ✓ | ok |
| ch023 | exact_term | new | ✓ | ok |
| ch024 | exact_term | new | ✓ | ok |
| ch025 | paraphrase | new | ✓ | ok |
| ch026 | paraphrase | new | ✗ | ok |
| ch027 | paraphrase | new | ✓ | ok |
| ch028 | paraphrase | new | ✓ | ok |
| ch029 | hard_negative | new | ✗ | ok |
| ch030 | hard_negative | new | ✓ | ok |
| ch031 | hard_negative | new | ✓ | ok |
| ch032 | hard_negative | new | ✓ | ok |
| ch033 | multi_hop | new | ✓ | ok |
| ch034 | multi_hop | new | ✓ | ok |
| ch035 | multi_hop | new | ✓ | ok |
| ch036 | multi_hop | new | ✓ | ok |
| ch037 | version_conflict | new | ✓ | ok |
| ch038 | version_conflict | new | ✓ | ok |
| ch039 | version_conflict | new | ✓ | ok |
| ch040 | version_conflict | new | ✓ | ok |

## 延迟（40 条）

- P50/P95: 0.03s / 0.04s

> 契约校验失败或召回下降不得以轨迹漂移解释；直接检索适配必须保持有序命中 ID/来源/内容。