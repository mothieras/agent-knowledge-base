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


class DocRef(BaseModel):
    """source.document（D6/PHASE2-T67 §4.2）：指向文档注册表的版本化文本。

    span 为规范化全文的字符区间（缺省 = 全文）；写入时机械校验存在与合法。
    """

    doc_id: str
    version: int
    span_start: int | None = None
    span_end: int | None = None


class SourceRef(BaseModel):
    url: str | None = None
    note: str | None = None
    document: DocRef | None = None


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


class MatchedChunk(BaseModel):
    """搜索命中的条目内片段（PHASE2-T67 §4.5）：短条目单块 = 全文。"""

    chunk_index: int
    span_start: int
    span_end: int
    text: str


class EntrySearchResult(BaseModel):
    """搜索结果（§4.2）：恒排除 archived/deleted/expired；命中绑定当前修订。

    matched 为加性字段（entry_id → 最佳匹配片段），results 形状不变。
    """

    query: str
    results: list[Entry]
    returned_k: int
    matched: dict[str, MatchedChunk] = Field(default_factory=dict)


ENTRY_ERROR_CODES = {
    "invalid_request": 400,
    "invalid_project": 400,
    "invalid_source": 400,
    "not_found": 404,
    "revision_conflict": 409,
    "idempotency_conflict": 409,
    "entry_deleted": 409,
    "invalid_transition": 409,
    "busy": 429,
}
