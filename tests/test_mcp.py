"""MCP 工具测试：注册、in-process 调用、structuredContent 与文本摘要分离。

不依赖真实网络握手；用 MCPServer.call_tool + 构造的 Context 直接调工具。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from api.mcp_app import create_mcp_server
from conftest import make_app_service


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
    return create_mcp_server()


@pytest.fixture
def mcp_ctx():
    return _make_context(make_app_service())


def test_tools_registered(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    assert names == {"search_knowledge", "get_context"}
    # structured_output=True 发布 outputSchema（Pi 结构化消费依据）
    by_name = {t.name: t for t in tools}
    assert by_name["search_knowledge"].output_schema is not None


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
    # 文本部分只给摘要，不重复完整正文
    text = result.content[0].text
    assert "命中" in text
    assert sc["hits"][0]["content"] not in text


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
