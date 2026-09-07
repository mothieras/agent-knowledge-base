"""L2 检索服务：search / read_evidence、快照绑定、公共 DTO 转换。

Retriever 返回 L0 RetrievalHit；这里映射为公共 EvidenceHit 并在 24 KiB
紧凑 UTF-8 DTO 预算内按检索顺序取可容纳的最长完整前缀。裁剪前 artifact
可在 debug/eval 模式下显式取回；检索指标与 trace 用裁剪前结果。
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional

import config
from db.evidence_store import EvidenceError, EvidenceStore
from db.retrieval import RetrievalHit
from schema.dto import (
    EvidenceHit,
    EvidenceWindow,
    SearchRequest,
    SearchResponse,
    Span,
)

DTO_BUDGET_BYTES = 24 * 1024


class ServiceError(Exception):
    """L2 应用错误：code 对应 schema.dto.ERROR_CODES。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def to_evidence_hit(hit: RetrievalHit, evidence_id: str, *, index_id: str,
                    source_uri: Optional[str] = None) -> EvidenceHit:
    if hit.span_start is None or hit.span_end is None:
        raise ServiceError("retrieval_failed", f"命中缺少 span，无法生成 evidence_id: {hit.parent_id}")
    return EvidenceHit(
        evidence_id=evidence_id,
        source=hit.source,
        source_uri=source_uri,
        version=hit.version,
        chunk_id=hit.chunk_id,
        parent_id=hit.parent_id,
        content=hit.content,
        content_hash=_content_hash(hit.content),
        span=Span(start=hit.span_start, end=hit.span_end),
        score=hit.score,
        retrieval_channel=hit.retrieval_channel,
        effective_date=hit.effective_date.isoformat() if hit.effective_date else None,
        expired_date=hit.expired_date.isoformat() if hit.expired_date else None,
        priority=hit.priority,
    )


class RetrievalService:
    def __init__(self, retriever, evidence_store: EvidenceStore,
                 source_uris: dict[str, str] | None = None):
        self._retriever = retriever
        self._evidence = evidence_store
        self._source_uris = source_uris or {}

    @property
    def index_id(self) -> str:
        return self._evidence.index_id

    def _full_hits(self, query: str, k: int) -> list[EvidenceHit]:
        raw = self._retriever.search(query, k=k)
        hits = []
        for h in raw:
            try:
                eid = self._evidence.evidence_id_for(h)
            except EvidenceError:
                continue  # 缺 span 的命中无法成为证据；跳过而非伪造
            hits.append(to_evidence_hit(h, eid, index_id=self.index_id,
                                        source_uri=self._source_uris.get(h.source)))
        return hits

    @staticmethod
    def _serialized_len(hit: EvidenceHit) -> int:
        return len(hit.model_dump_json().encode("utf-8"))

    def search(self, req: SearchRequest, request_id: str | None = None) -> SearchResponse:
        rid = request_id or uuid.uuid4().hex
        try:
            full = self._full_hits(req.query, req.k)
        except Exception as exc:
            raise ServiceError("retrieval_failed", f"检索失败: {type(exc).__name__}: {exc}") from exc

        hits, truncated = self._apply_budget(full)
        return SearchResponse(
            request_id=rid,
            index_id=self.index_id,
            requested_k=req.k,
            returned_k=len(hits),
            truncated=truncated,
            hits=hits,
            debug_artifact=full if req.include_debug_artifact else None,
        )

    @staticmethod
    def _apply_budget(full: list[EvidenceHit]) -> tuple[list[EvidenceHit], bool]:
        hits, total = [], 0
        for hit in full:
            size = RetrievalService._serialized_len(hit)
            if total + size > DTO_BUDGET_BYTES:
                if not hits:
                    # 单条命中即超预算：明确报错，不伪装成无命中
                    raise ServiceError("response_too_large",
                                       f"单条命中序列化 {size} B 超过 {DTO_BUDGET_BYTES} B 预算")
                return hits, True
            total += size
            hits.append(hit)
        return hits, False

    def read_evidence(self, evidence_id: str, offset: int = 0,
                      limit: int = 4000) -> EvidenceWindow:
        try:
            window = self._evidence.window(evidence_id, offset, limit)
        except EvidenceError as exc:
            raise ServiceError(exc.code, exc.message) from exc
        return EvidenceWindow(
            evidence_id=window["evidence_id"],
            index_id=window["index_id"],
            parent_id=window["parent_id"],
            source=window["source"],
            version=window["version"],
            offset=window["offset"],
            total_length=window["total_length"],
            content=window["content"],
            content_hash=_content_hash(window["content"]),
        )
