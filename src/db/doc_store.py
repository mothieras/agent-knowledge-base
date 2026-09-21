"""L0 文档注册表：版本化规范化文本（D6/PHASE2-T67 §4.3）。

独立 SQLite 文件（`DOCS_DB_PATH`，WAL）。离线 ingest 成功后在快照写入后注册；
同内容（content_sha256 相同）幂等跳过，内容变化 → version+1，旧版本永久保留——
「文档更新后旧引用仍指向原修订」由版本保留直接满足。写入方只有离线入库
进程，服务侧只读（doc_service）。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
    doc_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    origin_file TEXT,
    index_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, version)
);
"""


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DocStore:
    """单连接 + RLock；与 EntryStore 同模式（WAL、busy_timeout）。"""

    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def register(self, *, doc_id: str, name: str, content: str,
                 origin_file: str | None, index_id: str | None,
                 created_at: str) -> dict:
        """注册文档内容；返回 {doc_id, version, new_version: bool}。

        同 doc_id 且内容 hash 与最新版本相同 → 幂等跳过（new_version=False）；
        内容变化 → version+1。documents 行首次创建。
        """
        digest = content_sha256(content)
        with self.transaction():
            self._conn.execute(
                "INSERT INTO documents (doc_id, name, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(doc_id) DO NOTHING",
                (doc_id, name, created_at),
            )
            latest = self._conn.execute(
                "SELECT version, content_sha256 FROM document_versions"
                " WHERE doc_id = ? ORDER BY version DESC LIMIT 1",
                (doc_id,),
            ).fetchone()
            if latest is not None and latest["content_sha256"] == digest:
                return {"doc_id": doc_id, "version": latest["version"], "new_version": False}
            version = (latest["version"] + 1) if latest is not None else 1
            self._conn.execute(
                "INSERT INTO document_versions (doc_id, version, content_sha256,"
                " content, origin_file, index_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, version, digest, content, origin_file, index_id, created_at),
            )
            return {"doc_id": doc_id, "version": version, "new_version": True}

    def get_document(self, doc_id: str) -> dict | None:
        """{doc_id, name, created_at, latest_version, total_versions}。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id, name, created_at FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            if row is None:
                return None
            versions = self._conn.execute(
                "SELECT COUNT(*) AS n, MAX(version) AS latest FROM document_versions"
                " WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        return {
            "doc_id": row["doc_id"], "name": row["name"], "created_at": row["created_at"],
            "latest_version": versions["latest"], "total_versions": versions["n"],
        }

    def list_versions(self, doc_id: str) -> list[dict]:
        """版本列表（不含正文）：version/content_sha256/origin_file/index_id/created_at/total_length。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, content_sha256, origin_file, index_id, created_at,"
                " length(content) AS total_length FROM document_versions"
                " WHERE doc_id = ? ORDER BY version",
                (doc_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, doc_id: str, version: int) -> dict | None:
        """含正文：{doc_id, version, content, content_sha256, origin_file, index_id, created_at, total_length}。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT version, content, content_sha256, origin_file, index_id,"
                " created_at FROM document_versions WHERE doc_id = ? AND version = ?",
                (doc_id, version),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["doc_id"] = doc_id
        out["total_length"] = len(out["content"])
        return out
