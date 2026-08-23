"""T4 评测驱动：golden set 跑当前系统，产出 per-item jsonl + 汇总 md。

前提: project/.env 已配置 DEEPSEEK_API_KEY；语料已 ingest_corpus.py 入库。
运行: cd eval && uv run --python ../.venv/bin/python run_eval.py [--limit N] [--outdir reports]
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "project"))
os.environ.pop("HF_ENDPOINT", None)

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "project" / ".env")

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from core.rag_system import RAGSystem
import config
import metrics as eval_metrics

GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.jsonl"
REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "data" / "manifest.json"


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
    """manifest.json + 其引用语料文件内容的 sha256（索引由 manifest 驱动）。"""
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
        return h.hexdigest()[:16]
    except Exception as e:
        return f"n/a ({type(e).__name__})"


def index_points(rs: RAGSystem) -> int:
    try:
        return rs.vector_db.count_points(rs.collection_name)
    except Exception:
        return -1

def load_golden() -> list:
    """golden_set.jsonl 无 id 字段，按行号 1 起注入 id。"""
    return [
        {"id": i, **json.loads(line)}
        for i, line in enumerate(GOLDEN_SET.read_text(encoding="utf-8").splitlines(), start=1)
        if line.strip()
    ]


def should_skip(item: dict) -> bool:
    return bool(item.get("privacy_excluded"))


class RunCollector(BaseCallbackHandler):
    """回调采集：LLM 全量 token 用量与工具调用次数（含子图内部调用）。"""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0

    def on_llm_end(self, response, **kwargs):
        try:
            for gen_list in response.generations:
                for gen in gen_list:
                    usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                    self.input_tokens += usage.get("input_tokens", 0) or 0
                    self.output_tokens += usage.get("output_tokens", 0) or 0
        except Exception:
            pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls += 1


def run_one(rs, recorder, item: dict) -> dict:
    rs.reset_thread()
    recorder.clear()
    cfg = rs.get_config()
    collector = RunCollector()
    cfg["callbacks"] = (cfg.get("callbacks") or []) + [collector]
    t0 = time.time()
    try:
        state = rs.agent_graph.invoke(
            {"messages": [HumanMessage(content=item["question"])]}, config=cfg
        )
        error = None
    except Exception as e:
        return {"id": item["id"], "error": f"{type(e).__name__}: {e}"}

    latency = time.time() - t0
    pending = rs.agent_graph.get_state(cfg).next
    clarified = bool(pending)
    answer = state["messages"][-1].content if state.get("messages") else ""

    contexts = []
    seen = set()
    for ans in state.get("agent_answers", []):
        for c in ans.get("contexts", []):
            if c not in seen:
                seen.add(c)
                contexts.append(c)

    return {
        "id": item["id"],
        "category": item.get("category"),
        "topic": item.get("topic"),
        "question": item["question"],
        "answer": answer,
        "clarified": clarified,
        "contexts": contexts,
        "retrieval_hits": [(d.metadata.get("source", ""), d.page_content) for d in recorder],
        "latency_s": round(latency, 2),
        "input_tokens": collector.input_tokens,
        "output_tokens": collector.output_tokens,
        "cost_cny": round(eval_metrics.cost_estimate(collector.input_tokens, collector.output_tokens), 4),
        "tool_calls": collector.tool_calls,
        "error": error,
    }


def render_report(results: list, golden_items: list, gen_scores: dict, retrieval: dict,
                  refusal: dict, clarification: dict, breakdown: dict,
                  bindings: dict = None) -> str:
    executed = [r for r in results if "skip" not in r and not r.get("error")]
    lat = [r["latency_s"] for r in executed]
    def pct(xs, p):  # 无 numpy，简单分位数
        s = sorted(xs)
        return s[min(len(s) - 1, max(0, round(len(s) * p) - 1))] if s else 0.0

    lines = [
        f"# Agentic-RAG 评测基线 {date.today().isoformat()}",
        "",
        "## 可复现绑定",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| git commit | `{bindings['git_commit']}` |",
        f"| manifest hash (sha256:16) | `{bindings['manifest_hash']}` |",
        f"| golden_set 行数 | {bindings['golden_lines']} |",
        f"| 索引 points | {bindings['index_points']} |",
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
        "## 检索层",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| scored_items | {retrieval['scored_items']} |",
        f"| Recall@5 | {retrieval['recall@5']:.3f} |",
        f"| Recall@7 | {retrieval['recall@7']:.3f} |",
        f"| MRR | {retrieval['mrr']:.3f} |",
        f"| Hit Rate | {retrieval['hit_rate']:.3f} |",
        f"| section_hit_rate | {retrieval['section_hit_rate'] if retrieval['section_hit_rate'] is not None else 'n/a'} |",
        f"| expired_hit_items | {retrieval['expired_hit_items']} |",
        "",
        "",
    ]
    lines.append("## 检索层分维度明细")
    lines.append("")
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
        "## 生成层（排除拒答/澄清题）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| faithfulness | {gen_scores['faithfulness'] if gen_scores['faithfulness'] is not None else 'n/a'} |",
        f"| answer_relevancy | {gen_scores['answer_relevancy'] if gen_scores['answer_relevancy'] is not None else 'n/a'} |",
        f"| context_precision | {gen_scores['context_precision'] if gen_scores['context_precision'] is not None else 'n/a'} |",
        f"| context_recall | {gen_scores['context_recall'] if gen_scores['context_recall'] is not None else 'n/a'} |",
        "",
        "## 拒答 / 澄清",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| refusal_recall | {refusal['recall']:.3f} |",
        f"| refusal_precision | {refusal['precision']:.3f} |",
        f"| clarification_rate | {clarification['rate']:.3f} |",
        "",
        f"## 系统层（{len(executed)} 条执行均值）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| latency P50/P95 (s) | {pct(lat, 0.5):.2f} / {pct(lat, 0.95):.2f} |",
        f"| 平均 input/output tokens | {statistics.mean(r['input_tokens'] for r in executed):.0f} / {statistics.mean(r['output_tokens'] for r in executed):.0f} |",
        f"| 平均 cost (¥/query) | {statistics.mean(r['cost_cny'] for r in executed):.4f} |",
        f"| 平均 tool_calls | {statistics.mean(r['tool_calls'] for r in executed):.1f} |",
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
            lines.append(f"| {r['id']} | {r.get('category')} | {r.get('topic')} | clarified={r['clarified']}, hits={len(r['retrieval_hits'])}, lat={r['latency_s']}s |")
    lines += [
        "",
        "## 已知局限",
        "",
        f"- 拒答未显式实现（T6 规划中）；基座 agent 在检索无命中时自然拒答（实测 refusal_recall={refusal['recall']:.3f} / precision={refusal['precision']:.3f}，语料覆盖缺口的误拒留 T5 badcase 复核）",
        "- 澄清题为单轮口径（golden set 无澄清回复脚本）",
        f"- {sum(1 for r in results if r.get('skip'))} 条命中隐私排除守卫的题被 SKIP",
        "- manifest 版本字段尚未接入检索过滤；当前公开语料通过单一 Git revision 固定版本",
        "- token/成本由回调全量采集（含子图内部调用）",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条可评条目（0=全量）")
    ap.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "reports"))
    args = ap.parse_args()

    items = load_golden()
    recorder = []
    rs = RAGSystem(record_retrieval=lambda docs: recorder.extend(docs))
    print("初始化 RAGSystem（加载 embedding + 编译图）...")
    t_start = time.time()
    rs.initialize()

    results = []
    runnable = [it for it in items if not should_skip(it)]
    if args.limit:
        runnable = runnable[: args.limit]

    for idx, item in enumerate(runnable, 1):
        print(f"[{idx}/{len(runnable)}] #{item['id']} [{item.get('category')}] {item['question'][:40]}", flush=True)
        results.append(run_one(rs, recorder, item))

    for item in items:
        if should_skip(item):
            results.append({"id": item["id"], "category": item.get("category"),
                            "topic": item.get("topic"), "skip": "语料未收录（隐私排除）"})

    results.sort(key=lambda r: r["id"])
    judge = eval_metrics.build_judge_llm()

    # 检索层
    score_items = [it for it in items if not should_skip(it)]
    hits_by_item = {r["id"]: r["retrieval_hits"] for r in results if "retrieval_hits" in r}
    retrieval = eval_metrics.retrieval_metrics(score_items, hits_by_item)

    # 分 category / 分 topic 明细（spec 5.6-2）
    breakdown = {}
    for key in ("category", "topic"):
        breakdown[key] = {}
        for value in sorted({it.get(key) for it in score_items if it.get(key)}):
            sub_items = [it for it in score_items if it.get(key) == value]
            sub_hits = {it["id"]: hits_by_item.get(it["id"], []) for it in sub_items}
            breakdown[key][value] = eval_metrics.retrieval_metrics(sub_items, sub_hits)

    # 生成层
    gen_rows = [
        {
            "question": it["question"],
            "answer": r["answer"],
            "contexts": r["contexts"],
            "reference": it.get("expected_answer", ""),
        }
        for it in items for r in results
        if r["id"] == it["id"] and "answer" in r
        and it.get("category") not in ("unanswerable", "ambiguous_followup")
    ]
    gen_scores = (eval_metrics.generation_metrics(gen_rows, judge) if gen_rows
                  else {"faithfulness": None, "answer_relevancy": None,
                        "context_precision": None, "context_recall": None,
                        "implemented_by": "empty"})

    # 拒答
    refusal = {"recall": 0.0, "precision": 1.0, "n": 0, "false_refusals": 0}
    for it in items:
        r = next((x for x in results if x["id"] == it["id"] and "answer" in x), None)
        if not r:
            continue
        if it.get("category") == "unanswerable":
            refusal["n"] += 1
            if eval_metrics.refusal_judge(judge, it["question"], r["answer"]):
                refusal["recall"] += 1
        else:
            if eval_metrics.refusal_judge(judge, it["question"], r["answer"]):
                refusal["false_refusals"] += 1
    refusal["recall"] = refusal["recall"] / refusal["n"] if refusal["n"] else 0.0
    answerable_n = sum(1 for it in items if it.get("category") != "unanswerable"
                       and next((x for x in results if x["id"] == it["id"] and "answer" in x), None))
    refusal["precision"] = 1.0 - (refusal["false_refusals"] / answerable_n) if answerable_n else 1.0

    # 澄清
    clar_n = clar_yes = 0
    for it in items:
        if it.get("category") != "ambiguous_followup":
            continue
        r = next((x for x in results if x["id"] == it["id"] and "answer" in x), None)
        if not r:
            continue
        clar_n += 1
        if eval_metrics.clarification_judge(judge, it["question"], r["answer"], it.get("expected_answer", "")):
            clar_yes += 1
    clarification = {"rate": clar_yes / clar_n if clar_n else 0.0, "n": clar_n}

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    jsonl_path = outdir / f"baseline-{today}.jsonl"
    md_path = outdir / f"baseline-{today}.md"

    bindings = {
        "git_commit": git_commit(),
        "manifest_hash": manifest_hash(),
        "golden_lines": len(items),
        "index_points": index_points(rs),
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
        render_report(results, items, gen_scores, retrieval, refusal, clarification, breakdown,
                      bindings),
        encoding="utf-8",
    )
    print(f"\n[report] {md_path}")
    print(f"[jsonl] {jsonl_path}")
    print(json.dumps({"bindings": bindings, "retrieval": retrieval, "generation": gen_scores,
                      "refusal": refusal, "clarification": clarification},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
