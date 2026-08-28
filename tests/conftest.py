from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from db.retrieval import RetrievalHit


class _Snap:
    def __init__(self, next=(), values=None):
        self.next = next
        self.values = values or {}


async def _astream_seq(chunks):
    for chunk, metadata in chunks:
        yield chunk, metadata


def make_stub(*, stream_chunks=None, first_next=(), final_next=(), final_values=None):
    rs = MagicMock()
    rs.agent_graph = MagicMock()
    rs.get_config = lambda thread_id=None, **kw: {
        "configurable": {"thread_id": thread_id or "t"},
        "recursion_limit": 50,
    }
    first = _Snap(next=first_next)
    final = _Snap(next=final_next, values=final_values or {})
    rs.agent_graph.aget_state = AsyncMock(side_effect=[first, final])
    rs.agent_graph.ainvoke = AsyncMock(return_value=None)
    rs.agent_graph.astream = lambda *a, **k: _astream_seq(stream_chunks or [])
    return rs


@pytest.fixture
def stub_normal():
    return make_stub(
        stream_chunks=[
            (AIMessageChunk(content="", tool_calls=[{"name": "search_child_chunks", "id": "tc1", "args": {}}]),
             {"langgraph_node": "orchestrator"}),
            (ToolMessage(content="hit data", tool_call_id="tc1"), {"langgraph_node": "tools"}),
            (AIMessageChunk(content="Hello "), {"langgraph_node": "aggregate_answers"}),
            (AIMessageChunk(content="world"), {"langgraph_node": "aggregate_answers"}),
        ],
        final_values={
            "messages": [AIMessage(content="Hello world")],
            "agent_answers": [{"contexts": [RetrievalHit(source="redis.md", parent_id="p1", content="cache")]}],
            "rewrittenQuestions": ["q1"],
        },
    )


@pytest.fixture
def stub_clarify():
    return make_stub(
        stream_chunks=[
            (AIMessageChunk(content='{"is_clear":false,"clarification_needed":"which one?"}'),
             {"langgraph_node": "rewrite_query"}),
        ],
        final_next=("request_clarification",),
        final_values={
            "messages": [AIMessage(content="which one?", name="clarification")],
            "agent_answers": [],
            "rewrittenQuestions": [],
        },
    )


@pytest.fixture
def client():
    """Bare client without lifespan (no RAGSystem init). /health reports starting."""
    import api.main as m

    return TestClient(m.app)


@pytest.fixture
def stub_client(request):
    """Client with a stubbed RAGSystem injected (lifespan bypassed)."""
    import api.main as m
    from api.deps import get_rag_system

    stub = request.getfixturevalue(request.param)
    m.app.state.rag_system = stub
    m.app.dependency_overrides[get_rag_system] = lambda: stub
    yield TestClient(m.app)
    m.app.dependency_overrides.clear()
    m.app.state.rag_system = None
