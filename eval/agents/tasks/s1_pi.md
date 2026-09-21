# S1 任务（Pi）——修复 BOM bug 并自主记录

你在数据平台团队的 `mdstats` 项目里工作（纯标准库 CLI：统计 Markdown 文件行数与词数）。

## 任务

1. 运行 `python3 -m unittest` 看当前测试状态——有一个失败用例
2. 修复它（提示：`sample.md` 是 UTF-8 with BOM，`open(encoding='utf-8')` 不会剥除 BOM）
3. 全部测试通过后，确认 `python3 mdstats.py sample.md --json` 输出正确
4. 不要引入任何第三方依赖（项目约定：零依赖、只用标准库）

## 共享库记录（重要）

你接入了共享知识库（MCP 工具 `save_entry` / `search_entries` 等，服务名 agentic-rag）。
在任务过程中，把**值得跨会话、跨 Agent 复用的信息**自主记进去：

- 判断标准：其他会话/其他 Agent 的后续任务会不会用到？
  - 用得到 → 记共享库；本次任务的临时状态 → 不记
- type 按用途选：事实/约定/经历 → memory；方法/排障结论/操作知识 → knowledge
- scope：与 mdstats 项目相关的关联 `{"kind":"projects","projects":["mdstats"]}`；
  通用内容用 `{"kind":"global"}`；涉及多个项目可用多标签
- author 一律写 `pi/0.86.0`
- 每次写操作建议带 `idempotency_key`（如 `pi-s1-<序号>`），防网络重试重复写入
- 值得记的候选：项目的测试约定（unittest）、零依赖约定、BOM 排障结论（为什么
  `splitlines` 没救回 BOM、修复方式）、CLI 参数风格约定等

开始吧。完成后报告：修了什么、测试结果、记了哪些条目（ID 与标题）。
