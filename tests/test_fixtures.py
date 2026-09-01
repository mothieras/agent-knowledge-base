"""受治理 fixture 的静态契约测试：不依赖本地索引，fresh clone 也能跑。

守护 data/fixtures/manifest.json 的结构不变量——每个 topic 恰有一对
active/expired 文档，日期与优先级语义成立，expected_hits 引用真实 source。
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"
MANIFEST = FIXTURES / "manifest.json"


def _load():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = manifest["documents"]
    by_source = {d["source"]: d for d in docs}
    return manifest, docs, by_source


def test_fixture_files_exist_and_are_scanned():
    _, docs, _ = _load()
    listed = {doc["file"] for doc in docs}
    on_disk = {p.name for p in FIXTURES.glob("*.md")}
    assert listed == on_disk
    for doc in docs:
        path = FIXTURES / doc["file"]
        assert len(path.read_text(encoding="utf-8")) > 1000, path  # 足以走真实分块路径


def test_each_topic_has_one_active_and_one_expired_version():
    _, docs, _ = _load()
    topics = {}
    for doc in docs:
        topics.setdefault(doc["topic"], []).append(doc)

    assert topics, "fixture 至少应有一对版本"
    for topic, group in topics.items():
        assert len(group) == 2, f"{topic} 应恰有一对版本"
        expired = [d for d in group if d["expired_date"]]
        active = [d for d in group if not d["expired_date"]]
        assert len(expired) == 1 and len(active) == 1, f"{topic} 应一 expired 一 active"
        assert expired[0]["version"] != active[0]["version"], f"{topic} 两版本号必须可区分"
        # active 版本优先级更高，且接续 expired 的失效日期
        assert active[0]["priority"] > expired[0]["priority"], topic
        assert date.fromisoformat(active[0]["effective_date"]) == date.fromisoformat(expired[0]["expired_date"]) + timedelta(days=1), topic


def test_expected_hits_reference_real_sources():
    manifest, _, by_source = _load()
    for e in manifest["expected_hits"]:
        assert e["active_source"] in by_source
        assert e["expired_source"] in by_source
        active = by_source[e["active_source"]]
        assert active["expired_date"] is None
        assert by_source[e["expired_source"]]["expired_date"] is not None
        assert e["query"]


def test_fixture_license_is_project_owned():
    manifest, docs, _ = _load()
    assert manifest["origin"] == "project-owned"
    assert manifest["license"] == "MIT"
    assert all(re.fullmatch(r"fixtures/\w+\.md", d["source"]) for d in docs)
