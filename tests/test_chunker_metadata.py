from document_chunker import DocumentChunker


def test_create_chunks_single_carries_doc_meta(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# Title\n\n" + ("body text " * 300), encoding="utf-8")

    chunker = DocumentChunker()
    parents, children = chunker.create_chunks_single(
        md,
        source_name="src/doc.md",
        doc_meta={
            "version": "v1",
            "priority": 10,
            "effective_date": "2026-07-19",
            "expired_date": None,
            "topic": "rag_foundations",
        },
    )

    assert parents and children

    # parents is a list of (parent_id, Document); children are Documents that
    # inherit the parent's metadata through the splitter.
    parent_doc = parents[0][1]
    assert parent_doc.metadata["source"] == "src/doc.md"
    assert parent_doc.metadata["version"] == "v1"
    assert parent_doc.metadata["priority"] == 10
    assert parent_doc.metadata["effective_date"] == "2026-07-19"

    child_meta = children[0].metadata
    assert child_meta["source"] == "src/doc.md"
    assert child_meta["version"] == "v1"
    assert child_meta["parent_id"] == parent_doc.metadata["parent_id"]
    assert child_meta["priority"] == 10


def test_create_chunks_single_omits_doc_meta_when_none(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# Title\n\n" + ("body text " * 300), encoding="utf-8")

    chunker = DocumentChunker()
    parents, children = chunker.create_chunks_single(md, source_name="src/doc.md")

    child_meta = children[0].metadata
    assert child_meta["source"] == "src/doc.md"
    assert "version" not in child_meta
    assert "priority" not in child_meta
