"""T7 文档来源回查 HTTP 路由测试（D6/PHASE2-T67 §4.4）。

resolve / 版本列表 / 有界窗口回查；错误路径（not_found / invalid_request）；
窗口 content_hash 为全文 sha256、旧版本永久可解析。
"""
import pytest

import api.main as m
from conftest import make_app_service
from core.app_service import AppService
from core.doc_service import DocService
from db.doc_store import DocStore, content_sha256


@pytest.fixture
def doc_client(tmp_path):
    store = DocStore(str(tmp_path / "docs.db"))
    store.register(
        doc_id="docs/a.md", name="a.md", content="甲乙丙丁戊己庚辛壬癸" * 30,
        origin_file="data/a.md", index_id="idx-1",
        created_at="2026-09-20T00:00:00.000Z",
    )
    store.register(
        doc_id="docs/a.md", name="a.md", content="更新后的文档内容" * 30,
        origin_file="data/a.md", index_id="idx-2",
        created_at="2026-09-20T00:01:00.000Z",
    )
    svc = make_app_service(generation=False)
    svc.documents = DocService(store)
    # 条目服务与文档服务共用同一注册表（生产组装见 app_service.build_app_service）
    from core.entry_service import EntryService as ES
    from db.entry_store import EntryStore

    svc.entries = ES(EntryStore(":memory:"), doc_store=store)
    m.app.state.app_service = svc
    try:
        from fastapi.testclient import TestClient

        yield TestClient(m.app)
    finally:
        m.app.state.app_service = None
        store.close()


def test_resolve_document(doc_client):
    r = doc_client.get("/documents/resolve", params={"source": "docs/a.md"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"doc_id": "docs/a.md", "name": "a.md",
                    "latest_version": 2, "total_versions": 2}


def test_resolve_unknown_source(doc_client):
    r = doc_client.get("/documents/resolve", params={"source": "docs/nope.md"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_list_versions(doc_client):
    r = doc_client.get("/documents/docs/a.md/versions")
    assert r.status_code == 200
    body = r.json()
    assert body["doc_id"] == "docs/a.md" and body["name"] == "a.md"
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert all("content" not in v for v in body["versions"])  # 版本列表不含正文


def test_read_window_v1_and_v2(doc_client):
    r = doc_client.get("/documents/docs/a.md/versions/1", params={"offset": 0, "limit": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["total_length"] == 300
    assert body["content"] == "甲乙丙丁戊己庚辛壬癸" * 2
    assert body["content_hash"] == content_sha256("甲乙丙丁戊己庚辛壬癸" * 30)
    # 文档更新后旧版本仍可解析（旧引用指向原修订）
    r2 = doc_client.get("/documents/docs/a.md/versions/2", params={"offset": 0, "limit": 7})
    assert r2.json()["content"] == "更新后的文档内容"[:7]


def test_read_window_offset_clamp(doc_client):
    r = doc_client.get("/documents/docs/a.md/versions/1",
                       params={"offset": 295, "limit": 10})
    assert r.status_code == 200
    assert r.json()["content"] == "己庚辛壬癸"


def test_read_window_errors(doc_client):
    r = doc_client.get("/documents/docs/nope.md/versions/1")
    assert r.status_code == 404
    r = doc_client.get("/documents/docs/a.md/versions/99")
    assert r.status_code == 404
    r = doc_client.get("/documents/docs/a.md/versions/1", params={"offset": -1})
    assert r.status_code == 400
    r = doc_client.get("/documents/docs/a.md/versions/1", params={"limit": 99999})
    assert r.status_code == 400
    r = doc_client.get("/documents/docs/a.md/versions/1", params={"offset": 99999})
    assert r.status_code == 400


def test_health_includes_documents(doc_client):
    body = doc_client.get("/health").json()
    assert body["documents"] == "ready"


def test_entry_with_document_ref_http_roundtrip(doc_client):
    """HTTP 通道：条目 source.document 校验依赖真实注册表。"""
    payload = {
        "type": "knowledge",
        "body": "派生条目",
        "scope": {"kind": "global"},
        "author": "t/1.0",
        "source": {"document": {"doc_id": "docs/a.md", "version": 2,
                                "span_start": 0, "span_end": 3}},
    }
    r = doc_client.post("/entries", json=payload)
    assert r.status_code == 201
    assert r.json()["source"]["document"]["version"] == 2
    bad = {**payload, "source": {"document": {"doc_id": "docs/a.md", "version": 9}}}
    r = doc_client.post("/entries", json=bad)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_source"
