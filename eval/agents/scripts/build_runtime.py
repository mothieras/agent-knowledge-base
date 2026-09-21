"""T6/T7 隔离运行时构建（D6/PHASE2-T67 §5.3）。

用独立数据目录（eval/agents/runtime/）构建「受治理语料 + T6 场景语料」的索引，
写快照 manifest 并注册文档版本。不动主索引与日常条目库；生产 ingest 的注册钩子
已在 src/ingest_corpus.py（index_id 不变性有独立验证）。

用法（从 src/ 或仓库根运行均可）：
    cd src && env -u ALL_PROXY -u all_proxy ../.venv/bin/python ../eval/agents/scripts/build_runtime.py
场景文档更新后重跑本脚本即可注册新版本（旧版本永久保留）。
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC = REPO_ROOT / "src"
AGENTS = REPO_ROOT / "eval" / "agents"
RUNTIME = AGENTS / "runtime"
CORPUS = AGENTS / "corpus"

sys.path.insert(0, str(SRC))
os.environ.pop("HF_ENDPOINT", None)
os.environ["QDRANT_DB_PATH"] = str(RUNTIME / "qdrant_db")
os.environ["PARENT_STORE_PATH"] = str(RUNTIME / "parent_store")
os.environ["MARKDOWN_DIR"] = str(RUNTIME / "markdown_docs")
os.environ["DOCS_DB_PATH"] = str(RUNTIME / "docs.db")

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

import config  # noqa: E402
from db.parent_store_manager import ParentStoreManager  # noqa: E402
from db.vector_db_manager import VectorDbManager  # noqa: E402
from document_chunker import DocumentChunker  # noqa: E402
from core.document_manager import DocumentManager  # noqa: E402
from ingest_corpus import (  # noqa: E402
    _load_documents,
    _parent_store_hash,
    _register_documents,
    _source_sha256,
)

MANIFEST = REPO_ROOT / "data" / "manifest.json"
FIXTURES_MANIFEST = REPO_ROOT / "data" / "fixtures" / "manifest.json"


def _load_scenario_docs():
    """场景语料：T7 文档派生条目素材（source = t6-scenario/<文件名>）。"""
    paths, source_names, doc_meta = [], {}, {}
    for md in sorted(CORPUS.glob("*.md")):
        source = f"t6-scenario/{md.name}"
        p = str(md)
        paths.append(p)
        source_names[p] = source
        doc_meta[p] = {
            "version": "v1",
            "effective_date": "2026-09-20",
            "expired_date": None,
            "priority": 5,
            "topic": "t6_scenario",
            "doc_type": "team_process",
        }
    return paths, source_names, doc_meta


def main() -> int:
    from db.snapshot import build_manifest

    paths, source_names, doc_meta, fixture_count = _load_documents()
    s_paths, s_names, s_meta = _load_scenario_docs()
    paths += s_paths
    source_names.update(s_names)
    doc_meta.update(s_meta)

    vector_db = VectorDbManager()
    parent_store = ParentStoreManager()
    chunker = DocumentChunker()
    dm = DocumentManager(chunker, parent_store, vector_db, config.CHILD_COLLECTION)
    dm.clear_all()
    results = dm.add_documents(paths, source_names=source_names, doc_meta=doc_meta)
    added = sum(1 for r in results if r["status"] == "ok")
    failures = [r for r in results if r["status"] != "ok"]
    if failures:
        for r in failures:
            print(f"  [{r['status']}] {r['source']}: {r['detail']}", file=sys.stderr)
        return 1

    parent_count, parent_hash = _parent_store_hash()
    client = vector_db.client
    child_count, child_hash = _child_store_hash(client, config.CHILD_COLLECTION)
    print(f"[build] added={added} parent_chunks={parent_count} child_chunks={child_count}")

    manifest = build_manifest(
        corpus_manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        fixtures_manifest_sha256=hashlib.sha256(FIXTURES_MANIFEST.read_bytes()).hexdigest(),
        source_content_sha256=_source_sha256(paths, source_names),
        normalizer="pymupdf4llm/markdown-as-is",
        chunker={
            "parent_splitter": "MarkdownHeaderTextSplitter",
            "headers": config.HEADERS_TO_SPLIT_ON,
            "min_parent_size": config.MIN_PARENT_SIZE,
            "max_parent_size": config.MAX_PARENT_SIZE,
            "child_splitter": "RecursiveCharacterTextSplitter",
            "child_size": config.CHILD_CHUNK_SIZE,
            "child_overlap": config.CHILD_CHUNK_OVERLAP,
        },
        dense_model=config.DENSE_MODEL,
        sparse_model=config.SPARSE_MODEL,
        parent_count=parent_count,
        parent_content_sha256=parent_hash,
        child_count=child_count,
        child_content_sha256=child_hash,
        retrieval={
            "collection": config.CHILD_COLLECTION,
            "k_default": config.DEFAULT_RETRIEVAL_K,
            "score_threshold": config.RETRIEVAL_SCORE_THRESHOLD,
        },
        extra={"t6_scenario_docs": sorted(s_names.values())},
    )
    manifest_path = Path(config.QDRANT_DB_PATH) / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[snapshot] index_id={manifest['index_id']}")

    # 场景语料单独注册（受治理语料走 ingest_corpus 的公共注册路径，同函数复用）
    _register_documents(results, paths, source_names, manifest["index_id"])
    return 0


def _child_store_hash(client, collection: str) -> tuple[int, str]:
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection, limit=200, offset=offset, with_payload=True, with_vectors=False
        )
        points.extend(batch)
        if offset is None:
            break
    points.sort(key=lambda p: p.payload.get("metadata", {}).get("chunk_id", ""))
    h = hashlib.sha256()
    for p in points:
        h.update(p.payload.get("page_content", "").encode("utf-8"))
        h.update(b"\x00")
        h.update(p.payload.get("metadata", {}).get("chunk_id", "").encode("utf-8"))
        h.update(b"\x00")
    return len(points), hashlib.sha256(h.digest()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
