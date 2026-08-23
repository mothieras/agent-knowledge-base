from eval.metrics import retrieval_metrics


def test_retrieval_metrics_exclude_hitl_clarification_items():
    items = [
        {
            "id": 1,
            "category": "exact_term",
            "expected_sources": ["source.md"],
        },
        {
            "id": 2,
            "category": "ambiguous_followup",
            "expected_sources": ["source.md"],
        },
    ]
    hits = {1: [("source.md", "content")], 2: []}

    result = retrieval_metrics(items, hits)

    assert result["scored_items"] == 1
    assert result["recall@5"] == 1.0
    assert result["mrr"] == 1.0
