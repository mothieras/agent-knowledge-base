"""T7 source.document 引用测试（D6/PHASE2-T67 §4.2）。

机械校验：引用必须指向已注册版本、span 成对且 0 ≤ start < end ≤ 全文长度；
非法引用 400 invalid_source（不伪造引用）；修订同样校验；历史快照全量保留引用。
"""
from datetime import datetime, timezone

import pytest

from core.entry_service import EntryService, EntryServiceError
from db.doc_store import DocStore
from db.entry_store import EntryStore


@pytest.fixture
def doc_store(tmp_path):
    s = DocStore(str(tmp_path / "docs.db"))
    s.register(
        doc_id="docs/a.md", name="a.md", content="一二三四五六七八九十" * 3,
        origin_file="data/a.md", index_id="idx-1",
        created_at="2026-09-20T00:00:00.000Z",
    )
    yield s
    s.close()


@pytest.fixture
def svc(tmp_path, doc_store):
    clock = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    s = EntryService(EntryStore(str(tmp_path / "entries.db")),
                     clock=lambda: clock, doc_store=doc_store)
    yield s
    s._store.close()


def make(svc, source=None, **kw):
    return svc.create(
        type="knowledge", body="派生条目正文", scope={"kind": "global"},
        author="t/1.0", source=source, **kw,
    )


def test_create_with_document_ref_and_roundtrip(svc):
    e = make(svc, {"document": {"doc_id": "docs/a.md", "version": 1}})
    assert e.source.document.model_dump() == {
        "doc_id": "docs/a.md", "version": 1, "span_start": None, "span_end": None,
    }
    got = svc.get(e.id)
    assert got.source.document.doc_id == "docs/a.md"


def test_create_with_span_ref(svc):
    e = make(svc, {"document": {"doc_id": "docs/a.md", "version": 1,
                                "span_start": 0, "span_end": 5}})
    assert e.source.document.span_start == 0 and e.source.document.span_end == 5


def test_ref_unknown_version_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 99}})
    assert exc.value.code == "invalid_source"


def test_ref_unknown_doc_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/nope.md", "version": 1}})
    assert exc.value.code == "invalid_source"


def test_ref_span_unpaired_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 1, "span_start": 0}})
    assert exc.value.code == "invalid_source"


def test_ref_span_reversed_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 1,
                                "span_start": 5, "span_end": 1}})
    assert exc.value.code == "invalid_source"


def test_ref_span_out_of_range_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 1,
                                "span_start": 0, "span_end": 10 ** 6}})
    assert exc.value.code == "invalid_source"


def test_ref_unknown_fields_rejected(svc):
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 1, "bogus": 1}})
    assert exc.value.code == "invalid_source"
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"url": "https://x", "bogus": 1})
    assert exc.value.code == "invalid_request"


def test_ref_bad_types_rejected(svc):
    for bad in ({"document": "x"}, {"document": {"doc_id": "", "version": 1}},
                {"document": {"doc_id": "docs/a.md", "version": 0}},
                {"document": {"doc_id": "docs/a.md", "version": True}},
                {"document": {"doc_id": "docs/a.md", "version": 1,
                              "span_start": "0", "span_end": 1}}):
        with pytest.raises(EntryServiceError) as exc:
            make(svc, bad)
        assert exc.value.code == "invalid_source", bad


def test_ref_url_note_document_coexist(svc):
    e = make(svc, {"url": "https://x/y", "note": "口述",
                   "document": {"doc_id": "docs/a.md", "version": 1}})
    s = e.source
    assert s.url == "https://x/y" and s.note == "口述" and s.document.doc_id == "docs/a.md"


def test_revise_updates_and_validates_ref(svc):
    e = make(svc, {"note": "初版来源"})
    # 修订为文档引用：同样机械校验
    rev = svc.revise(e.id, expected_revision=1, author="t/1.0",
                     source={"document": {"doc_id": "docs/a.md", "version": 1}})
    assert rev.revision == 2 and rev.source.document.version == 1
    with pytest.raises(EntryServiceError) as exc:
        svc.revise(e.id, expected_revision=2, author="t/1.0",
                   source={"document": {"doc_id": "docs/a.md", "version": 9}})
    assert exc.value.code == "invalid_source"
    # 校验失败不产生修订
    assert svc.get(e.id).revision == 2


def test_ref_preserved_in_revision_history(svc):
    e = make(svc, {"document": {"doc_id": "docs/a.md", "version": 1}})
    snap = svc.revision_snapshot(e.id, 1)
    assert snap.source.document.doc_id == "docs/a.md"
    rev = svc.revise(e.id, expected_revision=1, author="t/1.0", body="改版")
    snap2 = svc.revision_snapshot(e.id, 2)
    assert snap2.source.document.doc_id == "docs/a.md"  # 缺省 = 不变
    assert snap2.body == "改版"


def test_idempotent_replay_with_doc_ref(svc):
    key = "k1"
    e = make(svc, {"document": {"doc_id": "docs/a.md", "version": 1}},
             idempotency_key=key)
    replay = make(svc, {"document": {"doc_id": "docs/a.md", "version": 1}},
                  idempotency_key=key)
    assert replay.id == e.id and replay.revision == 1  # 同键同内容重放原结果
    with pytest.raises(EntryServiceError) as exc:
        make(svc, {"document": {"doc_id": "docs/a.md", "version": 1, "span_start": 0, "span_end": 1}},
             idempotency_key=key)
    assert exc.value.code == "idempotency_conflict"
