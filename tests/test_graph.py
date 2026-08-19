from unittest.mock import MagicMock

from langgraph.checkpoint.memory import InMemorySaver

from rag_agent.graph import create_agent_graph


def _mock_llm():
    llm = MagicMock()
    llm.bind_tools.return_value = MagicMock()
    return llm


def test_graph_compiles_with_default_checkpointer():
    graph = create_agent_graph(_mock_llm(), [])
    assert graph.checkpointer is not None
    for node in ("summarize_history", "rewrite_query", "request_clarification", "agent", "aggregate_answers"):
        assert node in graph.nodes


def test_graph_accepts_injected_checkpointer():
    injected = InMemorySaver()
    graph = create_agent_graph(_mock_llm(), [], checkpointer=injected)
    assert graph.checkpointer is injected
