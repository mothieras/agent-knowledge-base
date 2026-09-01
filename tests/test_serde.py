from datetime import date

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from db.retrieval import RetrievalHit


def _sample_hits():
    return [
        RetrievalHit(
            source="redis.md",
            parent_id="p0",
            content="cache eviction policy",
            score=None,
            version="04b8ea2",
            effective_date=date(2026, 7, 19),
            expired_date=None,
            priority=10,
            chunk_id="p0_c0",
            span_start=42,
            span_end=64,
            retrieval_channel="hybrid",
        ),
        RetrievalHit(source="qdrant.md", parent_id="p1", content="hnsw index"),
    ]


def test_retrievalhit_round_trips_typed_through_serde():
    serde = JsonPlusSerializer(allowed_msgpack_modules=[RetrievalHit])
    hits = _sample_hits()

    restored = serde.loads_typed(serde.dumps_typed(hits))

    assert len(restored) == 2
    assert all(isinstance(h, RetrievalHit) for h in restored)
    assert restored[0] == hits[0]
    assert restored[0].effective_date == date(2026, 7, 19)
    assert restored[0].version == "04b8ea2"
    assert restored[0].chunk_id == "p0_c0"
    assert (restored[0].span_start, restored[0].span_end) == (42, 64)
    assert restored[0].retrieval_channel == "hybrid"
    assert restored[1] == hits[1]


def test_serde_blocks_retrievalhit_without_allowlist():
    # allowed_msgpack_modules=None => only built-in SAFE types pass; an
    # unregistered type degrades to its raw kwargs dict (strict-mode behavior).
    blocking_serde = JsonPlusSerializer(allowed_msgpack_modules=None)
    hits = _sample_hits()

    restored = blocking_serde.loads_typed(blocking_serde.dumps_typed(hits))

    assert restored and not isinstance(restored[0], RetrievalHit)
