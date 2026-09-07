import json

import pytest


def test_health_starting(client):
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "snapshot_unavailable"


def test_info_requires_service(client):
    r = client.get("/info")
    assert r.status_code == 503


def test_health_ready_with_service(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_info_exposes_snapshot_and_modes(app_client):
    r = app_client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["index_id"].startswith("sha256:")
    assert body["generation"] is None
    assert body["modes"] == []
    assert body["api_version"] == "2"


def test_search_returns_evidence(app_client):
    r = app_client.post("/search", json={"query": "RAG 是什么", "k": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["index_id"].startswith("sha256:")
    assert body["returned_k"] == 1
    hit = body["hits"][0]
    assert hit["evidence_id"]
    assert hit["content_hash"].startswith("sha256:")
    assert hit["span"]["end"] - hit["span"]["start"] == len(hit["content"])


def test_search_validation(app_client):
    assert app_client.post("/search", json={"query": "", "k": 7}).status_code == 422
    assert app_client.post("/search", json={"query": "x", "k": 99}).status_code == 422


def test_evidence_roundtrip(app_client):
    search = app_client.post("/search", json={"query": "RAG", "k": 7}).json()
    eid = search["hits"][0]["evidence_id"]
    r = app_client.get(f"/evidence/{eid}", params={"offset": 0, "limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["index_id"] == search["index_id"]
    assert body["total_length"] > 0


def test_evidence_invalid_id(app_client):
    r = app_client.get("/evidence/not-an-id")
    assert r.status_code in (404, 410)
    assert r.json()["detail"]["code"] in ("evidence_not_found", "stale_evidence")


def test_invoke_not_configured(app_client):
    r = app_client.post("/invoke", json={"message": "hi", "mode": "rag"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_not_configured"


def test_stream_not_configured(app_client):
    r = app_client.post("/stream", json={"message": "hi", "mode": "rag"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_not_configured"


def test_history_retired(app_client):
    r = app_client.get("/history", params={"thread_id": "t"})
    assert r.status_code == 410


def test_invoke_rejects_thread_id(app_client):
    r = app_client.post("/invoke", json={"message": "hi", "mode": "rag", "thread_id": "t"})
    assert r.status_code == 422


def test_bearer_auth_enforced(monkeypatch, app_client):
    import api.auth as auth

    monkeypatch.setattr(auth, "EXPECTED_TOKEN", "secret")
    try:
        assert app_client.post("/search", json={"query": "x"}).status_code == 401
        r = app_client.post(
            "/search",
            json={"query": "x"},
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code == 200
    finally:
        monkeypatch.setattr(auth, "EXPECTED_TOKEN", "")
