import json
import uuid

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from schema import (
    AnswerTokenEvent,
    ChatMessage,
    ClarificationEvent,
    DoneEvent,
    ErrorEvent,
    StreamInput,
    SystemStatusEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)

# Node-name sets (ported from core/chat_interface.py). These distinguish
# preprocessing/system nodes from the final synthesis node when routing
# AIMessageChunk content. Adding a new final-answer node (e.g. a refusal
# node) only requires updating FINAL_RESPONSE_NODES here.
SYSTEM_NODES = {"summarize_history", "rewrite_query"}
FINAL_RESPONSE_NODES = {"aggregate_answers"}

TOOL_RESULT_PREVIEW = 300


def _sse(event) -> str:
    return f"data: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"


def _extract_sources_contexts(state_values) -> tuple[list[str], list[str]]:
    contexts: list[str] = []
    seen: set[str] = set()
    for ans in state_values.get("agent_answers", []):
        for ctx in ans.get("contexts", []):
            if ctx not in seen:
                seen.add(ctx)
                contexts.append(ctx)

    sources: list[str] = []
    src_seen: set[str] = set()
    for ctx in contexts:
        for line in str(ctx).splitlines():
            line = line.strip()
            if line.startswith("File Name:"):
                name = line[len("File Name:"):].strip()
                if name and name not in src_seen:
                    src_seen.add(name)
                    sources.append(name)
    return sources, contexts


def _get_clarification(messages) -> str:
    for msg in reversed(messages):
        if getattr(msg, "name", None) == "clarification":
            return str(getattr(msg, "content", ""))
    return ""


def _get_answer(messages, interrupted: bool) -> str:
    if interrupted or not messages:
        return ""
    return str(getattr(messages[-1], "content", ""))


def collect_result(state_values, interrupted: bool) -> dict:
    sources, contexts = _extract_sources_contexts(state_values)
    return {
        "answer": _get_answer(state_values.get("messages", []), interrupted),
        "sources": sources,
        "contexts": contexts,
        "rewritten_questions": state_values.get("rewrittenQuestions", []),
        "interrupted": interrupted,
    }


def to_chat_message(msg) -> ChatMessage:
    if isinstance(msg, HumanMessage):
        mtype = "human"
    elif isinstance(msg, ToolMessage):
        mtype = "tool"
    elif isinstance(msg, AIMessage):
        mtype = "ai"
    else:
        mtype = "custom"
    tool_calls = [
        ToolCall(name=tc["name"], args=tc.get("args", {}), id=tc.get("id"))
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]
    return ChatMessage(
        type=mtype,
        content=str(getattr(msg, "content", "")),
        tool_calls=tool_calls,
        tool_call_id=getattr(msg, "tool_call_id", None),
    )


async def message_generator(rag_system, user_input: StreamInput):
    thread_id = user_input.thread_id or str(uuid.uuid4())
    config = rag_system.get_config(thread_id=thread_id)
    graph = rag_system.agent_graph

    state = await graph.aget_state(config)
    if state.next:
        graph.update_state(config, {"messages": [HumanMessage(content=user_input.message)]})
        stream_input = None
    else:
        stream_input = {"messages": [HumanMessage(content=user_input.message)]}

    system_node_buffer: dict[str, str] = {}

    try:
        async for chunk, metadata in graph.astream(stream_input, config=config, stream_mode="messages"):
            node = metadata.get("langgraph_node", "")

            if node in SYSTEM_NODES and isinstance(chunk, AIMessageChunk) and chunk.content:
                system_node_buffer[node] = system_node_buffer.get(node, "") + chunk.content
                yield _sse(SystemStatusEvent(node=node, data={"content": system_node_buffer[node]}))

            elif getattr(chunk, "tool_calls", None):
                for tc in chunk.tool_calls:
                    yield _sse(ToolCallEvent(name=tc["name"], id=tc.get("id"), args=tc.get("args", {})))

            elif isinstance(chunk, ToolMessage):
                preview = str(chunk.content)[:TOOL_RESULT_PREVIEW]
                yield _sse(ToolResultEvent(id=chunk.tool_call_id, preview=preview))

            elif isinstance(chunk, AIMessageChunk) and chunk.content and node in FINAL_RESPONSE_NODES:
                if user_input.stream_tokens:
                    yield _sse(AnswerTokenEvent(content=chunk.content))

        final_state = await graph.aget_state(config)
        values = final_state.values or {}
        interrupted = bool(final_state.next)

        if interrupted:
            clarification = _get_clarification(values.get("messages", []))
            if clarification:
                yield _sse(ClarificationEvent(question=clarification))

        result = collect_result(values, interrupted)
        yield _sse(DoneEvent(**result))

    except Exception as e:
        yield _sse(ErrorEvent(content=str(e)))
    finally:
        yield "data: [DONE]\n\n"
