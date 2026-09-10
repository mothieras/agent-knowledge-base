from typing import Literal
from langgraph.graph import END
from langgraph.types import Send

import config
from .graph_state import State, AgentState
from core.execution_logger import log_route
from rag_agent.budget import get_budget

def route_after_rewrite(state: State) -> str | list[Send]:
    if not state.get("questionIsClear", False):
        log_route("after_rewrite", "clarification_required", state)
        return END  # decision=clarification_required 已在 rewrite 节点落为单次终态
    sends = [
        Send("agent", {"question": query, "question_index": idx, "messages": []})
        for idx, query in enumerate(state["rewrittenQuestions"])
    ]
    log_route("after_rewrite", [f"Send(agent, {q[:40]!r})" for q in state["rewrittenQuestions"]], state)
    return sends

def route_after_orchestrator_call(state: AgentState, config=None) -> Literal["tools", "fallback_response", "collect_answer"]:
    budget = get_budget(config)
    budget.iterations += 1

    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []

    if not tool_calls:
        log_route("after_orchestrator_call", "collect_answer", state)
        return "collect_answer"

    # 调用工具前预留预算（共享预算，父子图同一份上限），不能执行后才发现超限
    if budget.iterations > budget.max_iterations or not budget.reserve_tool_calls(len(tool_calls)):
        log_route("after_orchestrator_call", "fallback_response (budget exhausted)", state)
        return "fallback_response"

    log_route("after_orchestrator_call", "tools", state)
    return "tools"

def route_after_validate(state: State) -> str:
    return "aggregate_answers" if state.get("repair_feedback") else END
