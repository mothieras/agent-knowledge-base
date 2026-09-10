"""从两份 baseline summary.json 生成 README「已记录基线」对照表。

用法: cd eval && ../.venv/bin/python make_readme_table.py \
          reports/baseline-<date>-agentic.summary.json \
          reports/baseline-<date>-rag.summary.json

两份 summary 必须同 git commit、同 manifest hash、同 golden 行数（同一场重跑的两半），
否则拒绝生成。输出 markdown 到 stdout；贴入 README 时不改数字。
解读性文字（两模式已知差异等）仍手写在 README，本脚本只产出可机核的数字表。
"""
import json
import sys
from pathlib import Path


def f3(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_pair(a: dict, b: dict) -> None:
    for key in ("git_commit", "manifest_hash", "golden_lines"):
        va, vb = a["bindings"][key], b["bindings"][key]
        if va != vb:
            sys.exit(f"错误: 两份 summary 的 {key} 不一致（{va!r} vs {vb!r}），"
                     "不是同一场重跑，拒绝生成对照表")
    if a["bindings"]["date"] != b["bindings"]["date"]:
        print(f"[warn] 两份 summary 日期不同: {a['bindings']['date']} / "
              f"{b['bindings']['date']}", file=sys.stderr)


def render(a: dict, b: dict) -> str:
    ba, bb = a["bindings"], b["bindings"]
    d = ba["date"]
    sa, sb = a["system"], b["system"]
    da, db = a["decisions"], b["decisions"]
    ra, rb = a["retrieval"], b["retrieval"]
    ga, gb = a["generation"], b["generation"]
    n_clear = da["clearly_answerable_n"]
    n_clar = da["clarification_n"]

    def pair(label, av, rv):
        return f"| {label} | {av} | {rv} |"

    lines = [
        f"**当前基准是 {d} 单次 decision 协议基线（最终 commit 重跑；rag/agentic 同 "
        f"index/模型/参数，README 表由 make_readme_table.py 从本场 summary 生成）**：完整报告见 "
        f"[`eval/reports/baseline-{d}-agentic.md`](eval/reports/baseline-{d}-agentic.md) 与 "
        f"[`baseline-{d}-rag.md`](eval/reports/baseline-{d}-rag.md)，per-item 数据与汇总见同名 "
        "`.jsonl` / `.summary.json`。检索指标统计进入检索流程的 20 题，"
        "5 条歧义题由 clarification 指标单独评估。",
        "",
        f"| 指标（单次 decision 协议） | {d} agentic | {d} rag |",
        "|---|---:|---:|",
        pair("执行 / 错误 / SKIP",
             f"{sa['executed']} / {sa['errors']} / {sa['skips']}",
             f"{sb['executed']} / {sb['errors']} / {sb['skips']}"),
        pair("Recall@5 / Recall@7",
             f"{f3(ra['recall@5'])} / {f3(ra['recall@7'])}",
             f"{f3(rb['recall@5'])} / {f3(rb['recall@7'])}"),
        pair("MRR", f3(ra["mrr"]), f3(rb["mrr"])),
        pair("expired/fixture 泄漏题数",
             f"{ra['expired_hit_items']} / {ra['fixture_hit_items']}",
             f"{rb['expired_hit_items']} / {rb['fixture_hit_items']}"),
        pair("Faithfulness", f3(ga["faithfulness"]), f3(gb["faithfulness"])),
        pair("Answer relevancy", f3(ga["answer_relevancy"]), f3(gb["answer_relevancy"])),
        pair("Context precision / recall",
             f"{f3(ga['context_precision'])} / {f3(ga['context_recall'])}",
             f"{f3(gb['context_precision'])} / {f3(gb['context_recall'])}"),
        pair("refusal precision（TP/(TP+FP)）/ recall",
             f"{da['refusal_precision']} / {da['refusal_recall']}",
             f"{db['refusal_precision']} / {db['refusal_recall']}"),
        pair(f"misrefusal / overclarify rate（明确可答 n={n_clear}）",
             f"{da['misrefusal_rate']} / {da['overclarify_rate']}",
             f"{db['misrefusal_rate']} / {db['overclarify_rate']}"),
        pair(f"Clarification rate（歧义题 n={n_clar}）",
             f"{da['clarification_rate']}", f"{db['clarification_rate']}"),
        pair("歧义题被硬拒（计入 refusal FP，不计入 misrefusal）",
             f"{da['ambiguous_refused']}", f"{db['ambiguous_refused']}"),
        pair("引用机械有效性（answered 题）",
             f"{a['citations']['answered']}/{a['citations']['valid']}",
             f"{b['citations']['answered']}/{b['citations']['valid']}"),
        pair("Latency P50 / P95",
             f"{sa['latency_p50_s']}s / {sa['latency_p95_s']}s",
             f"{sb['latency_p50_s']}s / {sb['latency_p95_s']}s"),
        pair("平均 cost（¥/query）",
             f"{sa['avg_cost_cny']:.4f}", f"{sb['avg_cost_cny']:.4f}"),
    ]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = load(sys.argv[1]), load(sys.argv[2])
    if a["bindings"]["mode"] != "agentic" or b["bindings"]["mode"] != "rag":
        sys.exit("错误: 参数顺序应为 <agentic.summary.json> <rag.summary.json>")
    check_pair(a, b)
    print(render(a, b))


if __name__ == "__main__":
    main()