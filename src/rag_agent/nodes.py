from typing import Literal, Set
from langchain_core.messages import SystemMessage, HumanMessage, RemoveMessage, AIMessage, ToolMessage
from langgraph.types import Command
from .graph_state import State, AgentState
from .schemas import QueryAnalysis, GenerationOutput
from .prompts import *
from utils import estimate_context_tokens
from db.retrieval import RetrievalHit
import config
from config import BASE_TOKEN_THRESHOLD, TOKEN_GROWTH_FACTOR
from rag_agent.validation import ResultValidationError, build_evidence_map, format_evidence_blocks, validate_generation
from rag_agent.budget import get_budget
from rag_agent.structured import invoke_structured


def _name_internal_message(message, name):
    """Tag a subgraph-only message so it is not treated as chat history."""
    return message.model_copy(update={"name": name})

def _retrieval_contexts(messages) -> list[RetrievalHit]:
    contexts = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None)
        if isinstance(artifact, list):
            contexts.extend(h for h in artifact if isinstance(h, RetrievalHit))
    return list(dict.fromkeys(contexts))


# --- Main graph nodes (single-shot) ---

def rewrite_query(state: State, llm):
    last_message = state["messages"][-1]
    current_query = str(last_message.content).strip()
    response = invoke_structured(llm, QueryAnalysis, [
        SystemMessage(content=(
            get_rewrite_query_prompt()
            + "\n\nRespond with a single JSON object containing exactly these keys: "
            '"is_clear" (boolean), "questions" (array of strings), "clarification_needed" (string).'
        )),
        HumanMessage(content=f"User Query:\n{current_query}"),
    ])

    if response.questions and response.is_clear:
        return {
            "questionIsClear": True,
            "originalQuery": current_query,
            "rewrittenQuestions": response.questions,
        }

    clarification = (response.clarification_needed
                     if response.clarification_needed and len(response.clarification_needed.strip()) > 10
                     else "I need more information to understand your question.")
    return {
        "questionIsClear": False,
        "originalQuery": "",
        "rewrittenQuestions": [],
        "decision": "clarification_required",
        "clarification_question": clarification,
        "answer": "",
        "limitations": [],
        "citations": [],
    }


# --- Agent (subgraph) Nodes ---

def orchestrator(state: AgentState, llm_with_tools):
    context_summary = state.get("context_summary", "").strip()
    sys_msg = SystemMessage(content=get_orchestrator_prompt())
    summary_injection = (
        [HumanMessage(content=f"[COMPRESSED CONTEXT FROM PRIOR RESEARCH]\n\n{context_summary}")]
        if context_summary else []
    )
    if not state.get("messages"):
        human_msg = HumanMessage(content=state["question"], name="agent_question")
        force_search = HumanMessage(content="YOU MUST CALL 'search_child_chunks' AS THE FIRST STEP TO ANSWER THIS QUESTION.")
        response = llm_with_tools.invoke([sys_msg] + summary_injection + [human_msg, force_search])
        response = _name_internal_message(response, "agent_response")
        return {"messages": [human_msg, response]}

    response = llm_with_tools.invoke([sys_msg] + summary_injection + state["messages"])
    response = _name_internal_message(response, "agent_response")
    return {"messages": [response]}


def fallback_response(state: AgentState, llm):
    seen = set()
    unique_contents = []
    for m in state["messages"]:
        if isinstance(m, ToolMessage) and m.content not in seen:
            unique_contents.append(m.content)
            seen.add(m.content)

    context_summary = state.get("context_summary", "").strip()

    context_parts = []
    if context_summary:
        context_parts.append(f"## Compressed Research Context (from prior iterations)\n\n{context_summary}")
    if unique_contents:
        context_parts.append(
            "## Retrieved Data (current iteration)\n\n" +
            "\n\n".join(f"--- DATA SOURCE {i} ---\n{content}" for i, content in enumerate(unique_contents, 1))
        )

    context_text = "\n\n".join(context_parts) if context_parts else "No data was retrieved from the documents."

    prompt_content = (
        f"USER QUERY: {state.get('question')}\n\n"
        f"{context_text}\n\n"
        f"INSTRUCTION:\nProvide the best possible answer using only the data above."
    )
    response = llm.invoke([SystemMessage(content=get_fallback_response_prompt()), HumanMessage(content=prompt_content)])
    response = _name_internal_message(response, "agent_response")
    return {"messages": [response]}


def should_compress_context(state: AgentState) -> Command[Literal["compress_context", "orchestrator"]]:
    messages = state["messages"]

    new_ids: Set[str] = set()
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc["name"] == "retrieve_parent_chunks":
                    raw = tc["args"].get("parent_id") or tc["args"].get("id") or tc["args"].get("ids") or []
                    if isinstance(raw, str):
                        new_ids.add(f"parent::{raw}")
                    else:
                        new_ids.update(f"parent::{r}" for r in raw)

                elif tc["name"] == "search_child_chunks":
                    query = tc["args"].get("query", "")
                    if query:
                        new_ids.add(f"search::{query}")
            break

    updated_ids = state.get("retrieval_keys", set()) | new_ids

    current_token_messages = estimate_context_tokens(messages)
    current_token_summary = estimate_context_tokens([HumanMessage(content=state.get("context_summary", ""))])
    current_tokens = current_token_messages + current_token_summary

    max_allowed = BASE_TOKEN_THRESHOLD + int(current_token_summary * TOKEN_GROWTH_FACTOR)

    goto = "compress_context" if current_tokens > max_allowed else "orchestrator"
    return Command(
        update={
            "retrieval_keys": updated_ids,
            "retrieved_contexts": _retrieval_contexts(messages),
        },
        goto=goto,
    )


def compress_context(state: AgentState, llm):
    messages = state["messages"]
    existing_summary = state.get("context_summary", "").strip()

    if not messages:
        return {}

    conversation_text = f"USER QUESTION:\n{state.get('question')}\n\nConversation to compress:\n\n"
    if existing_summary:
        conversation_text += f"[PRIOR COMPRESSED CONTEXT]\n{existing_summary}\n\n"

    for msg in messages[1:]:
        if isinstance(msg, AIMessage):
            tool_calls_info = ""
            if getattr(msg, "tool_calls", None):
                calls = ", ".join(f"{tc['name']}({tc['args']})" for tc in msg.tool_calls)
                tool_calls_info = f" | Tool calls: {calls}"
            conversation_text += f"[ASSISTANT{tool_calls_info}]\n{msg.content or '(tool call only)'}\n\n"
        elif isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "tool")
            conversation_text += f"[TOOL RESULT — {tool_name}]\n{msg.content}\n\n"

    summary_response = llm.invoke([SystemMessage(content=get_context_compression_prompt()), HumanMessage(content=conversation_text)])
    new_summary = summary_response.content

    retrieved_ids: Set[str] = state.get("retrieval_keys", set())
    if retrieved_ids:
        parent_ids = sorted(r for r in retrieved_ids if r.startswith("parent::"))
        search_queries = sorted(r.replace("search::", "") for r in retrieved_ids if r.startswith("search::"))

        block = "\n\n---\n**Already executed (do NOT repeat):**\n"
        if parent_ids:
            block += "Parent chunks retrieved:\n" + "\n".join(f"- {p.replace('parent::', '')}" for p in parent_ids) + "\n"
        if search_queries:
            block += "Search queries already run:\n" + "\n".join(f"- {q}" for q in search_queries) + "\n"
        new_summary += block

    return {"context_summary": new_summary, "messages": [RemoveMessage(id=m.id) for m in messages[1:]]}


def collect_answer(state: AgentState):
    last_message = state["messages"][-1]
    is_valid = isinstance(last_message, AIMessage) and last_message.content and not last_message.tool_calls
    answer = last_message.content if is_valid else "Unable to generate an answer."
    return {
        "final_answer": answer,
        "agent_answers": [{
            "index": state["question_index"],
            "question": state["question"],
            "answer": answer,
            "contexts": state.get("retrieved_contexts", []),
        }]
    }


# --- Main graph: aggregate + validate ---

def aggregate_answers(state: State, llm, evidence_store):
    """把子图答案与本次实际检索到的证据合成单次终态（结构化 decision + 候选引用）。"""
    all_contexts: list[RetrievalHit] = []
    for ans in state.get("agent_answers", []):
        all_contexts.extend(ans.get("contexts", []))
    all_contexts = list(dict.fromkeys(all_contexts))

    evidence_map = build_evidence_map(all_contexts, evidence_store)
    context_text = format_evidence_blocks(evidence_map, config.RAG_CONTEXT_MAX_CHARS)

    sorted_answers = sorted(state.get("agent_answers", []), key=lambda x: x["index"])
    formatted_answers = "\n".join(
        f"Sub-question {i}: {ans['question']}\nSub-answer: {ans['answer']}"
        for i, ans in enumerate(sorted_answers, start=1)
    ) or "(no sub-answers)"

    feedback = state.get("repair_feedback", "")
    user_parts = [
        f"Original user question: {state['originalQuery']}",
        "",
        formatted_answers,
        "",
        "Evidence retrieved during this request:",
        context_text,
    ]
    if feedback:
        user_parts.append(f"上一轮输出未通过引用校验，请修正：{feedback}")
    user_message = HumanMessage(content="\n\n".join(user_parts))

    gen = invoke_structured(llm, GenerationOutput, [
        SystemMessage(content=get_aggregation_prompt()), user_message,
    ])

    return {"generation": gen, "evidence_map": evidence_map, "context_text": context_text}


def validate_result(state: State, config=None):
    """主图终态校验：引用机械校验 + 预算内修复一次，仍失败抛 ResultValidationError。"""
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
            "citations": [c.model_dump() for c in citations],
            "repair_feedback": "",
        }
    budget = get_budget(config)
    if budget.reserve_repair():
        return {"repair_feedback": "；".join(errors)}
    raise ResultValidationError(errors)
