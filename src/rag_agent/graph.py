"""双图（agentic）单次化（DESIGN 7.2）：主图 + 检索子图，无历史摘要、无 interrupt。

主图：START → rewrite_query → (Send 子问题 → aggregate_answers → validate_result → END
                                | clarification_required → END)
子图：orchestrator ↔ tools → optional compression → evidence + answer

共享请求级预算经 config["configurable"]["budget"] 贯穿主图与全部子图；
决策与引用校验在 validate_result 完成，修复一次后仍失败抛 ResultValidationError。
"""
from functools import partial

from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode

from .graph_state import State, AgentState
from core.execution_logger import logged_node
from .nodes import (
    aggregate_answers,
    collect_answer,
    compress_context,
    fallback_response,
    orchestrator,
    rewrite_query,
    should_compress_context,
    validate_result,
)
from .edges import route_after_orchestrator_call, route_after_rewrite, route_after_validate


def create_agent_subgraph(llm_with_tools, tools_list, llm):
    """Compile the AgentState subgraph (orchestrator → tools → compress → answer).

    Exposed so the retrieval path (search → get_parent → compress → collect) can
    be driven in tests with an in-memory Retriever and a stubbed LLM.
    """
    tool_node = ToolNode(tools_list)

    agent_builder = StateGraph(AgentState)
    agent_builder.add_node("orchestrator", logged_node("agent.orchestrator", partial(orchestrator, llm_with_tools=llm_with_tools)))
    agent_builder.add_node("tools", tool_node)
    agent_builder.add_node("compress_context", logged_node("agent.compress_context", partial(compress_context, llm=llm)))
    agent_builder.add_node("fallback_response", logged_node("agent.fallback_response", partial(fallback_response, llm=llm)))
    agent_builder.add_node("should_compress_context", logged_node("agent.should_compress_context", should_compress_context))
    agent_builder.add_node("collect_answer", logged_node("agent.collect_answer", collect_answer))

    agent_builder.add_edge(START, "orchestrator")
    agent_builder.add_conditional_edges("orchestrator", route_after_orchestrator_call,
                                        {"tools": "tools", "fallback_response": "fallback_response", "collect_answer": "collect_answer"})
    agent_builder.add_edge("tools", "should_compress_context")
    agent_builder.add_edge("compress_context", "orchestrator")
    agent_builder.add_edge("fallback_response", "collect_answer")
    agent_builder.add_edge("collect_answer", END)

    return agent_builder.compile()


def create_agent_graph(llm, tools_list, evidence_store):
    llm_with_tools = llm.bind_tools(tools_list)

    agent_subgraph = create_agent_subgraph(llm_with_tools, tools_list, llm)

    graph_builder = StateGraph(State)
    graph_builder.add_node("rewrite_query", logged_node("main.rewrite_query", partial(rewrite_query, llm=llm)))
    graph_builder.add_node("agent", agent_subgraph)
    graph_builder.add_node("aggregate_answers", logged_node("main.aggregate_answers", partial(aggregate_answers, llm=llm, evidence_store=evidence_store)))
    graph_builder.add_node("validate_result", logged_node("main.validate_result", validate_result))

    graph_builder.add_edge(START, "rewrite_query")
    graph_builder.add_conditional_edges("rewrite_query", route_after_rewrite)
    graph_builder.add_edge("agent", "aggregate_answers")
    graph_builder.add_edge("aggregate_answers", "validate_result")
    graph_builder.add_conditional_edges("validate_result", route_after_validate,
                                        {"aggregate_answers": "aggregate_answers", END: END})

    return graph_builder.compile()
