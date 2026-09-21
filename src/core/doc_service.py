"""L2 文档服务：来源回查读路径（D6/PHASE2-T67 §4.4）。

只读：resolve（source → doc_id/最新版本）、版本列表、有界窗口回查。
写入方只有离线 ingest（db.doc_store.register）。错误码复用 EntryServiceError
（invalid_request / not_found），HTTP 层经 ENTRY_ERROR_CODES 映射。
"""
from __future__ import annotations

from db.doc_store import DocStore, content_sha256
from core.entry_service import EntryServiceError
from schema.doc_dto import (
    DocResolveResult,
    DocumentVersionList,
    DocumentVersionInfo,
    DocumentWindow,
)

WINDOW_LIMIT_MAX = 8000


class DocService:
    def __init__(self, store: DocStore):
        self._store = store

    def resolve(self, source: str) -> DocResolveResult:
        if not isinstance(source, str) or not source.strip():
            raise EntryServiceError("invalid_request", "source 必填且非空")
        doc = self._store.get_document(source)
        if doc is None:
            raise EntryServiceError("not_found", f"文档未注册: {source}")
        return DocResolveResult(
            doc_id=doc["doc_id"], name=doc["name"],
            latest_version=doc["latest_version"], total_versions=doc["total_versions"],
        )

    def list_versions(self, doc_id: str) -> DocumentVersionList:
        doc = self._require_document(doc_id)
        versions = [
            DocumentVersionInfo(**v) for v in self._store.list_versions(doc_id)
        ]
        return DocumentVersionList(doc_id=doc_id, name=doc["name"], versions=versions)

    def read_window(self, doc_id: str, version: int, offset: int, limit: int) -> DocumentWindow:
        self._require_document(doc_id)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise EntryServiceError("invalid_request", f"version 须为正整数: {version!r}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise EntryServiceError("invalid_request", f"offset 须为非负整数: {offset!r}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= WINDOW_LIMIT_MAX:
            raise EntryServiceError("invalid_request", f"limit 须为 1-{WINDOW_LIMIT_MAX}")
        stored = self._store.get_version(doc_id, version)
        if stored is None:
            raise EntryServiceError("not_found", f"文档版本不存在: {doc_id}#{version}")
        content = stored["content"]
        total = len(content)
        if offset > total:
            raise EntryServiceError("invalid_request", f"offset 超出全文长度 {total}")
        window = content[offset:offset + limit]
        return DocumentWindow(
            doc_id=doc_id, version=version, offset=offset, limit=limit,
            total_length=total, content=window,
            content_hash=content_sha256(content),
        )

    def _require_document(self, doc_id: str) -> dict:
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise EntryServiceError("invalid_request", "doc_id 必填且非空")
        doc = self._store.get_document(doc_id)
        if doc is None:
            raise EntryServiceError("not_found", f"文档未注册: {doc_id}")
        return doc
