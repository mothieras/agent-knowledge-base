"""L3 HTTP 路由：只调 L2 公共接口，不 import db/rag_agent 内部。"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import config
from api.auth import enforce_bearer
from core.app_service import AppService
from core.retrieval_service import ServiceError
from schema.dto import (
    ERROR_CODES,
    AnswerResponse,
    EvidenceRequest,
    EvidenceWindow,
    SearchRequest,
    SearchResponse,
    ServiceMetadata,
)

router = APIRouter()

RETRIEVAL_TIMEOUT_S = 30.0


class InvokeBody(BaseModel):
    model_config = {"extra": "forbid"}  # thread_id 等旧协议字段：明确拒绝，不静默忽略

    message: str = Field(min_length=1, max_length=2000)
    mode: str = Field(default="rag", pattern="^(rag|agentic)$")


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


@router.get("/health", dependencies=[Depends(enforce_bearer)])
async def health(request: Request):
    svc = getattr(request.app.state, "app_service", None)
    if svc is not None and svc.retrieval is not None:
        return {"status": "ready", "generation": "configured" if svc.llm_configured else "disabled"}
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
async def invoke(body: InvokeBody, request: Request):
    svc = get_app_service(request)
    raise _http_error(ServiceError("llm_not_configured", "生成模型未配置（Day 3 接入）"))


@router.post("/stream", dependencies=[Depends(enforce_bearer)])
async def stream(body: InvokeBody, request: Request):
    svc = get_app_service(request)
    raise _http_error(ServiceError("llm_not_configured", "生成模型未配置（Day 3 接入）"))


@router.get("/history", dependencies=[Depends(enforce_bearer)])
async def history():
    raise HTTPException(status_code=410, detail={"code": "not_found", "message": "/history 已退役：单次请求语义，不保留会话"})
