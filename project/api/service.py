import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

import config
from api.deps import get_rag_system
from api.events import collect_result, message_generator, to_chat_message
from schema import ChatHistory, InvokeResponse, ServiceMetadata, StreamInput, UserInput

router = APIRouter()


def _sse_response_example() -> dict:
    return {
        200: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {\"type\":\"answer_token\",\"content\":\"Hello\"}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@router.get("/health")
async def health(request: Request):
    rag_system = getattr(request.app.state, "rag_system", None)
    if rag_system is not None and rag_system.agent_graph is not None:
        return {"status": "ready"}
    raise HTTPException(status_code=503, detail="starting")


@router.get("/info", response_model=ServiceMetadata)
async def info():
    return ServiceMetadata(
        model=config.LLM_MODEL,
        collection=config.CHILD_COLLECTION,
        default_model=config.LLM_MODEL,
    )


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(user_input: UserInput, rag_system=Depends(get_rag_system)):
    thread_id = user_input.thread_id or str(uuid.uuid4())
    run_config = rag_system.get_config(thread_id=thread_id)
    graph = rag_system.agent_graph

    state = await graph.aget_state(run_config)
    if state.next:
        graph.update_state(run_config, {"messages": [HumanMessage(content=user_input.message)]})
        await graph.ainvoke(None, config=run_config)
    else:
        await graph.ainvoke({"messages": [HumanMessage(content=user_input.message)]}, config=run_config)

    final_state = await graph.aget_state(run_config)
    values = final_state.values or {}
    interrupted = bool(final_state.next)
    return InvokeResponse(**collect_result(values, interrupted))


@router.post("/stream", response_class=StreamingResponse, responses=_sse_response_example())
async def stream(user_input: StreamInput, rag_system=Depends(get_rag_system)):
    return StreamingResponse(
        message_generator(rag_system, user_input),
        media_type="text/event-stream",
    )


@router.get("/history", response_model=ChatHistory)
async def history(thread_id: str, rag_system=Depends(get_rag_system)):
    run_config = rag_system.get_config(thread_id=thread_id)
    state = await rag_system.agent_graph.aget_state(run_config)
    values = state.values or {}
    messages = values.get("messages", [])
    return ChatHistory(messages=[to_chat_message(m) for m in messages])
