"""证据存储与 L2 检索服务测试：evidence_id 解析、窗口、DTO 预算与不变量。"""
import hashlib

import pytest

from db.evidence_store import EvidenceError, EvidenceStore
from db.retrieval import RetrievalHit
from db.snapshot import Snapshot
from core.retrieval_service import DTO_BUDGET_BYTES, RetrievalService, ServiceError
from schema.dto import EvidenceWindow, SearchRequest


class _ParentStore:
    def __init__(self, parents: dict[str, dict]):
        self._parents = parents

    def load_content(self, parent_id: str):
        return self._parents.get(parent_id)


class _Retriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, k=7):
        return self._hits[:k]


def _snapshot(index_id="sha256:" + "a" * 64):
    return Snapshot(index_id, {
        "corpus_manifest_sha256": "c" * 64,
        "dense_model": "D",
        "sparse_model": "S",
        "parent_count": 1,
        "child_count": 1,
    })


def _hit(content="child excerpt", span_start=0, span_end=10, parent_id="doc_p0",
         source="src.md", chunk_id="doc_p0_c0"):
    return RetrievalHit(
        source=source,
        parent_id=parent_id,
        content=content,
        version="v2",
        chunk_id=chunk_id,
        span_start=span_start,
        span_end=span_end,
        score=0.8,
        retrieval_channel="hybrid",
    )


def test_evidence_id_roundtrip_and_span_slice():
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": "hello world body", "metadata": {"source": "src.md"}}}),
        _snapshot(),
    )
    hit = _hit(content="hello worl", span_start=0, span_end=10)
    eid = store.evidence_id_for(hit)
    resolved = store.resolve(eid)
    assert resolved.content == "hello worl"
    assert resolved.span_start == 0 and resolved.span_end == 10
    assert resolved.retrieval_channel == "evidence_resolve"


def test_evidence_id_rejects_stale_snapshot():
    store = EvidenceStore(_ParentStore({}), _snapshot())
    stale_id = "deadbeef:doc_p0:0:10"
    with pytest.raises(EvidenceError) as exc:
        store.parse(stale_id)
    assert exc.value.code == "stale_evidence"


def test_evidence_id_rejects_missing_parent():
    store = EvidenceStore(_ParentStore({}), _snapshot())
    eid = f"{'a' * 8}:doc_p0:0:10"
    with pytest.raises(EvidenceError) as exc:
        store.resolve(eid)
    assert exc.value.code == "evidence_not_found"


def test_window_bounded_and_hashed():
    body = "0123456789"
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": body, "metadata": {"source": "s.md"}}}),
        _snapshot(),
    )
    eid = f"{'a' * 8}:doc_p0:2:5"
    w = store.window(eid, offset=1, limit=4)
    assert w["content"] == "1234"
    assert w["offset"] == 1 and w["total_length"] == 10
    assert EvidenceWindow(**w, content_hash="sha256:" + hashlib.sha256(b"1234").hexdigest())


def test_search_maps_hit_to_dto_and_validates_invariants():
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": "hello world body", "metadata": {"source": "src.md"}}}),
        _snapshot(),
    )
    svc = RetrievalService(_Retriever([_hit(content="hello worl")]), store)
    resp = svc.search(SearchRequest(query="hello", k=7))
    assert resp.index_id == store.index_id
    assert resp.returned_k == 1 and resp.truncated is False
    hit = resp.hits[0]
    assert hit.source == "src.md" and hit.version == "v2"
    assert hit.content_hash == "sha256:" + hashlib.sha256(b"hello worl").hexdigest()
    assert hit.span.start == 0 and hit.span.end == 10


def test_search_debug_artifact_includes_full_hits():
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": "x" * 50, "metadata": {"source": "src.md"}}}),
        _snapshot(),
    )
    svc = RetrievalService(_Retriever([_hit(content="y" * 40, span_end=40)]), store)
    resp = svc.search(SearchRequest(query="q", k=7, include_debug_artifact=True))
    assert resp.debug_artifact is not None
    assert len(resp.debug_artifact) == 1
    resp2 = svc.search(SearchRequest(query="q", k=7))
    assert resp2.debug_artifact is None


def test_search_budget_truncates_and_flags():
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": "x", "metadata": {"source": "src.md"}}}),
        _snapshot(),
    )
    big = "z" * (DTO_BUDGET_BYTES // 2)
    svc = RetrievalService(
        _Retriever([_hit(content=big, span_end=len(big)),
                    _hit(content=big, span_end=len(big), chunk_id="doc_p0_c1")]),
        store,
    )
    resp = svc.search(SearchRequest(query="q", k=7))
    assert resp.truncated is True
    assert resp.returned_k == 1
    assert len(resp.hits[0].content) == len(big)


def test_single_hit_over_budget_raises():
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": "x", "metadata": {"source": "src.md"}}}),
        _snapshot(),
    )
    huge = "z" * (DTO_BUDGET_BYTES + 100)
    svc = RetrievalService(_Retriever([_hit(content=huge, span_end=len(huge))]), store)
    with pytest.raises(ServiceError) as exc:
        svc.search(SearchRequest(query="q", k=7))
    assert exc.value.code == "response_too_large"


def test_hit_without_span_is_skipped_not_faked():
    store = EvidenceStore(_ParentStore({}), _snapshot())
    svc = RetrievalService(_Retriever([_hit(span_start=None, span_end=None)]), store)
    resp = svc.search(SearchRequest(query="q", k=7))
    assert resp.returned_k == 0


def test_read_evidence_propagates_error_codes():
    store = EvidenceStore(_ParentStore({}), _snapshot())
    svc = RetrievalService(_Retriever([]), store)
    with pytest.raises(ServiceError) as exc:
        svc.read_evidence("deadbeef:doc_p0:0:10")
    assert exc.value.code == "stale_evidence"
