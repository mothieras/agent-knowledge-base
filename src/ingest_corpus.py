"""入库脚本：clear_all 重建 Qdrant，按 manifest.json + data/fixtures/manifest.json 入库全部语料。

前提: 根目录 .env 已配置 DEEPSEEK_API_KEY（无需 API 调用，仅保持同源加载）；已先运行 data/sync_sources.py。
运行: cd src && uv run --python ../.venv/bin/python ingest_corpus.py

构建完成后写入 qdrant_db/snapshot_manifest.json（index_id + 构建输入/产物绑定），
服务启动校验并随响应返回 index_id。
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
os.environ.pop("HF_ENDPOINT", None)  # 模型下载走官方 huggingface.co + 本机 SOCKS 代理

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import config
from db.parent_store_manager import ParentStoreManager
from db.vector_db_manager import VectorDbManager
from document_chunker import DocumentChunker
from core.document_manager import DocumentManager

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "data" / "manifest.json"
FIXTURES_MANIFEST = REPO_ROOT / "data" / "fixtures" / "manifest.json"


def _load_documents():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fixtures = json.loads(FIXTURES_MANIFEST.read_text(encoding="utf-8"))
    paths = []
    source_names = {}
    doc_meta = {}

    for doc in manifest["documents"]:
        if doc["processing"] == "copied":
            p = REPO_ROOT / "data" / "interview_docs" / doc["local_rel"]
        elif doc["processing"] == "code_wrapped":
            p = REPO_ROOT / "data" / "processed" / (doc["local_rel"] + ".md")
        else:
            print(f"[skip] 未知 processing: {doc}")
            continue
        p = str(p)
        paths.append(p)
        source_names[p] = doc["source"]
        doc_meta[p] = {
            "version": doc.get("version"),
            "effective_date": doc.get("effective_date"),
            "expired_date": doc.get("expired_date"),
            "priority": doc.get("priority"),
            "topic": doc.get("topic"),
            "doc_type": doc.get("doc_type"),
        }

    # 受治理 fixture（项目自有，版本冲突素材）：与第三方语料进同一 collection，
    # 版本冲突评测必须在混合语料上检索
    for doc in fixtures["documents"]:
        p = str(FIXTURES_MANIFEST.parent / doc["file"])
        paths.append(p)
        source_names[p] = doc["source"]
        doc_meta[p] = {
            "version": doc.get("version"),
            "effective_date": doc.get("effective_date"),
            "expired_date": doc.get("expired_date"),
            "priority": doc.get("priority"),
            "topic": doc.get("topic"),
            "doc_type": doc.get("doc_type"),
        }

    return paths, source_names, doc_meta, len(fixtures["documents"])


def _parent_store_hash() -> tuple[int, str]:
    files = sorted(Path(config.PARENT_STORE_PATH).glob("*.json"))
    h = hashlib.sha256()
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = data.get("metadata", {}).get("parent_id")
        h.update(pid.encode("utf-8") if pid else b"")
        h.update(b"\x00")
        h.update(hashlib.sha256(data.get("page_content", "").encode("utf-8")).digest())
    return len(files), hashlib.sha256(h.digest()).hexdigest()


def _child_store_hash(client, collection: str) -> tuple[int, str]:
    """Qdrant payload（page_content + metadata chunk_id）的确定性摘要。

    Point UUID 不入摘要（随机生成，db/snapshot.py 契约：同一输入和产物 → 同一
    index_id）；按 chunk_id 排序得到确定性顺序。
    """
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
        md = p.payload.get("metadata", {})
        h.update(md.get("chunk_id", "").encode("utf-8"))
        h.update(b"\x00")
    return len(points), hashlib.sha256(h.digest()).hexdigest()


def _source_sha256(paths, source_names) -> dict[str, str]:
    return {
        source_names[p]: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in paths
    }


def _register_documents(results, paths, source_names, index_id) -> None:
    """文档注册表（D6/PHASE2-T67 §4.3）：入库 ok 的文档注册版本化规范化文本。

    注册不改变快照输入（index_id 不变）；同内容幂等跳过，内容变化 version+1。
    """
    from db.doc_store import DocStore

    store = DocStore(config.DOCS_DB_PATH)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    repo_root = REPO_ROOT
    registered = skipped = 0
    for result in results:
        if result["status"] != "ok":
            continue
        source = result["source"]
        # 规范化产物命名与 DocumentManager 同源：source 以 __ 替换扁平化
        md_path = Path(config.MARKDOWN_DIR) / f"{source.replace('/', '__')}.md"
        if not md_path.exists():
            print(f"  [register skip] 产物缺失: {md_path}", file=sys.stderr)
            continue
        content = md_path.read_text(encoding="utf-8")
        origin_file = None
        for p in paths:
            if source_names[p] == source:
                origin_file = Path(p).relative_to(repo_root).as_posix()
                break
        outcome = store.register(
            doc_id=source, name=source.rsplit("/", 1)[-1], content=content,
            origin_file=origin_file, index_id=index_id, created_at=now,
        )
        if outcome["new_version"]:
            registered += 1
        else:
            skipped += 1
    store.close()
    print(f"[docs] 注册 {registered} 个新版本，{skipped} 篇内容未变（幂等跳过）")


def main():
    from db.snapshot import build_manifest

    paths, source_names, doc_meta, fixture_count = _load_documents()

    # 入库不需要生成模型：直接装配分块/存储组件（D-01）
    vector_db = VectorDbManager()
    parent_store = ParentStoreManager()
    chunker = DocumentChunker()
    dm = DocumentManager(chunker, parent_store, vector_db, config.CHILD_COLLECTION)
    print(f"[clear] 清空并重建 collection...")
    dm.clear_all()
    results = dm.add_documents(paths, source_names=source_names, doc_meta=doc_meta)
    added = sum(1 for r in results if r["status"] == "ok")
    print(f"[ingest] added={added}/{len(paths)} (第三方 {len(paths) - fixture_count} 篇 + fixture {fixture_count} 篇)")
    failures = [r for r in results if r["status"] != "ok"]
    if failures:
        by_status = {}
        for r in failures:
            by_status.setdefault(r["status"], []).append(r)
        for status in sorted(by_status):
            for r in by_status[status]:
                print(f"  [{status}] {r['source']}: {r['detail']}", file=sys.stderr)
        counts = ", ".join(f"{s}={len(rs)}" for s, rs in sorted(by_status.items()))
        print(f"错误: 入库不完整 added={added}，{counts}", file=sys.stderr)
        return 1

    parent_count, parent_hash = _parent_store_hash()
    # embedded 单进程只允许一个 client：复用已持有的实例
    client = vector_db.client
    child_count, child_hash = _child_store_hash(client, config.CHILD_COLLECTION)
    print(f"[chunks] parent_chunks={parent_count} child_chunks={child_count}")

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
    )
    manifest_path = Path(config.QDRANT_DB_PATH) / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[snapshot] index_id={manifest['index_id']} → {manifest_path}")
    _register_documents(results, paths, source_names, manifest["index_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
