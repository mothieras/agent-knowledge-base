"""文档服务公共 DTO（D6/PHASE2-T67 §4.4，HTTP/MCP 共用）。

窗口契约对齐 EvidenceWindow：offset/limit 有界读取、content_hash 为全文
sha256（客户端可复算）、total_length 供 span 定位。
"""
from __future__ import annotations

from pydantic import BaseModel


class DocumentWindow(BaseModel):
    doc_id: str
    version: int
    offset: int
    limit: int
    total_length: int
    content: str
    content_hash: str


class DocumentVersionInfo(BaseModel):
    version: int
    content_sha256: str
    origin_file: str | None
    index_id: str | None
    created_at: str
    total_length: int


class DocumentVersionList(BaseModel):
    doc_id: str
    name: str
    versions: list[DocumentVersionInfo]


class DocResolveResult(BaseModel):
    doc_id: str
    name: str
    latest_version: int
    total_versions: int
