# 数据目录说明

本目录是本仓库的**示例语料与评测数据**：保存由 `manifest.json` 治理的公开 RAG 教程语料，用于演示与评测检索质量。服务本身语料无关——入库与证据链路由 manifest 驱动，可整体替换为其他语料并重跑评测。未登记文件不会进入检索库；同步时会删除已不在清单中的旧文件，保持本地语料与清单一致。

## 当前语料

当前选取 [`mothieras/all-in-rag`](https://github.com/mothieras/all-in-rag) 固定修订版中的 14 篇中文 Markdown，覆盖：

- `rag_foundations`
- `data_preparation`
- `indexing`
- `retrieval`
- `generation`
- `evaluation`
- `graph_rag`

环境安装、英文重复版、示例菜谱和项目宣传页不入库。来源修订、逐文件 URL、SHA-256 与许可见 [`manifest.json`](manifest.json)，归属说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 目录结构

```text
data/
  raw/             从固定来源同步的原始 Markdown
  processed/       需要转换时生成的 Markdown
  interview_docs/  实际入库语料（Markdown，保持来源目录结构）
  manifest.json    来源、许可、版本、topic 与完整性清单
  fixtures/        项目自有的受治理版本冲突 fixture（见下）
  ingest_samples/  项目自有的入库管道样例集（见下）
```

## 受治理 fixture

[`fixtures/`](fixtures/) 是项目自有、随仓库 MIT 许可分发的虚构政策文档（"星辰科技"为虚构主体），与第三方语料刻意异域。当前两对 active/expired 版本（API 限流政策、数据保留政策），用于验证 manifest metadata（version/effective_date/expired_date/priority）全链路传播，并为版本冲突评测提供素材。

- 由 [`fixtures/manifest.json`](fixtures/manifest.json) 独立治理，不参与 `sync_sources.py` 的第三方 SHA 同步与清理。
- 入库时与第三方语料进同一 collection（`ingest_corpus.py` 自动合并）。
- 静态契约由 `tests/test_fixtures.py` 守护：每个 topic 恰一 expired 一 active、active 优先级更高且生效日期接续 expired 失效日期。

## 入库管道样例集

[`ingest_samples/`](ingest_samples/) 是项目自有（MIT）的入库管道样例集（PHASE1 冻结的 S1–S10）：正样例验证规范化与父子分块的结构保留、span 精确性与可复现性，负样例固定各类失败终态的目标行为（空文本、重复提交、同名冲突、不支持格式、非 UTF-8 编码）。

- 由 [`ingest_samples/manifest.json`](ingest_samples/manifest.json) 独立治理：逐文件登记用途、预登记结构锚点与 SHA-256；不进主 manifest、不参与 `sync_sources.py` 的同步与清理。
- 两级验证：Level A（`tests/test_ingest_samples_level_a.py`，无模型无索引，进 CI）直调分块与规范化；Level B（`eval/ingest_samples/run_level_b.py`，本地手动跑）经 scratch 索引端到端入库，不触碰质量索引。
- PDF 样例（S3/S4）由 `ingest_samples/generate_pdf_samples.py` 用 pymupdf 生成一次并登记 SHA，脚本仅作来源记录。

## 同步

直接从清单中的固定 GitHub revision 下载并校验：

```bash
python3 data/sync_sources.py
```

离线时可指向已检出的 `all-in-rag` 仓库：

```bash
SOURCES_ROOT=/path/to/all-in-rag python3 data/sync_sources.py
```

同步采用清单镜像语义：先校验全部来源，再覆盖清单文件并清理 `raw/`、`processed/`、`interview_docs/` 中未登记的旧文件。

## 语料治理原则

1. 只收录许可明确的公开内容；原样复制时保留来源、固定版本和第三方许可说明。
2. 来源不明或不允许再分发的内容不保存原文；确有需要时只保留自行撰写的要点总结。
3. 每篇文档必须登记 `source_url`、`sha256`、`license`、`topic`、版本和处理方式。
4. 根目录 MIT License 不覆盖第三方语料；当前语料遵循 CC BY-NC-SA 4.0，仅可按其条款使用。
