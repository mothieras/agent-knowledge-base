from typing import Any, Literal

from pydantic import BaseModel, Field


class UserInput(BaseModel):
    """Basic user input for the agent."""

    message: str = Field(description="User input to the agent.")
    thread_id: str | None = Field(
        default=None,
        description="Thread ID to persist and continue a multi-turn conversation.",
    )


class StreamInput(UserInput):
    """User input for streaming the agent's response."""

    stream_tokens: bool = Field(
        default=True,
        description="Whether to stream LLM tokens to the client.",
    )


class ToolCall(BaseModel):
    """Represents a request to call a tool."""

    name: str = Field(description="The name of the tool to be called.")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments to the tool call.")
    id: str | None = Field(default=None, description="Identifier associated with the tool call.")


class ChatMessage(BaseModel):
    """Message in a chat."""

    type: Literal["human", "ai", "tool", "custom"] = Field(
        default="ai", description="Role of the message."
    )
    content: str = Field(default="", description="Content of the message.")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Tool calls in the message.")
    tool_call_id: str | None = Field(
        default=None, description="Tool call that this message is responding to."
    )
    run_id: str | None = Field(default=None, description="Run ID of the message.")


class ChatHistory(BaseModel):
    messages: list[ChatMessage]


class ServiceMetadata(BaseModel):
    """Metadata about the service."""

    model: str = Field(description="LLM model in use.")
    collection: str = Field(description="Qdrant collection name.")
    default_model: str = Field(description="Default model name.")


class InvokeResponse(BaseModel):
    """Response for the non-streaming invoke endpoint."""

    answer: str = Field(default="", description="Final synthesized answer (empty if interrupted).")
    sources: list[str] = Field(
        default_factory=list,
        description="Unique source file names parsed from retrieved contexts.",
    )
    contexts: list[str] = Field(
        default_factory=list,
        description="Deduplicated retrieved context blocks (each contains source + content).",
    )
    rewritten_questions: list[str] = Field(
        default_factory=list, description="Sub-queries the agent researched."
    )
    interrupted: bool = Field(
        default=False, description="True if the graph paused for clarification."
    )


# --- SSE event schemas (discriminated by `type`) ---


class AnswerTokenEvent(BaseModel):
    type: Literal["answer_token"] = "answer_token"
    content: str = Field(description="A streaming token of the final answer.")


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str = Field(description="Name of the tool being called.")
    id: str | None = Field(default=None, description="Tool call identifier.")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments to the tool call.")


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    id: str | None = Field(default=None, description="Tool call identifier this result belongs to.")
    preview: str = Field(default="", description="Short preview of the tool result.")


class SystemStatusEvent(BaseModel):
    type: Literal["system_status"] = "system_status"
    node: str = Field(description="Graph node producing the status.")
    data: dict[str, Any] = Field(default_factory=dict, description="Status payload.")


class ClarificationEvent(BaseModel):
    type: Literal["clarification"] = "clarification"
    question: str = Field(description="Clarification question the agent asks the user.")


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    answer: str = Field(description="Final synthesized answer.")
    sources: list[str] = Field(
        default_factory=list,
        description="Unique source file names parsed from retrieved contexts.",
    )
    contexts: list[str] = Field(
        default_factory=list,
        description="Deduplicated retrieved context blocks (each contains source + content).",
    )
    rewritten_questions: list[str] = Field(
        default_factory=list, description="Sub-queries the agent researched."
    )
    interrupted: bool = Field(
        default=False, description="True if the graph paused for clarification."
    )


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    content: str = Field(description="Error message.")
