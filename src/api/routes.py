"""L3 HTTP 路由：只调 L2 公共接口，不 import db/rag_agent 内部。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

import config
from api.auth import enforce_bearer
from core.app_service import AppService
from core.entry_service import EntryServiceError, UNSET
from core.retrieval_service import ServiceError
from schema.dto import (
    ERROR_CODES,
    AnswerRequest,
    AnswerResponse,
    EvidenceWindow,
    SearchRequest,
    SearchResponse,
    ServiceMetadata,
)
from schema.entry_dto import (
    ENTRY_ERROR_CODES,
    Entry,
    EntrySearchResult,
    RevisionSummary,
)

router = APIRouter()

RETRIEVAL_TIMEOUT_S = 30.0
ANSWER_TIMEOUT_S = config.ANSWER_TOTAL_TIMEOUT_S


def get_app_service(request: Request) -> AppService:
    svc = getattr(request.app.state, "app_service", None)
    if svc is None or svc.retrieval is None:
        raise HTTPException(status_code=503, detail={"code": "snapshot_unavailable", "message": "检索服务未就绪"})
    return svc


def _http_error(exc: ServiceError) -> HTTPException:
    status = ERROR_CODES.get(exc.code, 502)
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


async def _run_search(svc: AppService, req: SearchRequest) -> SearchResponse:
    try:
        async with svc.acquire_slot():
            async with asyncio.timeout(RETRIEVAL_TIMEOUT_S):
                return await asyncio.to_thread(svc.retrieval.search, req)
    except ServiceError as exc:
        raise _http_error(exc) from exc
    except TimeoutError:
        raise HTTPException(status_code=504, detail={"code": "retrieval_failed", "message": "检索超时"}) from None


async def _run_answer(svc: AppService, req: AnswerRequest) -> AnswerResponse:
    """问答总预算 120s；超时给明确错误，后台任务保留槽位到实际结束（不叠加）。"""
    if svc.generation is None:
        raise _http_error(ServiceError("llm_not_configured", "生成模型未配置，仅检索可用"))
    try:
        async with svc.acquire_slot():
            async with asyncio.timeout(ANSWER_TIMEOUT_S):
                return await asyncio.to_thread(svc.generation.invoke, req)
    except ServiceError as exc:
        raise _http_error(exc) from exc
    except TimeoutError:
        raise HTTPException(status_code=429, detail={"code": "budget_exceeded", "message": "问答总超时"}) from None


@router.get("/health", dependencies=[Depends(enforce_bearer)])
async def health(request: Request):
    svc = getattr(request.app.state, "app_service", None)
    if svc is not None and svc.retrieval is not None:
        return {
            "status": "ready",
            "generation": "configured" if svc.llm_configured else "disabled",
            "entries": "ready" if svc.entries is not None else "unavailable",
        }
    raise HTTPException(status_code=503, detail={"code": "snapshot_unavailable", "message": "starting"})


@router.get("/info", response_model=ServiceMetadata, dependencies=[Depends(enforce_bearer)])
async def info(request: Request):
    svc = get_app_service(request)
    meta = svc.metadata()
    return ServiceMetadata(
        index_id=meta["index_id"],
        corpus_manifest_sha256=meta.get("corpus_manifest_sha256"),
        collection=meta.get("collection"),
        dense_model=meta.get("dense_model"),
        sparse_model=meta.get("sparse_model"),
        generation=meta.get("generation"),
        modes=meta.get("modes", []),
    )


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(enforce_bearer)])
async def search(req: SearchRequest, request: Request):
    svc = get_app_service(request)
    return await _run_search(svc, req)


@router.get("/evidence/{evidence_id}", response_model=EvidenceWindow,
            dependencies=[Depends(enforce_bearer)])
async def read_evidence(evidence_id: str, request: Request, offset: int = 0, limit: int = 4000):
    svc = get_app_service(request)
    if not (0 <= offset and 1 <= limit <= 8000):
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "offset/limit 越界"})
    try:
        async with svc.acquire_slot():
            return await asyncio.to_thread(svc.retrieval.read_evidence, evidence_id, offset, limit)
    except ServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/invoke", response_model=AnswerResponse, dependencies=[Depends(enforce_bearer)])
async def invoke(body: AnswerRequest, request: Request):
    svc = get_app_service(request)
    return await _run_answer(svc, body)


@router.post("/stream", dependencies=[Depends(enforce_bearer)])
async def stream(body: AnswerRequest, request: Request):
    svc = get_app_service(request)
    if svc.generation is None:
        raise _http_error(ServiceError("llm_not_configured", "生成模型未配置，仅检索可用"))

    async def _stream_with_budget():
        try:
            async with svc.acquire_slot():
                async with asyncio.timeout(ANSWER_TIMEOUT_S):
                    async for line in svc.generation.stream(body):
                        yield line
        except ServiceError as exc:
            from schema.dto import ErrorEvent

            yield f"data: {ErrorEvent(code=exc.code, message=exc.message).model_dump_json()}\n\n"
        except TimeoutError:
            from schema.dto import ErrorEvent

            yield f"data: {ErrorEvent(code='budget_exceeded', message='问答总超时').model_dump_json()}\n\n"

    return StreamingResponse(_stream_with_budget(), media_type="text/event-stream")


@router.get("/history", dependencies=[Depends(enforce_bearer)])
async def history():
    raise HTTPException(status_code=410, detail={"code": "not_found", "message": "/history 已退役：单次请求语义，不保留会话"})


# --- 条目服务（PHASE2 §5.1/§5.2：请求体由 L2 统一校验，错误恒为 {code,message}） ---

_ENTRY_CREATE_FIELDS = ("type", "body", "scope", "author", "expires_at", "source", "idempotency_key")
_ENTRY_CHANGE_FIELDS = ("type", "body", "scope", "expires_at", "source")
_ENTRY_REVISE_FIELDS = ("expected_revision", "author", "idempotency_key", *_ENTRY_CHANGE_FIELDS)
_ENTRY_LIFECYCLE_FIELDS = ("op", "expected_revision", "author", "idempotency_key")


def get_entries_service(request: Request) -> AppService:
    svc = getattr(request.app.state, "app_service", None)
    if svc is None or svc.entries is None:
        raise HTTPException(status_code=503, detail={"code": "entry_store_unavailable", "message": "条目服务未就绪"})
    return svc


def _entry_http_error(exc: EntryServiceError) -> HTTPException:
    # revision_conflict 的 data.current 合并进错误体（§5.2 冲突体）
    detail = {"code": exc.code, "message": exc.message, **(exc.data or {})}
    return HTTPException(status_code=ENTRY_ERROR_CODES.get(exc.code, 500), detail=detail)


def _reject_unknown_fields(payload: dict, allowed) -> None:
    extra = set(payload) - set(allowed)
    if extra:
        raise _entry_http_error(EntryServiceError("invalid_request", f"未知字段: {sorted(extra)}"))


async def _run_entry_write(svc: AppService, run):
    """条目写操作复用并发槽纪律（busy 不排队 → 429）。"""
    try:
        async with svc.acquire_slot():
            return await asyncio.to_thread(run)
    except ServiceError as exc:
        if exc.code == "busy":
            raise HTTPException(status_code=429, detail={"code": "busy", "message": exc.message}) from exc
        raise _http_error(exc) from exc
    except EntryServiceError as exc:
        raise _entry_http_error(exc) from exc


async def _run_entry_read(run):
    try:
        return await asyncio.to_thread(run)
    except EntryServiceError as exc:
        raise _entry_http_error(exc) from exc


@router.post("/entries", response_model=Entry, status_code=201,
             dependencies=[Depends(enforce_bearer)])
async def create_entry(payload: dict, request: Request):
    svc = get_entries_service(request)
    _reject_unknown_fields(payload, _ENTRY_CREATE_FIELDS)

    def run():
        return svc.entries.create(
            type=payload.get("type"), body=payload.get("body"),
            scope=payload.get("scope"), author=payload.get("author"),
            expires_at=payload.get("expires_at"), source=payload.get("source"),
            idempotency_key=payload.get("idempotency_key"),
        )

    return await _run_entry_write(svc, run)


@router.post("/entries/search", response_model=EntrySearchResult,
             dependencies=[Depends(enforce_bearer)])
async def search_entries(payload: dict, request: Request):
    svc = get_entries_service(request)
    _reject_unknown_fields(payload, ("query", "projects", "all_projects", "type", "limit"))

    def run():
        return svc.entries.search(
            query=payload.get("query"), projects=payload.get("projects"),
            all_projects=payload.get("all_projects", False),
            type=payload.get("type"), limit=payload.get("limit", 20),
        )

    return await _run_entry_read(run)


@router.get("/entries/{entry_id}", response_model=Entry, dependencies=[Depends(enforce_bearer)])
async def read_entry(entry_id: str, request: Request):
    svc = get_entries_service(request)
    return await _run_entry_read(lambda: svc.entries.get(entry_id))


@router.get("/entries/{entry_id}/revisions", response_model=list[RevisionSummary],
            dependencies=[Depends(enforce_bearer)])
async def list_entry_revisions(entry_id: str, request: Request):
    svc = get_entries_service(request)
    return await _run_entry_read(lambda: svc.entries.history(entry_id))


@router.get("/entries/{entry_id}/revisions/{revision}", response_model=Entry,
            dependencies=[Depends(enforce_bearer)])
async def read_entry_revision(entry_id: str, revision: int, request: Request):
    svc = get_entries_service(request)
    return await _run_entry_read(lambda: svc.entries.revision_snapshot(entry_id, revision))


@router.post("/entries/{entry_id}/revisions", response_model=Entry,
             dependencies=[Depends(enforce_bearer)])
async def revise_entry(entry_id: str, payload: dict, request: Request):
    svc = get_entries_service(request)
    _reject_unknown_fields(payload, _ENTRY_REVISE_FIELDS)

    def run():
        # 键存在 = 修改（null = 清除 expires_at/source），缺省 = 不变（§3.6）
        changes = {k: (payload[k] if k in payload else UNSET) for k in _ENTRY_CHANGE_FIELDS}
        return svc.entries.revise(
            entry_id, expected_revision=payload.get("expected_revision"),
            author=payload.get("author"), idempotency_key=payload.get("idempotency_key"),
            **changes,
        )

    return await _run_entry_write(svc, run)


@router.post("/entries/{entry_id}/lifecycle", response_model=Entry,
             dependencies=[Depends(enforce_bearer)])
async def entry_lifecycle(entry_id: str, payload: dict, request: Request):
    svc = get_entries_service(request)
    _reject_unknown_fields(payload, _ENTRY_LIFECYCLE_FIELDS)

    def run():
        return svc.entries.lifecycle(
            entry_id, op=payload.get("op"),
            expected_revision=payload.get("expected_revision"),
            author=payload.get("author"),
            idempotency_key=payload.get("idempotency_key"),
        )

    return await _run_entry_write(svc, run)
