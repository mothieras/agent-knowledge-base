import json

import pytest


def test_health_starting(client):
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["detail"] == "starting"


def test_info(client):
    r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "deepseek-chat"
    assert body["collection"] == "document_child_chunks"


@pytest.mark.parametrize("stub_client", [pytest.param("stub_normal", id="normal")], indirect=True)
def test_invoke_normal(stub_client):
    r = stub_client.post("/invoke", json={"message": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Hello world"
    assert body["sources"] == ["redis.md"]
    assert body["rewritten_questions"] == ["q1"]
    assert body["interrupted"] is False


def _parse_sse(text):
    events = []
    for line in text.split("\n"):
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError:
            pass
    return events


@pytest.mark.parametrize("stub_client", [pytest.param("stub_normal", id="normal")], indirect=True)
def test_stream_normal(stub_client):
    r = stub_client.post("/stream", json={"message": "hello", "stream_tokens": True})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types.count("answer_token") == 2
    done = [e for e in events if e["type"] == "done"][0]
    assert done["answer"] == "Hello world"
    assert done["sources"] == ["redis.md"]
    assert done["interrupted"] is False


@pytest.mark.parametrize("stub_client", [pytest.param("stub_clarify", id="clarify")], indirect=True)
def test_stream_clarification(stub_client):
    r = stub_client.post("/stream", json={"message": "ambiguous", "stream_tokens": True})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert "system_status" in types
    assert "clarification" in types
    clar = [e for e in events if e["type"] == "clarification"][0]
    assert "which one" in clar["question"]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["interrupted"] is True
