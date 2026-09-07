import asyncio

import pytest
from fastapi.testclient import TestClient

from core.app_service import AppService
from core.retrieval_service import RetrievalService
from db.evidence_store import EvidenceStore
from db.snapshot import Snapshot


class _ParentStore:
    def __init__(self, parents=None):
        self._parents = parents or {}

    def load_content(self, parent_id):
        return self._parents.get(parent_id)


class _Retriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, k=7):
        return self._hits[:k]


def make_retrieval_service():
    """真实 L2 检索服务（内存版），带一个可回查的父文档。"""
    body = "检索增强生成（RAG）结合参数化与非参数化知识。" * 10
    from db.retrieval import RetrievalHit

    hit = RetrievalHit(
        source="src.md",
        parent_id="doc_p0",
        content=body[:20],
        version="v2",
        chunk_id="doc_p0_c0",
        span_start=0,
        span_end=20,
        score=0.9,
        retrieval_channel="hybrid",
    )
    snapshot = Snapshot("sha256:" + "a" * 64, {
        "dense_model": "D", "sparse_model": "S",
        "parent_count": 1, "child_count": 1,
    })
    store = EvidenceStore(
        _ParentStore({"doc_p0": {"content": body, "metadata": {"source": "src.md"}}}),
        snapshot,
    )
    return RetrievalService(_Retriever([hit]), store)


def make_app_service():
    return AppService(
        snapshot=Snapshot("sha256:" + "b" * 64, {
            "dense_model": "D", "sparse_model": "S",
            "parent_count": 1, "child_count": 1,
            "corpus_manifest_sha256": "c" * 64,
        }),
        retrieval_service=make_retrieval_service(),
    )


@pytest.fixture
def client():
    """Bare client without lifespan. /health 503；注入 app_service 后可用。"""
    import api.main as m

    return TestClient(m.app)


@pytest.fixture
def app_client():
    """Client with a stubbed AppService injected (lifespan bypassed)."""
    import api.main as m

    svc = make_app_service()
    m.app.state.app_service = svc
    yield TestClient(m.app)
    m.app.state.app_service = None
