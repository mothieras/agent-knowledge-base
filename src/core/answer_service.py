"""L2 问答服务（DESIGN 2/7）：单次运行、mode 选择、共享预算、最终结果与事件。

- 两种图共用一套模型配置（不按图要求不同 API key）
- 每请求独立 RequestBudget，经 configurable 传入图；跨请求无共享状态
- 引用校验失败 = ResultValidationError → result_validation_failed（502），
  不伪造引用、不冒充正常拒答
- stream 先跑完整图（校验后），只发状态/工具事件，最终答案随 done 一次发布
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage

import config
from core.retrieval_service import ServiceError, to_evidence_hit
from rag_agent.budget import RequestBudget
from rag_agent.graph import create_agent_graph
from rag_agent.rag_graph import create_rag_graph
from rag_agent.tools import ToolFactory
from rag_agent.validation import ResultValidationError
from schema.dto import (
    AnswerRequest,
    AnswerResponse,
    Citation,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
)

TOOL_RESULT_PREVIEW = 300


class _UsageCollector(BaseCallbackHandler):
    """采集本次请求的 LLM 用量与工具调用次数（与 eval 的 RunCollector 同口径）。"""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.model_calls = 0
        self.tool_calls = 0

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.model_calls += 1

    def on_llm_end(self, response, **kwargs):
        try:
            for gen_list in response.generations:
                for gen in gen_list:
                    usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                    self.input_tokens += usage.get("input_tokens", 0) or 0
                    self.output_tokens += usage.get("output_tokens", 0) or 0
        except Exception:
            pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls += 1


def _usage_dto(collector: _UsageCollector) -> Usage:
    cost = (collector.input_tokens / 1e6 * config.PRICE_IN_PER_M
            + collector.output_tokens / 1e6 * config.PRICE_OUT_PER_M)
    return Usage(
        input_tokens=collector.input_tokens,
        output_tokens=collector.output_tokens,
        model_calls=collector.model_calls,
        estimated_cost_cny=round(cost, 6),
    )


class AnswerService:
    """单次问答：invoke（阻塞，由调用方放线程）/ stream（异步事件流）。"""

    def __init__(self, llm, retriever, evidence_store, source_uris: dict[str, str] | None = None):
        self._llm = llm
        self._evidence_store = evidence_store
        self._source_uris = source_uris or {}
        tools = ToolFactory(retriever).create_tools()
        self._rag_graph = create_rag_graph(llm, retriever, evidence_store)
        self._agentic_graph = create_agent_graph(llm, tools, evidence_store)
        self._langfuse_handler = None
        if config.LANGFUSE_ENABLED:
            from core.observability import Observability

            self._langfuse_handler = Observability().get_handler()

    @property
    def index_id(self) -> str:
        return self._evidence_store.index_id

    def modes(self) -> list[str]:
        return ["rag", "agentic"]

    def metadata(self) -> dict:
        return {"model": config.LLM_MODEL, "base_url": config.LLM_BASE_URL}

    def _graph(self, mode: str):
        if mode == "rag":
            return self._rag_graph
        return self._agentic_graph

    def _config(self, budget: RequestBudget, collector: _UsageCollector) -> dict:
        cfg = {
            "configurable": {"budget": budget},
            "recursion_limit": config.GRAPH_RECURSION_LIMIT,
            "callbacks": [collector],
        }
        if self._langfuse_handler:
            cfg["callbacks"].append(self._langfuse_handler)
        return cfg

    @staticmethod
    def _initial_state(req: AnswerRequest) -> dict:
        return {"messages": [HumanMessage(content=req.message)], "question": req.message}

    def _build_response(self, request_id: str, req: AnswerRequest, final: dict,
                        collector: _UsageCollector) -> AnswerResponse:
        citations = final.get("citations") or []
        parsed = [c if isinstance(c, Citation) else Citation.model_validate(c) for c in citations]
        debug = None
        if req.include_debug_artifact:
            evidence = []
            for evidence_id, hit in (final.get("evidence_map") or {}).items():
                evidence.append(to_evidence_hit(
                    hit, evidence_id, index_id=self.index_id,
                    source_uri=self._source_uris.get(hit.source),
                ))
            debug = {
                "evidence": [h.model_dump() for h in evidence],
                "rewritten_questions": final.get("rewrittenQuestions", []),
            }
        return AnswerResponse(
            request_id=request_id,
            index_id=self.index_id,
            mode=req.mode,
            decision=final.get("decision", ""),
            answer=final.get("answer", ""),
            clarification_question=final.get("clarification_question") or None,
            limitations=final.get("limitations", []),
            citations=parsed,
            usage=_usage_dto(collector),
            debug_artifact=debug,
        )

    def invoke(self, req: AnswerRequest) -> AnswerResponse:
        """阻塞执行单次问答；网络/图异常映射为 ServiceError。"""
        request_id = uuid.uuid4().hex
        budget = RequestBudget()
        collector = _UsageCollector()
        try:
            final = self._graph(req.mode).invoke(
                self._initial_state(req), config=self._config(budget, collector)
            )
        except ResultValidationError as exc:
            raise ServiceError("result_validation_failed", str(exc)) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError("upstream_failed", f"{type(exc).__name__}: {exc}") from exc
        return self._build_response(request_id, req, final, collector)

    async def stream(self, req: AnswerRequest) -> AsyncIterator[str]:
        """SSE 事件流：status/tool_call/tool_result → done（恰好一个终态）。

        最终答案经 validate_result 校验后才随 done 发布；异常时恰好一个 error。
        """
        request_id = uuid.uuid4().hex
        budget = RequestBudget()
        collector = _UsageCollector()
        graph = self._graph(req.mode)

        def _sse(event) -> str:
            return f"data: {event.model_dump_json()}\n\n"

        yield _sse(StatusEvent(stage="started", data={"mode": req.mode, "request_id": request_id}))
        final = None
        try:
            async for chunk in graph.astream(
                self._initial_state(req),
                config=self._config(budget, collector),
                stream_mode=["messages", "values"],
            ):
                mode, payload = chunk
                if mode == "messages":
                    message, _metadata = payload
                    tool_calls = getattr(message, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            yield _sse(ToolCallEvent(
                                name=tc["name"], id=tc.get("id"), args=tc.get("args", {}),
                            ))
                    elif isinstance(message, ToolMessage):
                        yield _sse(ToolResultEvent(
                            id=getattr(message, "tool_call_id", None),
                            preview=str(getattr(message, "content", ""))[:TOOL_RESULT_PREVIEW],
                        ))
                else:
                    final = payload
            if final is None:
                raise ServiceError("upstream_failed", "图执行未产出终态")
            result = self._build_response(request_id, req, final, collector)
            yield _sse(DoneEvent(result=result))
        except ResultValidationError as exc:
            yield _sse(ErrorEvent(code="result_validation_failed", message=str(exc), request_id=request_id))
        except ServiceError as exc:
            yield _sse(ErrorEvent(code=exc.code, message=exc.message, request_id=request_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield _sse(ErrorEvent(code="upstream_failed",
                                  message=f"{type(exc).__name__}: {exc}", request_id=request_id))
        yield "data: [DONE]\n\n"
