"""T1 条目服务单测（PHASE2 §7.1 验证口径）。

覆盖：并发修订单胜一败、幂等重放/同键异内容、生命周期全转换矩阵、可控
时钟到期判定、标签规范化、scope 校验、修订语义与字段边界、持久化。
"""
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.entry_service import EntryService, EntryServiceError
from db.entry_store import EntryStore

ID_RE = re.compile(r"^entry_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


class FakeClock:
    def __init__(self, start=None):
        self.now = start or datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now = self.now + timedelta(**kw)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    s = EntryStore(str(tmp_path / "entries.db"))
    yield s
    s.close()


@pytest.fixture
def svc(store, clock):
    return EntryService(store, clock=clock)


def make_entry(svc, **kw):
    params = dict(
        type="memory",
        body="Agent 共享记忆条目：检索优先演示版当前只读。",
        scope={"kind": "global"},
        author="pi/0.85.1",
    )
    params.update(kw)
    return svc.create(**params)


def error_code(excinfo):
    return excinfo.value.code


# --- 创建与读取 ---


def test_create_and_get_roundtrip(svc):
    entry = svc.create(
        type="knowledge",
        body="部署约束：embedded Qdrant 单进程持有索引。",
        scope={"kind": "projects", "projects": ["rag", "deploy"]},
        author="claude-code/1.0",
        source={"url": "https://example.com/doc", "note": "部署手册"},
    )
    assert ID_RE.match(entry.id)
    assert entry.revision == 1
    assert entry.status == "active"
    assert entry.expired is False
    assert entry.source.model_dump(exclude_none=True) == {"url": "https://example.com/doc", "note": "部署手册"}
    assert entry.created_at == entry.updated_at
    assert svc.get(entry.id) == entry


def test_id_unique_and_ulid_shape(svc):
    ids = {make_entry(svc, body=f"b{i}").id for i in range(50)}
    assert len(ids) == 50
    assert all(ID_RE.match(i) for i in ids)


def test_not_found(svc):
    for call in (
        lambda: svc.get("entry_0AAAAAAAAAAAAAAAAAAAAAAA"),
        lambda: svc.history("entry_0AAAAAAAAAAAAAAAAAAAAAAA"),
        lambda: svc.revision_snapshot("entry_0AAAAAAAAAAAAAAAAAAAAAAA", 1),
        lambda: svc.revise("entry_0AAAAAAAAAAAAAAAAAAAAAAA", expected_revision=1, author="a"),
        lambda: svc.lifecycle("entry_0AAAAAAAAAAAAAAAAAAAAAAA", op="archive",
                              expected_revision=1, author="a"),
    ):
        with pytest.raises(EntryServiceError) as exc:
            call()
        assert error_code(exc) == "not_found"


# --- 字段校验（§3.1/§3.2） ---


def test_type_enum(svc):
    with pytest.raises(EntryServiceError) as exc:
        make_entry(svc, type="note")
    assert error_code(exc) == "invalid_request"
    # 类型修正 = 正常修订
    e = make_entry(svc, type="memory")
    assert svc.revise(e.id, expected_revision=1, author="a", type="knowledge").type == "knowledge"


def test_body_limits(svc):
    with pytest.raises(EntryServiceError) as exc:
        make_entry(svc, body="")
    assert error_code(exc) == "invalid_request"
    with pytest.raises(EntryServiceError) as exc:
        make_entry(svc, body="a" * (64 * 1024 + 1))
    assert error_code(exc) == "invalid_request"
    assert make_entry(svc, body="a" * 64 * 1024).revision == 1


def test_author_rules(svc):
    for bad in ("", "   ", "x" * 129):
        with pytest.raises(EntryServiceError) as exc:
            make_entry(svc, author=bad)
        assert error_code(exc) == "invalid_request"


def test_project_tag_normalization(svc):
    e = make_entry(svc, scope={"kind": "projects", "projects": [" Rag-Deploy ", "EVAL"]})
    assert e.scope.projects == ["rag-deploy", "eval"]
    for bad in ("a b", "-a", "_a", "a" * 65, "", "项目"):
        with pytest.raises(EntryServiceError) as exc:
            make_entry(svc, scope={"kind": "projects", "projects": [bad]})
        assert error_code(exc) == "invalid_project"
    assert make_entry(
        svc, scope={"kind": "projects", "projects": ["a" * 64]}
    ).scope.projects == ["a" * 64]


def test_scope_validation(svc):
    cases = [
        {"kind": "projects", "projects": []},                      # 空数组非法
        {"kind": "projects"},                                      # 缺 projects
        {"kind": "projects", "projects": [f"p{i}" for i in range(17)]},  # 超 16
        {"kind": "projects", "projects": ["a", "A"]},              # 规范化后重复
        {"kind": "global", "projects": ["a"]},                     # global 带标签
        {"kind": "team"},                                          # 未知 kind
        "global",                                                  # 非对象
    ]
    for scope in cases:
        with pytest.raises(EntryServiceError) as exc:
            make_entry(svc, scope=scope)
        assert error_code(exc) == "invalid_request"
    assert make_entry(
        svc, scope={"kind": "projects", "projects": [f"p{i}" for i in range(16)]}
    ).revision == 1


def test_source_validation(svc):
    for bad in ({}, {"url": "  "}, {"url": 1}, {"href": "x"}, "x"):
        with pytest.raises(EntryServiceError) as exc:
            make_entry(svc, source=bad)
        assert error_code(exc) == "invalid_request"
    assert make_entry(svc, source={"note": "用户口述"}).source.model_dump(exclude_none=True) == {"note": "用户口述"}
    assert make_entry(svc).source is None


def test_expires_at_parsing(svc):
    for bad in ("2026-13-01T00:00:00Z", "2026-09-16T12:00:00", 123):
        with pytest.raises(EntryServiceError) as exc:
            make_entry(svc, expires_at=bad)
        assert error_code(exc) == "invalid_request"
    # 带时区偏移 → 规范化为 UTC
    e = make_entry(svc, expires_at="2027-01-01T08:00:00+08:00")
    assert e.expires_at == "2027-01-01T00:00:00.000Z"


# --- 修订与并发（§3.3） ---


def test_revise_changes_and_history(svc):
    e = make_entry(svc, body="初版正文", author="pi/0.85.1")
    r2 = svc.revise(
        e.id, expected_revision=1, author="claude-code/1.0",
        body="修订正文", scope={"kind": "projects", "projects": ["rag"]},
        source={"note": "来源更新"},
    )
    assert r2.revision == 2
    assert r2.body == "修订正文"
    assert r2.scope.projects == ["rag"]
    assert r2.author == "pi/0.85.1"  # author 为创建者，修订只记 modifier
    assert r2.updated_at >= r2.created_at

    history = svc.history(e.id)
    assert [(h.revision, h.op, h.modifier) for h in history] == [
        (1, "create", "pi/0.85.1"), (2, "update", "claude-code/1.0"),
    ]
    assert svc.revision_snapshot(e.id, 1).body == "初版正文"
    assert svc.revision_snapshot(e.id, 1).scope.kind == "global"


def test_revise_expires_at_semantics(svc):
    e = make_entry(svc, expires_at="2027-01-01T00:00:00Z")
    # 缺省 = 不变
    assert svc.revise(e.id, expected_revision=1, author="a").expires_at == e.expires_at
    # 替换
    r3 = svc.revise(e.id, expected_revision=2, author="a", expires_at="2028-06-01T00:00:00Z")
    assert r3.expires_at == "2028-06-01T00:00:00.000Z"
    # 显式 null = 清除
    assert svc.revise(e.id, expected_revision=3, author="a", expires_at=None).expires_at is None


def test_revision_conflict_carries_current(svc):
    e = make_entry(svc)
    svc.revise(e.id, expected_revision=1, author="a", body="v2")
    with pytest.raises(EntryServiceError) as exc:
        svc.revise(e.id, expected_revision=1, author="b", body="v2'")
    assert error_code(exc) == "revision_conflict"
    assert exc.value.data["current"]["revision"] == 2
    assert exc.value.data["current"]["body"] == "v2"


def test_expected_revision_validation(svc):
    e = make_entry(svc)
    for bad in (0, -1, "1", None, True):
        with pytest.raises(EntryServiceError) as exc:
            svc.revise(e.id, expected_revision=bad, author="a", body="x")
        assert error_code(exc) == "invalid_request"


def test_concurrent_revise_one_wins(svc):
    e = make_entry(svc)
    results = []
    barrier = threading.Barrier(2)

    def worker(i):
        barrier.wait()
        try:
            results.append(("ok", svc.revise(
                e.id, expected_revision=1, author=f"a{i}", body=f"body-{i}")))
        except EntryServiceError as err:
            results.append(("err", err))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and len(errs) == 1
    assert errs[0][1].code == "revision_conflict"
    assert svc.get(e.id).revision == 2
    assert len(svc.history(e.id)) == 2  # 仅 create + 一次成功 update


# --- 幂等（§3.4） ---


def test_idempotent_replay_create(svc, store):
    params = dict(
        type="memory", body="幂等正文", scope={"kind": "global"},
        author="pi/0.85.1", idempotency_key="op-123",
    )
    first = svc.create(**params)
    replayed = svc.create(**params)
    assert replayed == first
    count = store.get_entry(first.id)["revision"]
    assert count == 1
    assert len(svc.history(first.id)) == 1


def test_idempotent_replay_returns_original_result(svc):
    e = make_entry(svc, idempotency_key="rev-1")
    first = svc.revise(e.id, expected_revision=1, author="a", body="v2", idempotency_key="rev-2")
    svc.revise(e.id, expected_revision=2, author="b", body="v3")  # 他人推进
    replay = svc.revise(e.id, expected_revision=1, author="a", body="v2", idempotency_key="rev-2")
    assert replay == first  # 重放原始成功结果，不重复修改
    assert svc.get(e.id).revision == 3


def test_idempotency_key_conflict(svc):
    make_entry(svc, body="A", idempotency_key="k1")
    with pytest.raises(EntryServiceError) as exc:
        svc.create(
            type="memory", body="B", scope={"kind": "global"},
            author="pi/0.85.1", idempotency_key="k1",
        )
    assert error_code(exc) == "idempotency_conflict"
    # 键全局唯一：跨操作类型同样冲突
    e2 = make_entry(svc)
    with pytest.raises(EntryServiceError) as exc:
        svc.revise(e2.id, expected_revision=1, author="a", body="x", idempotency_key="k1")
    assert error_code(exc) == "idempotency_conflict"


def test_failed_operation_not_recorded_as_idempotent(svc):
    e = make_entry(svc)
    with pytest.raises(EntryServiceError):
        svc.revise(e.id, expected_revision=99, author="a", body="x", idempotency_key="k9")
    # 失败后同键可重试成功
    r = svc.revise(e.id, expected_revision=1, author="a", body="x", idempotency_key="k9")
    assert r.revision == 2


# --- 生命周期（§3.5） ---


def _to_status(svc, entry, status):
    e = entry
    while e.status != status:
        if e.status == "active":
            e = svc.lifecycle(e.id, op="archive", expected_revision=e.revision, author="a")
        elif e.status == "archived":
            e = svc.lifecycle(
                e.id, op="delete" if status == "deleted" else "unarchive",
                expected_revision=e.revision, author="a",
            )
    return e


@pytest.mark.parametrize("status,op,expected", [
    ("active", "archive", "archived"),
    ("active", "delete", "deleted"),
    ("archived", "unarchive", "active"),
    ("archived", "delete", "deleted"),
    ("deleted", "restore", "active"),
])
def test_lifecycle_legal_transitions(svc, status, op, expected):
    e = _to_status(svc, make_entry(svc), status)
    out = svc.lifecycle(e.id, op=op, expected_revision=e.revision, author="pi/0.85.1")
    assert out.status == expected
    assert out.revision == e.revision + 1
    assert svc.history(e.id)[-1].op == op


@pytest.mark.parametrize("status,op", [
    ("active", "unarchive"),
    ("active", "restore"),
    ("archived", "archive"),
    ("archived", "restore"),
    ("deleted", "archive"),
    ("deleted", "unarchive"),
    ("deleted", "delete"),
])
def test_lifecycle_invalid_transitions(svc, status, op):
    e = _to_status(svc, make_entry(svc), status)
    with pytest.raises(EntryServiceError) as exc:
        svc.lifecycle(e.id, op=op, expected_revision=e.revision, author="a")
    assert error_code(exc) == "invalid_transition"
    assert svc.get(e.id).revision == e.revision  # 未产生修订


def test_lifecycle_full_cycle_and_history(svc):
    e = make_entry(svc)
    for op, expected_status in [
        ("archive", "archived"), ("delete", "deleted"), ("restore", "active"),
    ]:
        e = svc.lifecycle(e.id, op=op, expected_revision=e.revision, author="a")
        assert e.status == expected_status
    assert [(h.revision, h.op) for h in svc.history(e.id)] == [
        (1, "create"), (2, "archive"), (3, "delete"), (4, "restore"),
    ]


def test_revise_on_deleted_and_archived(svc):
    e = make_entry(svc)
    d = svc.lifecycle(e.id, op="delete", expected_revision=1, author="a")
    with pytest.raises(EntryServiceError) as exc:
        svc.revise(e.id, expected_revision=d.revision, author="a", body="x")
    assert error_code(exc) == "entry_deleted"
    assert svc.get(e.id).status == "deleted"  # 显式读取可见
    # archived 可修订
    a = svc.lifecycle(e.id, op="restore", expected_revision=d.revision, author="a")
    a = svc.lifecycle(e.id, op="archive", expected_revision=a.revision, author="a")
    assert svc.revise(e.id, expected_revision=a.revision, author="a", body="v2").revision > a.revision


def test_body_and_history_survive_delete(svc):
    e = make_entry(svc, body="待删正文")
    svc.revise(e.id, expected_revision=1, author="a", body="v2")
    svc.lifecycle(e.id, op="delete", expected_revision=2, author="a")
    assert svc.revision_snapshot(e.id, 1).body == "待删正文"  # 历史全保留
    assert svc.get(e.id).body == "v2"


# --- 到期（§3.6，可控时钟） ---


def test_expiry_judgment_with_controllable_clock(svc, clock):
    e = make_entry(svc, expires_at="2026-09-16T13:00:00Z")
    assert svc.get(e.id).expired is False
    clock.advance(minutes=59)
    assert svc.get(e.id).expired is False
    clock.advance(minutes=2)  # 跨过到期点
    assert svc.get(e.id).expired is True
    assert svc.get(e.id).status == "active"  # 到期 ≠ 状态变化
    assert svc.revision_snapshot(e.id, 1).expires_at == "2026-09-16T13:00:00.000Z"


def test_expired_entry_still_revisable(svc, clock):
    e = make_entry(svc, expires_at="2026-09-16T12:30:00Z")
    clock.advance(hours=1)
    assert svc.get(e.id).expired is True
    r = svc.revise(e.id, expected_revision=1, author="a", body="到期后修订")
    assert r.body == "到期后修订"
    # 访问不续期：修订不改变 expires_at（缺省不变）
    assert svc.get(e.id).expired is True


# --- 持久化与 FTS（存储层） ---


def test_persistence_across_reopen(tmp_path, clock):
    path = str(tmp_path / "entries.db")
    store = EntryStore(path)
    svc = EntryService(store, clock=clock)
    e = make_entry(svc, expires_at="2027-01-01T00:00:00Z")
    svc.revise(e.id, expected_revision=1, author="a", body="v2")
    store.close()

    store2 = EntryStore(path)
    svc2 = EntryService(store2, clock=clock)
    got = svc2.get(e.id)
    assert got.body == "v2"
    assert got.revision == 2
    assert svc2.revision_snapshot(e.id, 1).body == "Agent 共享记忆条目：检索优先演示版当前只读。"
    assert [h.op for h in svc2.history(e.id)] == ["create", "update"]
    assert svc2.create(
        type="memory", body="x", scope={"kind": "global"}, author="a",
        idempotency_key="k",
    ).id  # 幂等表随库持久
    store2.close()


def test_fts_rows_maintained_in_transaction(svc, store):
    from db.entry_store import default_tokenize

    e = make_entry(svc, body="hybrid retrieval 混合检索")
    rowid = store._entry_rowid(e.id)
    tokens = store._conn.execute(
        "SELECT tokens FROM entries_fts WHERE rowid = ?", (rowid,)
    ).fetchone()[0]
    assert tokens == default_tokenize("hybrid retrieval 混合检索")  # 入库与查询同分词器

    svc.revise(e.id, expected_revision=1, author="a", body="new body")
    rows = store._conn.execute(
        "SELECT tokens FROM entries_fts WHERE rowid = ?", (rowid,)
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "new body"
