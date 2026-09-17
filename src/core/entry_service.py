"""L2 条目服务：PHASE2 §3 契约执行——校验、单事务修订/生命周期、幂等、到期。

一切变更在 BEGIN IMMEDIATE 单事务内完成「读当前修订 → 比较 → 应用 →
追加历史」，原子提交（§3.3）。到期为查询时判定（§3.6），时钟经注入 seam
可控。存储原语见 db.entry_store；不含搜索（T3）。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Callable

from db.entry_store import EntryStore
from schema.entry_dto import (
    Entry,
    EntrySearchResult,
    RevisionSummary,
    Scope,
    SourceRef,
)

PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_PROJECTS = 16
MAX_BODY_BYTES = 64 * 1024
MAX_AUTHOR_CHARS = 128
MAX_IDEMPOTENCY_KEY_CHARS = 128

_ENTRY_TYPES = ("memory", "knowledge")
_LIFECYCLE_OPS = ("archive", "unarchive", "delete", "restore")
# (当前状态, 操作) -> 目标状态；其余组合非法（§3.5）
_TRANSITIONS = {
    ("active", "archive"): "archived",
    ("archived", "unarchive"): "active",
    ("active", "delete"): "deleted",
    ("archived", "delete"): "deleted",
    ("deleted", "restore"): "active",
}

UNSET = object()  # 修订请求中区分「未提供」与「显式 null」（§3.6 expires_at 语义）

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class EntryServiceError(Exception):
    """code 对应 schema.entry_dto.ENTRY_ERROR_CODES；data 携带冲突详情。"""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, r = divmod(value, 32)
        out.append(_CROCKFORD[r])
    return "".join(reversed(out))


def _new_entry_id(now: datetime) -> str:
    """`entry_` + ULID（48bit 毫秒时间戳 + 80bit 随机，Q5）。"""
    ms = round(now.timestamp() * 1000)
    return "entry_" + _b32(ms & ((1 << 48) - 1), 10) + _b32(
        int.from_bytes(secrets.token_bytes(10), "big"), 16
    )


def _rfc3339(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_rfc3339(value) -> datetime:
    if not isinstance(value, str):
        raise EntryServiceError("invalid_request", "时间须为 RFC3339 字符串")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise EntryServiceError("invalid_request", f"非法 RFC3339 时间: {value!r}") from None
    if dt.tzinfo is None:
        raise EntryServiceError("invalid_request", f"时间须带时区: {value!r}")
    return dt.astimezone(timezone.utc)


def _validate_type(value) -> str:
    if value not in _ENTRY_TYPES:
        raise EntryServiceError("invalid_request", f"type 须为 memory 或 knowledge: {value!r}")
    return value


def _validate_body(value) -> str:
    if not isinstance(value, str) or not value:
        raise EntryServiceError("invalid_request", "body 必填且非空")
    if len(value.encode("utf-8")) > MAX_BODY_BYTES:
        raise EntryServiceError("invalid_request", f"body 超过 {MAX_BODY_BYTES} 字节上限")
    return value


def _normalize_project(raw) -> str:
    if not isinstance(raw, str):
        raise EntryServiceError("invalid_project", f"项目标签须为字符串: {raw!r}")
    tag = raw.strip().lower()
    if not PROJECT_RE.match(tag):
        raise EntryServiceError(
            "invalid_project",
            f"项目标签非法: {raw!r}（trim+小写后须匹配 {PROJECT_RE.pattern}，不静默改写）",
        )
    return tag


def _validate_scope(value) -> Scope:
    if not isinstance(value, dict) or "kind" not in value:
        raise EntryServiceError("invalid_request", "scope 须为 {kind: global|projects, ...}")
    kind = value["kind"]
    extra = set(value) - {"kind", "projects"}
    if kind == "global":
        if "projects" in value or extra:
            raise EntryServiceError("invalid_request", "global scope 仅 {kind: global}")
        return Scope(kind="global", projects=[])
    if kind == "projects":
        if extra:
            raise EntryServiceError("invalid_request", f"scope 含未知字段: {sorted(extra)}")
        raw = value.get("projects")
        if not isinstance(raw, list) or not raw:
            raise EntryServiceError(
                "invalid_request", "projects scope 须携带 1-16 个标签（空数组非法，全局须显式 global）"
            )
        if len(raw) > MAX_PROJECTS:
            raise EntryServiceError("invalid_request", f"projects 最多 {MAX_PROJECTS} 个")
        tags = [_normalize_project(p) for p in raw]
        if len(set(tags)) != len(tags):
            raise EntryServiceError("invalid_request", "projects 存在重复标签（规范化后）")
        return Scope(kind="projects", projects=tags)
    raise EntryServiceError("invalid_request", f"scope.kind 须为 global 或 projects: {kind!r}")


def _validate_author(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntryServiceError("invalid_request", "author 必填（自报「客户端/版本」）")
    if len(value) > MAX_AUTHOR_CHARS:
        raise EntryServiceError("invalid_request", f"author 超过 {MAX_AUTHOR_CHARS} 字符上限")
    return value


def _validate_source(value) -> SourceRef | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EntryServiceError("invalid_request", "source 须为 null 或 {url?, note?}")
    extra = set(value) - {"url", "note"}
    if extra:
        raise EntryServiceError("invalid_request", f"source 含未知字段: {sorted(extra)}")
    url, note = value.get("url"), value.get("note")
    for k, v in (("url", url), ("note", note)):
        if v is not None and (not isinstance(v, str) or not v.strip()):
            raise EntryServiceError("invalid_request", f"source.{k} 须为非空字符串或省略")
    if not (url or note):
        raise EntryServiceError("invalid_request", "source 须含 url 或 note（未知来源用 null，不伪造引用）")
    return SourceRef(url=url, note=note)


def _validate_idempotency_key(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntryServiceError("invalid_request", "idempotency_key 须为非空字符串")
    if len(value) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise EntryServiceError("invalid_request", f"idempotency_key 超过 {MAX_IDEMPOTENCY_KEY_CHARS} 字符")
    return value


def _validate_expected_revision(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EntryServiceError("invalid_request", f"expected_revision 须为正整数: {value!r}")
    return value


def _request_hash(op: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{op}:{canonical}".encode("utf-8")).hexdigest()


def _source_dict(ref: SourceRef | dict | None) -> dict | None:
    """规范化为不含空键的 dict（state 内即此形态，二次调用直接透传）。"""
    if ref is None:
        return None
    if isinstance(ref, dict):
        return ref or None
    out = {}
    if ref.url:
        out["url"] = ref.url
    if ref.note:
        out["note"] = ref.note
    return out


def _row_of(state: dict) -> dict:
    return {
        "id": state["id"],
        "type": state["type"],
        "body": state["body"],
        "scope_kind": state["scope"]["kind"],
        "scope_projects": json.dumps(state["scope"]["projects"], ensure_ascii=False),
        "author": state["author"],
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
        "revision": state["revision"],
        "status": state["status"],
        "expires_at": state["expires_at"],
        "source": json.dumps(_source_dict(state["source"]), ensure_ascii=False)
        if state["source"] is not None else None,
    }


def _state_of(row: dict) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "body": row["body"],
        "scope": {"kind": row["scope_kind"], "projects": json.loads(row["scope_projects"])},
        "author": row["author"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "revision": row["revision"],
        "status": row["status"],
        "expires_at": row["expires_at"],
        "source": json.loads(row["source"]) if row["source"] else None,
    }


def _entry_of(state: dict, now: datetime) -> Entry:
    expires_at = state["expires_at"]
    expired = expires_at is not None and datetime.fromisoformat(expires_at) <= now
    return Entry(**state, expired=expired)


def _scope_dict(scope: Scope) -> dict:
    return {"kind": scope.kind, "projects": list(scope.projects)}


class EntryService:
    def __init__(self, store: EntryStore, *, clock: Callable[[], datetime] | None = None):
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return self._clock()

    def _check_idempotency(self, key: str, request_hash: str) -> Entry | None:
        """已有同键记录：同内容返回原始结果重放，异内容 409。"""
        existing = self._store.get_idempotency(key)
        if existing is None:
            return None
        if existing["request_hash"] != request_hash:
            raise EntryServiceError("idempotency_conflict", f"幂等键已绑定不同请求内容: {key!r}")
        return Entry.model_validate_json(existing["response"])

    def _record_idempotency(self, key: str, request_hash: str, result: Entry) -> None:
        self._store.put_idempotency(key, request_hash, result.model_dump_json(), _rfc3339(self._now()))

    def _require_entry(self, entry_id: str) -> dict:
        row = self._store.get_entry(entry_id)
        if row is None:
            raise EntryServiceError("not_found", f"条目不存在: {entry_id}")
        return row

    def create(self, *, type, body, scope, author, expires_at=None, source=None,
               idempotency_key=None) -> Entry:
        entry_type = _validate_type(type)
        entry_body = _validate_body(body)
        entry_scope = _validate_scope(scope)
        entry_author = _validate_author(author)
        key = _validate_idempotency_key(idempotency_key) if idempotency_key is not None else None
        expires_norm = _rfc3339(_parse_rfc3339(expires_at)) if expires_at is not None else None
        source_ref = _validate_source(source)
        request_hash = _request_hash("create", {
            "type": type, "body": body, "scope": scope, "author": author,
            "expires_at": expires_at, "source": source,
        })

        now = self._now()
        with self._store.transaction():
            if key is not None:
                replay = self._check_idempotency(key, request_hash)
                if replay is not None:
                    return replay
            state = {
                "id": _new_entry_id(now),
                "type": entry_type,
                "body": entry_body,
                "scope": _scope_dict(entry_scope),
                "author": entry_author,
                "created_at": _rfc3339(now),
                "updated_at": _rfc3339(now),
                "revision": 1,
                "status": "active",
                "expires_at": expires_norm,
                "source": _source_dict(source_ref),
            }
            self._store.insert_entry(_row_of(state))
            self._store.append_revision(
                state["id"], 1, "create", entry_author,
                state["created_at"], json.dumps(state, ensure_ascii=False),
            )
            result = _entry_of(state, now)
            if key is not None:
                self._record_idempotency(key, request_hash, result)
            return result

    def get(self, entry_id: str) -> Entry:
        return _entry_of(_state_of(self._require_entry(entry_id)), self._now())

    def search(self, *, query, projects=None, all_projects=False, type=None,
               limit=20) -> EntrySearchResult:
        """全文搜索（§4.1/§4.2）：范围语义与恒定过滤见 store.search_fts。"""
        if not isinstance(query, str) or not query.strip():
            raise EntryServiceError("invalid_request", "query 必填且非空")
        if len(query) > 2000:
            raise EntryServiceError("invalid_request", "query 超过 2000 字符")
        if not isinstance(all_projects, bool):
            raise EntryServiceError("invalid_request", "all_projects 须为布尔值")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise EntryServiceError("invalid_request", "limit 须为 1-50，默认 20")
        entry_type = _validate_type(type) if type is not None else None

        if projects is not None:
            if all_projects:
                raise EntryServiceError("invalid_request", "projects 与 all_projects 互斥")
            if not isinstance(projects, list) or not projects:
                raise EntryServiceError("invalid_request", "projects 须为非空标签列表（仅查全局则省略）")
            scope = sorted({_normalize_project(p) for p in projects})
        elif all_projects:
            scope = None
        else:
            scope = "global"  # 未指定 = 仅全局（§4.1）

        now = self._now()
        rows = self._store.search_fts(
            query, scope=scope, type_filter=entry_type,
            now_iso=_rfc3339(now), limit=limit,
        )
        results = [_entry_of(_state_of(r), now) for r in rows]
        return EntrySearchResult(query=query, results=results, returned_k=len(results))

    def history(self, entry_id: str) -> list[RevisionSummary]:
        self._require_entry(entry_id)
        return [
            RevisionSummary(**r) for r in self._store.list_revisions(entry_id)
        ]

    def revision_snapshot(self, entry_id: str, revision: int) -> Entry:
        revision = _validate_expected_revision(revision)
        row = self._store.get_revision(entry_id, revision)
        if row is None:
            self._require_entry(entry_id)  # 区分条目不存在与修订不存在
            raise EntryServiceError("not_found", f"修订不存在: {entry_id}#{revision}")
        return _entry_of(json.loads(row["snapshot"]), self._now())

    def revise(self, entry_id: str, *, expected_revision: int, author,
               body=UNSET, type=UNSET, scope=UNSET, expires_at=UNSET,
               source=UNSET, idempotency_key=None) -> Entry:
        expected_revision = _validate_expected_revision(expected_revision)
        entry_author = _validate_author(author)
        key = _validate_idempotency_key(idempotency_key) if idempotency_key is not None else None

        new_body = _validate_body(body) if body is not UNSET else None
        new_type = _validate_type(type) if type is not UNSET else None
        new_scope = _validate_scope(scope) if scope is not UNSET else None
        new_expires = (
            _rfc3339(_parse_rfc3339(expires_at)) if expires_at is not None else None
        ) if expires_at is not UNSET else UNSET
        new_source = _validate_source(source) if source is not UNSET else UNSET

        payload = {
            "entry_id": entry_id, "expected_revision": expected_revision, "author": author,
        }
        for name, raw in (("body", body), ("type", type), ("scope", scope),
                          ("expires_at", expires_at), ("source", source)):
            if raw is not UNSET:
                payload[name] = raw
        request_hash = _request_hash("revise", payload)

        with self._store.transaction():
            if key is not None:
                replay = self._check_idempotency(key, request_hash)
                if replay is not None:
                    return replay
            row = self._require_entry(entry_id)
            if row["status"] == "deleted":
                raise EntryServiceError(
                    "entry_deleted", f"条目已删除，仅允许 restore 与显式读取: {entry_id}"
                )
            self._check_revision(row, expected_revision)

            state = _state_of(row)
            if new_body is not None:
                state["body"] = new_body
            if new_type is not None:
                state["type"] = new_type
            if new_scope is not None:
                state["scope"] = _scope_dict(new_scope)
            if new_expires is not UNSET:
                state["expires_at"] = new_expires
            if new_source is not UNSET:
                state["source"] = _source_dict(new_source)
            result = self._apply(state, "update", entry_author)
            if key is not None:
                self._record_idempotency(key, request_hash, result)
            return result

    def lifecycle(self, entry_id: str, *, op, expected_revision: int, author,
                  idempotency_key=None) -> Entry:
        if op not in _LIFECYCLE_OPS:
            raise EntryServiceError("invalid_request", f"op 须为 {'/'.join(_LIFECYCLE_OPS)}: {op!r}")
        expected_revision = _validate_expected_revision(expected_revision)
        entry_author = _validate_author(author)
        key = _validate_idempotency_key(idempotency_key) if idempotency_key is not None else None
        request_hash = _request_hash("lifecycle", {
            "entry_id": entry_id, "op": op,
            "expected_revision": expected_revision, "author": author,
        })

        with self._store.transaction():
            if key is not None:
                replay = self._check_idempotency(key, request_hash)
                if replay is not None:
                    return replay
            row = self._require_entry(entry_id)
            self._check_revision(row, expected_revision)
            target = _TRANSITIONS.get((row["status"], op))
            if target is None:
                raise EntryServiceError(
                    "invalid_transition", f"状态 {row['status']} 不允许 {op}"
                )
            state = _state_of(row)
            state["status"] = target
            result = self._apply(state, op, entry_author)
            if key is not None:
                self._record_idempotency(key, request_hash, result)
            return result

    def _check_revision(self, row: dict, expected_revision: int) -> None:
        if row["revision"] != expected_revision:
            current = _entry_of(_state_of(row), self._now())
            raise EntryServiceError(
                "revision_conflict",
                f"期望修订 {expected_revision}，当前为 {row['revision']}",
                data={"current": current.model_dump()},
            )

    def _apply(self, state: dict, op: str, modifier: str) -> Entry:
        """应用变更并追加修订记录；调用方须已持有事务。"""
        now = self._now()
        state["revision"] += 1
        state["updated_at"] = _rfc3339(now)
        row = _row_of(state)
        mutable = {k: v for k, v in row.items() if k not in ("id", "author", "created_at")}
        self._store.update_entry(state["id"], mutable)
        self._store.append_revision(
            state["id"], state["revision"], op, modifier,
            state["updated_at"], json.dumps(state, ensure_ascii=False),
        )
        result = _entry_of(state, now)
        return result
