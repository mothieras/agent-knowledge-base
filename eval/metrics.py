"""评测指标：检索层自算 + ragas 封装(或兜底 judge) + 拒答/澄清 judge。

检索层命中判定锚点: expected_sources 文件名级匹配(metadata.source == source，
带 #anchor 的条目按去锚后路径比)；过期/背景源算命中，另计 expired_hits 信号。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langchain_openai import ChatOpenAI
import config

# deepseek-chat 定价(元/百万 token)，2026-08；更新价格改这里
PRICE_IN_PER_M = 0.28
PRICE_OUT_PER_M = 1.68

# data/fixtures/manifest.json 中 expired_date 非空的受治理 fixture 源；
# golden set 30 题不应命中它们，expired_hit_items 是版本过滤门禁的前置信号
EXPIRED_SOURCES = {
    "fixtures/api_rate_limit_policy_v1.md",
    "fixtures/data_retention_policy_v1.md",
}

# 全部受治理 fixture 源：golden set 30 题全部指向第三方语料，
# 任何 fixture 命中都是跨域污染信号（fixture_hit_items）
FIXTURE_SOURCES = {
    "fixtures/api_rate_limit_policy_v1.md",
    "fixtures/api_rate_limit_policy_v2.md",
    "fixtures/data_retention_policy_v1.md",
    "fixtures/data_retention_policy_v2.md",
}


def build_judge_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=config.JUDGE_MODEL,
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        temperature=0,
    )


def _strip_anchor(source: str) -> str:
    return source.split("#")[0]


def _parse_hits(hits_by_item, item_id: str) -> list[dict]:
    """兼容两种 per-item 命中形态：source 列表或 {source, ...} 记录。"""
    hits = hits_by_item.get(item_id, [])
    if hits and isinstance(hits[0], str):
        return [{"source": s} for s in hits]
    return hits


def _grade_map(item: dict) -> dict[str, int]:
    """challenge 题：qrels {source, grade, anchor} → {source: grade}；grade1 命中计分。"""
    grades = {}
    for q in item.get("qrels", []):
        grades[q["source"]] = max(grades.get(q["source"], 0), int(q.get("grade", 0)))
    return grades


def _expected_set(item: dict):
    """返回 (expected_files, grades)。challenge 题用 qrels，golden 题用 expected_sources。"""
    if item.get("qrels"):
        grades = _grade_map(item)
        return set(grades), grades
    expected = [s for s in item.get("expected_sources", []) if s]
    files = {_strip_anchor(s) for s in expected}
    return files, {f: 2 for f in files}


def _ndcg(rels: list[int], k: int) -> float:
    """nDCG@k：rel 为名次相关性（0/1/2）。"""
    import math

    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(rels[:k]))
    ideal = sorted(rels, reverse=True)[:k]
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def challenge_retrieval_metrics(items, hits_by_item, k_values=(5, 7)) -> dict:
    """检索挑战集（40 题）计分：Recall@k、MRR、source_precision@5、nDCG@5、
    hit_rate、hard-negative 泄漏、各题型明细。

    口径（acceptance.yaml 冻结的 retrieval_scoring）：
    - grade 2 完全相关、grade 1 部分相关（版本冲突旧版）：命中计分
    - hard_negatives 命中计入泄漏信号，不进 Recall/MRR 分母
    - source 去重后计名次
    - 回归锚点：origin 以 golden- 开头的 20 条
    """
    recall_at = {k: [] for k in k_values}
    mrr_vals = []
    prec5_vals = []
    ndcg5_vals = []
    leak_items = []
    scored = 0
    breakdown = {}

    for item in items:
        expected_files, grades = _expected_set(item)
        if not expected_files:
            continue
        scored += 1
        hits = _parse_hits(hits_by_item, item["id"])
        dedup_sources = list(dict.fromkeys(h["source"] for h in hits))

        for k in k_values:
            recall_at[k].append(1.0 if expected_files & set(dedup_sources[:k]) else 0.0)

        best = None
        for i, s in enumerate(dedup_sources, start=1):
            if s in expected_files and (best is None or i < best):
                best = i
        mrr_vals.append(1.0 / best if best else 0.0)

        prec5_vals.append(
            sum(1 for s in dedup_sources[:5] if s in expected_files) / 5.0
        )

        rels = [grades.get(s, 0) for s in dedup_sources]
        ndcg5_vals.append(_ndcg(rels, 5))

        leaked = [s for s in dedup_sources if s in set(item.get("hard_negatives", []))]
        if leaked:
            leak_items.append({"id": item["id"], "leaked": leaked})

        cat = item.get("category", "n/a")
        b = breakdown.setdefault(cat, {"scored_items": 0, "recall@5": [], "recall@7": [], "mrr": [], "leak_items": 0})
        b["scored_items"] += 1
        b["recall@5"].append(recall_at[5][-1])
        b["recall@7"].append(recall_at[7][-1])
        b["mrr"].append(mrr_vals[-1])
        b["leak_items"] += len(leaked)

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    anchor_ids = {item["id"] for item in items if item.get("origin", "").startswith("golden-")}
    anchor_recall5 = []
    for item in items:
        if item["id"] not in anchor_ids:
            continue
        expected_files, _ = _expected_set(item)
        if not expected_files:
            continue
        hits = _parse_hits(hits_by_item, item["id"])
        top5 = set(list(dict.fromkeys(h["source"] for h in hits))[:5])
        anchor_recall5.append(1.0 if expected_files & top5 else 0.0)

    return {
        "scored_items": scored,
        **{f"recall@{k}": _avg(v) for k, v in recall_at.items()},
        "mrr": _avg(mrr_vals),
        "hit_rate": _avg(recall_at[min(k_values)]),
        "source_precision@5": _avg(prec5_vals),
        "ndcg@5": _avg(ndcg5_vals),
        "hard_negative_leak_items": leak_items,
        "hard_negative_leak_count": len(leak_items),
        "breakdown": {
            cat: {
                "scored_items": b["scored_items"],
                "recall@5": _avg(b["recall@5"]),
                "recall@7": _avg(b["recall@7"]),
                "mrr": _avg(b["mrr"]),
                "leak_items": b["leak_items"],
            }
            for cat, b in breakdown.items()
        },
        "regression_anchor": {
            "ids": sorted(anchor_ids),
            "count": len(anchor_ids),
            "recall@5": _avg(anchor_recall5),
        },
    }


def retrieval_metrics(items, hits_by_item, k_values=(5, 7)) -> dict:
    """计算实际进入检索流程的题；HITL 澄清题由 clarification 指标单独评估。"""
    recall_at = {k: [] for k in k_values}
    mrr_vals = []
    section_total = section_hit = 0
    expired_hit_items = 0
    fixture_hit_items = 0
    scored = 0

    for item in items:
        if item.get("category") == "ambiguous_followup":
            continue
        expected = [s for s in item.get("expected_sources", []) if s]
        if not expected:
            continue
        scored += 1
        expected_files = {_strip_anchor(s) for s in expected}
        anchored = {_strip_anchor(s): s.split("#", 1)[1] for s in expected if "#" in s}
        hits = hits_by_item.get(item["id"], [])
        hit_sources = [h["source"] for h in hits]

        # Recall@K / Hit Rate
        for k in k_values:
            top = set(hit_sources[:k])
            recall_at[k].append(1.0 if expected_files & top else 0.0)

        # MRR: 每个 expected source 取最优(最早)排名
        best = None
        for i, s in enumerate(hit_sources, start=1):
            if s in expected_files and (best is None or i < best):
                best = i
        mrr_vals.append(1.0 / best if best else 0.0)

        # Section bonus
        for file, section in anchored.items():
            section_total += 1
            for h in hits:
                if h["source"] == file and any(
                    section in line
                    for line in h["content"].splitlines()
                    if line.lstrip().startswith("#")
                ):
                    section_hit += 1
                    break

        # expired / fixture 泄漏信号
        if set(hit_sources) & EXPIRED_SOURCES:
            expired_hit_items += 1
        if set(hit_sources) & FIXTURE_SOURCES:
            fixture_hit_items += 1

    return {
        "scored_items": scored,
        **{f"recall@{k}": (sum(v) / len(v) if v else 0.0) for k, v in recall_at.items()},
        "mrr": sum(mrr_vals) / len(mrr_vals) if mrr_vals else 0.0,
        "hit_rate": sum(1 for v in recall_at[min(k_values)] if v) / len(recall_at[min(k_values)]) if recall_at[min(k_values)] else 0.0,
        "section_hit_rate": section_hit / section_total if section_total else None,
        "expired_hit_items": expired_hit_items,
        "fixture_hit_items": fixture_hit_items,
    }


def _judge_bool(llm, prompt: str) -> bool:
    import json

    def _parse(text: str):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None

    resp = llm.invoke(prompt)
    parsed = _parse(resp.content)
    if isinstance(parsed, dict) and "refused" in parsed:
        return bool(parsed["refused"])
    resp = llm.invoke(prompt + "\n必须只输出 JSON，不要输出任何其他内容。")
    parsed = _parse(resp.content)
    if isinstance(parsed, dict) and "refused" in parsed:
        return bool(parsed["refused"])
    print(f"[warn] judge 输出无法解析为 JSON，判定为 False: {resp.content[:200]!r}")
    return False


def refusal_judge(llm, question: str, answer: str) -> bool:
    """回答是否明确拒答/声明无依据(而非编造内容)。"""
    return _judge_bool(llm, (
        "判断一个 RAG 系统的回答是否属于\"拒答\"：明确表示知识库中没有依据、无法回答、"
        "或拒绝编造。若回答尝试直接给出答案(哪怕错误)则不算拒答。\n"
        f"问题: {question}\n回答: {answer}\n"
        "只输出一个 JSON 对象：{\"refused\": true 或 false, \"reason\": \"一句话理由\"}。"
    ))


def clarification_judge(llm, question: str, answer: str, expected_answer: str) -> bool:
    """回答是否为澄清追问，且覆盖预期答案中列出的至少一个确认维度。"""
    return _judge_bool(llm, (
        "判断一个 RAG 系统的回答是否是\"先追问澄清\"而非直接作答：回答应提出澄清问题，"
        "且追问的维度至少覆盖预期澄清点之一。\n"
        f"问题: {question}\n回答: {answer}\n预期澄清点(参考): {expected_answer}\n"
        "只输出一个 JSON 对象：{\"refused\": true 或 false, \"reason\": \"一句话理由\"}。"
    ))


def _fallback_generation_metrics(rows, judge_llm) -> dict:
    """ragas 不可用时的兜底 LLM-judge 指标(与 ragas 语义对齐, 报告标注自实现)。"""
    from langchain_core.messages import HumanMessage

    faithfulness, relevancy, cprecision, crecall = [], [], [], []
    for row in rows:
        q, a, ctxs, ref = row["question"], row["answer"], row["contexts"], row["reference"]
        context_text = "\n\n".join(f"[C{i}]\n{c}" for i, c in enumerate(ctxs)) or "(无检索上下文)"

        # faithfulness: 回答的每句话是否被上下文支持
        resp = judge_llm.invoke(HumanMessage(content=(
            "把回答拆成陈述句，逐句判断是否被给定上下文支持。输出格式："
            "第一行 JSON 数组，元素为 true/false，与陈述句一一对应。\n"
            f"回答:\n{a}\n\n上下文:\n{context_text}"
        )))
        import json
        try:
            arr = json.loads(resp.content.split("\n")[0])
            faithfulness.append(sum(bool(x) for x in arr) / len(arr) if arr else 1.0)
        except Exception:
            faithfulness.append(None)

        # answer relevancy: 1-5 分
        resp = judge_llm.invoke(HumanMessage(content=(
            f"问题: {q}\n回答: {a}\n"
            "回答与问题的相关度打 1-5 分，只输出数字。"
        )))
        try:
            relevancy.append(int(resp.content.strip()[0]) / 5.0)
        except Exception:
            relevancy.append(None)

        # context precision: 每个上下文片段是否对回答该问题有用
        resp = judge_llm.invoke(HumanMessage(content=(
            f"问题: {q}\n上下文片段:\n{context_text}\n"
            "输出 JSON 数组，元素为 true/false，表示每个片段是否与回答该问题相关。"
        )))
        try:
            arr = json.loads(resp.content.split("\n")[0])
            cprecision.append(sum(bool(x) for x in arr) / len(arr) if arr else 0.0)
        except Exception:
            cprecision.append(None)

        # context recall: 参考答案的每个要点是否被上下文覆盖
        resp = judge_llm.invoke(HumanMessage(content=(
            f"参考答案:\n{ref}\n\n上下文:\n{context_text}\n"
            "参考答案的信息要点是否都能从上下文找到依据？1-5 分，只输出数字。"
        )))
        try:
            crecall.append(int(resp.content.strip()[0]) / 5.0)
        except Exception:
            crecall.append(None)

    def _avg(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    return {
        "faithfulness": _avg(faithfulness),
        "answer_relevancy": _avg(relevancy),
        "context_precision": _avg(cprecision),
        "context_recall": _avg(crecall),
        "implemented_by": "fallback_judge",
    }


def generation_metrics(rows, judge_llm):
    """rows: [{question, answer, contexts, reference}] → 4 指标均值。

    优先 ragas（Task 6 确认的 API 形态）；import 失败自动走兜底 judge。
    """
    try:
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.metrics import (
            faithfulness, answer_relevancy, context_precision, context_recall,
        )
        from langchain_huggingface import HuggingFaceEmbeddings
    except Exception:
        return _fallback_generation_metrics(rows, judge_llm)

    samples = [
        SingleTurnSample(
            user_input=row["question"],
            response=row["answer"],
            retrieved_contexts=row["contexts"],
            reference=row["reference"],
        )
        for row in rows
    ]
    dataset = EvaluationDataset(samples=samples)
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=LangchainLLMWrapper(judge_llm, bypass_n=True),
        embeddings=LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=config.DENSE_MODEL)),
    )
    df = result.to_pandas()
    return {
        "faithfulness": float(df["faithfulness"].mean()),
        "answer_relevancy": float(df["answer_relevancy"].mean()),
        "context_precision": float(df["context_precision"].mean()),
        "context_recall": float(df["context_recall"].mean()),
        "implemented_by": "ragas",
    }


def cost_estimate(input_tokens: int, output_tokens: int) -> float:
    """人民币元。"""
    return input_tokens / 1e6 * PRICE_IN_PER_M + output_tokens / 1e6 * PRICE_OUT_PER_M
