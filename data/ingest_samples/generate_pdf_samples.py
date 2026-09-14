"""ingest 样例集 PDF 生成脚本（来源记录，不重复执行）。

生成 S3（文本层：标题层级/代码块/表格）与 S4（多页：跨页段落与跨页表格）。
固定 metadata 日期保证可复现；生成产物登记 SHA-256 于 manifest.json 后，
本脚本仅作来源记录——重跑会改变已冻结的样例身份，需先 --force 并重新登记。

段落折行手工计算（insert_textbox 溢出即截断，无法续页）；锚点断言在测试侧
做空白归一化，PDF 提取文本的换行不影响子串匹配。

运行: .venv/bin/python data/ingest_samples/generate_pdf_samples.py
"""
import sys
from pathlib import Path

import pymupdf

OUT_DIR = Path(__file__).resolve().parent
S3_PATH = OUT_DIR / "s3_pdf_text_layer.pdf"
S4_PATH = OUT_DIR / "s4_pdf_multipage.pdf"
FIXED_DATE = "D:20260914000000Z"

PAGE_W, PAGE_H = 595, 842
MARGIN = 60

# 字号梯度供 pymupdf4llm 的标题识别区分层级；锚点断言只看文本子串
F_TITLE, F_H2, F_H3, F_BODY, F_CODE = 20, 15, 12.5, 10.5, 9.5


def _page(doc):
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _wrap(text, fontname, fontsize, width):
    """贪心折行；返回行列表（空行保留为 ''）。"""
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        cur = ""
        for word in raw.split(" "):
            cand = f"{cur} {word}".strip()
            if cur and pymupdf.get_text_length(cand, fontname, fontsize) > width:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        lines.append(cur)
    return lines


def _write_lines(page, lines, x, y, fontname, fontsize, lh):
    for line in lines:
        if line:
            page.insert_text(pymupdf.Point(x, y), line, fontname=fontname, fontsize=fontsize)
        y += lh
    return y


def _heading(page, y, text, fontsize):
    page.insert_text(pymupdf.Point(MARGIN, y), text, fontname="hebo", fontsize=fontsize)
    return y + fontsize * 2.2


def _para(page, y, text, width=PAGE_W - 2 * MARGIN, ymax=PAGE_H - MARGIN, fontsize=F_BODY):
    """写一段折行文本；返回 (结束 y, 未写出的行)。"""
    lines = _wrap(text, "helv", fontsize, width)
    lh = fontsize * 1.35
    i = 0
    while i < len(lines) and y + lh <= ymax:
        line = lines[i]
        if line:
            page.insert_text(pymupdf.Point(MARGIN, y), line, fontname="helv", fontsize=fontsize)
        y += lh
        i += 1
    return y, lines[i:]


def _ruled_table(page, x0, y0, col_ws, rows, row_h=20):
    """手绘网格表：find_tables 依赖完整 ruling，跨页片段各自完整成格。"""
    xs, x = [], x0
    for w in col_ws:
        x += w
        xs.append(x)
    total_h = row_h * len(rows)
    for i in range(len(rows) + 1):
        yy = y0 + i * row_h
        page.draw_line(pymupdf.Point(x0, yy), pymupdf.Point(xs[-1], yy))
    for xv in [x0] + xs:
        page.draw_line(pymupdf.Point(xv, y0), pymupdf.Point(xv, y0 + total_h))
    for r, row in enumerate(rows):
        cx = x0
        for c, cell in enumerate(row):
            font = "hebo" if r == 0 else "helv"
            cell_lines = _wrap(cell, font, 9, col_ws[c] - 6)
            _write_lines(page, cell_lines[:1], cx + 3, y0 + r * row_h + 13, font, 9, 9)
            assert len(cell_lines) == 1, f"cell wraps, widen column: {cell!r}"
            cx += col_ws[c]


def _set_meta(doc, title):
    doc.set_metadata({
        "title": title,
        "producer": "ingest_samples/generate_pdf_samples.py",
        "creator": "agent-knowledge-base sample generator",
        "creationDate": FIXED_DATE,
        "modDate": FIXED_DATE,
    })


def build_s3():
    doc = pymupdf.open()
    page = _page(doc)
    y = MARGIN + F_TITLE
    page.insert_text(pymupdf.Point(MARGIN, y), "S3 Sample: Structure Preservation",
                     fontname="hebo", fontsize=F_TITLE)
    y = _heading(page, y + 40, "Section One - Retrieval Pipeline Overview", F_H2)
    y, _ = _para(page, y,
                 "This document exercises the PDF normalization path with a real text layer. "
                 "Headings at three sizes, a fenced-style monospace code block and a ruled table "
                 "must all survive conversion into normalized markdown. Anchor: dense and sparse "
                 "channels are fused by reciprocal rank fusion before threshold filtering.")
    y = _heading(page, y + 8, "Fusion and Threshold Details", F_H3)
    code = "\n".join([
        "def rrf_fusion(dense_hits, sparse_hits, k=60):",
        "    scores = {}",
        "    for channel in (dense_hits, sparse_hits):",
        "        for rank, hit in enumerate(channel):",
        "            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)",
        "    return sorted(scores.items(), key=lambda kv: -kv[1])",
    ])
    code_lines = _wrap(code, "cour", F_CODE, PAGE_W - 2 * MARGIN - 12)
    y = _write_lines(page, code_lines, MARGIN + 12, y + F_CODE, "cour", F_CODE, F_CODE * 1.4)
    y = _heading(page, y + 12, "Section Two - Channel Configuration", F_H2)
    y, _ = _para(page, y,
                 "The two retrieval channels and their models are summarized below. "
                 "Every cell text is a registered anchor substring for the sample manifest.")
    _ruled_table(page, MARGIN, y + 6, [95, 165, 145, 70], [
        ["channel", "model", "purpose", "status"],
        ["dense", "Qwen3-Embedding-0.6B", "semantic recall", "active"],
        ["sparse", "Qdrant/bm25 lexical", "lexical recall", "active"],
        ["fusion", "RRF k=60 fixed", "rank merging", "frozen"],
    ])
    y += 6 + 20 * 4 + 14
    _, rest = _para(page, y,
                    "Closing body paragraph of S3. It exists to verify that ordinary prose "
                    "after a table is not swallowed by table detection during normalization.")
    assert not rest, "S3 内容超出单页"
    _set_meta(doc, "S3 ingest sample")
    doc.save(S3_PATH)
    doc.close()


S4_PADDING = (
    "Padding prose follows, deliberately repetitive: evidence chains must remain "
    "verifiable across page boundaries. "
)


def build_s6():
    """无文本层 PDF：先把文本页栅格化，再把位图作为整页图像嵌入新页。
    产物除图像外无任何文本对象——扫描件形态。"""
    src = pymupdf.open()
    page = _page(src)
    y = MARGIN + F_BODY
    for line in _wrap(
        "S6 Sample: No Text Layer. This page exists only as a rasterized image; "
        "extracting text from it must yield nothing, and the pipeline must fail "
        "explicitly without invoking any OCR engine.", "helv", F_BODY,
        PAGE_W - 2 * MARGIN,
    ):
        page.insert_text(pymupdf.Point(MARGIN, y), line, fontname="helv", fontsize=F_BODY)
        y += F_BODY * 1.35
    pix = page.get_pixmap(dpi=72)

    doc = pymupdf.open()
    dst = _page(doc)
    dst.insert_image(pymupdf.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN), pixmap=pix)
    # 防御性自检：文本层必须为空
    assert dst.get_text().strip() == ""
    _set_meta(doc, "S6 ingest sample")
    doc.save(OUT_DIR / "s6_no_text_layer.pdf")
    doc.close()
    src.close()


def build_s4():
    doc = pymupdf.open()

    page = _page(doc)
    y = MARGIN + F_TITLE
    page.insert_text(pymupdf.Point(MARGIN, y), "S4 Sample: Multi-Page Continuity",
                     fontname="hebo", fontsize=F_TITLE)
    y = _heading(page, y + 40, "Continuous Narrative Section", F_H2)
    narrative = (
        "This long paragraph deliberately starts on page one and continues onto page "
        "two without any heading boundary in between. Cross-page continuity is a "
        "normalization risk: page separators may be inserted into the middle of a "
        "logical paragraph, and downstream chunking then sees an artificial boundary. "
        "The paragraph body is padded with ordinary technical prose so that its natural "
        "length exceeds the remaining space on this page. Retrieval quality work cares "
        "about this because a split inside a sentence degrades both embedding coherence "
        "and evidence readability. " + S4_PADDING * 40 +
        "This sentence is the paragraph tail that lands on page two: S4-PAGE-TAIL-ANCHOR."
    )
    y, rest = _para(page, y, narrative, ymax=PAGE_H - 2 * MARGIN)
    assert rest, "S4 叙事段未溢出第一页，跨页场景失效"

    page = _page(doc)
    y, more = _para(page, MARGIN + F_BODY, " ".join(rest))
    assert not more, "S4 续段超出第二页顶部预留区"
    y = _heading(page, y + 10, "Cross-Page Table Section", F_H2)
    y, _ = _para(page, y,
                 "The ruled table below starts near the bottom of page two and continues "
                 "on page three with the same column layout.")
    _ruled_table(page, MARGIN, y + 6, [110, 240, 125], [
        ["metric", "definition", "gate"],
        ["recall_at_5", "anchor questions hit in top 5", "1.000"],
        ["mrr_anchor", "mean reciprocal rank on anchors", "no drop"],
    ])

    page = _page(doc)
    _ruled_table(page, MARGIN, MARGIN + F_BODY, [110, 240, 125], [
        ["metric", "definition", "gate"],
        ["leak_count", "unanswerable questions answered", "0"],
        ["error_skip", "errored or skipped eval items", "0"],
    ])
    y = MARGIN + F_BODY + 20 * 3 + 14
    _, rest2 = _para(page, y,
                     "Closing paragraph of S4 on page three. S4-PAGE-TAIL-ANCHOR and every "
                     "table cell above are registered anchors; their presence in normalized "
                     "output is the multi-page preservation contract of this sample.")
    assert not rest2, "S4 第三页内容溢出"
    _set_meta(doc, "S4 ingest sample")
    doc.save(S4_PATH)
    doc.close()


if __name__ == "__main__":
    import hashlib

    targets = (S3_PATH, S4_PATH)
    if "--s6" in sys.argv:
        # 只补生成 S6（T2），不动已冻结的 S3/S4
        build_s6()
        p = OUT_DIR / "s6_no_text_layer.pdf"
        print(f"{p.name}: sha256={hashlib.sha256(p.read_bytes()).hexdigest()} ({p.stat().st_size} bytes)")
        sys.exit(0)
    if any(p.exists() for p in targets) and "--force" not in sys.argv:
        sys.exit("S3/S4 已生成并登记 SHA；重跑需 --force 并重新登记 manifest")
    build_s3()
    build_s4()
    for p in targets:
        print(f"{p.name}: sha256={hashlib.sha256(p.read_bytes()).hexdigest()} ({p.stat().st_size} bytes)")
