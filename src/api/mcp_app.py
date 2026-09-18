"""L3 MCP 适配：官方 Python MCP SDK 2.x Streamable HTTP ASGI app。

工具经 lifespan context 拿 L2 AppService；文本与结构化双通道同载荷
（Pi 实测发现 pi-mcp-adapter 等客户端只把 TextContent 渲染进模型上下文，
摘要式文本会让接地场景拿不到证据正文，故文本通道也承载完整 JSON）。
鉴权覆盖整个 ASGI mount（含 transport 各 HTTP 方法），不依赖 FastAPI 路由依赖。

2.x 要点（2026-09-07 实测）：MCPServer 替代 FastMCP；lifespan 必须
``async with mcp.session_manager.run()``；``streamable_http_path='/'`` 挂载到
``/mcp`` 得到单层端点；工具返回 ``CallToolResult(content=[TextContent(完整JSON)],
structured_content=dto)``。
"""
from __future__ import annotations

import json
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
from core.entry_service import EntryServiceError, UNSET
from core.retrieval_service import ServiceError
from schema.dto import AnswerRequest, AnswerResponse, EvidenceWindow, SearchRequest, SearchResponse
from schema.entry_dto import Entry, EntrySearchResult, RevisionList

EXPECTED_TOKEN = os.environ.get("DEMO_API_TOKEN", "")


def _text_payload(payload: dict) -> TextContent:
    """文本通道承载完整 JSON（与 structuredContent 同源同构）。

    2026-09-10 Pi 实测：pi-mcp-adapter 只把 TextContent 渲染进模型上下文，
    structuredContent 不达模型——摘要式文本会让接地场景拿不到证据正文。
    双通道同载荷（SearchResponse 受 24KiB DTO 预算约束），保证任何客户端
    至少有一条可用通道。
    """
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))


def _entry_tool_error(exc: EntryServiceError) -> ToolError:
    # 错误载荷为 JSON（含 code 与冲突时的 current 条目），调用方可解析重读重试
    return ToolError(json.dumps(
        {"code": exc.code, "message": exc.message, **(exc.data or {})}, ensure_ascii=False))


def _entry_result(payload) -> CallToolResult:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload
    return CallToolResult(
        content=[_text_payload(data)],
        structured_content=data,
    )


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


def create_mcp_server(include_ask: bool = True) -> MCPServer:
    server = MCPServer(
        "agentic-rag",
        title="Agentic RAG 知识检索",
        version="0.3.0",
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
            content=[_text_payload(resp.model_dump())],
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
            content=[_text_payload(window.model_dump())],
            structured_content=window.model_dump(),
        )

    if include_ask:

        @server.tool(structured_output=True)
        async def ask_knowledge(
            ctx: Context[Any, Any],
            message: Annotated[str, "要回答的问题，≤2000 字符"],
            mode: Annotated[str, "问答模式：rag（固定单图）或 agentic（双图 Agent），默认 rag"] = "rag",
        ) -> Annotated[CallToolResult, AnswerResponse]:
            """整体委托服务端内置问答：返回单次 decision + 答案 + 引用。

            仅生成模型配置启用时发布；未启用时调用方会收到可识别错误。
            """
            svc: AppService = ctx.request_context.lifespan_context
            if svc.generation is None:
                raise ToolError("生成模型未配置，问答能力不可用")
            import asyncio

            try:
                req = AnswerRequest(message=message, mode=mode)
                resp = await asyncio.to_thread(svc.generation.invoke, req)
            except ServiceError as exc:
                raise ToolError(exc.message) from exc
            return CallToolResult(
                content=[_text_payload(resp.model_dump())],
                structured_content=resp.model_dump(),
            )

    # --- 条目服务工具（PHASE2 §5.3；search_entries 随 T3 无模型搜索落地） ---

    @server.tool(structured_output=True)
    async def save_entry(
        ctx: Context[Any, Any],
        type: Annotated[str, "条目类型：memory | knowledge"],
        body: Annotated[str, "条目正文（非空，≤64KiB）"],
        author: Annotated[str, "自报作者，约定「客户端/版本」如 pi/0.85.1（记录用途，非强身份）"],
        scope: Annotated[dict, '必填：{"kind":"global"} 或 {"kind":"projects","projects":[标签1-16]}；无默认值，全局须显式声明'],
        expires_at: Annotated[str | None, "有效期 RFC3339（可选，查询时判定到期）"] = None,
        source: Annotated[dict | None, '{"url?","note?"} 来源引用；未知来源传 null，不伪造'] = None,
        idempotency_key: Annotated[str | None, "幂等键：同键同请求重试返回原结果"] = None,
    ) -> Annotated[CallToolResult, Entry]:
        """保存一条共享 Memory/Knowledge 条目（revision=1）。

        记什么、不记什么见 docs/ENTRY_TOOL_GUIDE.md：跨会话/跨 Agent 复用的
        事实与方法记这里；一次性过程状态留在原生记忆。
        """
        svc: AppService = ctx.request_context.lifespan_context
        try:
            entry = svc.entries.create(
                type=type, body=body, author=author, scope=scope,
                expires_at=expires_at, source=source,
                idempotency_key=idempotency_key,
            )
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(entry)

    @server.tool(structured_output=True)
    async def get_entry(
        ctx: Context[Any, Any],
        entry_id: Annotated[str, "条目 ID（entry_ 前缀）"],
    ) -> Annotated[CallToolResult, Entry]:
        """按 ID 读取条目当前完整状态（含到期判定；已删除条目也可显式读取）。"""
        svc: AppService = ctx.request_context.lifespan_context
        try:
            entry = svc.entries.get(entry_id)
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(entry)

    @server.tool(structured_output=True)
    async def revise_entry(
        ctx: Context[Any, Any],
        entry_id: Annotated[str, "条目 ID"],
        expected_revision: Annotated[int, "调用方持有的修订号；不匹配返回 revision_conflict + 当前条目"],
        author: Annotated[str, "自报作者（修订人，记入历史）"],
        updates: Annotated[dict | None, '变更字段 {"type?","body?","scope?","expires_at?","source?"}：键存在=修改，值为 null=清除（仅 expires_at/source）；缺省不变'] = None,
        idempotency_key: Annotated[str | None, "幂等键"] = None,
    ) -> Annotated[CallToolResult, Entry]:
        """修订条目（revision+1，历史全量快照保留）。

        冲突时读取错误载荷中的 current 条目，整合后以新修订号重试；服务不
        自动合并。
        """
        svc: AppService = ctx.request_context.lifespan_context
        fields = ("type", "body", "scope", "expires_at", "source")
        updates = updates or {}
        unknown = set(updates) - set(fields)
        if unknown:
            raise _entry_tool_error(EntryServiceError(
                "invalid_request", f"未知字段: {sorted(unknown)}")) from None
        changes = {k: (updates[k] if k in updates else UNSET) for k in fields}
        try:
            entry = svc.entries.revise(
                entry_id, expected_revision=expected_revision, author=author,
                idempotency_key=idempotency_key, **changes,
            )
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(entry)

    @server.tool(structured_output=True)
    async def entry_lifecycle(
        ctx: Context[Any, Any],
        entry_id: Annotated[str, "条目 ID"],
        op: Annotated[str, "archive（归档）| unarchive | delete（软删）| restore"],
        expected_revision: Annotated[int, "调用方持有的修订号"],
        author: Annotated[str, "自报作者"],
        idempotency_key: Annotated[str | None, "幂等键"] = None,
    ) -> Annotated[CallToolResult, Entry]:
        """生命周期操作：active/archive/delete 三态软转换，历史与正文全保留。"""
        svc: AppService = ctx.request_context.lifespan_context
        try:
            entry = svc.entries.lifecycle(
                entry_id, op=op, expected_revision=expected_revision,
                author=author, idempotency_key=idempotency_key,
            )
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(entry)

    @server.tool(structured_output=True)
    async def list_entry_revisions(
        ctx: Context[Any, Any],
        entry_id: Annotated[str, "条目 ID"],
    ) -> Annotated[CallToolResult, RevisionList]:
        """列出修订历史（revision/op/modifier/时间）；按修订回查走 HTTP。"""
        svc: AppService = ctx.request_context.lifespan_context
        try:
            revisions = svc.entries.history(entry_id)
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(RevisionList(revisions=revisions))

    @server.tool(structured_output=True)
    async def search_entries(
        ctx: Context[Any, Any],
        query: Annotated[str, "检索查询（中文/英文/混合，≤2000 字符）"],
        projects: Annotated[list[str] | None, "项目标签列表：候选=全局+关联任一标签；省略则仅查全局"] = None,
        all_projects: Annotated[bool, "true=全局+全部项目关联（与 projects 互斥）"] = False,
        type: Annotated[str | None, "memory | knowledge；省略查双类型"] = None,
        limit: Annotated[int, "返回条数（1-50），默认 20"] = 20,
    ) -> Annotated[CallToolResult, EntrySearchResult]:
        """按全文相关性搜索条目（无模型，纯词法 bm25）。

        恒排除已归档/已删除/已到期；未指定范围仅查全局，指定项目则包含全局。
        命中带当前修订号，修订内容需回查时用 get_entry。
        """
        svc: AppService = ctx.request_context.lifespan_context
        try:
            result = svc.entries.search(
                query=query, projects=projects, all_projects=all_projects,
                type=type, limit=limit,
            )
        except EntryServiceError as exc:
            raise _entry_tool_error(exc) from exc
        return _entry_result(result)

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
