"""T3 目标终态断言（PHASE1 §4 门槛 4 前半）：入库失败分类与结构化结果。

以 PHASE1-IMPL 附录 A 行为表为"前"（Level A 的 *_current_behavior_* 只记录现状）：
S5/S6/S7/S8/S9/S10 的目标终态在此断言。全部无模型：真实 chunker + 桩 store/vdb，
markdown 输出目录 monkeypatch 到 tmp。成功路径（.md 逐字节复制、PDF slug 落位）
同时断言不被结构化改造破坏。
"""
from pathlib import Path

import config
from document_chunker import DocumentChunker
from core.document_manager import DocumentManager

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "ingest_samples"


def _p(name):
    return str(SAMPLES_DIR / name)


class _StubStore:
    def __init__(self):
        self.saved = []

    def save_many(self, parents):
        self.saved.extend(parents)

    def delete_many(self, ids):
        self.saved = [p for p in self.saved if p[0] not in set(ids)]


class _StubCollection:
    def __init__(self):
        self.added = []

    def add_documents(self, children):
        self.added.extend(children)


class _StubVdb:
    def __init__(self):
        self.collection = _StubCollection()

    def get_collection(self, name):
        return self.collection


def _manager(tmp_path, monkeypatch):
    md_dir = tmp_path / "markdown_docs"
    monkeypatch.setattr(config, "MARKDOWN_DIR", str(md_dir))
    store, vdb = _StubStore(), _StubVdb()
    dm = DocumentManager(DocumentChunker(), store, vdb, "unused")
    return dm, store, vdb, md_dir


def _statuses(results):
    return [r["status"] for r in results]


def _store_text(store):
    return "\n".join(doc.page_content for _, doc in store.saved)


def test_s5_empty_and_whitespace_classified(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    results = dm.add_documents(
        [_p("s5a_empty.md"), _p("s5b_whitespace.txt")],
        source_names={_p("s5a_empty.md"): "samples/s5a_empty.md",
                      _p("s5b_whitespace.txt"): "samples/s5b_whitespace.txt"},
    )
    assert _statuses(results) == ["empty_text", "empty_text"]
    assert all(r["detail"] for r in results)
    assert list(md_dir.glob("*")) == []
    assert store.saved == [] and vdb.collection.added == []


def test_s6_no_text_layer_classified(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    results = dm.add_documents([_p("s6_no_text_layer.pdf")])
    assert _statuses(results) == ["no_text_layer"]
    assert results[0]["detail"]  # 原因可读
    assert list(md_dir.glob("*.md")) == []
    assert store.saved == [] and vdb.collection.added == []


def test_s7_second_submission_duplicate(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    p = _p("s7_duplicate.md")
    names = {p: "samples/s7_duplicate.md"}

    first = dm.add_documents([p], source_names=names)
    n_parents = len(store.saved)
    second = dm.add_documents([p], source_names=names)

    assert _statuses(first) == ["ok"]
    assert _statuses(second) == ["duplicate"]
    assert second[0]["source"] == "samples/s7_duplicate.md"
    assert len(store.saved) == n_parents  # 不重复入库
    assert sorted(md_dir.glob("*.md")) == [md_dir / "samples__s7_duplicate.md.md"]


def test_s8a_same_source_different_content_conflict(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    a, b = _p("s8a_with_source/first.md"), _p("s8a_with_source/second.md")

    first = dm.add_documents([a], source_names={a: "samples/s8a/shared.md"})
    second = dm.add_documents([b], source_names={b: "samples/s8a/shared.md"})

    assert _statuses(first) == ["ok"]
    assert _statuses(second) == ["conflict"]
    text = _store_text(store)
    assert "FIRST-FILE-CONTENT-A" in text and "SECOND-FILE-CONTENT-B" not in text


def test_s8b_same_stem_no_source_conflict(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    left = _p("s8b_no_source/left/shared_note.md")
    right = _p("s8b_no_source/right/shared_note.md")

    first = dm.add_documents([left])
    second = dm.add_documents([right])

    assert _statuses(first) == ["ok"]
    assert _statuses(second) == ["conflict"]
    text = _store_text(store)
    assert "LEFT-NOTE-CONTENT-L" in text and "RIGHT-NOTE-CONTENT-R" not in text


def test_s9_unsupported_format_counted(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    results = dm.add_documents([_p("s9_unsupported.docx")],
                               source_names={_p("s9_unsupported.docx"): "samples/s9_unsupported.docx"})
    assert _statuses(results) == ["unsupported_format"]  # 显式记录而非静默剔除
    assert results[0]["source"] == "samples/s9_unsupported.docx"
    assert list(md_dir.glob("*")) == []
    assert store.saved == [] and vdb.collection.added == []


def test_s10_gbk_unsupported_encoding(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    results = dm.add_documents([_p("s10_gbk.txt")],
                               source_names={_p("s10_gbk.txt"): "samples/s10_gbk.txt"})
    assert _statuses(results) == ["unsupported_encoding"]
    assert "utf-8" in results[0]["detail"]  # 原因可读
    assert list(md_dir.glob("*.md")) == []
    assert store.saved == [] and vdb.collection.added == []


def test_pdf_with_source_name_lands_at_slug_path(tmp_path, monkeypatch):
    """缺陷①（附录 A 尾注）：source_names 的 slug 与转换器 stem 输出不一致 → 修复后
    PDF 带标识提交应成功且产物落在 slug 路径，无 stem 残留。"""
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    p = _p("s3_pdf_text_layer.pdf")
    results = dm.add_documents([p], source_names={p: "samples/s3_pdf_text_layer.pdf"})

    assert _statuses(results) == ["ok"]
    assert sorted(md_dir.glob("*.md")) == [md_dir / "samples__s3_pdf_text_layer.pdf.md"]
    for _, doc in store.saved:
        assert doc.metadata["source"] == "samples/s3_pdf_text_layer.pdf"
    assert vdb.collection.added


def test_md_success_path_byte_identical_copy(tmp_path, monkeypatch):
    dm, store, vdb, md_dir = _manager(tmp_path, monkeypatch)
    p = _p("s1_structure.md")
    results = dm.add_documents([p], source_names={p: "samples/s1_structure.md"})

    assert _statuses(results) == ["ok"]
    md = md_dir / "samples__s1_structure.md.md"
    assert md.read_bytes() == Path(p).read_bytes()  # 复制语义逐字节不变


def test_pdf_entries_carry_explicit_missing_page_locator(tmp_path):
    """T4/G5：PDF 条目原文件页码定位显式标 missing（页界标记→页码映射需改
    chunker 核心合并/拆分逻辑，超出阶段 1 边界，见 PHASE1-IMPL §5）。"""
    chunker = DocumentChunker()
    for pdf in ["s3_pdf_text_layer.pdf", "s4_pdf_multipage.pdf"]:
        # PDF 产物先行转换到 tmp（create_chunks_single 吃规范化 md）
        from utils import pdf_to_markdown
        pdf_to_markdown(_p(pdf), tmp_path)
        md = tmp_path / (Path(pdf).stem + ".md")
        parents, children = chunker.create_chunks_single(md)
        assert parents and children
        for _, p in parents:
            assert p.metadata.get("origin_page") == "missing", f"{pdf} parent 缺 locator 标注"
        for c in children:
            assert c.metadata.get("origin_page") == "missing", f"{pdf} child 缺 locator 标注"

    # 非 PDF 条目不带该字段（原文件即规范化文件，页码概念不适用）
    parents, children = chunker.create_chunks_single(SAMPLES_DIR / "s1_structure.md",
                                                     source_name="s1_structure.md")
    for _, p in parents:
        assert "origin_page" not in p.metadata
    for c in children:
        assert "origin_page" not in c.metadata
