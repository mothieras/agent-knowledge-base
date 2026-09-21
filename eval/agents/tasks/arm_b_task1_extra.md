# 价值对照 任务1（臂 B 追加）——自主记录指引

你接入了共享知识库（MCP 工具 `save_entry` / `search_entries`，服务名 agentic-rag）。
任务过程中，把值得跨会话、跨 Agent 复用的信息自主记进去：

- 判断标准：其他会话/其他 Agent 的后续任务会不会用到
- type：事实/约定/经历 → memory；方法/排障结论 → knowledge
- scope：与 mdstats 相关的关联 {"kind":"projects","projects":["mdstats"]}，通用内容 {"kind":"global"}
- author 一律写 claude/2.1.268；写操作带 idempotency_key
- 候选：测试约定、零依赖约定、BOM 排障结论、CLI 参数风格
