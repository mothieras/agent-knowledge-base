"""条目服务公共 DTO（PHASE2 §3 契约，HTTP/MCP 共用）。

L3 只依赖这里；契约校验在 L2 core.entry_service，错误码映射见
ENTRY_ERROR_CODES（§5.2）。expired 为查询时判定（§3.6），不落库。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntryType = Literal["memory", "knowledge"]
EntryStatus = Literal["active", "archived", "deleted"]
EntryOp = Literal["create", "update", "archive", "unarchive", "delete", "restore"]


class Scope(BaseModel):
    kind: Literal["global", "projects"]
    projects: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    url: str | None = None
    note: str | None = None


class Entry(BaseModel):
    id: str
    type: EntryType
    body: str
    scope: Scope
    author: str
    created_at: str
    updated_at: str
    revision: int
    status: EntryStatus
    expires_at: str | None
    expired: bool = Field(description="读取时到期判定（服务端 UTC 时钟）")
    source: SourceRef | None


class RevisionSummary(BaseModel):
    revision: int
    op: EntryOp
    modifier: str
    recorded_at: str


class RevisionList(BaseModel):
    revisions: list[RevisionSummary]


ENTRY_ERROR_CODES = {
    "invalid_request": 400,
    "invalid_project": 400,
    "not_found": 404,
    "revision_conflict": 409,
    "idempotency_conflict": 409,
    "entry_deleted": 409,
    "invalid_transition": 409,
    "busy": 429,
}
