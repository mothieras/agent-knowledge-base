"""快照模块测试：index_id 计算、manifest 校验、失败路径。"""
import hashlib
import json

import pytest

from db.snapshot import (
    build_manifest,
    compute_index_id,
    is_valid_index_id,
    verify_snapshot,
)


def _base_manifest(**overrides):
    m = {
        "manifest_version": 1,
        "corpus_manifest_sha256": "a" * 64,
        "fixtures_manifest_sha256": "b" * 64,
        "source_content_sha256": {"src-a.md": "c" * 64},
        "normalizer": "as-is",
        "chunker": {"min": 2000, "max": 4000},
        "dense_model": "Qwen/Qwen3-Embedding-0.6B",
        "sparse_model": "Qdrant/bm25",
        "parent_count": 1,
        "parent_content_sha256": "d" * 64,
        "child_count": 2,
        "child_content_sha256": "e" * 64,
        "retrieval": {"k_default": 7},
    }
    m.update(overrides)
    return m


def test_compute_index_id_deterministic_and_bound_to_content():
    m = _base_manifest()
    m["index_id"] = compute_index_id(m)
    assert is_valid_index_id(m["index_id"])
    assert compute_index_id(m) == m["index_id"]

    changed = _base_manifest(child_count=3)
    assert compute_index_id(changed) != m["index_id"]


def test_index_id_ignores_time_and_path_fields():
    m = _base_manifest()
    m["index_id"] = compute_index_id(m)
    # 时间/状态不参与摘要；extra 属于构建输入，参与摘要
    m2 = dict(m, built_at="2026-09-07T00:00:00", build_status="running")
    assert compute_index_id(m2) == m["index_id"]
    m3 = dict(m, extra={"normalizer_version": "2"})
    assert compute_index_id(m3) != m["index_id"]


def test_index_id_rejects_nan():
    import math

    m = _base_manifest(extra={"x": math.nan})
    with pytest.raises(ValueError):
        compute_index_id(m)


def test_index_id_rejects_missing_required():
    m = _base_manifest()
    del m["parent_content_sha256"]
    with pytest.raises(ValueError):
        compute_index_id(m)


def test_build_manifest_roundtrip(tmp_path):
    m = build_manifest(
        corpus_manifest_sha256="a" * 64,
        fixtures_manifest_sha256="b" * 64,
        source_content_sha256={"z.md": "c" * 64, "a.md": "d" * 64},
        normalizer="as-is",
        chunker={"min": 2000},
        dense_model="D",
        sparse_model="S",
        parent_count=1,
        parent_content_sha256="e" * 64,
        child_count=2,
        child_content_sha256="f" * 64,
        retrieval={"k": 7},
    )
    assert is_valid_index_id(m["index_id"])
    # source map 按 key 排序，保证 canonical 稳定
    assert list(m["source_content_sha256"]) == ["a.md", "z.md"]
    assert m["build_status"] == "complete"


def test_verify_snapshot_ok(tmp_path):
    parent_dir = tmp_path / "parent_store"
    parent_dir.mkdir()
    content = "parent body"
    pid = "doc_p0"
    (parent_dir / f"{pid}.json").write_text(
        json.dumps({"page_content": content, "metadata": {"parent_id": pid}}),
        encoding="utf-8",
    )
    # 磁盘 hash 与 verify_snapshot 内部算法一致
    h = hashlib.sha256()
    h.update(pid.encode())
    h.update(b"\x00")
    h.update(hashlib.sha256(content.encode()).digest())
    disk_hash = hashlib.sha256(h.digest()).hexdigest()

    class FakeQdrant:
        def count(self, collection, exact=True):
            class R:
                count = 2
            return R()

    m = _base_manifest(parent_count=1, parent_content_sha256=disk_hash)
    m["index_id"] = compute_index_id(m)
    snap = verify_snapshot(m, parent_dir=parent_dir, qdrant_client=FakeQdrant(), collection="c")
    assert snap.index_id == m["index_id"]


def test_verify_snapshot_detects_parent_drift(tmp_path):
    parent_dir = tmp_path / "parent_store"
    parent_dir.mkdir()
    (parent_dir / "doc_p0.json").write_text(
        json.dumps({"page_content": "different", "metadata": {"parent_id": "doc_p0"}}),
        encoding="utf-8",
    )
    m = _base_manifest()
    m["index_id"] = compute_index_id(m)

    class FakeQdrant:
        def count(self, collection, exact=True):
            class R:
                count = 2
            return R()

    with pytest.raises(RuntimeError, match="parent store 内容 hash 不一致"):
        verify_snapshot(m, parent_dir=parent_dir, qdrant_client=FakeQdrant(), collection="c")


def test_verify_snapshot_detects_point_drift(tmp_path):
    parent_dir = tmp_path / "parent_store"
    parent_dir.mkdir()
    (parent_dir / "doc_p0.json").write_text(
        json.dumps({"page_content": "x", "metadata": {"parent_id": "doc_p0"}}),
        encoding="utf-8",
    )
    h = hashlib.sha256()
    h.update(b"doc_p0\x00")
    h.update(hashlib.sha256(b"x").digest())
    m = _base_manifest(parent_count=1, parent_content_sha256=hashlib.sha256(h.digest()).hexdigest())
    m["index_id"] = compute_index_id(m)

    class FakeQdrant:
        def count(self, collection, exact=True):
            class R:
                count = 3
            return R()

    with pytest.raises(RuntimeError, match="Qdrant point 数不一致"):
        verify_snapshot(m, parent_dir=parent_dir, qdrant_client=FakeQdrant(), collection="c")
