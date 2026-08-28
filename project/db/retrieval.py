"""L0 retrieval seam.

``RetrievalHit`` is the one typed shape a retrieval result takes as it flows
through the graph (``AgentState.retrieved_contexts``), out to the API edge
(``api.events``) and into eval. It replaces the six ad-hoc encodings that used
to live across ``tools`` / ``nodes`` / ``events`` / ``run_eval``: the
``CHILD_CHUNK_SEPARATOR`` join, the ``"File Name:"`` re-parse, the
``parent::`` / ``search::`` string protocol, and the eval ``(source, content)``
tuple. Carrying manifest metadata (version / effective / expired / priority)
lets version filtering (roadmap M2) land behind this seam without re-shaping
state.

``Retriever`` is the seam itself. Two adapters justify it:
``QdrantRetriever`` (prod, dense+sparse hybrid + parent store) and
``InMemoryRetriever`` (test, faithful two-hop) — "the interface is the test
surface."
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol


@dataclass(frozen=True)
class RetrievalHit:
    source: str
    parent_id: str
    content: str
    score: Optional[float] = None
    version: Optional[str] = None
    effective_date: Optional[date] = None
    expired_date: Optional[date] = None
    priority: Optional[int] = None


class Retriever(Protocol):
    def search(self, query: str, k: int = 7) -> list[RetrievalHit]: ...

    def get_parent(self, parent_id: str) -> Optional[RetrievalHit]: ...


def _to_date(value) -> Optional[date]:
    if isinstance(value, date) or value is None:
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def hit_from_stored(content: str, metadata: dict) -> RetrievalHit:
    """Build a RetrievalHit from a stored chunk/parent payload + its metadata."""
    return RetrievalHit(
        source=str(metadata.get("source", "")),
        parent_id=str(metadata.get("parent_id", "")),
        content=content,
        score=None,
        version=metadata.get("version"),
        effective_date=_to_date(metadata.get("effective_date")),
        expired_date=_to_date(metadata.get("expired_date")),
        priority=metadata.get("priority"),
    )


class QdrantRetriever:
    """Prod adapter over a QdrantVectorStore collection + parent store."""

    def __init__(self, collection, parent_store):
        self._collection = collection
        self._parent_store = parent_store

    def search(self, query: str, k: int = 7) -> list[RetrievalHit]:
        import config

        results = self._collection.similarity_search(
            query, k=k, score_threshold=config.RETRIEVAL_SCORE_THRESHOLD
        )
        return [hit_from_stored(doc.page_content, doc.metadata) for doc in results]

    def get_parent(self, parent_id: str) -> Optional[RetrievalHit]:
        parent = self._parent_store.load_content(parent_id)
        if not parent:
            return None
        return hit_from_stored(parent.get("content", ""), parent.get("metadata", {}))
