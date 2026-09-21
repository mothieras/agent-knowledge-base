# eval/agents — 真实 Agent 验证材料（T6/T7）

阶段 2 收尾验证的冻结材料与脚本（spec：`docs/spec/PHASE2-T67.md`；实测报告：
`eval/reports/agents-2026-09-21.md`）。运行时产物（索引/条目库/会话转录/日志）
在 `runtime/`、`artifacts/`（gitignore，不提交）。

## 结构

- `corpus/`：T7 场景语料（发布流程、环境约束）——隔离实例的额外入库文档；
  文档更新场景 = 修改这些文件后重跑 `scripts/build_runtime.py`（旧版本永久保留）
- `tasks/`：各场景与价值对照两臂的冻结任务 prompt（S1/S2/S4/T7a/T7b、臂 A/B）
- `scratch_project/`：S1/S2/S4 的任务工程 `mdstats`（初始态：UTF-8 BOM bug、
  1/5 测试失败；当前内容为场景执行后的状态）
- `scratch_project_arm_{a,b}/`：价值对照两臂的独立副本（执行后状态；初始态
  与 scratch_project 初始态一致：BOM bug + 1 失败用例）
- `scripts/`：`build_runtime.py`（隔离索引构建 + 文档注册）、`audit_sessions.py`
  （Claude Code 会话转录审计）、`settings-claude.example.json`（无 hook 的隔离
  Claude settings 模板；真实 settings 为本地文件不提交）

## 运行要点（实测记录）

- 服务：隔离环境变量（见报告 §7）指向 `runtime/`，`DEMO_API_TOKEN` 设置后
  HTTP/MCP 统一 Bearer
- Pi 无头：`pi -p --mcp-config <repo>/.mcp.json --tools "mcp,bash,read,edit,write"`
  —— pi-mcp-adapter 不做祖先目录发现，`--mcp-config` 必须显式指定
- Claude Code 无头：`--settings scripts/settings-claude.json --allowedTools
  "Bash,Read,Edit,Write,mcp__agentic-rag__*" --dangerously-skip-permissions`；
  项目 `.mcp.json` 需 `enabledMcpjsonServers` 预批准（`~/.claude.json`），
  `--session-id` 须为合法 UUID（`--resume` 续会话同用）
- 环境 gotcha：跑任何加载 HF 模型/embedding 的进程用 `env -u ALL_PROXY -u all_proxy`
