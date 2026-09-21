"""T7 文档注册表测试（D6/PHASE2-T67 §4.3）。

注册幂等（同内容不新增版本）、内容变化版本递增、旧版本永久保留、
resolve/版本列表/含正文读取。
"""
import pytest

from db.doc_store import DocStore, content_sha256


@pytest.fixture
def store(tmp_path):
    s = DocStore(str(tmp_path / "docs.db"))
    yield s
    s.close()


def register_v1(store):
    return store.register(
        doc_id="docs/a.md", name="a.md", content="第一版正文内容",
        origin_file="data/a.md", index_id="idx-1",
        created_at="2026-09-20T00:00:00.000Z",
    )


def test_register_first_and_idempotent(store):
    out = register_v1(store)
    assert out == {"doc_id": "docs/a.md", "version": 1, "new_version": True}
    # 同内容重注册：幂等跳过，不新增版本
    again = store.register(
        doc_id="docs/a.md", name="a.md", content="第一版正文内容",
        origin_file="data/a.md", index_id="idx-2",
        created_at="2026-09-20T00:01:00.000Z",
    )
    assert again == {"doc_id": "docs/a.md", "version": 1, "new_version": False}
    assert store.get_document("docs/a.md")["total_versions"] == 1


def test_content_change_increments_version(store):
    register_v1(store)
    out = store.register(
        doc_id="docs/a.md", name="a.md", content="第二版正文内容（已更新）",
        origin_file="data/a.md", index_id="idx-2",
        created_at="2026-09-20T00:01:00.000Z",
    )
    assert out == {"doc_id": "docs/a.md", "version": 2, "new_version": True}
    doc = store.get_document("docs/a.md")
    assert doc["latest_version"] == 2 and doc["total_versions"] == 2


def test_old_version_kept_after_update(store):
    register_v1(store)
    store.register(
        doc_id="docs/a.md", name="a.md", content="第二版正文内容（已更新）",
        origin_file="data/a.md", index_id="idx-2",
        created_at="2026-09-20T00:01:00.000Z",
    )
    v1 = store.get_version("docs/a.md", 1)
    assert v1["content"] == "第一版正文内容"
    assert v1["content_sha256"] == content_sha256("第一版正文内容")
    assert v1["index_id"] == "idx-1" and v1["origin_file"] == "data/a.md"
    assert v1["total_length"] == len("第一版正文内容")
    v2 = store.get_version("docs/a.md", 2)
    assert v2["content"] == "第二版正文内容（已更新）"


def test_list_versions_and_missing(store):
    assert store.get_document("nope") is None
    assert store.list_versions("nope") == []
    assert store.get_version("nope", 1) is None
    register_v1(store)
    versions = store.list_versions("docs/a.md")
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["content_sha256"] == content_sha256("第一版正文内容")
    assert versions[0]["total_length"] == 7
