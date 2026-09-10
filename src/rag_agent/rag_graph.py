"""固定 RAG 单图（DESIGN 7.1）：START → retrieve → assemble_context → generate → validate_result → END。

- 零命中走确定性无证据分支（decision=refused，不调用生成模型）
- 不做 LLM 改写或工具循环；上下文按可复现规则（完整命中、累计截断）组装
- generate 输出结构化 GenerationOutput（decision + 候选引用）
- validate_result 机械校验引用；失败在预算内修复一次，仍失败抛
  ResultValidationError（L2 映射 result_validation_failed），不伪造引用
"""
from __future__ import annotations

from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

import config
from db.retrieval import RetrievalHit
from rag_agent.budget import get_budget
from rag_agent.prompts import get_rag_generate_prompt
from rag_agent.schemas import GenerationOutput
from rag_agent.structured import invoke_structured
from rag_agent.validation import (
    ResultValidationError,
    build_evidence_map,
    format_evidence_blocks,
    validate_generation,
)


class RagState(TypedDict, total=False):
    question: str
    hits: list[RetrievalHit]
    evidence_map: dict[str, RetrievalHit]
    context_text: str
    generation: GenerationOutput | None
    repair_feedback: str
    decision: str
    answer: str
    clarification_question: str
    limitations: list[str]
    citations: list  # schema.dto.Citation（保持 TypedDict 简单，用裸 list）


def _retrieve(state: RagState, retriever) -> dict:
    hits = retriever.search(state["question"], k=config.DEFAULT_RETRIEVAL_K)
    return {"hits": list(hits)}


def _route_after_retrieve(state: RagState) -> Literal["assemble_context", "no_evidence"]:
    return "assemble_context" if state.get("hits") else "no_evidence"


def _no_evidence(state: RagState) -> dict:
    # 确定性无证据分支：不调用生成模型
    return {
        "decision": "refused",
        "answer": "",
        "clarification_question": "",
        "limitations": ["知识库中没有检索到相关证据。"],
        "citations": [],
    }


def _assemble_context(state: RagState, evidence_store) -> dict:
    evidence_map = build_evidence_map(state["hits"], evidence_store)
    return {
        "evidence_map": evidence_map,
        "context_text": format_evidence_blocks(evidence_map, config.RAG_CONTEXT_MAX_CHARS),
    }


def _generate(state: RagState, llm) -> dict:
    feedback = state.get("repair_feedback", "")
    user_parts = [
        f"用户问题：{state['question']}",
        "",
        "本次检索到的证据：",
        state["context_text"],
    ]
    if feedback:
        user_parts.append(
            f"上一轮输出未通过引用校验，请修正后重新输出：\n{feedback}\n\n"
            "注意：citations 的 quote 必须与证据 Content 中的文字逐字符一致（含标点、"
            "加粗标记如 ** 等），从证据原文整段复制，不要改写或省略任何字符。"
        )
    user = HumanMessage(content="\n\n".join(user_parts))
    # DeepSeek 适配：json_mode + 手动解析（沿用 rewrite_query 的兼容方式）
    gen = invoke_structured(llm, GenerationOutput, [
        SystemMessage(content=get_rag_generate_prompt()),
        user,
    ])
    return {"generation": gen}


def _validate_result(state: RagState, config: dict | None) -> dict:
    gen = state.get("generation")
    if gen is None:
        raise ResultValidationError(["缺少生成结果"])
    citations, errors = validate_generation(gen, state.get("evidence_map", {}))
    if not errors:
        return {
            "decision": gen.decision,
            "answer": gen.answer,
            "clarification_question": gen.clarification_question,
            "limitations": gen.limitations,
            "citations": citations,
            "repair_feedback": "",
        }
    budget = get_budget(config)
    if budget.reserve_repair():
        return {"repair_feedback": "；".join(errors)}
    raise ResultValidationError(errors)


def _route_after_validate(state: RagState) -> Literal["generate", END]:
    return "generate" if state.get("repair_feedback") else END


def create_rag_graph(llm, retriever, evidence_store):
    graph = StateGraph(RagState)
    graph.add_node("retrieve", lambda state: _retrieve(state, retriever))
    graph.add_node("no_evidence", _no_evidence)
    graph.add_node("assemble_context", lambda state: _assemble_context(state, evidence_store))
    graph.add_node("generate", lambda state: _generate(state, llm))
    graph.add_node("validate_result", lambda state, config=None: _validate_result(state, config))

    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", _route_after_retrieve,
                                {"assemble_context": "assemble_context", "no_evidence": "no_evidence"})
    graph.add_edge("no_evidence", END)
    graph.add_edge("assemble_context", "generate")
    graph.add_edge("generate", "validate_result")
    graph.add_conditional_edges("validate_result", _route_after_validate, {"generate": "generate", END: END})
    return graph.compile()
