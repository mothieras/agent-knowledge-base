import json

import httpx


class AgentClient:
    """Thin client for the Agentic RAG FastAPI service.

    invoke() returns the full response dict; stream() yields parsed SSE event
    dicts (each carrying a ``type`` field) until the terminal ``[DONE]``.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def invoke(self, message: str, thread_id: str | None = None) -> dict:
        payload = {"message": message}
        if thread_id:
            payload["thread_id"] = thread_id
        resp = httpx.post(f"{self.base_url}/invoke", json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()

    def stream(self, message: str, thread_id: str | None = None, stream_tokens: bool = True):
        payload = {"message": message, "stream_tokens": stream_tokens}
        if thread_id:
            payload["thread_id"] = thread_id
        with httpx.stream(
            "POST", f"{self.base_url}/stream", json=payload, timeout=None
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    def health(self) -> dict:
        resp = httpx.get(f"{self.base_url}/health", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def history(self, thread_id: str) -> dict:
        resp = httpx.get(
            f"{self.base_url}/history", params={"thread_id": thread_id}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()
