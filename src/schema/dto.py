"""HTTP/MCP 共用公共 DTO（L2 证据契约）。

L3 只依赖这里；不 import db/ 或 rag_agent/ 内部类型。evidence_id/span/hash
不变量见 DESIGN 第 5 节：parent_text[span.start:span.end] == content 且
"sha256:" + sha256(content) == content_hash，resolved.index_id == response.index_id。
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

INDEX_ID_RE = r"^sha256:[0-9a-f]{64}$"
EVIDENCE_ID_RE = r"^[a-f0-9]{8}:[A-Za-z0-9_.-]{1,128}:[0-9]+:[0-9]+$"
CONTENT_HASH_RE = r"^sha256:[0-9a-f]{64}$"

Decision = Literal["answered", "clarification_required", "refused"]


class Span(BaseModel):
    """规范化 parent 文本中的 Python 字符索引，左闭右开。"""

    start: int = Field(ge=0, description="span 起点（字符索引，含）")
    end: int = Field(gt=0, description="span 终点（字符索引，不含）")


class EvidenceHit(BaseModel):
    """公共证据命中：绑定 index_id、source、parent/chunk 与精确切片。"""

    evidence_id: str = Field(pattern=EVIDENCE_ID_RE)
    source: str = Field(description="受治理来源标识（manifest source）")
    source_uri: str | None = Field(default=None, description="可选来源 URL")
    version: str | None = Field(default=None, description="来源版本元数据，未知为 null")
    chunk_id: str | None = Field(default=None)
    parent_id: str | None = Field(default=None)
    content: str = Field(description="返回的完整证据片段")
    content_hash: str = Field(pattern=CONTENT_HASH_RE)
    span: Span
    score: float | None = Field(default=None, description="原始检索分数，非置信概率")
    retrieval_channel: str | None = Field(default=None)
    effective_date: str | None = Field(default=None)
    expired_date: str | None = Field(default=None)
    priority: int | None = Field(default=None)

    @model_validator(mode="after")
    def _check_invariants(self) -> "EvidenceHit":
        if self.span.start >= self.span.end:
            raise ValueError(f"span 非法: {self.span}")
        if self.content_hash != "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest():
            raise ValueError("content_hash 与 content 不符")
        return self


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="检索查询（1-2000 字符）")
    k: int = Field(default=7, ge=1, le=20, description="期望返回命中数（1-20）")
    include_debug_artifact: bool = Field(
        default=False, description="返回裁剪前完整命中 artifact（debug/eval 用）"
    )


class SearchResponse(BaseModel):
    request_id: str
    index_id: str = Field(pattern=INDEX_ID_RE)
    requested_k: int = Field(ge=1, le=20)
    returned_k: int = Field(ge=0)
    truncated: bool = Field(description="是否因 24 KiB DTO 预算裁剪了完整命中记录")
    hits: list[EvidenceHit]
    debug_artifact: list[EvidenceHit] | None = Field(default=None)


class EvidenceRequest(BaseModel):
    offset: int = Field(default=0, ge=0, description="窗口起点（字符）")
    limit: int = Field(default=4000, ge=1, le=8000, description="窗口长度（字符，≤8000）")


class EvidenceWindow(BaseModel):
    evidence_id: str = Field(pattern=EVIDENCE_ID_RE)
    index_id: str = Field(pattern=INDEX_ID_RE)
    parent_id: str
    source: str
    version: str | None = None
    offset: int = Field(ge=0)
    total_length: int = Field(ge=0)
    content: str = Field(description="parent 文本的 [offset, offset+len(content)) 窗口")
    content_hash: str = Field(pattern=CONTENT_HASH_RE)

    @model_validator(mode="after")
    def _check_hash(self) -> "EvidenceWindow":
        expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise ValueError("content_hash 与窗口内容不符")
        return self


class Citation(BaseModel):
    citation_id: str
    evidence_id: str = Field(pattern=EVIDENCE_ID_RE)
    source: str
    chunk_id: str | None = None
    span: Span
    quote: str = Field(description="规范化 parent 的准确切片（可为命中 span 的子区间）")


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_calls: int | None = None
    estimated_cost_cny: float | None = None


class AnswerResponse(BaseModel):
    request_id: str
    index_id: str = Field(pattern=INDEX_ID_RE)
    mode: Literal["rag", "agentic"]
    decision: Decision
    answer: str = ""
    clarification_question: str | None = None
    limitations: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage | None = None
    debug_artifact: Any | None = Field(default=None, description="debug/eval 诊断 artifact")


class ServiceMetadata(BaseModel):
    """/info：快照、能力与可选模型标识；不返回凭据或内部路径。"""

    index_id: str = Field(pattern=INDEX_ID_RE)
    corpus_manifest_sha256: str | None = None
    collection: str | None = None
    dense_model: str | None = None
    sparse_model: str | None = None
    generation: dict[str, Any] | None = Field(default=None, description="生成模型标识或 null")
    modes: list[str] = Field(default_factory=list, description="可用问答 modes")
    api_version: str = "2"


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


ERROR_CODES = {
    "invalid_request": 400,
    "not_authenticated": 401,
    "not_found": 404,
    "evidence_not_found": 404,
    "snapshot_unavailable": 503,
    "retrieval_failed": 502,
    "llm_not_configured": 503,
    "upstream_failed": 502,
    "budget_exceeded": 429,
    "response_too_large": 413,
    "busy": 503,
    "stale_evidence": 410,
}
