from schema import (
    AnswerRequest,
    AnswerResponse,
    Citation,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    Span,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
)


def test_answer_request_defaults():
    req = AnswerRequest(message="hi")
    assert req.mode == "rag"
    assert req.include_debug_artifact is False


def test_answer_request_rejects_extra_fields():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnswerRequest(message="hi", thread_id="t")


def test_answer_request_mode_validation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnswerRequest(message="hi", mode="chat")


def test_answer_response_minimal():
    r = AnswerResponse(
        request_id="r1",
        index_id="sha256:" + "a" * 64,
        mode="rag",
        decision="refused",
        limitations=["x"],
    )
    assert r.answer == ""
    assert r.citations == []
    assert r.usage is None


def test_citation_model():
    c = Citation(
        citation_id="c1",
        evidence_id="abc12345:doc_p0:0:10",
        source="src.md",
        span=Span(start=0, end=10),
        quote="检索增强",
    )
    assert c.span.start == 0
    assert c.span.end == 10


def test_sse_event_discriminators():
    cases = [
        (StatusEvent(stage="started"), "status"),
        (ToolCallEvent(name="t"), "tool_call"),
        (ToolResultEvent(id="i", preview="p"), "tool_result"),
        (ErrorEvent(code="upstream_failed", message="e"), "error"),
    ]
    for event, expected_type in cases:
        dumped = event.model_dump()
        assert dumped["type"] == expected_type


def test_done_event_carries_result():
    result = AnswerResponse(
        request_id="r1",
        index_id="sha256:" + "b" * 64,
        mode="rag",
        decision="answered",
        answer="a",
        usage=Usage(input_tokens=1, output_tokens=2, model_calls=1, estimated_cost_cny=0.0),
    )
    d = DoneEvent(result=result)
    assert d.model_dump()["type"] == "done"
    assert d.result.decision == "answered"
    assert d.result.usage.input_tokens == 1
