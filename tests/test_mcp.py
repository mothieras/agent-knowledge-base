"""MCP 工具测试：注册、in-process 调用、文本/结构化双通道同载荷。

不依赖真实网络握手；用 MCPServer.call_tool + 构造的 Context 直接调工具。
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from api.mcp_app import create_mcp_server
from conftest import _FakeGeneration, make_app_service, make_retrieval_service


def _make_context(svc) -> "Context":
    from mcp.server.context import ServerRequestContext
    from mcp.server.mcpserver.context import Context

    rc = ServerRequestContext(
        session=MagicMock(),
        lifespan_context=svc,
        protocol_version="2025-11-25",
        method="tools/call",
        params={},
        request_id=1,
    )
    return Context(request_context=rc)


@pytest.fixture
def mcp_server():
    return create_mcp_server(include_ask=False)


@pytest.fixture
def mcp_server_with_ask():
    return create_mcp_server(include_ask=True)


@pytest.fixture
def mcp_ctx():
    return _make_context(make_app_service(generation=False))


@pytest.fixture
def mcp_ctx_gen():
    svc = make_app_service(generation=True)
    svc.generation = _FakeGeneration()
    return _make_context(svc)


def test_tools_registered_without_generation(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    # 未启用生成能力时不发布 ask_knowledge；条目工具（PHASE2 T2）与文档工具（D6）常驻
    assert names == {
        "search_knowledge", "get_context",
        "save_entry", "get_entry", "revise_entry", "entry_lifecycle", "list_entry_revisions",
        "search_entries",
        "resolve_document", "list_document_versions", "read_document",
    }
    by_name = {t.name: t for t in tools}
    assert by_name["search_knowledge"].output_schema is not None


def test_ask_tool_published_when_generation_enabled(mcp_server_with_ask):
    tools = asyncio.run(mcp_server_with_ask.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "search_knowledge", "get_context", "ask_knowledge",
        "save_entry", "get_entry", "revise_entry", "entry_lifecycle", "list_entry_revisions",
        "search_entries",
        "resolve_document", "list_document_versions", "read_document",
    }
    assert {t.name: t for t in tools}["ask_knowledge"].output_schema is not None


def test_search_knowledge_returns_structured_content(mcp_server, mcp_ctx):
    result = asyncio.run(
        mcp_server.call_tool("search_knowledge", {"query": "RAG 是什么", "k": 7}, mcp_ctx)
    )
    assert not result.is_error
    assert result.structured_content is not None
    sc = result.structured_content
    assert sc["index_id"].startswith("sha256:")
    assert sc["returned_k"] == 1
    assert sc["hits"][0]["evidence_id"]
    # 文本通道与结构化通道同载荷（Pi 实测：只读文本通道的客户端也要能拿到正文）
    text_payload = json.loads(result.content[0].text)
    assert text_payload == sc
    assert sc["hits"][0]["content"] in result.content[0].text


def test_get_context_window(mcp_server, mcp_ctx):
    search = asyncio.run(
        mcp_server.call_tool("search_knowledge", {"query": "RAG", "k": 7}, mcp_ctx)
    )
    eid = search.structured_content["hits"][0]["evidence_id"]
    result = asyncio.run(
        mcp_server.call_tool(
            "get_context", {"evidence_id": eid, "offset": 0, "limit": 100}, mcp_ctx
        )
    )
    assert not result.is_error
    sc = result.structured_content
    assert sc["total_length"] > 0
    assert sc["evidence_id"] == eid


def test_get_context_invalid_id(mcp_server, mcp_ctx):
    with pytest.raises(Exception):
        asyncio.run(
            mcp_server.call_tool(
                "get_context", {"evidence_id": "garbage", "offset": 0, "limit": 100},
                mcp_ctx,
            )
        )


def test_search_knowledge_validates_args(mcp_server, mcp_ctx):
    with pytest.raises(Exception):
        asyncio.run(
            mcp_server.call_tool("search_knowledge", {"query": "", "k": 7}, mcp_ctx)
        )


def test_ask_knowledge_returns_decision_protocol(mcp_server_with_ask, mcp_ctx_gen):
    result = asyncio.run(
        mcp_server_with_ask.call_tool(
            "ask_knowledge", {"message": "什么是 RAG？", "mode": "agentic"}, mcp_ctx_gen
        )
    )
    assert not result.is_error
    sc = result.structured_content
    assert sc["decision"] == "answered"
    assert sc["mode"] == "agentic"
    assert sc["usage"]["model_calls"] == 1
    # 文本通道承载完整 JSON（双通道同载荷）
    assert json.loads(result.content[0].text)["decision"] == "answered"


def test_ask_knowledge_not_available_without_generation(mcp_server_with_ask, mcp_ctx):
    with pytest.raises(Exception):
        asyncio.run(
            mcp_server_with_ask.call_tool(
                "ask_knowledge", {"message": "什么是 RAG？", "mode": "rag"}, mcp_ctx
            )
        )


def test_ask_knowledge_validates_mode(mcp_server_with_ask, mcp_ctx_gen):
    with pytest.raises(Exception):
        asyncio.run(
            mcp_server_with_ask.call_tool(
                "ask_knowledge", {"message": "x", "mode": "chat"}, mcp_ctx_gen
            )
        )
