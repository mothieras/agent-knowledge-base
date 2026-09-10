from unittest.mock import MagicMock

from rag_agent.graph import create_agent_graph, create_agent_subgraph
from rag_agent.rag_graph import create_rag_graph


def _mock_llm():
    llm = MagicMock()
    llm.bind_tools.return_value = MagicMock()
    llm.with_structured_output.return_value = MagicMock()
    return llm


def _mock_evidence_store():
    store = MagicMock()
    store.evidence_id_for.return_value = "aaaabbbb:doc_p0:0:10"
    return store


def test_agent_graph_compiles_single_shot():
    graph = create_agent_graph(_mock_llm(), [], _mock_evidence_store())
    # 单次化：无 summarize_history / request_clarification 暂停节点
    for node in ("rewrite_query", "agent", "aggregate_answers", "validate_result"):
        assert node in graph.nodes
    assert "summarize_history" not in graph.nodes


def test_rag_graph_compiles():
    graph = create_rag_graph(_mock_llm(), MagicMock(), _mock_evidence_store())
    for node in ("retrieve", "assemble_context", "generate", "validate_result", "no_evidence"):
        assert node in graph.nodes


def test_agent_subgraph_compiles():
    subgraph = create_agent_subgraph(MagicMock(), [], MagicMock())
    for node in ("orchestrator", "tools", "compress_context", "collect_answer"):
        assert node in subgraph.nodes
