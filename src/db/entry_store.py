"""L0 条目存储：单 SQLite 文件（WAL），entries / revisions / idempotency + FTS5 索引。

事务由 L2 服务经 transaction()（BEGIN IMMEDIATE）组合，本层只提供行级原语。
FTS5 存空格预分词文本（unicode61 按空格切分，PHASE2 Q9）；分词器为注入
seam，default_tokenize = jieba cut_for_search（T3 实测采定）。条目与 FTS 行
在同一事务内提交（写成功即可搜索，Q8）。
"""
from __future__ import annotations

import re
import sqlite3
import threading
import warnings
from contextlib import contextmanager
from typing import Callable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    body TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_projects TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS revisions (
    entry_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    op TEXT NOT NULL,
    modifier TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    PRIMARY KEY (entry_id, revision)
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(tokens);
"""

_ENTRY_FIELDS = (
    "id", "type", "body", "scope_kind", "scope_projects", "author",
    "created_at", "updated_at", "revision", "status", "expires_at", "source",
)

# 过滤纯标点 token（保留至少含一个字母/数字的词元）
_HAS_ALNUM_RE = re.compile(r"[^\W_]", re.UNICODE)


def default_tokenize(text: str) -> str:
    """jieba search 模式 + 小写，空格连接；入库与查询共用（Q9，T3 采定）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # jieba 0.42 在 3.12 有无害 Syntax/pkg_resources 告警
        import jieba
    return " ".join(
        t for t in jieba.cut_for_search(text.lower()) if _HAS_ALNUM_RE.search(t)
    )


class EntryStore:
    """单连接 + RLock 串行化；跨线程共用安全（check_same_thread=False）。"""

    def __init__(self, path: str, *, tokenize: Callable[[str], str] = default_tokenize):
        self._tokenize = tokenize
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
        """写事务：持锁整段串行；异常回滚，正常提交。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def insert_entry(self, entry: dict) -> None:
        fields = {k: entry[k] for k in _ENTRY_FIELDS}
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = self._conn.execute(
            f"INSERT INTO entries ({cols}) VALUES ({marks})", tuple(fields.values())
        )
        self._conn.execute(
            "INSERT INTO entries_fts (rowid, tokens) VALUES (?, ?)",
            (cur.lastrowid, self._tokenize(entry["body"])),
        )

    def get_entry(self, entry_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_entry(self, entry_id: str, fields: dict) -> None:
        allowed = {k: v for k, v in fields.items() if k in _ENTRY_FIELDS}
        sets = ", ".join(f"{k} = ?" for k in allowed)
        self._conn.execute(
            f"UPDATE entries SET {sets} WHERE id = ?",
            (*allowed.values(), entry_id),
        )
        if "body" in allowed:
            rowid = self._entry_rowid(entry_id)
            self._conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (rowid,))
            self._conn.execute(
                "INSERT INTO entries_fts (rowid, tokens) VALUES (?, ?)",
                (rowid, self._tokenize(allowed["body"])),
            )

    def _entry_rowid(self, entry_id: str) -> int:
        return self._conn.execute(
            "SELECT rowid FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()[0]

    def append_revision(self, entry_id: str, revision: int, op: str,
                        modifier: str, recorded_at: str, snapshot: str) -> None:
        self._conn.execute(
            "INSERT INTO revisions (entry_id, revision, op, modifier, recorded_at, snapshot)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, revision, op, modifier, recorded_at, snapshot),
        )

    def list_revisions(self, entry_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT revision, op, modifier, recorded_at FROM revisions"
                " WHERE entry_id = ? ORDER BY revision",
                (entry_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_revision(self, entry_id: str, revision: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM revisions WHERE entry_id = ? AND revision = ?",
                (entry_id, revision),
            ).fetchone()
        return dict(row) if row else None

    def get_idempotency(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM idempotency WHERE key = ?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def put_idempotency(self, key: str, request_hash: str,
                        response: str, created_at: str) -> None:
        self._conn.execute(
            "INSERT INTO idempotency (key, request_hash, response, created_at)"
            " VALUES (?, ?, ?, ?)",
            (key, request_hash, response, created_at),
        )

    def search_fts(self, query: str, *, scope: str | list[str] | None = None,
                   type_filter: str | None = None, now_iso: str,
                   limit: int) -> list[dict]:
        """FTS5 全文检索（§4.1/§4.2）：恒排除非 active 与已到期。

        scope：None=不限（all_projects）；"global"=仅全局；标签列表=全局+关联
        任一标签。WHERE 先于 LIMIT（范围过滤先于最终截断）。分词与写路径同源。
        """
        # OR + bm25：AND 会被查询侧虚词/措辞差异整题击落，OR 由排序承担选择性
        tokens = " OR ".join(f'"{t}"' for t in self._tokenize(query).split())
        if not tokens:
            return []
        where = [
            "e.status = 'active'",
            "(e.expires_at IS NULL OR e.expires_at > ?)",
            "entries_fts MATCH ?",
        ]
        params: list = [now_iso, tokens]
        if type_filter is not None:
            where.append("e.type = ?")
            params.append(type_filter)
        if scope == "global":
            where.append("e.scope_kind = 'global'")
        elif isinstance(scope, list):
            marks = ",".join("?" for _ in scope)
            where.append(
                "(e.scope_kind = 'global' OR EXISTS ("
                "SELECT 1 FROM json_each(e.scope_projects) jp WHERE jp.value IN (" + marks + ")))"
            )
            params.extend(scope)
        sql = (
            "SELECT e.* FROM entries_fts JOIN entries e ON e.rowid = entries_fts.rowid"
            " WHERE " + " AND ".join(where) + " ORDER BY rank LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
