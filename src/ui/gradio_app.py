import os

import gradio as gr

from client import AgentClient

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

DECISION_LABELS = {
    "answered": "✅ 已回答",
    "clarification_required": "❓ 需要澄清",
    "refused": "🚫 依据不足，已拒答",
}

TOOL_TITLES = {
    "search_child_chunks": "🔍 检索子块",
    "retrieve_parent_chunks": "📄 回查父块",
}


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


def _render_done(result: dict) -> str:
    decision = result.get("decision", "")
    lines = [DECISION_LABELS.get(decision, decision)]

    if decision == "clarification_required" and result.get("clarification_question"):
        lines.append(f"**{result['clarification_question']}**")
    if result.get("answer"):
        lines.append(result["answer"])
    if result.get("limitations"):
        lines.append("\n**缺口/限制**\n" + "\n".join(f"- {x}" for x in result["limitations"]))
    if result.get("citations"):
        lines.append("\n**引用**")
        for c in result["citations"]:
            quote = c.get("quote", "")
            lines.append(f"- `{c.get('source')}`：{quote}")
    return "\n\n".join(lines)


def create_gradio_ui():
    client = AgentClient(API_URL)

    def chat_handler(msg, _hist, mode):
        response_messages = []
        saw_done = False
        try:
            for ev in client.stream(msg, mode=mode):
                etype = ev.get("type")
                if etype == "status":
                    stage = ev.get("stage", "")
                    _upsert(response_messages, "status",
                            "⏳ " + {"started": "开始处理…"}.get(stage, stage),
                            title="Status")
                elif etype == "tool_call":
                    key = f"tool:{ev.get('id') or ev.get('name')}"
                    name = ev.get("name", "")
                    _upsert(
                        response_messages, key,
                        f"Running `{name}`...",
                        title=TOOL_TITLES.get(name, f"🛠️ {name}"),
                    )
                elif etype == "tool_result":
                    key = f"tool:{ev.get('id')}"
                    idx = _find_idx(response_messages, key)
                    if idx is not None:
                        preview = ev.get("preview", "")
                        response_messages[idx]["content"] = f"```\n{preview}\n```"
                elif etype == "done":
                    saw_done = True
                    _upsert(response_messages, "answer", _render_done(ev.get("result", {})),
                            title="Answer")
                elif etype == "error":
                    saw_done = True
                    _upsert(response_messages, "error",
                            f"❌ [{ev.get('code')}] {ev.get('message', '')}")
                yield response_messages
        except Exception as e:
            if not saw_done:
                response_messages.append(_make_message(f"❌ Error: {e}"))
            yield response_messages

    with gr.Blocks(title="Agentic RAG") as demo:
        with gr.Tab("Chat"):
            chatbot = gr.Chatbot(
                height=720,
                placeholder=(
                    "<strong>单次问答演示</strong><br>"
                    "<em>rag：固定检索单图；agentic：双图 Agent。每次请求独立，不保留历史。</em>"
                ),
                show_label=False,
                avatar_images=(None, os.path.join(ASSETS_DIR, "chatbot_avatar.png")),
                layout="bubble",
            )
            with gr.Row():
                mode = gr.Radio(["rag", "agentic"], value="rag", label="模式", interactive=True)
            gr.ChatInterface(fn=chat_handler, chatbot=chatbot, additional_inputs=[mode])
        with gr.Tab("About"):
            gr.Markdown(
                "### Agentic RAG Service\n\n"
                f"API endpoint: `{API_URL}`\n\n"
                "Service endpoints: `/health` `/info` `/search` `/evidence/{id}` `/invoke` `/stream`\n\n"
                "单次请求语义：`mode` 选择 `rag`/`agentic`；答案携带 decision 与可回查引用。"
            )

    return demo
