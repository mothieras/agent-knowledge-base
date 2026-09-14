"""T1 Level A：ingest 样例集的无模型管道验证（PHASE1 §4 门槛 2–3，进 CI）。

正样（S1/S2/S3/S4）：清单预登记锚点经空白归一化后全部出现在规范化产物中；
每个 child 是其 parent 的精确 span 切片；source/doc_meta 元数据传播；
两次独立分块的内容散列一致。PDF 转换写 tmp，不触碰仓库 markdown_docs。

负样（S5/S9/S10）只固定当前实际行为，不断言目标终态——
目标终态断言是 T3 以本文件行为表为"前"的红测试。
"""
import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from document_chunker import DocumentChunker
from utils import pdf_to_markdown

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "ingest_samples"


def _manifest():
    return json.loads((SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _doc(sample):
    for d in _manifest()["documents"]:
        if d["sample"] == sample:
            return d
    raise KeyError(sample)


def _norm(text):
    """空白归一化：PDF 提取的折行与缩进剥离不影响锚点子串判定。"""
    return " ".join(text.split())


def _assert_anchors(anchors, product_text, label):
    norm = _norm(product_text)
    missing = [a for a in anchors if _norm(a) not in norm]
    assert not missing, f"{label} 缺失锚点: {missing}"


def _assert_span_integrity(parents, children):
    parent_by_id = {pid: doc for pid, doc in parents}
    by_parent = {}
    for c in children:
        by_parent.setdefault(c.metadata["parent_id"], []).append(c)

    chunk_ids = [c.metadata["chunk_id"] for c in children]
    assert len(set(chunk_ids)) == len(chunk_ids)

    for pid, group in by_parent.items():
        assert [c.metadata["chunk_id"] for c in group] == [f"{pid}_c{j}" for j in range(len(group))]
        starts = [c.metadata["start_index"] for c in group]
        assert starts == sorted(starts)
        parent = parent_by_id[pid]
        for c in group:
            start = c.metadata["start_index"]
            assert parent.page_content[start:start + len(c.page_content)] == c.page_content


def _pipeline_digest(parents, children):
    h = hashlib.sha256()
    for pid, doc in parents:
        h.update(pid.encode("utf-8"))
        h.update(b"\x00")
        h.update(doc.page_content.encode("utf-8"))
        h.update(b"\x00")
    for c in children:
        h.update(c.metadata["chunk_id"].encode("utf-8"))
        h.update(b"\x00")
        h.update(c.page_content.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _chunk_md(md_path, source_name, doc_meta=None):
    chunker = DocumentChunker()
    return chunker.create_chunks_single(md_path, source_name=source_name, doc_meta=doc_meta)


def test_manifest_sha256_matches_files():
    """冻结身份守卫：任何样例文件被改动（含 PDF 重生成）都会在此失败。"""
    for d in _manifest()["documents"]:
        actual = hashlib.sha256((SAMPLES_DIR / d["file"]).read_bytes()).hexdigest()
        assert actual == d["sha256"], f"{d['file']} SHA-256 与清单不符"


def test_s1_structure_anchors_span_and_metadata(tmp_path):
    doc = _doc("S1")
    src = SAMPLES_DIR / doc["file"]
    raw = src.read_text(encoding="utf-8")
    _assert_anchors(doc["anchors"], raw, "S1 原文")

    doc_meta = {"version": "s1-v1", "topic": "ingest_samples"}
    parents, children = _chunk_md(src, "samples/s1_structure.md", doc_meta)
    assert parents and children

    _assert_anchors(doc["anchors"], "\n\n".join(p.page_content for _, p in parents), "S1 分块产物")
    _assert_span_integrity(parents, children)

    for c in children:
        assert c.metadata["source"] == "samples/s1_structure.md"
        assert c.metadata["version"] == "s1-v1"
        assert c.metadata["topic"] == "ingest_samples"

    again = _chunk_md(src, "samples/s1_structure.md", doc_meta)
    assert _pipeline_digest(*again) == _pipeline_digest(parents, children)


def test_s2_plain_text_chunks_by_size(tmp_path):
    doc = _doc("S2")
    src = SAMPLES_DIR / doc["file"]
    raw = src.read_text(encoding="utf-8")
    assert len(raw) > 4000  # 超过 MAX_PARENT_SIZE 才会触发大块拆分

    # TXT 规范化 = UTF-8 读入直写（document_manager 的 .txt 分支）
    md = tmp_path / "s2_plain_text.md"
    md.write_text(raw, encoding="utf-8")

    parents, children = _chunk_md(md, "samples/s2_plain_text.txt")
    assert len(parents) >= 2  # 无标题文本只按尺寸切分
    _assert_anchors(doc["anchors"], "\n\n".join(p.page_content for _, p in parents), "S2 分块产物")
    _assert_span_integrity(parents, children)

    again = _chunk_md(md, "samples/s2_plain_text.txt")
    assert _pipeline_digest(*again) == _pipeline_digest(parents, children)


@pytest.mark.parametrize("sample", ["S3", "S4"])
def test_pdf_normalization_preserves_anchors(sample, tmp_path):
    doc = _doc(sample)
    src = SAMPLES_DIR / doc["file"]

    pdf_to_markdown(str(src), tmp_path)
    md = tmp_path / (src.stem + ".md")
    product = md.read_text(encoding="utf-8")
    assert product.strip(), f"{sample} 转换产物为空"
    _assert_anchors(doc["anchors"], product, f"{sample} 规范化产物")

    if sample == "S4":
        # 多页样例：page_separators=True 的页界标记必须在场（跨页切分的证据）
        assert product.count("end of page") >= 2

    parents, children = _chunk_md(md, f"samples/{doc['file']}")
    assert parents and children
    _assert_anchors(doc["anchors"], "\n\n".join(p.page_content for _, p in parents), f"{sample} 分块产物")
    _assert_span_integrity(parents, children)

    again = _chunk_md(md, f"samples/{doc['file']}")
    assert _pipeline_digest(*again) == _pipeline_digest(parents, children)


def test_s5_current_behavior_empty_and_whitespace(tmp_path):
    """现状记录（非目标断言）：空与仅空白都在 header splitter 层返回 0 文档，
    chunker 静默返回 ([], [])；失败判定完全依赖 document_manager 的
    No child chunks 检查，且以通用异常形式呈现而非 empty_text 类别。"""
    empty_md = tmp_path / "s5a_empty.md"
    empty_md.write_text("", encoding="utf-8")
    assert _chunk_md(empty_md, "samples/s5a_empty.md") == ([], [])

    ws_md = tmp_path / "s5b_whitespace.md"
    ws_md.write_text((SAMPLES_DIR / "s5b_whitespace.txt").read_text(encoding="utf-8"), encoding="utf-8")
    assert _chunk_md(ws_md, "samples/s5b_whitespace.txt") == ([], [])


def test_s9_current_behavior_suffix_silently_dropped():
    """现状记录：不支持后缀在解析前被过滤，返回 (0,0)，
    无任何输出或记录——S9 的目标终态 unsupported_format 是 T3 的改造对象。"""

    class _Recording:
        def __init__(self):
            self.calls = 0

        def create_chunks_single(self, *a, **kw):
            self.calls += 1
            return [], []

    chunker = _Recording()
    store = type("S", (), {"save_many": staticmethod(lambda *a: None),
                           "delete_many": staticmethod(lambda *a: None)})()
    vdb = type("V", (), {"get_collection": staticmethod(lambda *a: pytest.fail("不应触达索引"))})()

    from core.document_manager import DocumentManager
    dm = DocumentManager(chunker, store, vdb, "unused")
    added, skipped = dm.add_documents([str(SAMPLES_DIR / "s9_unsupported.docx")])

    assert (added, skipped) == (0, 0)
    assert chunker.calls == 0


def test_s6_current_behavior_no_text_layer(tmp_path):
    """现状记录（T2 双证据）：无文本层 PDF 的提取产物只剩页界标记，
    document_manager 的空文本检查会被它骗过（假成功，见附录 A / Level B）。
    无 OCR 行为证据：本机无 tesseract/pytesseract/rapidocr，pymupdf4llm
    select_ocr_function() 返回 None → OCRMode.NEVER，转换输出无 OCR 痕迹；
    注意默认 use_ocr 并非 NEVER——装入任一 OCR 依赖后图像页会自动启用 OCR。"""
    import pymupdf

    src = SAMPLES_DIR / "s6_no_text_layer.pdf"
    with pymupdf.open(src) as doc:
        assert all(p.get_text().strip() == "" for p in doc)  # 无文本层的直接证据

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        pdf_to_markdown(str(src), tmp_path)
    product = (tmp_path / "s6_no_text_layer.md").read_text(encoding="utf-8")

    # 除页界标记外无任何文本——且该标记正是骗过空文本检查的内容
    stripped = product.replace("--- end of page.page_number=1 ---", "")
    assert stripped.strip() == ""
    assert "OCR" not in out.getvalue() and "OCR" not in err.getvalue()


def test_s10_current_behavior_gbk_raises_unicode_error():
    """现状记录：非 UTF-8 TXT 在 read_text 抛裸 UnicodeDecodeError，
    上层通用 except 把它吞成 print + skipped。"""
    with pytest.raises(UnicodeDecodeError):
        (SAMPLES_DIR / "s10_gbk.txt").read_text(encoding="utf-8")
