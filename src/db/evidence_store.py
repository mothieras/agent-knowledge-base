"""L0 证据存储：evidence_id ↔ RetrievalHit 解析与有界原文窗口。

evidence_id 不是路径，解析按分段拆分并校验每一段与快照绑定；
不存在的 parent、旧快照引用、越界窗口走明确错误路径。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from db.retrieval import RetrievalHit


def _to_date(value) -> Optional[date]:
    if isinstance(value, date) or value is None:
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _to_int(value) -> Optional[int]:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class EvidenceError(ValueError):
    """证据解析/校验失败（not_found / stale / invalid）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # schema.dto.ERROR_CODES 键
        self.message = message


@dataclass(frozen=True)
class EvidenceRef:
    """evidence_id 的解析结果：绑定 index_id、parent_id 与精确切片。"""

    index_id: str
    parent_id: str
    span_start: int
    span_end: int
    token: str


class EvidenceStore:
    """parent store 上的证据解析层；snapshot 与 parent 均由 bootstrap 注入。"""

    def __init__(self, parent_store, snapshot):
        self._parent_store = parent_store
        self._snapshot = snapshot

    @property
    def index_id(self) -> str:
        return self._snapshot.index_id

    def evidence_id_for(self, hit: RetrievalHit) -> str:
        if hit.span_start is None or hit.span_end is None:
            raise EvidenceError("invalid_request", f"命中缺少 span: {hit.chunk_id or hit.parent_id}")
        return ":".join(
            (self._snapshot.index_id.split(":", 1)[1][:8], str(hit.parent_id),
             str(hit.span_start), str(hit.span_end))
        )

    def parse(self, evidence_id: str) -> EvidenceRef:
        parts = (evidence_id or "").split(":")
        if len(parts) != 4:
            raise EvidenceError("evidence_not_found", f"非法 evidence_id: {evidence_id!r}")
        token, parent_id, start_s, end_s = parts
        if token != self._snapshot.index_id.split(":", 1)[1][:8]:
            raise EvidenceError("stale_evidence",
                                f"evidence_id 属于旧快照（当前 {self._snapshot.index_id[:16]}…）")
        try:
            start, end = int(start_s), int(end_s)
        except ValueError:
            raise EvidenceError("evidence_not_found", f"非法 span: {evidence_id!r}") from None
        if start < 0 or end <= start:
            raise EvidenceError("evidence_not_found", f"非法 span 范围: {evidence_id!r}")
        return EvidenceRef(self._snapshot.index_id, parent_id, start, end, token)

    def resolve(self, evidence_id: str) -> RetrievalHit:
        """回查父文本并重建命中（span 精确切片，带 hash 校验由上层 DTO 完成）。"""
        ref = self.parse(evidence_id)
        parent = self._parent_store.load_content(ref.parent_id)
        if not parent:
            raise EvidenceError("evidence_not_found", f"parent 不存在: {ref.parent_id}")
        content = parent.get("content", "")
        if ref.span_end > len(content):
            raise EvidenceError("evidence_not_found",
                                f"span 越界: {ref.span_end} > {len(content)}")
        from db.retrieval import RetrievalHit

        meta = parent.get("metadata", {})
        return RetrievalHit(
            source=str(meta.get("source", "")),
            parent_id=ref.parent_id,
            content=content[ref.span_start:ref.span_end],
            version=meta.get("version"),
            effective_date=_to_date(meta.get("effective_date")),
            expired_date=_to_date(meta.get("expired_date")),
            priority=_to_int(meta.get("priority")),
            chunk_id=None,
            span_start=ref.span_start,
            span_end=ref.span_end,
            retrieval_channel="evidence_resolve",
        )

    def window(self, evidence_id: str, offset: int, limit: int) -> dict:
        """有界原文窗口：携带自身 offset、总长度与 hash，不改变原命中身份。"""
        ref = self.parse(evidence_id)
        parent = self._parent_store.load_content(ref.parent_id)
        if not parent:
            raise EvidenceError("evidence_not_found", f"parent 不存在: {ref.parent_id}")
        content = parent.get("content", "")
        if offset > len(content):
            raise EvidenceError("evidence_not_found",
                                f"窗口越界: offset {offset} > 总长 {len(content)}")
        window = content[offset:offset + limit]
        return {
            "evidence_id": evidence_id,
            "index_id": ref.index_id,
            "parent_id": ref.parent_id,
            "source": parent.get("metadata", {}).get("source", ""),
            "version": parent.get("metadata", {}).get("version"),
            "offset": offset,
            "total_length": len(content),
            "content": window,
        }
