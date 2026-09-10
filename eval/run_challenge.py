"""检索挑战集 runner：40 题直接检索（不经图），按冻结契约计分。

前提: 语料已 ingest_corpus.py 重建并写入 snapshot manifest；根目录 .env 可空（无生成调用）。
运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_challenge.py

产出: eval/reports/challenge-<date>.jsonl（per-item 有序命中 trace + 契约校验）
      eval/reports/challenge-<date>.md（汇总 + 回归锚点对照冻结基线）
"""
import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.pop("HF_ENDPOINT", None)
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import config  # noqa: E402
import metrics as eval_metrics  # noqa: E402
from core.app_service import build_app_service  # noqa: E402
from schema.dto import SearchRequest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CHALLENGE_SET = Path(__file__).resolve().parent / "challenge_set.jsonl"
ACCEPTANCE = Path(__file__).resolve().parent / "acceptance.yaml"

# 冻结基线（acceptance.yaml baseline_freezing）
BASELINE_RETRIEVAL = {"recall@5": 1.0, "recall@7": 1.0, "mrr": 0.975}


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception as exc:
        return f"n/a ({type(exc).__name__})"


def load_items():
    return [json.loads(line) for line in CHALLENGE_SET.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def verify_hit(hit: dict, svc) -> dict:
    """机械校验证据契约不变量，返回 {ok, checks}。"""
    checks = {}
    checks["content_hash"] = hit["content_hash"] == "sha256:" + hashlib.sha256(
        hit["content"].encode("utf-8")).hexdigest()
    checks["span"] = (
        hit.get("span") is not None
        and hit["span"]["start"] < hit["span"]["end"]
        and hit["span"]["end"] - hit["span"]["start"] == len(hit["content"])
    )
    try:
        span = hit["span"]
        window = svc.retrieval.read_evidence(
            hit["evidence_id"], span["start"], span["end"] - span["start"]
        )
        checks["evidence_resolves"] = window.index_id == svc.index_id
        checks["evidence_content"] = window.content == hit["content"]
    except Exception:
        checks["evidence_resolves"] = False
        checks["evidence_content"] = False
    return {"ok": all(checks.values()), "checks": checks}


def run_one(svc, item: dict) -> dict:
    t0 = time.time()
    try:
        resp = svc.retrieval.search(
            SearchRequest(query=item["question"], k=7, include_debug_artifact=True)
        )
        error = None
    except Exception as exc:
        return {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}
    latency = time.time() - t0

    full = resp.debug_artifact or resp.hits
    hits = []
    for h in full:
        hit = {
            "source": h.source,
            "content": h.content,
            "version": h.version,
            "chunk_id": h.chunk_id,
            "parent_id": h.parent_id,
            "score": h.score,
            "retrieval_channel": h.retrieval_channel,
            "span": {"start": h.span.start, "end": h.span.end},
            "evidence_id": h.evidence_id,
            "content_hash": h.content_hash,
        }
        hit.update(verify_hit({"content": h.content, "content_hash": h.content_hash,
                               "span": {"start": h.span.start, "end": h.span.end},
                               "evidence_id": h.evidence_id}, svc))
        hits.append(hit)

    return {
        "id": item["id"],
        "origin": item.get("origin"),
        "category": item.get("category"),
        "question": item["question"],
        "returned_k": resp.returned_k,
        "requested_k": resp.requested_k,
        "truncated": resp.truncated,
        "latency_s": round(latency, 3),
        "hits": hits,
        "error": error,
    }


def render_report(results, items, metrics, bindings) -> str:
    executed = [r for r in results if not r.get("error")]
    lat = [r["latency_s"] for r in executed]

    def pct(xs, p):
        s = sorted(xs)
        return s[min(len(s) - 1, max(0, round(len(s) * p) - 1))] if s else 0.0

    lines = [
        f"# 检索挑战集评测 {date.today().isoformat()}",
        "",
        "## 可复现绑定",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| git commit | `{bindings['git_commit']}` |",
        f"| index_id | `{bindings['index_id']}` |",
        f"| parent/child chunks | {bindings['parent_count']} / {bindings['child_count']} |",
        f"| dense / sparse | {bindings['dense_model']} / {bindings['sparse_model']} |",
        f"| k / threshold | {bindings['k']} / {bindings['score_threshold']} |",
        f"| 执行 | {len(executed)} 条 / 错误 {sum(1 for r in results if r.get('error'))} 条 |",
        "",
        "## 总指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| scored_items | {metrics['scored_items']} |",
        f"| Recall@5 | {metrics['recall@5']:.3f} |",
        f"| Recall@7 | {metrics['recall@7']:.3f} |",
        f"| MRR | {metrics['mrr']:.3f} |",
        f"| Hit Rate | {metrics['hit_rate']:.3f} |",
        f"| source_precision@5 | {metrics['source_precision@5']:.3f} |",
        f"| nDCG@5 | {metrics['ndcg@5']:.3f} |",
        f"| hard_negative 泄漏题数 | {metrics['hard_negative_leak_count']} |",
        "",
        "## 回归锚点（20 条 golden 计分题）",
        "",
        "| 指标 | 冻结基线 | 本次 |",
        "|---|---|---|",
        f"| Recall@5 | {BASELINE_RETRIEVAL['recall@5']:.3f} | {metrics['regression_anchor']['recall@5']:.3f} |",
        f"| anchor 题数 | 20 | {metrics['regression_anchor']['count']} |",
        "",
        "## 分题型",
        "",
        "| category | scored | Recall@5 | Recall@7 | MRR | leak_items |",
        "|---|---|---|---|---|---|",
    ]
    for cat, b in sorted(metrics["breakdown"].items()):
        lines.append(
            f"| {cat} | {b['scored_items']} | {b['recall@5']:.3f} | {b['recall@7']:.3f} "
            f"| {b['mrr']:.3f} | {b['leak_items']} |"
        )

    lines += [
        "",
        "## 契约机械校验",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 命中数 | {bindings['total_hits']} |",
        f"| span/hash/evidence 全部通过 | {bindings['contract_ok']} |",
        f"| 契约失败数 | {bindings['contract_fail']} |",
        "",
        "## per-item",
        "",
        "| id | category | origin | Recall@5 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['id']} | {r.get('category')} | {r.get('origin')} | - | ERROR |")
            continue
        hits = r["hits"]
        expected = set(q["source"] for q in next(
            (i for i in items if i["id"] == r["id"]), {}).get("qrels", []))
        top5 = set(h["source"] for h in hits[:5])
        recall5 = "✓" if expected & top5 else "✗"
        status = "TRUNCATED" if r["truncated"] else "ok"
        lines.append(f"| {r['id']} | {r.get('category')} | {r.get('origin')} | {recall5} | {status} |")

    lines += [
        "",
        f"## 延迟（{len(executed)} 条）",
        "",
        f"- P50/P95: {pct(lat, 0.5):.2f}s / {pct(lat, 0.95):.2f}s",
        "",
        "> 契约校验失败或召回下降不得以轨迹漂移解释；直接检索适配必须保持有序命中 ID/来源/内容。",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    items = load_items()
    t0 = time.time()
    svc = build_app_service(build_generation=False)

    results = []
    hits_by_item = {}
    for i, item in enumerate(items, start=1):
        r = run_one(svc, item)
        results.append(r)
        hits_by_item[item["id"]] = r.get("hits", [])
        print(f"[{i}/{len(items)}] {item['id']} {item.get('category')} "
              f"{'ERROR' if r.get('error') else 'ok'}", flush=True)

    metrics = eval_metrics.challenge_retrieval_metrics(items, hits_by_item)

    total_hits = sum(len(r.get("hits", [])) for r in results)
    contract_ok = sum(
        1 for r in results for h in r.get("hits", []) if h.get("ok")
    )
    bindings = {
        "git_commit": git_commit(),
        "index_id": svc.index_id,
        "parent_count": svc.snapshot.parent_count,
        "child_count": svc.snapshot.child_count,
        "dense_model": svc.snapshot.dense_model,
        "sparse_model": svc.snapshot.sparse_model,
        "k": config.DEFAULT_RETRIEVAL_K,
        "score_threshold": config.RETRIEVAL_SCORE_THRESHOLD,
        "total_hits": total_hits,
        "contract_ok": contract_ok,
        "contract_fail": total_hits - contract_ok,
        "elapsed_s": time.time() - t0,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    jsonl_path = outdir / f"challenge-{stamp}.jsonl"
    md_path = outdir / f"challenge-{stamp}.md"
    jsonl_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
                          encoding="utf-8")
    md_path.write_text(render_report(results, items, metrics, bindings), encoding="utf-8")

    print(f"\n结果: {md_path}")
    print(f"Recall@5={metrics['recall@5']:.3f} Recall@7={metrics['recall@7']:.3f} "
          f"MRR={metrics['mrr']:.3f} P@5={metrics['source_precision@5']:.3f} "
          f"nDCG@5={metrics['ndcg@5']:.3f} leak={metrics['hard_negative_leak_count']}")
    print(f"回归锚点 Recall@5={metrics['regression_anchor']['recall@5']:.3f} (基线 1.000)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
