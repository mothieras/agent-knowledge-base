# S4b（Pi，后提交方——竞争失败）

共享库条目 `entry_01M31DR62Z47RH569YRKR04RAJ`（mdstats 项目约定）记录了端口约定。
你在本会话早些时候读过它，手里的缓存修订号是 3。

任务：把「生产环境禁止 embedded 模式（embedded 只用于本地演示与评测）」补充进
这条条目，author 写 pi/0.86.0，直接以你缓存的 revision 3 作为 expected_revision
提交 revise_entry。

预期你会收到 revision_conflict（另一个客户端已提交了更新）。收到冲突后：
读错误载荷里的 current 条目，把你要加的内容合并到对方版本上，以 current.revision
重试——不允许直接覆盖对方提交。完成后报告：冲突发生与否、最终修订号、合并结果。
