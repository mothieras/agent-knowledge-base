"""T7 文档 MCP 工具测试（D6/PHASE2-T67 §4.4）。

resolve → list → read 窗口；条目 source.document 经 MCP save 校验；错误载荷。
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from api.mcp_app import create_mcp_server
from conftest import make_app_service
from core.doc_service import DocService
from core.entry_service import EntryService
from db.doc_store import DocStore
from db.entry_store import EntryStore


def _make_context(svc):
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
def doc_ctx(tmp_path):
    store = DocStore(str(tmp_path / "docs.db"))
    store.register(
        doc_id="docs/a.md", name="a.md", content="甲乙丙丁戊己庚辛壬癸" * 30,
        origin_file="data/a.md", index_id="idx-1",
        created_at="2026-09-20T00:00:00.000Z",
    )
    svc = make_app_service(generation=False)
    svc.documents = DocService(store)
    svc.entries = EntryService(EntryStore(":memory:"), doc_store=store)
    ctx = _make_context(svc)
    yield ctx
    store.close()


def call(server, ctx, name, args):
    return asyncio.run(server.call_tool(name, args, ctx))


def test_doc_tools_dual_channel(mcp_server, doc_ctx):
    result = call(mcp_server, doc_ctx, "resolve_document", {"source": "docs/a.md"})
    assert not result.is_error
    sc = result.structured_content
    assert sc["doc_id"] == "docs/a.md" and sc["latest_version"] == 1
    assert json.loads(result.content[0].text) == sc  # 双通道同载荷

    result = call(mcp_server, doc_ctx, "list_document_versions", {"doc_id": "docs/a.md"})
    assert [v["version"] for v in result.structured_content["versions"]] == [1]

    result = call(mcp_server, doc_ctx, "read_document",
                  {"doc_id": "docs/a.md", "version": 1, "offset": 0, "limit": 10})
    w = result.structured_content
    assert w["content"] == "甲乙丙丁戊己庚辛壬癸" and w["total_length"] == 300
    assert len(w["content_hash"]) == 64


def test_doc_tool_errors(mcp_server, doc_ctx):
    with pytest.raises(Exception, match="not_found"):
        call(mcp_server, doc_ctx, "resolve_document", {"source": "docs/nope.md"})
    with pytest.raises(Exception, match="not_found"):
        call(mcp_server, doc_ctx, "read_document",
             {"doc_id": "docs/a.md", "version": 9})
    with pytest.raises(Exception, match="invalid_request"):
        call(mcp_server, doc_ctx, "read_document",
             {"doc_id": "docs/a.md", "version": 1, "offset": -1})


def test_save_entry_with_document_ref_via_mcp(mcp_server, doc_ctx):
    result = call(mcp_server, doc_ctx, "save_entry", {
        "type": "knowledge", "body": "派生条目", "author": "pi/0.86.0",
        "scope": {"kind": "global"},
        "source": {"document": {"doc_id": "docs/a.md", "version": 1,
                                "span_start": 0, "span_end": 2}},
    })
    assert not result.is_error
    assert result.structured_content["source"]["document"]["version"] == 1
    with pytest.raises(Exception, match="invalid_source"):
        call(mcp_server, doc_ctx, "save_entry", {
            "type": "knowledge", "body": "派生条目", "author": "pi/0.86.0",
            "scope": {"kind": "global"},
            "source": {"document": {"doc_id": "docs/a.md", "version": 99}},
        })
