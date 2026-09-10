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


class _FakeGeneration:
    """stub 生成服务：返回固定 AnswerResponse（API/MCP 契约测试用）。"""

    def __init__(self):
        self.calls = []

    def metadata(self):
        return {"model": "fake-model", "base_url": "http://fake"}

    def modes(self):
        return ["rag", "agentic"]

    def invoke(self, req):
        from schema.dto import AnswerResponse, Usage

        self.calls.append(req.model_dump())
        return AnswerResponse(
            request_id="r-test",
            index_id="sha256:" + "b" * 64,
            mode=req.mode,
            decision="answered",
            answer="fake answer",
            citations=[],
            usage=Usage(input_tokens=1, output_tokens=2, model_calls=1, estimated_cost_cny=0.0),
        )

    async def stream(self, req):
        import json

        yield 'data: {"type":"status","stage":"started","data":{}}\n\n'
        result = self.invoke(req)
        yield f"data: {json.dumps({'type': 'done', 'result': result.model_dump()})}\n\n"
        yield "data: [DONE]\n\n"


def make_app_service(generation=True):
    svc = AppService(
        snapshot=Snapshot("sha256:" + "b" * 64, {
            "dense_model": "D", "sparse_model": "S",
            "parent_count": 1, "child_count": 1,
            "corpus_manifest_sha256": "c" * 64,
        }),
        retrieval_service=make_retrieval_service(),
        generation=_FakeGeneration() if generation else None,
    )
    return svc


@pytest.fixture
def client():
    """Bare client without lifespan. /health 503；注入 app_service 后可用。"""
    import api.main as m

    return TestClient(m.app)


@pytest.fixture
def app_client():
    """Client with a stubbed AppService injected (lifespan bypassed)."""
    import api.main as m

    svc = make_app_service(generation=False)
    m.app.state.app_service = svc
    yield TestClient(m.app)
    m.app.state.app_service = None


@pytest.fixture
def gen_client():
    """Client with generation-enabled stubbed AppService."""
    import api.main as m

    svc = make_app_service(generation=True)
    m.app.state.app_service = svc
    yield TestClient(m.app)
    m.app.state.app_service = None
