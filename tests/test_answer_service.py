"""L2 AnswerService 测试：rag/agentic 单次执行、三种 decision、引用修复、
错误路径与跨请求状态隔离（无真实 LLM，stub 结构化输出）。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.answer_service import AnswerService
from core.retrieval_service import ServiceError
from db.evidence_store import EvidenceStore
from db.retrieval import RetrievalHit
from db.snapshot import Snapshot
from rag_agent.schemas import CitationDraft, GenerationOutput
from rag_agent.validation import ResultValidationError
from schema.dto import AnswerRequest
from fixtures.in_memory_retriever import InMemoryRetriever


class _ParentStore:
    def __init__(self, parents):
        self._parents = parents

    def load_content(self, parent_id):
        return self._parents.get(parent_id)


class _StructuredLLM:
    """顺序吐出 GenerationOutput 的 stub；with_structured_output().invoke 返回下一个。"""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def with_structured_output(self, schema, method=None):
        return self

    def invoke(self, messages, **kwargs):
        if not self._outputs:
            raise AssertionError("stub LLM 输出耗尽")
        self.calls += 1
        return self._outputs.pop(0)

    def bind_tools(self, tools):
        return self


BODY = "检索增强生成（RAG）结合参数化与非参数化知识，生成前先检索相关信息。"


def _make_answer_service(outputs, parents=None):
    parent = RetrievalHit(
        source="src.md", parent_id="p0", content=BODY,
        version="v1", chunk_id="p0_c0", span_start=0, span_end=len(BODY),
    )
    hit = RetrievalHit(
        source="src.md", parent_id="p0", content=BODY,
        version="v1", chunk_id="p0_c0", span_start=0, span_end=len(BODY),
    )
    retriever = InMemoryRetriever(parents=[parent], children=[hit])
    snapshot = Snapshot("sha256:" + "a" * 64, {"dense_model": "D", "sparse_model": "S",
                                               "parent_count": 1, "child_count": 1})
    store = EvidenceStore(_ParentStore({"p0": {"content": BODY, "metadata": {"source": "src.md"}}}), snapshot)
    return AnswerService(_StructuredLLM(outputs), retriever, store, source_uris={"src.md": "http://x/src.md"})


def _answered():
    return GenerationOutput(
        decision="answered",
        answer="RAG 结合参数化与非参数化知识。",
        citations=[CitationDraft(evidence_id=_eid(), quote=BODY[:12])],
    )


def _eid():
    return f"aaaaaaaa:p0:0:{len(BODY)}"


def test_rag_answered_with_valid_citation():
    svc = _make_answer_service([_answered()])
    resp = svc.invoke(AnswerRequest(message="RAG", mode="rag"))
    assert resp.decision == "answered"
    assert resp.answer.startswith("RAG")
    assert len(resp.citations) == 1
    c = resp.citations[0]
    assert c.evidence_id == _eid()
    assert c.span.start == 0 and c.span.end == len(BODY[:12])
    # 引用可精确回查：窗口内容 == quote
    window = svc._evidence_store.window(c.evidence_id, c.span.start, c.span.end - c.span.start)
    assert window["content"] == c.quote


def test_rag_refused_on_zero_hits_without_llm_call():
    parent = RetrievalHit(source="src.md", parent_id="p0", content=BODY, span_start=0, span_end=len(BODY))
    retriever = InMemoryRetriever(parents=[parent], children=[])  # 检索无命中
    snapshot = Snapshot("sha256:" + "a" * 64, {"dense_model": "D", "sparse_model": "S"})
    store = EvidenceStore(_ParentStore({"p0": {"content": BODY, "metadata": {"source": "src.md"}}}), snapshot)
    llm = _StructuredLLM([])
    svc = AnswerService(llm, retriever, store)
    resp = svc.invoke(AnswerRequest(message="完全无关的问题", mode="rag"))
    assert resp.decision == "refused"
    assert resp.citations == []
    assert llm.calls == 0  # 确定性无证据分支不调用生成模型


def test_rag_clarification_terminal():
    gen = GenerationOutput(decision="clarification_required",
                           clarification_question="你说的『它』指什么？")
    svc = _make_answer_service([gen])
    resp = svc.invoke(AnswerRequest(message="参数化", mode="rag"))
    assert resp.decision == "clarification_required"
    assert resp.clarification_question == "你说的『它』指什么？"
    assert resp.answer == ""


def test_invalid_citation_repairs_once_then_succeeds():
    bad = GenerationOutput(
        decision="answered", answer="a",
        citations=[CitationDraft(evidence_id="not-in-evidence", quote="xxx")],
    )
    svc = _make_answer_service([bad, _answered()])
    resp = svc.invoke(AnswerRequest(message="RAG", mode="rag"))
    assert resp.decision == "answered"
    assert len(resp.citations) == 1
    assert resp.citations[0].evidence_id == _eid()


def test_invalid_citation_exhausts_repair_budget():
    bad = GenerationOutput(
        decision="answered", answer="a",
        citations=[CitationDraft(evidence_id="not-in-evidence", quote="xxx")],
    )
    svc = _make_answer_service([bad, bad])  # 修复一次后仍失败
    with pytest.raises(ServiceError) as exc:
        svc.invoke(AnswerRequest(message="参数化", mode="rag"))
    assert exc.value.code == "result_validation_failed"


def test_answered_without_citations_repairs():
    no_cite = GenerationOutput(decision="answered", answer="RAG 是检索增强生成。", citations=[])
    svc = _make_answer_service([no_cite, _answered()])
    resp = svc.invoke(AnswerRequest(message="RAG", mode="rag"))
    assert resp.decision == "answered"
    assert resp.citations


def test_upstream_failure_maps_to_service_error():
    class _Boom:
        def with_structured_output(self, schema, method=None):
            return self

        def invoke(self, messages, **kwargs):
            raise RuntimeError("network down")

        def bind_tools(self, tools):
            return self

    parent = RetrievalHit(source="src.md", parent_id="p0", content=BODY, span_start=0, span_end=len(BODY))
    retriever = InMemoryRetriever(parents=[parent], children=[parent])
    snapshot = Snapshot("sha256:" + "a" * 64, {"dense_model": "D", "sparse_model": "S"})
    store = EvidenceStore(_ParentStore({"p0": {"content": BODY, "metadata": {"source": "src.md"}}}), snapshot)
    svc = AnswerService(_Boom(), retriever, store)
    with pytest.raises(ServiceError) as exc:
        svc.invoke(AnswerRequest(message="参数化", mode="rag"))
    assert exc.value.code == "upstream_failed"


def test_cross_request_state_isolation():
    # 两个请求各自独立：第二次请求不受第一次的 generation 影响
    svc = _make_answer_service([_answered(), _answered()])
    r1 = svc.invoke(AnswerRequest(message="RAG", mode="rag"))
    r2 = svc.invoke(AnswerRequest(message="RAG", mode="rag"))
    assert r1.request_id != r2.request_id
    assert r1.decision == "answered" and r2.decision == "answered"


def _run_stream(agen) -> list:
    return asyncio.run(_collect(agen))


def test_stream_emits_status_and_done_only_for_rag():
    svc = _make_answer_service([_answered()])
    lines = _run_stream(svc.stream(AnswerRequest(message="RAG", mode="rag")))
    types = [_parse(line)["type"] for line in lines if line.startswith("data: {")]
    assert types[0] == "status"
    assert types[-1] == "done"
    assert types.count("done") == 1


def test_stream_error_terminal_for_validation_failure():
    bad = GenerationOutput(decision="answered", answer="a",
                           citations=[CitationDraft(evidence_id="nope", quote="x")])
    svc = _make_answer_service([bad, bad])
    lines = _run_stream(svc.stream(AnswerRequest(message="参数化", mode="rag")))
    parsed = [_parse(line) for line in lines if line.startswith("data: {")]
    assert parsed[-1]["type"] == "error"
    assert parsed[-1]["code"] == "result_validation_failed"
    assert sum(1 for p in parsed if p["type"] == "done") == 0


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


def _parse(line):
    import json
    return json.loads(line[len("data: "):])


def test_budget_object_shared_limits():
    from rag_agent.budget import RequestBudget

    b = RequestBudget()
    assert b.reserve_tool_calls(8)
    assert not b.reserve_tool_calls(1)
    assert b.reserve_repair()
    assert not b.reserve_repair()


def test_agentic_clarification_terminal():
    # agentic：rewrite 判不明 → 单次终态 clarification_required（无 interrupt）
    from rag_agent.schemas import QueryAnalysis

    class _DispatchLLM:
        def __init__(self):
            self.schema = None

        def with_structured_output(self, schema, method=None):
            self.schema = schema
            return self

        def invoke(self, messages, **kwargs):
            if self.schema is QueryAnalysis:
                return QueryAnalysis(is_clear=False, questions=[],
                                     clarification_needed="请说明你说的『它』具体指哪篇文档？")
            return GenerationOutput(decision="refused", answer="")

        def bind_tools(self, tools):
            return self

    parent = RetrievalHit(source="src.md", parent_id="p0", content=BODY, span_start=0, span_end=len(BODY))
    retriever = InMemoryRetriever(parents=[parent], children=[])
    snapshot = Snapshot("sha256:" + "a" * 64, {"dense_model": "D", "sparse_model": "S"})
    store = EvidenceStore(_ParentStore({"p0": {"content": BODY, "metadata": {"source": "src.md"}}}), snapshot)
    svc = AnswerService(_DispatchLLM(), retriever, store)
    resp = svc.invoke(AnswerRequest(message="参数化", mode="agentic"))
    assert resp.decision == "clarification_required"
    assert resp.clarification_question == "请说明你说的『它』具体指哪篇文档？"
    assert resp.citations == []
