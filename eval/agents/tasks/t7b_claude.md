# T7b（Claude Code）——检索派生条目并回查来源

你接入了共享知识库（MCP 工具，服务名 agentic-rag）。

任务：验证一条派生条目的来源回查。

1. 用 `search_entries` 搜索共享库，关键词「回滚」（all_projects=true）
2. 找到那条关于发布流程回滚规则的 knowledge 条目，读它的 source.document
   （doc_id、version、span）
3. 用 `read_document(doc_id, version, offset=span_start, limit=span_end-span_start)`
   回查来源原文，确认窗口内容与条目 body 描述的事实一致
4. 用 `list_document_versions(doc_id)` 检查该文档当前有几个版本
5. 报告：条目 ID、回查窗口内容摘要、一致性结论、文档版本数
