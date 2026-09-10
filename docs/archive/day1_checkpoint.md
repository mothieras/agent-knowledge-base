# Day 1 检查点报告：范围与评测契约冻结

> 日期：2026-09-07 ｜ git commit：`c6eb42379afd9d1a9974af5bf4b977796bdff6f2`（工作树含未提交的 docs/PRD、DESIGN、ROADMAP 及本报告新增资产）
> 检查点依据：[ROADMAP](../ROADMAP.md) Day 1；本报告是 Day 1 必须留下的证据，不是能力完成声明。

## 1. 冻结产物

| 产物 | 路径 | SHA-256 |
|---|---|---|
| 原 30 题 golden set | `eval/golden_set.jsonl` | `b14b1caa…c1ea88` |
| 检索挑战集（40 题） | `eval/challenge_set.jsonl` | `2db5a9b4…92542` |
| 语义审计子集（11 题） | `eval/audit_subset.jsonl` | `d369d8a7…9e7959` |
| 评测冻结契约 | `eval/acceptance.yaml` | `ba6ae519…50e8df` |
| Day 1 基线报告 | `eval/reports/baseline-2026-09-07.md` | `9dbe002e…a94cd0` |
| Day 1 基线 per-item | `eval/reports/baseline-2026-09-07.jsonl` | `0af89dc5…5ef35e` |

语料绑定：`data/manifest.json` `95cf6162…77e11b7b`；`data/fixtures/manifest.json` `38c4b644…057ad30`；第三方语料 `mothieras/all-in-rag@04b8ea2`；索引 354 points（Qwen/Qwen3-Embedding-0.6B + Qdrant/bm25）。

## 2. 基线重跑（绑定当前 commit）

在行为未改的 HEAD 上重跑 30 题，留原始 trace：

| 指标 | 2026-09-07（Day 1 重跑） | 2026-09-01（历史） |
|---|---:|---:|
| Recall@5 / Recall@7 | 1.000 / 1.000 | 1.000 / 1.000 |
| MRR | 0.975 | 0.975 |
| faithfulness / relevancy | 0.943 / 0.921 | 0.895 / 0.909 |
| context precision / recall | 0.738 / 0.958 | 0.679 / 0.908 |
| refusal recall / legacy specificity | 1.000 / 0.840 | 1.000 / 0.840 |
| clarification rate | 0.800 | 0.800 |
| latency P50/P95 (s) | 13.89 / 28.50 | 15.11 / 24.85 |

- 检索层与历史基线持平，有序命中 trace 留存于 per-item jsonl。
- **如实记录**：本次执行 29 条 / 1 条 ERROR（id=21，`OutputParserException`，历史运行中偶发）；expired/fixture 泄漏信号由 0/0 变为 1/1，属轨迹漂移，不构成检索指标回归，但须在 D-06 语义审计门禁下复核。历史报告不回写。

## 3. 挑战集与 qrels（40 题）

- 结构：`id / origin / category / question / decision_label / qrels[{source, grade, anchor}] / hard_negatives[]`。
- 组成：原 30 题中实际进入检索计分的 20 题（origin=golden-*，与旧 runner 同口径）+ 20 条新题。
- 新题覆盖：术语（4）、同义改写（4）、hard negative（4）、多跳（4）、来源版本冲突（4，基于受治理 fixture 的 active/expired 配对）。
- qrels 分级：grade 2=核心依据；grade 1=部分相关（仅用于版本冲突题的旧版来源）。hard_negatives 命中计入泄漏信号，不进 Recall/MRR 分母。
- 冲突题中 v1/v2 都标注 qrels（v2 grade 2、v1 grade 1），不预设版本过滤已实现。

## 4. 单次 decision 标注与语义审计子集

- decision 三值冻结：`answered / clarification_required / refused`；误拒率、过度澄清率定义与 TP/(TP+FP) 口径写入 `acceptance.yaml`。
- 语义审计子集 11 题（≥10）：6 题预期 `answered`（含 2 题版本冲突）、3 题预期 `refused`（原 unanswerable）、2 题预期 `clarification_required`（原 ambiguous_followup）。每题预标 key_facts、required_sources、allowed_gaps。
- 旧 `refusal.precision`（1 - false_refusals/answerable_n）在报告中保留为 `legacy_specificity`，新拒答 precision 用 TP/(TP+FP)，两者不直接比较。

## 5. Pi / MCP 最小兼容性验证（真实运行）

官方 Python MCP SDK **2.1.1**（`uv pip install "mcp[cli]==2.1.1"`，已入 venv）最小 ASGI 验证通过：

- `MCPServer`（2.x 替代 FastMCP）`streamable_http_app()` 挂载 FastAPI/uvicorn，官方 `streamable_http_client` 完成 initialize、tools/list、tools/call 全链路。
- 协议协商版本 `2025-11-25`；`@mcp.tool(structured_output=True)` + TypedDict 返回 → `structuredContent` 通道可用，工具声明带 outputSchema。
- 关键约束（Day 2 实施要遵守）：lifespan 必须 `async with mcp.session_manager.run()`；挂载 `/mcp` 后实际端点路径为 `/mcp/mcp`（后续实施会显式配置 path 收敛为单层）。
- Pi 侧静态证据：`~/.pi/agent/npm/node_modules/pi-mcp-adapter` 2.32.1 支持 `httpTransport: "streamable-http" | "sse"`、`url` server 定义与 Bearer 凭据存储（`mcp-bearer-store.ts`）。真实 Pi 连通属 Day 5 验收，不提前宣称。

## 6. ARM64 Docker 依赖可行性

- 本机 Docker：aarch64 Linux 可用。
- PyPI 最新版 wheel 探测：onnxruntime（1.29.0）、pymupdf（1.28.2）、torch（2.14.0）均有 `manylinux_2_28_aarch64` 轮子；fastembed/pymupdf4llm/sentence-transformers/gradio 为纯 Python 轮。
- 结论：核心原生依赖有 ARM64 wheel，镜像构建可行；模型下载（HF，~2 GiB 级）与内存预算在 Day 2 写 Dockerfile 时实测，模型卷挂载方案另行记录。

## 7. 回归验证

- `pytest tests/`：**33 passed**（7 个既有 deprecation warning）。
- 未修改任何检索/Agent/语料行为代码；本次为纯文档/评测资产改动，无需付费 eval 之外的重跑（基线重跑为 Day 1 检查点证据，已执行）。

## 8. 检查点结论与遗留

- **完成**：范围与评测契约冻结、基线绑定重跑、挑战集/qrels、decision 标注、语义审计子集、Pi/MCP 最小验证、ARM64 Docker 可行性。
- **遗留给后续**：`acceptance.yaml` 中 capacity 目标（100 文档/1 万块/3 并发）待 Day 4 实测；真实 Pi 端到端属 Day 5；检索挑战集的 runner 与新指标（source_precision@5、nDCG@5、hard-negative 泄漏）在 Day 2 与 L2 检索服务一起实现。
- 检查点失败项：无。
