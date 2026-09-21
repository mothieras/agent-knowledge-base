"""T7 长条目分块测试（D6/PHASE2-T67 §4.5）。

span 精确切片、短条目单块等价、长条目搜索返回 matched 片段且条目身份唯一、
修订重建分块、存量库幂等迁移（entries_fts 弃用删除）。
"""
import sqlite3

import pytest

import config
from core.entry_service import EntryService
from db.entry_store import EntryStore, split_entry_body


@pytest.fixture
def svc(tmp_path):
    s = EntryService(EntryStore(str(tmp_path / "entries.db")))
    yield s
    s._store.close()


def add(svc, body, scope=None):
    return svc.create(type="knowledge", body=body,
                      scope=scope or {"kind": "global"}, author="t/1.0")


def test_split_short_entry_single_chunk():
    body = "短条目正文"
    chunks = split_entry_body(body)
    assert chunks == [(0, len(body), body)]


def test_split_long_entry_spans_are_exact_slices():
    body = "甲乙丙丁戊己庚辛壬癸" * 500  # 5000 字符 > 阈值
    chunks = split_entry_body(body)
    assert len(chunks) > 1
    for start, end, text in chunks:
        assert body[start:end] == text  # span 精确覆盖 body 切片
    assert chunks[0][0] == 0 and chunks[-1][1] == len(body)


def test_short_entry_search_matched_full_body(svc):
    e = add(svc, "余弦相似度解释")
    r = svc.search(query="余弦", limit=5)
    assert [x.id for x in r.results] == [e.id]
    m = r.matched[e.id]
    assert m.text == "余弦相似度解释"
    assert m.chunk_index == 0 and m.span_start == 0 and m.span_end == len("余弦相似度解释")


def test_long_entry_search_returns_matched_chunk_once(svc):
    prefix = "前言部分与主题无关。"
    tail = ("向量数据库的余弦相似度用于衡量两个向量方向一致性，" * 80)
    e = add(svc, prefix + tail)
    r = svc.search(query="余弦相似度", limit=10)
    ids = [x.id for x in r.results]
    assert ids.count(e.id) == 1  # 多 chunk 命中同一条目只返回一次
    m = r.matched[e.id]
    assert "余弦相似度" in m.text
    assert body_slice_ok(prefix + tail, m.span_start, m.span_end, m.text)
    # matched span 是 body 的精确切片
    assert m.chunk_index >= 0


def body_slice_ok(body, start, end, text):
    return body[start:end] == text


def test_long_entry_search_scope_filter_before_truncation(svc):
    long = add(svc, "全局的余弦相似度说明" + ("无关内容" * 600),
               {"kind": "global"})
    add(svc, "项目的余弦相似度说明" + ("详解展开" * 400),
        {"kind": "projects", "projects": ["p"]})
    r = svc.search(query="余弦相似度", limit=1)  # 未指定范围仅全局；过滤先于截断
    assert [x.id for x in r.results] == [long.id]


def test_revise_rebuilds_chunks(svc):
    e = add(svc, "qwertyz 独有词 短内容")
    rev = svc.revise(e.id, expected_revision=1, author="t/1.0",
                     body="全新主题" + ("长尾内容" * 600))
    r = svc.search(query="全新主题", limit=5)
    assert [x.id for x in r.results] == [rev.id]
    old = svc.search(query="qwertyz", limit=5)
    assert e.id not in [x.id for x in old.results]


def test_legacy_db_migrated_on_open(tmp_path):
    """存量库（entries_fts 整 body 索引）打开后：分块回填、旧索引删除。"""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE entries (
        id TEXT PRIMARY KEY, type TEXT NOT NULL, body TEXT NOT NULL,
        scope_kind TEXT NOT NULL, scope_projects TEXT NOT NULL, author TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL,
        status TEXT NOT NULL, expires_at TEXT, source TEXT
    );
    CREATE TABLE revisions (
        entry_id TEXT NOT NULL, revision INTEGER NOT NULL, op TEXT NOT NULL,
        modifier TEXT NOT NULL, recorded_at TEXT NOT NULL, snapshot TEXT NOT NULL,
        PRIMARY KEY (entry_id, revision)
    );
    CREATE TABLE idempotency (
        key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        response TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE entries_fts USING fts5(tokens);
    """)
    conn.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        "entry_legacy1", "memory", "存量条目正文", "global", "[]", "a",
        "2026-09-20T00:00:00.000Z", "2026-09-20T00:00:00.000Z", 1,
        "active", None, None,
    ))
    conn.commit()
    conn.close()

    store = EntryStore(path)
    try:
        svc = EntryService(store)
        r = svc.search(query="存量", limit=5)
        assert [x.id for x in r.results] == ["entry_legacy1"]
        assert r.matched["entry_legacy1"].text == "存量条目正文"
        assert store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'entries_fts'"
        ).fetchone() is None  # 旧整 body 索引已弃用删除
    finally:
        store.close()


def test_chunks_in_same_transaction(tmp_path):
    """写入与分块同事务：store 层的分块行与 FTS 行同步提交（PHASE2 Q8）。"""
    from db.entry_store import EntryStore as ES

    store = ES(str(tmp_path / "tx.db"))
    try:
        svc = EntryService(store)
        svc.create(type="knowledge", body="事务内分块", scope={"kind": "global"}, author="a")
        n = store._conn.execute(
            "SELECT COUNT(*) FROM entry_chunks c JOIN entries e ON e.id = c.entry_id"
        ).fetchone()[0]
        assert n == 1
    finally:
        store.close()
