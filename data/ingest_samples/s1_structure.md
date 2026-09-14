# 混合结构样例 S1：标题、代码、表格与列表

本文件是入库管道样例集的 S1（结构保留正样例）。它刻意同时包含多级标题、围栏代码块、Markdown 表格、有序与无序列表以及中英混排段落，用于验证规范化与父子分块不会丢失任何预登记的结构锚点（锚点清单见 `ingest_samples/manifest.json`）。This paragraph mixes English sentences with 中文句子 to cover bilingual tokenization in both the splitter and the embedder.

样例集本身是项目自有内容（MIT），不参与主 manifest 与 `sync_sources.py` 的第三方语料治理；本文件的全部文字只为管道验证服务，不承载任何真实业务语义。所有锚点句都以可检索的稳定子串形式出现，例如这一句：hybrid search combines dense vectors with sparse lexical signals。

## 检索链路概览

检索增强生成（Retrieval-Augmented Generation, RAG）把模型的参数化知识与外部语料中的非参数化知识结合起来。检索质量决定了生成质量的上限：召回缺失的证据无法靠生成模型弥补，而错误命中的证据会把模型带向错误结论。因此工程上通常把检索链路拆成召回、融合、过滤三个阶段分别度量。

召回阶段的双通道设计是当前系统的基线形态：

- 候选召回：dense embedding 与 BM25 双通道并行
- 融合排序：Reciprocal Rank Fusion (RRF) 合并两路候选
- 阈值过滤：score threshold 过滤低置信命中

单通道各有盲区：dense 通道对精确词形与低频专名不敏感，sparse 通道对同义改写无能为力。双通道经 RRF 融合后，两类查询都能落在可接受的召回区间。融合之后的阈值过滤是最后一道闸门，低于阈值的命中宁可丢弃也不进入上下文，避免噪声证据污染生成。

一次完整的离线入库按以下顺序执行（ordered list 锚点）：

1. 文档规范化（normalization）：统一转 Markdown 落盘
2. 父子分块（parent/child chunking）：父块保完整语义，子块保召回粒度
3. 向量入库（indexing）：子块向量化并写入 collection
4. 混合检索（hybrid retrieval）：查询期双通道召回并融合

本节文字同时承担一个工程目的：把该 H2 节的长度撑到接近父块最小尺寸（2000 字符），从而在样例上实际触发小块合并逻辑，而不是让每个标题节都独立成块。合并行为会把相邻小节的 metadata 以标题链的形式拼接，这正是 T5 边界质量检查要审的对象；本样例先把材料备好。

## 代码块样例

下面是一个围栏代码块。代码行锚点必须原样出现在规范化产物中，任何一行丢失都视为结构保留缺陷：

```python
def rrf_fusion(dense_hits, sparse_hits, k=60):
    scores = {}
    for rank, hit in enumerate(dense_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
    for rank, hit in enumerate(sparse_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])
```

代码块在分块时的风险点在于被大块拆分逻辑从中间切断：递归拆分优先在空白行处下刀，围栏代码块内部通常没有空白行，因此多数情况下能整体保留在一个父块内。若未来调整拆分参数，本样例的代码行锚点就是回归守卫。The code block above is intentionally kept short enough to fit within a single parent chunk.

当前系统的双通道配置如下（表格锚点）：

| 通道 | 模型 | 用途 |
| --- | --- | --- |
| dense | Qwen3-Embedding-0.6B | 语义召回 |
| sparse | Qdrant/bm25 | 词法召回 |

表格行锚点：Qdrant/bm25。表格在 MarkdownHeaderTextSplitter 眼里只是普通文本，真正的风险同样在 PDF 规范化一侧；Markdown 样例里的表格锚点主要作为 md 原样复制路径的守卫存在。

### 数值稳定细节

RRF 的常数 k 越大，排名差异对融合分数的影响越平滑：k=60 时第 1 名与第 2 名的贡献差约为 1/61 与 1/62 之差，量级远小于原始分差。这使得 RRF 对两通道分数尺度不敏感，无需先做分数归一化。代价是极端强信号（某通道唯一命中）也被压平，因此阈值过滤必须基于融合后的绝对分数而不是排名。

k 的选择没有理论最优值，工程惯例在 20–100 之间取样。本系统取 60 并在评测中冻结：任何对 k 的调整都属于检索策略变更，须走全量评测对照，不能只看样例行为。

## 无标题长文本的对照

本节刻意不加 H3 子标题，模拟结构较弱的文档主体。当一节文字足够长时，它会先被并入父块，再在超过 4000 字符时被递归拆分。拆分点优先选择空白行边界，其次单换行，最后才是空格硬切。硬切只应出现在完全没有边界的极端文本上；若在真实语料上频繁观察到空格硬切，说明该语料的结构弱于分块参数的假设，应当调整参数而不是接受碎片。

段落之间以空行分隔是本样例贯穿始终的约定。空行既是 Markdown 的段落边界，也是拆分器最优先的切分锚点；保持这个约定能让大多数拆分发生在语义自然的位置。This is the last English sentence anchor of S1: chunk boundaries shall respect paragraph separators.
