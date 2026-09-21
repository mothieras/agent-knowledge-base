# S2 任务（Claude Code）——跨客户端复用、修订与历史回查

你在数据平台团队的 `mdstats` 项目里工作。这是一个**全新的会话**——你没有本项目
的历史上下文，但团队有一个共享知识库（MCP 工具，服务名 agentic-rag：
`search_entries` / `get_entry` / `revise_entry` / `list_entry_revisions` 等）。

## 任务

1. 为 mdstats 增加 `--lines-only` 参数：只输出行数（文本模式输出 `N 行`，
   `--json` 模式输出 `{"file": ..., "lines": N}`），与既有参数可组合
2. 动手写代码**之前**，先用 `search_entries` 检索共享库：
   - 项目范围：projects 传 ["mdstats"]（会自动包含全局条目）
   - 关键词：约定、CLI、端口、测试
   - 遵循检索到的约定（零依赖、CLI 风格、开发端口等）；项目内的 README 也可以看
3. 补一条 unittest 用例覆盖 `--lines-only`，运行 `python3 -m unittest` 全绿
4. 修订一条过时条目：用户告知「开发端口约定已从 18000 改为 18080」。找到共享库里
   记录端口的那条条目，用 `revise_entry` 修订它。**故意先提交一次错误的
   expected_revision**（用你读到的修订号减 1），观察冲突错误里的 current 条目，
   再以 current.revision 重试成功——这是团队要求的冲突处理演练
5. 用 `list_entry_revisions` 回查该条目的完整修订历史，确认你的修订在里面

完成后报告：检索到什么约定、--lines-only 的实现与测试结果、修订的条目 ID、
冲突处理过程、历史回查结果。
