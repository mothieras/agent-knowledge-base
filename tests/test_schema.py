from schema import (
    AnswerTokenEvent,
    ChatMessage,
    DoneEvent,
    ErrorEvent,
    StreamInput,
    SystemStatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserInput,
    ClarificationEvent,
)


def test_user_input_defaults():
    u = UserInput(message="hi")
    assert u.thread_id is None


def test_stream_input_defaults():
    s = StreamInput(message="hi")
    assert s.stream_tokens is True


def test_chat_message_types():
    assert ChatMessage(content="x").type == "ai"


def test_sse_event_discriminators():
    cases = [
        (AnswerTokenEvent(content="x"), "answer_token"),
        (ToolCallEvent(name="t"), "tool_call"),
        (ToolResultEvent(id="i", preview="p"), "tool_result"),
        (SystemStatusEvent(node="n"), "system_status"),
        (ClarificationEvent(question="q"), "clarification"),
        (DoneEvent(answer="a"), "done"),
        (ErrorEvent(content="e"), "error"),
    ]
    for event, expected_type in cases:
        dumped = event.model_dump()
        assert dumped["type"] == expected_type


def test_done_event_fields():
    d = DoneEvent(answer="a", sources=["s1", "s2"], rewritten_questions=["q1"], interrupted=True)
    assert d.sources == ["s1", "s2"]
    assert d.interrupted is True
