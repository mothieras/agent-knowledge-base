"""T4 评测驱动（单次 decision 协议）：golden set 跑 L2 AnswerService。

模式：--mode rag|agentic（默认 agentic，与历史基线同口径）。每题独立请求，
记录 decision/answer/citations/证据/usage/延迟；引用机械有效性单独校验。

运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_eval.py [--mode rag|agentic] [--limit N]
产出: eval/reports/baseline-<date>-<mode>.md / .jsonl（历史报告不回写）。
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

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import config
from core.app_service import build_app_service
from schema.dto import AnswerRequest
import metrics as eval_metrics

GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.jsonl"
REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "data" / "manifest.json"
FIXTURES_MANIFEST = REPO_ROOT / "data" / "fixtures" / "manifest.json"


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"n/a ({type(e).__name__})"


def manifest_hash() -> str:
    """manifest.json + fixtures manifest + 其引用语料文件内容的 sha256（索引由两份 manifest 驱动）。"""
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        h = hashlib.sha256()
        h.update(MANIFEST.read_bytes())
        for doc in manifest["documents"]:
            if doc["processing"] == "copied":
                p = REPO_ROOT / "data" / "interview_docs" / doc["local_rel"]
            elif doc["processing"] == "code_wrapped":
                p = REPO_ROOT / "data" / "processed" / (doc["local_rel"] + ".md")
            else:
                continue
            h.update(p.read_bytes())
        h.update(FIXTURES_MANIFEST.read_bytes())
        for doc in json.loads(FIXTURES_MANIFEST.read_text(encoding="utf-8"))["documents"]:
            h.update((FIXTURES_MANIFEST.parent / doc["file"]).read_bytes())
        return h.hexdigest()[:16]
    except Exception as e:
        return f"n/a ({type(e).__name__})"


def load_golden() -> list:
    """golden_set.jsonl 无 id 字段，按行号 1 起注入 id。"""
    return [
        {"id": i, **json.loads(line)}
        for i, line in enumerate(GOLDEN_SET.read_text(encoding="utf-8").splitlines(), start=1)
        if line.strip()
    ]


def should_skip(item: dict) -> bool:
    return bool(item.get("privacy_excluded"))


def verify_citations(svc, resp) -> dict:
    """引用机械有效性（A-05）：每条引用可精确回查，窗口内容 == quote。"""
    checks = []
    for c in resp.citations:
        try:
            window = svc.retrieval.read_evidence(
                c.evidence_id, c.span.start, c.span.end - c.span.start
            )
            checks.append(window.index_id == resp.index_id and window.content == c.quote)
        except Exception:
            checks.append(False)
    return {"count": len(checks), "valid": all(checks) if checks else None}


def run_one(svc, item: dict, mode: str) -> dict:
    t0 = time.time()
    try:
        resp = svc.generation.invoke(AnswerRequest(
            message=item["question"], mode=mode, include_debug_artifact=True,
        ))
        error = None
    except Exception as e:
        return {"id": item["id"], "category": item.get("category"),
                "topic": item.get("topic"), "question": item["question"],
                "error": f"{type(e).__name__}: {e}"}

    latency = time.time() - t0
    debug = resp.debug_artifact or {}
    evidence = debug.get("evidence", [])
    retrieval_hits = [
        {"source": e["source"], "content": e["content"],
         "version": e.get("version"), "chunk_id": e.get("chunk_id")}
        for e in evidence
    ]
    contexts = [
        f"Parent ID: {e.get('parent_id')}\nFile Name: {e['source']}\nContent: {e['content'].strip()}"
        for e in evidence
    ]
    citation_check = verify_citations(svc, resp)

    return {
        "id": item["id"],
        "category": item.get("category"),
        "topic": item.get("topic"),
        "question": item["question"],
        "decision": resp.decision,
        "answer": resp.answer,
        "clarification_question": resp.clarification_question,
        "limitations": resp.limitations,
        "contexts": contexts,
        "retrieval_hits": retrieval_hits,
        "citations": [c.model_dump() for c in resp.citations],
        "citation_valid": citation_check["valid"],
        "citation_count": citation_check["count"],
        "latency_s": round(latency, 2),
        "input_tokens": resp.usage.input_tokens if resp.usage else None,
        "output_tokens": resp.usage.output_tokens if resp.usage else None,
        "cost_cny": resp.usage.estimated_cost_cny if resp.usage else None,
        "tool_calls": 0,  # 单次协议：AnswerResponse 无工具计数，agentic 轨迹留 Day 4 debug 扩展
        "error": error,
    }


def decision_metrics(items: list, results: list) -> dict:
    """冻结 decision 口径（acceptance.yaml）：以应拒答（unanswerable）为正类。

    TP=应拒且实际 refused；FP=非应拒却 refused；FN=应拒但其他 decision。
    precision=TP/(TP+FP)，recall=TP/(TP+FN)。legacy_specificity 为历史非误拒率
    （1 - false_refusals / answerable_n），与新 precision 并列，不直接比较。
    """
    by_id = {r["id"]: r for r in results if "decision" in r}
    def decision_of(item):
        r = by_id.get(item["id"])
        return r["decision"] if r else None

    tp = fp = fn = 0
    misrefusal = overclarify = 0
    answerable_n = 0
    clar_n = clar_yes = 0
    for item in items:
        d = decision_of(item)
        if item.get("category") == "unanswerable":
            if d == "refused":
                tp += 1
            elif d is not None:
                fn += 1
            continue
        # 非 unanswerable（含澄清题）：answerable_n 保留历史口径
        answerable_n += 1
        if d == "refused":
            fp += 1
            misrefusal += 1
        elif d == "clarification_required":
            overclarify += 1
        if item.get("category") == "ambiguous_followup":
            clar_n += 1
            if d == "clarification_required":
                clar_yes += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    legacy_specificity = 1 - (fp / answerable_n) if answerable_n else None
    return {
        "refusal_tp": tp, "refusal_fp": fp, "refusal_fn": fn,
        "refusal_precision": round(precision, 3) if precision is not None else None,
        "refusal_recall": round(recall, 3) if recall is not None else None,
        "legacy_specificity": round(legacy_specificity, 3) if legacy_specificity is not None else None,
        "misrefusal_rate": round(misrefusal / answerable_n, 3) if answerable_n else None,
        "overclarify_rate": round(overclarify / answerable_n, 3) if answerable_n else None,
        "clarification_rate": round(clar_yes / clar_n, 3) if clar_n else None,
        "clarification_n": clar_n,
    }


def render_report(results: list, golden_items: list, gen_scores: dict, retrieval: dict,
                  decisions: dict, breakdown: dict, bindings: dict = None) -> str:
    executed = [r for r in results if "skip" not in r and not r.get("error")]
    lat = [r["latency_s"] for r in executed]
    def pct(xs, p):  # 无 numpy，简单分位数
        s = sorted(xs)
        return s[min(len(s) - 1, max(0, round(len(s) * p) - 1))] if s else 0.0

    lines = [
        f"# RAG 问答评测基线 {date.today().isoformat()}（mode={bindings['mode']}，单次 decision 协议）",
        "",
        "## 可复现绑定",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| git commit | `{bindings['git_commit']}` |",
        f"| manifest hash (sha256:16) | `{bindings['manifest_hash']}` |",
        f"| golden_set 行数 | {bindings['golden_lines']} |",
        f"| 索引 points | {bindings['index_points']} |",
        f"| index_id | `{bindings['index_id']}` |",
        f"| dense 模型 | {bindings['dense_model']} |",
        f"| sparse 模型 | {bindings['sparse_model']} |",
        f"| LLM | {bindings['llm_model']} @ {bindings['llm_base_url']} |",
        f"| judge | {bindings['judge_model']} |",
        f"| 检索参数 | k={bindings['retrieval_k']}, threshold={bindings['score_threshold']} |",
        f"| 执行时长 (s) | {bindings['elapsed_s']:.1f} |",
        "",
        f"- 执行 {len(executed)} 条 / 错误 {sum(1 for r in results if r.get('error'))} 条 / "
        f"SKIP {sum(1 for r in results if r.get('skip'))} 条",
        f"- 生成层 judge: {bindings['judge_model']}，实现: {gen_scores.get('implemented_by', 'n/a')}",
        "",
        "## decision（单次终态）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| refusal TP / FP / FN | {decisions['refusal_tp']} / {decisions['refusal_fp']} / {decisions['refusal_fn']} |",
        f"| refusal precision（TP/(TP+FP)） | {decisions['refusal_precision']} |",
        f"| refusal recall（TP/(TP+FN)） | {decisions['refusal_recall']} |",
        f"| legacy specificity（历史非误拒率） | {decisions['legacy_specificity']} |",
        f"| misrefusal rate（明确可答题中 refused） | {decisions['misrefusal_rate']} |",
        f"| overclarify rate（明确可答题中 clarification） | {decisions['overclarify_rate']} |",
        f"| clarification rate（歧义题，n={decisions['clarification_n']}） | {decisions['clarification_rate']} |",
        "",
        "## 引用机械有效性（A-05）",
        "",
    ]
    cited = [r for r in executed if r["decision"] == "answered"]
    valid_n = sum(1 for r in cited if r["citation_valid"] is True)
    invalid_n = sum(1 for r in cited if r["citation_valid"] is False)
    uncited = sum(1 for r in cited if r["citation_count"] == 0)
    lines.append(f"- answered {len(cited)} 条：引用全部可回查 {valid_n} 条 / 存在无效引用 {invalid_n} 条 / 无引用 {uncited} 条")
    lines += [
        "",
        "## 检索层",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| scored_items | {retrieval['scored_items']} |",
        f"| Recall@5 | {retrieval['recall@5']:.3f} |",
        f"| Recall@7 | {retrieval['recall@7']:.3f} |",
        f"| MRR | {retrieval['mrr']:.3f} |",
        f"| Hit Rate | {retrieval['hit_rate']:.3f} |",
        f"| expired_hit_items | {retrieval['expired_hit_items']} |",
        f"| fixture_hit_items | {retrieval['fixture_hit_items']} |",
        "",
        "## 检索层分维度明细",
        "",
    ]
    for key, label in (("category", "题型"), ("topic", "topic")):
        lines.append(f"### 按 {label}")
        lines.append("")
        lines.append(f"| {label} | scored | Recall@5 | Recall@7 | MRR | Hit Rate |")
        lines.append("|---|---|---|---|---|---|")
        for value, m in breakdown.get(key, {}).items():
            lines.append(
                f"| {value} | {m['scored_items']} | {m['recall@5']:.3f} | {m['recall@7']:.3f} "
                f"| {m['mrr']:.3f} | {m['hit_rate']:.3f} |"
            )
    lines += [
        "## 生成层（answered 题）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| faithfulness | {gen_scores['faithfulness']:.3f} |" if gen_scores['faithfulness'] is not None else "| faithfulness | n/a |",
        f"| answer_relevancy | {gen_scores['answer_relevancy']:.3f} |" if gen_scores['answer_relevancy'] is not None else "| answer_relevancy | n/a |",
        f"| context_precision | {gen_scores['context_precision']:.3f} |" if gen_scores['context_precision'] is not None else "| context_precision | n/a |",
        f"| context_recall | {gen_scores['context_recall']:.3f} |" if gen_scores['context_recall'] is not None else "| context_recall | n/a |",
        "",
        f"## 系统层（{len(executed)} 条执行均值）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| latency P50/P95 (s) | {pct(lat, 0.5):.2f} / {pct(lat, 0.95):.2f} |",
        f"| 平均 input/output tokens | {statistics.mean(r['input_tokens'] or 0 for r in executed):.0f} / {statistics.mean(r['output_tokens'] or 0 for r in executed):.0f} |",
        f"| 平均 cost (¥/query) | {statistics.mean(r['cost_cny'] or 0 for r in executed):.4f} |",
        "",
        "## per-item",
        "",
        "| id | category | topic | 得分/状态 |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r.get("skip"):
            lines.append(f"| {r['id']} | {r.get('category')} | {r.get('topic')} | SKIP: {r['skip']} |")
        elif r.get("error"):
            lines.append(f"| {r['id']} | {r.get('category')} | {r.get('topic')} | ERROR: {r['error'][:60]} |")
        else:
            lines.append(
                f"| {r['id']} | {r.get('category')} | {r.get('topic')} | "
                f"decision={r['decision']}, hits={len(r['retrieval_hits'])}, cites={r['citation_count']}, lat={r['latency_s']}s |"
            )
    lines += [
        "",
        "## 已知局限",
        "",
        "- 单次 decision 协议：clarification_required 是终态，不再有 HITL 暂停/恢复",
        "- legacy_specificity 为历史非误拒率口径（1 - false_refusals / answerable_n），与新 refusal precision 并列不直接比较",
        "- 引用机械有效性只证明 span/hash/quote 可回查；语义支持性由冻结审计子集单独核查",
        f"- {sum(1 for r in results if r.get('skip'))} 条命中隐私排除守卫的题被 SKIP",
        "- token/成本由回调全量采集",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rag", "agentic"], default="agentic",
                    help="问答模式（默认 agentic，与历史基线同口径）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条可评条目（0=全量）")
    ap.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "reports"))
    args = ap.parse_args()

    items = load_golden()
    svc = build_app_service()
    if svc.generation is None:
        print("错误: 未配置生成模型（DEEPSEEK_API_KEY），问答评测无法执行", file=sys.stderr)
        return 1
    print(f"初始化 L2 服务（index={svc.index_id[:16]}…, mode={args.mode}）...")
    t_start = time.time()

    results = []
    runnable = [it for it in items if not should_skip(it)]
    if args.limit:
        runnable = runnable[: args.limit]

    for idx, item in enumerate(runnable, 1):
        print(f"[{idx}/{len(runnable)}] #{item['id']} [{item.get('category')}] {item['question'][:40]}", flush=True)
        results.append(run_one(svc, item, args.mode))

    for item in items:
        if should_skip(item):
            results.append({"id": item["id"], "category": item.get("category"),
                            "topic": item.get("topic"), "skip": "语料未收录（隐私排除）"})

    results.sort(key=lambda r: r["id"])
    judge = eval_metrics.build_judge_llm()

    # 检索层（证据来自 debug_artifact；amb 题由 clarification 指标单独评估）
    score_items = [it for it in items if not should_skip(it)]
    hits_by_item = {r["id"]: r["retrieval_hits"] for r in results if "retrieval_hits" in r}
    retrieval = eval_metrics.retrieval_metrics(score_items, hits_by_item)

    breakdown = {}
    for key in ("category", "topic"):
        breakdown[key] = {}
        for value in sorted({it.get(key) for it in score_items if it.get(key)}):
            sub_items = [it for it in score_items if it.get(key) == value]
            sub_hits = {it["id"]: hits_by_item.get(it["id"], []) for it in sub_items}
            breakdown[key][value] = eval_metrics.retrieval_metrics(sub_items, sub_hits)

    # 生成层（answered 题）
    gen_rows = [
        {
            "question": it["question"],
            "answer": r["answer"],
            "contexts": r["contexts"],
            "reference": it.get("expected_answer", ""),
        }
        for it in items for r in results
        if r["id"] == it["id"] and "decision" in r and r["decision"] == "answered"
        and it.get("category") not in ("unanswerable", "ambiguous_followup")
    ]
    gen_scores = (eval_metrics.generation_metrics(gen_rows, judge) if gen_rows
                  else {"faithfulness": None, "answer_relevancy": None,
                        "context_precision": None, "context_recall": None,
                        "implemented_by": "empty"})

    decisions = decision_metrics(items, results)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    jsonl_path = outdir / f"baseline-{today}-{args.mode}.jsonl"
    md_path = outdir / f"baseline-{today}-{args.mode}.md"

    bindings = {
        "mode": args.mode,
        "git_commit": git_commit(),
        "manifest_hash": manifest_hash(),
        "golden_lines": len(items),
        "index_points": svc.snapshot.child_count,
        "index_id": svc.index_id,
        "dense_model": config.DENSE_MODEL,
        "sparse_model": config.SPARSE_MODEL,
        "llm_model": config.LLM_MODEL,
        "llm_base_url": config.LLM_BASE_URL,
        "judge_model": config.JUDGE_MODEL,
        "retrieval_k": config.DEFAULT_RETRIEVAL_K,
        "score_threshold": config.RETRIEVAL_SCORE_THRESHOLD,
        "elapsed_s": time.time() - t_start,
    }

    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8"
    )
    md_path.write_text(
        render_report(results, items, gen_scores, retrieval, decisions, breakdown, bindings),
        encoding="utf-8",
    )
    print(f"\n[report] {md_path}")
    print(f"[jsonl] {jsonl_path}")
    print(json.dumps({"bindings": bindings, "retrieval": retrieval, "generation": gen_scores,
                      "decisions": decisions},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
