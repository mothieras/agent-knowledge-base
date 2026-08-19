import json
import os
import re
import uuid

import gradio as gr

from client import AgentClient

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

SYSTEM_NODE_CONFIG = {
    "rewrite_query": {"title": "🔍 Query Analysis & Rewriting"},
    "summarize_history": {"title": "📋 Chat History Summary"},
}


def _parse_rewrite_json(buffer):
    match = re.search(r"\{.*\}", buffer, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except Exception:
        return None


def _format_rewrite_content(buffer):
    data = _parse_rewrite_json(buffer)
    if not data:
        return "⏳ Analyzing query..."
    if data.get("is_clear"):
        lines = ["✅ **Query is clear**"]
        if data.get("questions"):
            lines += ["\n**Rewritten queries:**"] + [f"- {q}" for q in data["questions"]]
    else:
        lines = ["❓ **Query is unclear**"]
        clarification = data.get("clarification_needed", "")
        if clarification and clarification.strip().lower() != "no":
            lines.append(f"\nClarification needed: *{clarification}*")
    return "\n".join(lines)


def _make_message(content, *, title=None, key=None):
    msg = {"role": "assistant", "content": content}
    meta = {k: v for k, v in {"title": title, "key": key}.items() if v}
    if meta:
        msg["metadata"] = meta
    return msg


def _find_idx(messages, key):
    return next(
        (i for i, m in enumerate(messages) if m.get("metadata", {}).get("key") == key),
        None,
    )


def _upsert(messages, key, content, *, title=None):
    idx = _find_idx(messages, key)
    if idx is None:
        messages.append(_make_message(content, title=title, key=key))
    else:
        messages[idx]["content"] = content


def _append_answer(messages, token):
    last = messages[-1] if messages else None
    if not (last and last.get("role") == "assistant" and "key" not in last.get("metadata", {})):
        messages.append(_make_message(""))
    messages[-1]["content"] += token


def create_gradio_ui():
    client = AgentClient(API_URL)
    session = {"thread_id": str(uuid.uuid4())}

    def chat_handler(msg, _hist):
        response_messages = []
        saw_answer = False
        try:
            for ev in client.stream(msg, thread_id=session["thread_id"]):
                etype = ev.get("type")
                if etype == "system_status":
                    node = ev.get("node", "")
                    content = ev.get("data", {}).get("content", "")
                    rendered = _format_rewrite_content(content) if node == "rewrite_query" else content
                    _upsert(
                        response_messages,
                        node,
                        rendered,
                        title=SYSTEM_NODE_CONFIG.get(node, {}).get("title"),
                    )
                elif etype == "tool_call":
                    key = f"tool:{ev.get('id') or ev.get('name')}"
                    _upsert(
                        response_messages,
                        key,
                        f"Running `{ev.get('name')}`...",
                        title=f"🛠️ {ev.get('name')}",
                    )
                elif etype == "tool_result":
                    key = f"tool:{ev.get('id')}"
                    idx = _find_idx(response_messages, key)
                    if idx is not None:
                        preview = ev.get("preview", "")
                        response_messages[idx]["content"] = f"```\n{preview}\n```"
                elif etype == "answer_token":
                    saw_answer = True
                    _append_answer(response_messages, ev.get("content", ""))
                elif etype == "clarification":
                    _upsert(response_messages, "clarification", ev.get("question", ""))
                elif etype == "done":
                    answer = ev.get("answer", "")
                    if answer and not saw_answer:
                        _append_answer(response_messages, answer)
                    sources = ev.get("sources", [])
                    if sources:
                        _upsert(
                            response_messages,
                            "sources",
                            "📚 " + ", ".join(sources),
                            title="Sources",
                        )
                elif etype == "error":
                    _upsert(response_messages, "error", f"❌ {ev.get('content', '')}")
                yield response_messages
        except Exception as e:
            response_messages.append(_make_message(f"❌ Error: {e}"))
            yield response_messages

    def clear_chat_handler():
        session["thread_id"] = str(uuid.uuid4())

    with gr.Blocks(title="Agentic RAG") as demo:
        with gr.Tab("Chat"):
            chatbot = gr.Chatbot(
                height=720,
                placeholder=(
                    "<strong>Ask me anything!</strong><br>"
                    "<em>I'll search, reason, and act to give you the best answer :)</em>"
                ),
                show_label=False,
                avatar_images=(None, os.path.join(ASSETS_DIR, "chatbot_avatar.png")),
                layout="bubble",
            )
            chatbot.clear(clear_chat_handler)
            gr.ChatInterface(fn=chat_handler, chatbot=chatbot)
        with gr.Tab("About"):
            gr.Markdown(
                "### Agentic RAG Service\n\n"
                f"API endpoint: `{API_URL}`\n\n"
                "Ingest documents via the CLI: `python ingest_corpus.py` (manifest-driven).\n\n"
                "Service endpoints: `/health` `/info` `/invoke` `/stream` `/history`"
            )

    return demo
