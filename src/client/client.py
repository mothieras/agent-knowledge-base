import json

import httpx


class AgentClient:
    """Thin client for the Agentic RAG FastAPI service (protocol v2).

    invoke(message, mode) returns the AnswerResponse dict; stream() yields
    parsed SSE event dicts until the terminal ``done`` / ``error`` event.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def search(self, query: str, k: int = 7) -> dict:
        resp = httpx.post(f"{self.base_url}/search", json={"query": query, "k": k}, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def evidence(self, evidence_id: str, offset: int = 0, limit: int = 4000) -> dict:
        resp = httpx.get(
            f"{self.base_url}/evidence/{evidence_id}",
            params={"offset": offset, "limit": limit},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def invoke(self, message: str, mode: str = "rag", include_debug_artifact: bool = False) -> dict:
        payload = {"message": message, "mode": mode}
        if include_debug_artifact:
            payload["include_debug_artifact"] = True
        resp = httpx.post(f"{self.base_url}/invoke", json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()

    def stream(self, message: str, mode: str = "rag"):
        """Yield parsed SSE event dicts until the terminal ``done`` or ``error``."""
        with httpx.stream(
            "POST", f"{self.base_url}/stream", json={"message": message, "mode": mode},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                yield event
                if event.get("type") in ("done", "error"):
                    break

    def health(self) -> dict:
        resp = httpx.get(f"{self.base_url}/health", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def info(self) -> dict:
        resp = httpx.get(f"{self.base_url}/info", timeout=10)
        resp.raise_for_status()
        return resp.json()
