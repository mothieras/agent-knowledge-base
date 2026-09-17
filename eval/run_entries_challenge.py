"""条目检索挑战集运行器（PHASE2 §4.5；契约：eval/entries_challenge_contract.yaml）。

在临时 SQLite 上重建种子语料 → 逐题 EntryService.search → 对照 qrels 计算
recall@5 / mrr@10 → 报告写 eval/reports/。全链路无模型（FTS5 + jieba 预分词）。
先校验数据集 sha256 与冻结契约一致，防口径漂移。

运行: PYTHONPATH=src ../.venv/bin/python run_entries_challenge.py（在 eval/ 下）
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import statistics
import sys
import tempfile
from datetime import datetime, timezone

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.entry_service import EntryService  # noqa: E402
from db.entry_store import EntryStore  # noqa: E402

CONTRACT = pathlib.Path(__file__).resolve().parent / "entries_challenge_contract.yaml"
REPORTS = pathlib.Path(__file__).resolve().parent / "reports"


def _load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_service(db_path: str) -> EntryService:
    fixed_now = lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)  # noqa: E731
    return EntryService(EntryStore(db_path), clock=fixed_now)


def main() -> int:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    seeds_path = ROOT / contract["datasets"]["seed_corpus"]["path"]
    queries_path = ROOT / contract["datasets"]["challenge"]["path"]
    for path, meta in ((seeds_path, contract["datasets"]["seed_corpus"]),
                       (queries_path, contract["datasets"]["challenge"])):
        actual = _sha256(path)
        if actual != meta["sha256"]:
            print(f"[freeze] 失败: {path.name} sha256 与契约不符（{actual}）")
            return 1

    seeds = _load_jsonl(seeds_path)
    queries = _load_jsonl(queries_path)
    th = contract["thresholds"]

    with tempfile.TemporaryDirectory() as tmp:
        svc = build_service(str(pathlib.Path(tmp) / "entries.db"))
        seed_to_id = {}
        for s in seeds:
            entry = svc.create(
                type=s["type"], body=s["body"], scope=s["scope"], author=s["author"],
            )
            seed_to_id[s["seed_id"]] = entry.id

        per_query = []
        for q in queries:
            rel = {seed_to_id[r["seed_id"]] for r in q["qrels"]}
            res = svc.search(query=q["query"], limit=10, **q["search_args"])
            hits = [e.id for e in res.results]
            top5 = set(hits[:5])
            recall5 = len(rel & top5) / len(rel)
            rr = 0.0
            for rank, hid in enumerate(hits[:10], start=1):
                if hid in rel:
                    rr = 1.0 / rank
                    break
            per_query.append({
                "id": q["id"], "category": q["category"], "query": q["query"],
                "qrels": sorted(r["seed_id"] for r in q["qrels"]),
                "hits_top10": [i for i in hits],
                "recall_at_5": recall5, "rr_at_10": rr,
            })

    macro_recall = statistics.mean(p["recall_at_5"] for p in per_query)
    macro_mrr = statistics.mean(p["rr_at_10"] for p in per_query)
    exact = [p for p in per_query if p["category"] == "exact_term"]
    exact_recall = statistics.mean(p["recall_at_5"] for p in exact) if exact else 1.0

    checks = {
        "recall_at_5": (macro_recall, th["recall_at_5"], macro_recall >= th["recall_at_5"]),
        "mrr_at_10": (macro_mrr, th["mrr_at_10"], macro_mrr >= th["mrr_at_10"]),
        "exact_term_recall_at_5": (exact_recall, th["exact_term_recall_at_5"],
                                   exact_recall >= th["exact_term_recall_at_5"]),
    }
    passed = all(c[2] for c in checks.values())

    by_cat = {}
    for p in per_query:
        by_cat.setdefault(p["category"], []).append(p["recall_at_5"])

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    REPORTS.mkdir(exist_ok=True)
    detail_path = REPORTS / f"entries-challenge-{stamp}.jsonl"
    detail_path.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in per_query) + "\n", encoding="utf-8")

    lines = [
        "# 条目检索挑战集报告（PHASE2 T3）",
        "",
        f"- 日期：{stamp}（数据集与门槛见 entries_challenge_contract.yaml，运行前 sha256 校验通过）",
        f"- 实现：SQLite FTS5 + jieba cut_for_search 空格预分词 + bm25；无 embedding/rerank/生成",
        f"- 分母：{len(per_query)} 题（独立报告，不与 30 题 golden / 40 题检索挑战集混算）",
        "",
        "| 指标 | 实测 | 门槛 | 判定 |",
        "|---|---|---|---|",
    ]
    for name, (actual, threshold, ok) in checks.items():
        lines.append(f"| {name} | {actual:.4f} | ≥ {threshold:.2f} | {'PASS' if ok else 'FAIL'} |")
    lines += ["", "## 分类别 recall@5", "", "| 类别 | 题数 | recall@5 |", "|---|---|---|"]
    for cat, vals in sorted(by_cat.items()):
        lines.append(f"| {cat} | {len(vals)} | {statistics.mean(vals):.4f} |")
    lines += ["", "未命中题（recall@5 < 1）："]
    misses = [p for p in per_query if p["recall_at_5"] < 1]
    if misses:
        for p in misses:
            lines.append(f"- {p['id']} [{p['category']}] «{p['query']}» recall@5={p['recall_at_5']:.2f}")
    else:
        lines.append("- 无")
    lines += ["", f"逐题明细：`{detail_path.relative_to(ROOT)}`", "",
              "**结论：" + ("PASS（三门槛全过）" if passed else "FAIL") + "**"]
    report_path = REPORTS / f"entries-challenge-{stamp}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"recall@5={macro_recall:.4f} (≥{th['recall_at_5']})  "
          f"mrr@10={macro_mrr:.4f} (≥{th['mrr_at_10']})  "
          f"exact_term recall@5={exact_recall:.4f}")
    print(f"报告: {report_path}")
    print("[entries-challenge] " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
