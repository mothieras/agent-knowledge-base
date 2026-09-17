"""T2 条目 MCP 工具测试：注册、双通道同载荷、updates 修订语义、错误载荷。

真实协议层冒烟见 src/smoke_mcp.py（Streamable HTTP 全链路）。
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from api.mcp_app import create_mcp_server
from conftest import make_app_service

ENTRY_TOOLS = {"save_entry", "get_entry", "revise_entry", "entry_lifecycle", "list_entry_revisions"}


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
def ctx():
    return _make_context(make_app_service(generation=False))


def call(server, ctx, name, args):
    return asyncio.run(server.call_tool(name, args, ctx))


def save(server, ctx, **kw):
    args = {"type": "memory", "body": "用户偏好：默认中文回复。", "author": "pi/0.85.1"}
    args.update(kw)
    return call(server, ctx, "save_entry", args)


def test_entry_tools_registered(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    assert ENTRY_TOOLS <= names
    by_name = {t.name: t for t in tools}
    assert by_name["save_entry"].output_schema is not None


def test_save_and_get_dual_channel(mcp_server, ctx):
    result = save(mcp_server, ctx, scope={"kind": "projects", "projects": ["RAG "]})
    assert not result.is_error
    sc = result.structured_content
    assert sc["scope"]["projects"] == ["rag"]
    assert sc["revision"] == 1
    # 双通道同载荷
    assert json.loads(result.content[0].text) == sc

    got = call(mcp_server, ctx, "get_entry", {"entry_id": sc["id"]})
    assert got.structured_content == sc


def test_revise_via_updates(mcp_server, ctx):
    entry = save(mcp_server, ctx, expires_at="2027-01-01T00:00:00Z").structured_content
    # 键存在 = 修改；未提供键 = 不变
    r = call(mcp_server, ctx, "revise_entry", {
        "entry_id": entry["id"], "expected_revision": 1, "author": "cc/1.0",
        "updates": {"body": "用户偏好：默认中文回复（修订）。"},
    }).structured_content
    assert r["revision"] == 2
    assert r["expires_at"] == entry["expires_at"]
    # 值为 null = 清除
    r = call(mcp_server, ctx, "revise_entry", {
        "entry_id": entry["id"], "expected_revision": 2, "author": "cc/1.0",
        "updates": {"expires_at": None},
    }).structured_content
    assert r["expires_at"] is None


def test_revise_rejects_unknown_update_fields(mcp_server, ctx):
    entry = save(mcp_server, ctx).structured_content
    with pytest.raises(Exception, match="invalid_request"):
        call(mcp_server, ctx, "revise_entry", {
            "entry_id": entry["id"], "expected_revision": 1, "author": "a",
            "updates": {"title": "x"},
        })


def test_revise_conflict_payload_carries_current(mcp_server, ctx):
    entry = save(mcp_server, ctx).structured_content
    call(mcp_server, ctx, "revise_entry", {
        "entry_id": entry["id"], "expected_revision": 1, "author": "a", "updates": {"body": "v2"}})
    with pytest.raises(Exception, match="revision_conflict"):
        call(mcp_server, ctx, "revise_entry", {
            "entry_id": entry["id"], "expected_revision": 1, "author": "b",
            "updates": {"body": "v3"}})


def test_lifecycle_and_history(mcp_server, ctx):
    entry = save(mcp_server, ctx).structured_content
    r = call(mcp_server, ctx, "entry_lifecycle", {
        "entry_id": entry["id"], "op": "archive",
        "expected_revision": 1, "author": "a"})
    assert r.structured_content["status"] == "archived"
    assert json.loads(r.content[0].text)["status"] == "archived"

    history = call(mcp_server, ctx, "list_entry_revisions", {"entry_id": entry["id"]})
    assert [(h["revision"], h["op"]) for h in history.structured_content["revisions"]] == [
        (1, "create"), (2, "archive"),
    ]


def test_get_entry_not_found(mcp_server, ctx):
    with pytest.raises(Exception, match="not_found"):
        call(mcp_server, ctx, "get_entry", {"entry_id": "entry_0AAAAAAAAAAAAAAAAAAAAAAA"})


def test_save_validates_type(mcp_server, ctx):
    with pytest.raises(Exception, match="invalid_request"):
        save(mcp_server, ctx, type="note")


def test_idempotent_save_replay(mcp_server, ctx):
    a = save(mcp_server, ctx, idempotency_key="mcp-k").structured_content
    b = save(mcp_server, ctx, idempotency_key="mcp-k").structured_content
    assert a == b
