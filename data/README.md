# 数据目录说明

本目录保存由 `manifest.json` 治理的公开 RAG 教程语料。未登记文件不会进入检索库；同步时还会删除已不在清单中的旧文件，避免被移除的隐私语料残留。

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
```

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

## 隐私排除原则

个人简历、投递记录、面试经历原文、求职目标和面试准备话术均不入库，也不登记到 manifest。主分支历史已清除旧私有语料；同步脚本会继续删除已退出清单的陈旧文件。旧克隆、fork 或平台缓存不受本地重写控制，相关持有者仍须删除旧副本。
