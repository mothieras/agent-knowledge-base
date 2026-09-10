"""引用与 decision 机械校验单元测试（DESIGN 7.3 / A-05）。"""
from db.retrieval import RetrievalHit
from rag_agent.schemas import CitationDraft, GenerationOutput
from rag_agent.validation import validate_generation


def _hit(content="检索增强生成（RAG）结合参数化与非参数化知识。", source="src.md",
         parent_id="parent-doc", span_start=0, chunk_id="parent-doc_c0"):
    return RetrievalHit(
        source=source, parent_id=parent_id, content=content,
        span_start=span_start, span_end=span_start + len(content),
        chunk_id=chunk_id, version="v1", score=0.9,
    )


EID = "aaaaaaaa:parent-doc:0:36"
FAKE_EID = "bbbbbbbb:parent-doc:0:36"


def _map():
    return {EID: _hit()}


def test_valid_answered_with_exact_quote():
    gen = GenerationOutput(
        decision="answered",
        answer="RAG 结合参数化与非参数化知识。",
        citations=[CitationDraft(evidence_id=EID, quote="结合参数化与非参数化知识")],
    )
    citations, errors = validate_generation(gen, _map())
    assert errors == []
    assert len(citations) == 1
    c = citations[0]
    assert c.citation_id == "c1"
    assert c.evidence_id == EID
    assert c.source == "src.md"
    # span 推导：quote 在命中内容中的偏移（"检索增强生成（RAG）" 为 10 字符，故 11 起）
    assert c.span.start == 11
    assert c.span.end == 11 + len("结合参数化与非参数化知识")


def test_answered_without_citations_rejected():
    gen = GenerationOutput(decision="answered", answer="some answer", citations=[])
    citations, errors = validate_generation(gen, _map())
    assert citations == []
    assert any("没有任何引用" in e for e in errors)


def test_answered_with_empty_answer_rejected():
    gen = GenerationOutput(decision="answered", answer="", citations=[CitationDraft(evidence_id=EID, quote="x")])
    _, errors = validate_generation(gen, _map())
    assert any("answer 为空" in e for e in errors)


def test_citation_outside_evidence_set_rejected():
    gen = GenerationOutput(
        decision="answered",
        answer="a",
        citations=[CitationDraft(evidence_id=FAKE_EID, quote="结合参数化")],
    )
    _, errors = validate_generation(gen, _map())
    assert any("不在本次证据集合" in e for e in errors)


def test_citation_quote_not_substring_rejected():
    gen = GenerationOutput(
        decision="answered",
        answer="a",
        citations=[CitationDraft(evidence_id=EID, quote="这句话不在原文里")],
    )
    _, errors = validate_generation(gen, _map())
    assert any("不是证据原文" in e for e in errors)


def test_clarification_requires_question_and_drops_citations():
    gen = GenerationOutput(decision="clarification_required", clarification_question="你指的是哪篇文档？",
                           citations=[CitationDraft(evidence_id=EID, quote="x")])
    citations, errors = validate_generation(gen, _map())
    assert errors == []
    assert citations == []  # 非 answered 不发布引用

    gen_bad = GenerationOutput(decision="clarification_required", clarification_question="",
                               citations=[])
    _, errors_bad = validate_generation(gen_bad, _map())
    assert any("缺少澄清问题" in e for e in errors_bad)


def test_refused_is_valid_without_citations():
    gen = GenerationOutput(decision="refused", answer="", citations=[])
    citations, errors = validate_generation(gen, _map())
    assert errors == []
    assert citations == []


def test_refused_with_citations_still_no_public_citations():
    # refused 不带引用：citations 字段即使被模型填了也不发布
    gen = GenerationOutput(decision="refused", answer="", citations=[CitationDraft(evidence_id=EID, quote="x")])
    citations, errors = validate_generation(gen, _map())
    assert errors == []
    assert citations == []
