"""L0 retrieval seam.

``RetrievalHit`` is the one typed shape a retrieval result takes as it flows
through the graph (``AgentState.retrieved_contexts``), out to the API edge
(``api.events``) and into eval. It replaces the six ad-hoc encodings that used
to live across ``tools`` / ``nodes`` / ``events`` / ``run_eval``: the
``CHILD_CHUNK_SEPARATOR`` join, the ``"File Name:"`` re-parse, the
``parent::`` / ``search::`` string protocol, and the eval ``(source, content)``
tuple. Carrying manifest metadata (version / effective / expired / priority)
plus chunk identity (``chunk_id``, ``span_start``/``span_end`` into the parent
text) lets version filtering and citation upgrades land behind this
seam without re-shaping state.

``Retriever`` is the seam itself. Two adapters justify it:
``QdrantRetriever`` (prod, dense+sparse hybrid + parent store) and
``InMemoryRetriever`` (test, faithful two-hop) — "the interface is the test
surface."
"""

from dataclasses import dataclass, replace
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
    chunk_id: Optional[str] = None
    # 扁平 int 而非嵌套 span 对象：msgpack 往返安全；API 边缘再组装 {start, end}
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    retrieval_channel: Optional[str] = None


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


def _to_int(value) -> Optional[int]:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def hit_from_stored(content: str, metadata: dict) -> RetrievalHit:
    """Build a RetrievalHit from a stored chunk/parent payload + its metadata."""
    start = _to_int(metadata.get("start_index"))
    return RetrievalHit(
        source=str(metadata.get("source", "")),
        parent_id=str(metadata.get("parent_id", "")),
        content=content,
        score=None,
        version=metadata.get("version"),
        effective_date=_to_date(metadata.get("effective_date")),
        expired_date=_to_date(metadata.get("expired_date")),
        priority=_to_int(metadata.get("priority")),
        chunk_id=metadata.get("chunk_id"),
        span_start=start,
        # add_start_index 用 find 定位切片起点，故 span 精确覆盖 content
        span_end=start + len(content) if start is not None else None,
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
        # 单次融合调用拿不到 per-channel 归因；真实归因留待检索消融，先如实标 hybrid
        return [
            replace(hit_from_stored(doc.page_content, doc.metadata), retrieval_channel="hybrid")
            for doc in results
        ]

    def get_parent(self, parent_id: str) -> Optional[RetrievalHit]:
        parent = self._parent_store.load_content(parent_id)
        if not parent:
            return None
        hit = hit_from_stored(parent.get("content", ""), parent.get("metadata", {}))
        return replace(
            hit,
            chunk_id=hit.parent_id,
            span_start=0,
            span_end=len(hit.content),
            retrieval_channel="parent_store",
        )
