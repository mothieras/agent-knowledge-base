# 数据目录说明

中文求职知识库语料。所有入库文档须在 `manifest.json` 登记，未登记的文档不进入检索语料。

## 目录结构

```text
data/
  raw/            原始素材（PDF、未清洗的下载文档）
  processed/      清洗后的 Markdown（要点改写、去页眉页脚）
  interview_docs/ 入库语料（Markdown，按 topic 组织）
  manifest.json   语料清单与 metadata
```

## 语料治理原则

1. 只收录自有笔记与授权内容；第三方内容先做要点改写，不存原文。
2. 面经类内容只抽取问题，不收录原帖正文。
3. 每篇文档在 manifest 登记来源、license 与处理方式。

## manifest.json schema

```json
{
  "documents": [
    {
      "source": "八股笔记/Redis-缓存三连.md",
      "license": "own",
      "doc_type": "tech_note",
      "topic": "java_backend",
      "version": "2026-08-14",
      "created_at": "2026-08-14",
      "effective_date": "2026-08-14",
      "expired_date": null,
      "priority": 10,
      "processing": "copied | rewritten | questions_only"
    }
  ]
}
```

topic 枚举：`java_backend` / `agent_runtime` / `rag` / `project_story` / `interview_strategy`

## 本轮新增字段与枚举

- `local_rel`：来源文件在 `findjob/` 下的相对路径，供 `data/sync_sources.py` 收料定位（不含本机绝对路径）。
- `processing` 枚举扩展：`copied | rewritten | questions_only | code_wrapped`（`code_wrapped` = 代码文件包装为 markdown 代码块入库）。
- `doc_type` 枚举扩展：`code_file`（与 `code_wrapped` 配对）。

## 隐私排除原则

个人隐私文档（简历、投递记录、面试准备话术）**不入库**：不进 data/、不登记 manifest。
