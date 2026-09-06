"""T3 入库脚本：clear_all 重建 Qdrant，按 manifest.json + data/fixtures/manifest.json 入库全部语料。

前提: project/.env 已配置 DEEPSEEK_API_KEY；已先运行 data/sync_sources.py。
运行: cd project && uv run --python ../.venv/bin/python ingest_corpus.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
os.environ.pop("HF_ENDPOINT", None)  # 模型下载走官方 huggingface.co + 本机 SOCKS 代理

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import config
from core.rag_system import RAGSystem
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
    # 版本冲突评测必须在混合语料上检索（ROADMAP M1/M2）
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


def main():
    paths, source_names, doc_meta, fixture_count = _load_documents()

    rs = RAGSystem()
    rs.initialize()
    dm = DocumentManager(rs)
    print(f"[clear] 清空并重建 collection...")
    dm.clear_all()
    added, skipped = dm.add_documents(paths, source_names=source_names, doc_meta=doc_meta)
    print(f"[ingest] added={added} skipped={skipped} (第三方 {len(paths) - fixture_count} 篇 + fixture {fixture_count} 篇)")
    if added != len(paths):
        print("错误: 存在 skipped，入库不完整", file=sys.stderr)
        return 1
    # parent_store 一个 chunk 一个 json，直接数文件即可得 parent chunk 数
    parents = len(list(Path(config.PARENT_STORE_PATH).glob("*.json")))
    print(f"[chunks] parent_chunks={parents}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
