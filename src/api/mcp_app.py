"""L3 MCP 适配：官方 Python MCP SDK 2.x Streamable HTTP ASGI app。

工具经 lifespan context 拿 L2 AppService；文本部分只给短摘要/状态/证据 ID，
完整结果走 structuredContent（不再复制正文）。鉴权覆盖整个 ASGI mount（含
transport 各 HTTP 方法），不依赖 FastAPI 路由依赖。

2.x 要点（Day 1 验证）：MCPServer 替代 FastMCP；lifespan 必须
``async with mcp.session_manager.run()``；``streamable_http_path='/'`` 挂载到
``/mcp`` 得到单层端点；工具返回 ``CallToolResult(content=[TextContent(摘要)],
structured_content=dto)`` 控制文本部分。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer, Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.app_service import AppService
from core.retrieval_service import ServiceError
from schema.dto import EvidenceWindow, SearchRequest, SearchResponse

EXPECTED_TOKEN = os.environ.get("DEMO_API_TOKEN", "")


class BearerMiddleware(BaseHTTPMiddleware):
    """鉴权覆盖整个 ASGI mount：MCP 握手/各 transport 方法先过这里。"""

    async def dispatch(self, request: Request, call_next):
        if EXPECTED_TOKEN:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != EXPECTED_TOKEN:
                from starlette.responses import JSONResponse

                return JSONResponse(
                    {"code": "not_authenticated", "message": "Bearer 凭据无效或缺失"},
                    status_code=401,
                )
        return await call_next(request)


def build_mcp_asgi(server: MCPServer):
    """streamable_http_app 外包鉴权中间件；单层路径挂载到 /mcp。"""
    return BearerMiddleware(server.streamable_http_app(streamable_http_path="/"))


def create_mcp_server() -> MCPServer:
    server = MCPServer(
        "agentic-rag",
        title="Agentic RAG 知识检索",
        version="0.2.0",
        lifespan=_lifespan,
    )

    @server.tool(structured_output=True)
    async def search_knowledge(
        ctx: Context[Any, Any],
        query: Annotated[str, "检索查询，≤2000 字符"],
        k: Annotated[int, "期望命中数（1-20），默认 7"] = 7,
    ) -> Annotated[CallToolResult, SearchResponse]:
        """在知识库中检索与 query 相关的证据片段。

        返回命中列表：evidence_id、source、version、span、content_hash 与完整
        content。evidence_id 可用于 get_context 拉取原文窗口。不调用生成模型。
        """
        svc: AppService = ctx.request_context.lifespan_context
        try:
            req = SearchRequest(query=query, k=k)
        except Exception as exc:
            raise ToolError(exc) from exc
        try:
            resp = svc.retrieval.search(req)
        except ServiceError as exc:
            raise ToolError(exc.message) from exc
        return CallToolResult(
            content=[TextContent(
                type="text",
                text=f"命中 {resp.returned_k}/{resp.requested_k}"
                     + ("（已按预算裁剪）" if resp.truncated else "")
                     + f"，index_id={resp.index_id[:16]}…，"
                     + "证据 ID: " + ", ".join(h.evidence_id for h in resp.hits),
            )],
            structured_content=resp.model_dump(),
        )

    @server.tool(structured_output=True)
    async def get_context(
        ctx: Context[Any, Any],
        evidence_id: Annotated[str, "search_knowledge 返回的 evidence_id"],
        offset: Annotated[int, "父文本窗口起点（字符）"] = 0,
        limit: Annotated[int, "窗口长度（字符，≤8000）"] = 4000,
    ) -> Annotated[CallToolResult, EvidenceWindow]:
        """按 evidence_id 拉取有界父文本窗口。

        窗口携带自身 offset、total_length 与 content_hash；不改变原命中身份。
        """
        svc: AppService = ctx.request_context.lifespan_context
        try:
            window: EvidenceWindow = svc.retrieval.read_evidence(evidence_id, offset, limit)
        except ServiceError as exc:
            raise ToolError(exc.message) from exc
        return CallToolResult(
            content=[TextContent(
                type="text",
                text=f"{window.source} 窗口 {window.offset}+{len(window.content)}/"
                     f"{window.total_length} 字符，index_id={window.index_id[:16]}…",
            )],
            structured_content=window.model_dump(),
        )

    return server


@asynccontextmanager
async def _lifespan(server: MCPServer) -> AsyncIterator[AppService]:
    # lifespan 由 FastAPI lifespan 内的 session_manager.run() 触发；
    # app_service 从 FastAPI app.state 取，避免与 L2 双实例化。
    from api.main import app as fastapi_app

    svc: AppService = fastapi_app.state.app_service
    if svc is None:
        raise RuntimeError("app_service 未就绪，MCP 无法启动")
    yield svc
