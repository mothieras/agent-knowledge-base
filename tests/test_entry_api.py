"""T2 条目 HTTP 路由测试（PHASE2 §5.1/§5.2 验证口径）。

错误码逐个覆盖（invalid_request / invalid_project / not_found /
revision_conflict / idempotency_conflict / entry_deleted /
invalid_transition / busy）与修订字段语义的 HTTP 通道。
"""
import asyncio

import core.app_service as app_service_module
import pytest

from conftest import make_app_service

CREATE_PAYLOAD = {
    "type": "memory",
    "body": "项目 A 使用 Java 21。",
    "scope": {"kind": "global"},
    "author": "pi/0.85.1",
}


def create_entry(client, **overrides):
    payload = {**CREATE_PAYLOAD, **overrides}
    return client.post("/entries", json=payload)


def test_health_includes_entries_readiness(app_client):
    body = app_client.get("/health").json()
    assert body["status"] == "ready"
    assert body["entries"] == "ready"


def test_entries_unavailable_without_store(client):
    import api.main as m

    svc = make_app_service(generation=False)
    svc.entries = None
    m.app.state.app_service = svc
    try:
        r = client.post("/entries", json=CREATE_PAYLOAD)
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "entry_store_unavailable"
    finally:
        m.app.state.app_service = None


def test_create_and_get_roundtrip(app_client):
    r = create_entry(app_client)
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("entry_")
    assert body["revision"] == 1 and body["status"] == "active"
    assert body["scope"] == {"kind": "global", "projects": []}
    assert body["expires_at"] is None and body["expired"] is False

    got = app_client.get(f"/entries/{body['id']}")
    assert got.status_code == 200
    assert got.json() == body


def test_create_invalid_request(app_client):
    for overrides in ({"type": "note"}, {"body": ""}, {"author": ""},
                      {"body": "a" * (64 * 1024 + 1)}, {"bogus": 1},
                      {"scope": {"kind": "projects", "projects": []}}):
        r = create_entry(app_client, **overrides)
        assert r.status_code == 400, overrides
        assert r.json()["detail"]["code"] == "invalid_request", overrides


def test_create_invalid_project(app_client):
    r = create_entry(app_client, scope={"kind": "projects", "projects": ["A B"]})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_project"


def test_create_normalizes_project_tags(app_client):
    r = create_entry(app_client, scope={"kind": "projects", "projects": [" Proj-A "]})
    assert r.json()["scope"]["projects"] == ["proj-a"]


def test_create_not_found_read(app_client):
    assert app_client.get("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA").status_code == 404
    r = app_client.get("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA")
    assert r.json()["detail"]["code"] == "not_found"


def test_idempotent_create_replay(app_client):
    first = create_entry(app_client, idempotency_key="op-1").json()
    second = create_entry(app_client, idempotency_key="op-1")
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]


def test_idempotency_conflict(app_client):
    create_entry(app_client, idempotency_key="k")
    r = create_entry(app_client, body="不同内容", idempotency_key="k")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "idempotency_conflict"


def test_revisions_and_snapshot(app_client):
    entry = create_entry(app_client, body="v1").json()
    app_client.post(f"/entries/{entry['id']}/revisions",
                    json={"expected_revision": 1, "author": "cc/1.0", "body": "v2"})

    history = app_client.get(f"/entries/{entry['id']}/revisions")
    assert history.status_code == 200
    assert [(h["revision"], h["op"]) for h in history.json()] == [
        (1, "create"), (2, "update"),
    ]

    snap = app_client.get(f"/entries/{entry['id']}/revisions/1")
    assert snap.status_code == 200
    assert snap.json()["body"] == "v1"
    assert app_client.get(f"/entries/{entry['id']}/revisions/9").status_code == 404
    assert app_client.get("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA/revisions").status_code == 404


def test_revise_field_presence_semantics(app_client):
    entry = create_entry(app_client, expires_at="2027-01-01T00:00:00Z",
                         source={"note": "来源"}).json()
    # 缺省 = 不变
    r = app_client.post(f"/entries/{entry['id']}/revisions",
                        json={"expected_revision": 1, "author": "a", "body": "v2"})
    assert r.json()["expires_at"] == entry["expires_at"]
    assert r.json()["source"]["note"] == "来源"
    # null = 清除
    r = app_client.post(f"/entries/{entry['id']}/revisions",
                        json={"expected_revision": 2, "author": "a",
                              "expires_at": None, "source": None})
    assert r.status_code == 200
    assert r.json()["expires_at"] is None
    assert r.json()["source"] is None


def test_revise_conflict_carries_current(app_client):
    entry = create_entry(app_client).json()
    app_client.post(f"/entries/{entry['id']}/revisions",
                    json={"expected_revision": 1, "author": "a", "body": "v2"})
    r = app_client.post(f"/entries/{entry['id']}/revisions",
                        json={"expected_revision": 1, "author": "b", "body": "v3"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["current"]["revision"] == 2
    assert detail["current"]["body"] == "v2"


def test_revise_deleted(app_client):
    entry = create_entry(app_client).json()
    app_client.post(f"/entries/{entry['id']}/lifecycle",
                    json={"op": "delete", "expected_revision": 1, "author": "a"})
    r = app_client.post(f"/entries/{entry['id']}/revisions",
                        json={"expected_revision": 2, "author": "a", "body": "x"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entry_deleted"


def test_lifecycle_and_invalid_transition(app_client):
    entry = create_entry(app_client).json()
    r = app_client.post(f"/entries/{entry['id']}/lifecycle",
                        json={"op": "archive", "expected_revision": 1, "author": "a"})
    assert r.status_code == 200 and r.json()["status"] == "archived"

    r = app_client.post(f"/entries/{entry['id']}/lifecycle",
                        json={"op": "archive", "expected_revision": 2, "author": "a"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "invalid_transition"

    r = app_client.post(f"/entries/{entry['id']}/lifecycle",
                        json={"op": "restore", "expected_revision": 2, "author": "a"})
    assert r.status_code == 409  # archived 只允许 unarchive/delete

    r = app_client.post(f"/entries/{entry['id']}/lifecycle",
                        json={"op": "bogus", "expected_revision": 2, "author": "a"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_request"


def test_lifecycle_not_found(app_client):
    r = app_client.post("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA/lifecycle",
                        json={"op": "archive", "expected_revision": 1, "author": "a"})
    assert r.status_code == 404


def test_write_busy(monkeypatch, app_client):
    monkeypatch.setattr(app_service_module, "SLOT_TIMEOUT_S", 0.05)
    svc = app_client.app.state.app_service
    svc._slots = asyncio.Semaphore(0)  # 槽位恒不可得：busy 不排队
    r = create_entry(app_client)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "busy"


@pytest.mark.parametrize("path,payload", [
    ("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA/revisions",
     {"expected_revision": 1, "author": "a", "body": "x"}),
    ("/entries/entry_0AAAAAAAAAAAAAAAAAAAAAAA/lifecycle",
     {"op": "archive", "expected_revision": 1, "author": "a"}),
])
def test_write_not_found(app_client, path, payload):
    assert app_client.post(path, json=payload).status_code == 404


def test_search_scope_and_filters(app_client):
    global_id = create_entry(app_client, body="全局的 SQLite WAL 并发条目").json()["id"]
    create_entry(app_client, body="项目的 SQLite WAL 并发条目",
                 scope={"kind": "projects", "projects": ["proj"]})

    r = app_client.post("/entries/search", json={"query": "WAL 并发"})
    assert r.status_code == 200
    body = r.json()
    assert body["returned_k"] == 1 and body["results"][0]["id"] == global_id

    r = app_client.post("/entries/search", json={"query": "WAL 并发", "projects": ["proj"]})
    assert r.json()["returned_k"] == 2

    r = app_client.post("/entries/search", json={"query": "WAL 并发", "all_projects": True})
    assert r.json()["returned_k"] == 2

    for bad in ({"query": ""}, {"query": "x", "limit": 0}, {"query": "x", "type": "note"},
                {"query": "x", "projects": []}, {"query": "x", "projects": ["a"], "all_projects": True}):
        resp = app_client.post("/entries/search", json=bad)
        assert resp.status_code == 400, bad
        assert resp.json()["detail"]["code"] == "invalid_request"


def test_search_excludes_archived_and_expired(app_client):
    entry = create_entry(app_client, body="唯一的 ephemeral 条目",
                         expires_at="2027-01-01T00:00:00Z").json()
    assert app_client.post("/entries/search", json={"query": "ephemeral"}).json()["returned_k"] == 1
    app_client.post(f"/entries/{entry['id']}/lifecycle",
                    json={"op": "archive", "expected_revision": 1, "author": "a"})
    assert app_client.post("/entries/search", json={"query": "ephemeral"}).json()["returned_k"] == 0
