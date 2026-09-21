# T7a（Pi）——从文档提取派生条目并关联来源

你接入了共享知识库（MCP 工具，服务名 agentic-rag，含语料检索与条目工具）。

任务：从团队语料中提取一条可复用的 Knowledge 条目，并关联来源文档。

1. 用 `search_knowledge` 检索语料，关键词「发布流程」「回滚」，找到团队发布流程文档
2. 用 `resolve_document` 把命中的 source 解析为 doc_id（记下 version）
3. 用 `read_document` 读原文窗口，确定你要提取内容在全文中的精确字符区间
4. 用 `save_entry` 保存派生条目：
   - type=knowledge，author=pi/0.86.0，scope={"kind":"global"}
   - body 提炼「团队发布流程的回滚规则」（含 5 分钟决策时限、回滚方式、24 小时
     事故简报要求），不照抄全文
   - source.document 必须带上 {doc_id, version, span_start, span_end}（span 用
     步骤 3 算出的全文字符区间，覆盖你依据的段落）
   - idempotency_key=pi-t7-release-rollback
5. 报告：条目 ID、引用的 doc_id/version/span、body 内容
