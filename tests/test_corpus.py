import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "manifest.json"
GOLDEN_SET_PATH = ROOT / "eval" / "golden_set.jsonl"


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def managed_files(directory):
    return {
        path.relative_to(directory)
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_corpus_is_an_exact_manifest_snapshot():
    documents = load_manifest()["documents"]
    expected = {Path(doc["local_rel"]) for doc in documents}

    assert len(documents) == len(expected) == 14
    assert managed_files(ROOT / "data" / "raw") == expected
    assert managed_files(ROOT / "data" / "interview_docs") == expected
    assert managed_files(ROOT / "data" / "processed") == set()

    for doc in documents:
        path = ROOT / "data" / "interview_docs" / doc["local_rel"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc["sha256"]
        assert doc["license"] == "CC-BY-NC-SA-4.0"


def test_corpus_has_no_common_personal_identifiers():
    patterns = (
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    )
    for path in (ROOT / "data" / "interview_docs").rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        assert not any(pattern.search(content) for pattern in patterns), path


def test_golden_set_matches_public_corpus():
    manifest_sources = {doc["source"] for doc in load_manifest()["documents"]}
    items = [
        json.loads(line)
        for line in GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(items) == 30
    assert Counter(item["category"] for item in items) == {
        "exact_term": 5,
        "concept_contrast": 5,
        "multi_hop": 5,
        "version_conflict": 5,
        "ambiguous_followup": 5,
        "unanswerable": 5,
    }
    for item in items:
        assert set(item["expected_sources"]) <= manifest_sources
        assert bool(item["expected_sources"]) == item["answerable"]
