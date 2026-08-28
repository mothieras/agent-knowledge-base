"""In-memory Retriever adapter for the retrieval-path test.

Faithful two-hop: it owns real parent and child hits and ``get_parent`` actually
resolves a child's ``parent_id`` to its parent. "Two adapters justify the
seam" — this is the test adapter; ``QdrantRetriever`` is the prod adapter.
"""

from db.retrieval import RetrievalHit


class InMemoryRetriever:
    def __init__(self, parents: list[RetrievalHit], children: list[RetrievalHit]):
        self._parents = {p.parent_id: p for p in parents}
        self._children = list(children)

    def search(self, query: str, k: int = 7) -> list[RetrievalHit]:
        q = (query or "").lower()
        hits = [c for c in self._children if q and q in c.content.lower()]
        return hits[:k]

    def get_parent(self, parent_id: str) -> RetrievalHit | None:
        return self._parents.get(parent_id)
