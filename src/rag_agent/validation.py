"""引用与 decision 校验（DESIGN 7.3）：两种图共用的机械校验。

规则：
- evidence_id 必须属于本次实际取得的证据集合（不在集合内 = 伪造引用）
- quote 必须是证据原文的精确子串；span 由 quote 在命中内容中的位置推导
  （parent 文本坐标 = hit.span_start + 命中内偏移）
- answered 必须有非空 answer 与至少一条有效引用
- clarification_required 必须带澄清问题，且不携带引用
- refused 不携带引用

失败由调用方（validate_result 节点）在预算内修复一次；修复后仍失败必须
报结果校验错误，不伪造引用、不冒充正常拒答。
"""
from __future__ import annotations

from typing import Optional

from db.retrieval import RetrievalHit
from schema.dto import Citation, Span
from rag_agent.schemas import GenerationOutput


class ResultValidationError(Exception):
    """引用/decision 校验失败且修复预算耗尽（L2 映射为 result_validation_failed）。"""

    def __init__(self, errors: list[str]):
        super().__init__("；".join(errors))
        self.errors = errors


def validate_generation(gen: GenerationOutput,
                        evidence_map: dict[str, RetrievalHit]) -> tuple[list[Citation], list[str]]:
    """返回 (有效引用, 错误列表)。空错误 = 通过。"""
    errors: list[str] = []
    citations: list[Citation] = []

    if gen.decision == "answered":
        if not gen.answer.strip():
            errors.append("decision=answered 但 answer 为空")
        if not gen.citations:
            errors.append("decision=answered 但没有任何引用")
    elif gen.decision == "clarification_required":
        if not gen.clarification_question.strip():
            errors.append("decision=clarification_required 但缺少澄清问题")
    elif gen.decision != "refused":
        errors.append(f"非法 decision: {gen.decision!r}")

    if gen.decision != "answered":
        # 非 answered 终态不发布引用：候选 citations 直接丢弃（无意义且易伪造）
        return [], errors

    for i, draft in enumerate(gen.citations, start=1):
        hit = evidence_map.get(draft.evidence_id)
        if hit is None:
            errors.append(f"引用 #{i} evidence_id={draft.evidence_id} 不在本次证据集合中")
            continue
        if hit.span_start is None or hit.span_end is None:
            errors.append(f"引用 #{i} 证据缺少 span，无法定位")
            continue
        offset = hit.content.find(draft.quote)
        if offset < 0:
            errors.append(f"引用 #{i} 的 quote 不是证据原文的精确子串: {draft.quote[:60]!r}")
            continue
        citations.append(Citation(
            citation_id=f"c{len(citations) + 1}",
            evidence_id=draft.evidence_id,
            source=hit.source,
            chunk_id=hit.chunk_id,
            span=Span(start=hit.span_start + offset,
                      end=hit.span_start + offset + len(draft.quote)),
            quote=draft.quote,
        ))
    return citations, errors


def format_evidence_blocks(evidence_map: dict[str, RetrievalHit],
                           max_chars: int) -> str:
    """可复现的证据上下文：按插入序取完整命中，累计超过 max_chars 即停止。

    每条命中要么完整出现（携带 evidence_id），要么不出现——不做半条截断，
    避免 LLM 引用被截断的片段导致校验失败。
    """
    blocks: list[str] = []
    total = 0
    for evidence_id, hit in evidence_map.items():
        block = (
            f"[Evidence ID: {evidence_id}]\n"
            f"Source: {hit.source}\n"
            f"Content:\n{hit.content}"
        )
        if blocks and total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n---\n\n".join(blocks)


def build_evidence_map(hits: list[RetrievalHit], evidence_store) -> dict[str, RetrievalHit]:
    """本次检索证据集：evidence_id → RetrievalHit（去重、缺 span 的不可引用，跳过）。"""
    from db.evidence_store import EvidenceError

    evidence_map: dict[str, RetrievalHit] = {}
    for hit in hits:
        if hit.span_start is None or hit.span_end is None:
            continue
        try:
            evidence_id = evidence_store.evidence_id_for(hit)
        except EvidenceError:
            continue
        evidence_map.setdefault(evidence_id, hit)
    return evidence_map
