from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from db.retrieval import RetrievalHit, hit_from_stored
from rag_agent.graph import create_agent_subgraph
from rag_agent.tools import ToolFactory
from fixtures.in_memory_retriever import InMemoryRetriever


def test_hit_from_stored_computes_span_and_coerces_ints():
    hit = hit_from_stored(
        "hello world",
        {
            "source": "a.md",
            "parent_id": "a_p0",
            "chunk_id": "a_p0_c1",
            "start_index": 7,
            "priority": "10",  # JSON 往返可能给出字符串
        },
    )
    assert hit.span_start == 7
    assert hit.span_end == 18  # start + len(content)
    assert hit.priority == 10
    assert hit.chunk_id == "a_p0_c1"
    assert hit.span_end - hit.span_start == len(hit.content)


def test_hit_from_stored_without_start_index_has_no_span():
    hit = hit_from_stored("x", {"source": "a.md", "parent_id": "a_p0"})
    assert hit.span_start is None
    assert hit.span_end is None


def _make_retriever():
    parent = RetrievalHit(
        source="redis.md",
        parent_id="p0",
        content="parent body discusses cache eviction policy",
        version="04b8ea2",
        priority=10,
    )
    child = RetrievalHit(
        source="redis.md",
        parent_id="p0",
        content="child excerpt mentions cache",
        version="04b8ea2",
        priority=10,
    )
    return InMemoryRetriever(parents=[parent], children=[child])


def _tool_call(name, args, id_):
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


def test_search_getparent_collect_flows_typed_retrievalhit():
    retriever = _make_retriever()
    tools = ToolFactory(retriever).create_tools()

    search_call = AIMessage(content="", tool_calls=[_tool_call("search_child_chunks", {"query": "cache"}, "tc1")])
    parent_call = AIMessage(content="", tool_calls=[_tool_call("retrieve_parent_chunks", {"parent_id": "p0"}, "tc2")])
    final = AIMessage(content="final answer uses cache eviction", tool_calls=[])

    llm_with_tools = MagicMock()
    llm_with_tools.invoke = MagicMock(side_effect=[search_call, parent_call, final])

    subgraph = create_agent_subgraph(llm_with_tools, tools, MagicMock())

    state = subgraph.invoke({"question": "what about cache", "question_index": 0, "messages": []})

    contexts = state["retrieved_contexts"]
    assert contexts, "expected retrieved contexts"
    assert all(isinstance(c, RetrievalHit) for c in contexts)

    # two-hop: the child (search) and the parent (get_parent) both landed, typed.
    assert {c.parent_id for c in contexts} == {"p0"}
    assert {c.source for c in contexts} == {"redis.md"}
    assert any(c.version == "04b8ea2" for c in contexts)

    answers = state.get("agent_answers", [])
    assert answers
    ans_ctx = answers[0].get("contexts", [])
    assert ans_ctx and isinstance(ans_ctx[0], RetrievalHit)
    assert answers[0]["answer"] == "final answer uses cache eviction"


def test_compress_context_runs_with_typed_hits(monkeypatch):
    # Force the compress branch: any non-empty message set exceeds a 0 threshold.
    monkeypatch.setattr("rag_agent.nodes.BASE_TOKEN_THRESHOLD", 0)

    retriever = _make_retriever()
    tools = ToolFactory(retriever).create_tools()

    search_call = AIMessage(content="", tool_calls=[_tool_call("search_child_chunks", {"query": "cache"}, "tc1")])
    final = AIMessage(content="compressed then answered", tool_calls=[])

    llm_with_tools = MagicMock()
    llm_with_tools.invoke = MagicMock(side_effect=[search_call, final])

    llm = MagicMock()
    llm.invoke = MagicMock(return_value=AIMessage(content="compressed research summary"))

    subgraph = create_agent_subgraph(llm_with_tools, tools, llm)

    state = subgraph.invoke({"question": "what about cache", "question_index": 0, "messages": []})

    # compress ran and produced a summary.
    assert state.get("context_summary")
    assert "compressed research summary" in state["context_summary"]

    # typed hits survived compress into the collected answer's contexts.
    answers = state.get("agent_answers", [])
    assert answers
    ans_ctx = answers[0].get("contexts", [])
    assert ans_ctx and isinstance(ans_ctx[0], RetrievalHit)
    assert answers[0]["answer"] == "compressed then answered"
