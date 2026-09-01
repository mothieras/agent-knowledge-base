"""T4 评测指标：检索层自算 + ragas 封装(或兜底 judge) + 拒答/澄清 judge。

检索层命中判定锚点: expected_sources 文件名级匹配(metadata.source == source，
带 #anchor 的条目按去锚后路径比)；过期/背景源算命中，另计 expired_hits 信号。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "project"))

from langchain_openai import ChatOpenAI
import config

# deepseek-chat 定价(元/百万 token)，2026-08；更新价格改这里
PRICE_IN_PER_M = 0.28
PRICE_OUT_PER_M = 1.68

# data/fixtures/manifest.json 中 expired_date 非空的受治理 fixture 源；
# golden set 30 题不应命中它们，expired_hit_items 是 M3 过滤门禁的前置信号
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
