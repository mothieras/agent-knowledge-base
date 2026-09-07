"""L0 快照与一致性：index_id 计算、manifest 持久化、启动校验。

``index_id`` 是 ``sha256:<64位小写hex>``，摘要输入为构建 manifest 的 canonical
JSON（键排序、ensure_ascii=False、allow_nan=False）。参与摘要的是构建输入、模型
/处理版本和逻辑产物的 SHA-256/数量；不参与的是 index_id 自身、时间、物理路径、
运行状态与 Qdrant 随机 point UUID。同一输入和产物 → 同一 index_id，产物变化 →
不同 index_id。

manifest 存放在 Qdrant 数据目录相邻的 ``snapshot_manifest.json``，由离线 ingest
构建完成时写入；服务启动只读并校验，不自己写。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

INDEX_ID_PREFIX = "sha256:"
_INDEX_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# 参与摘要的构建输入/处理字段（manifest 根级键）。
# 时间/路径/状态/自身 id 不入摘要：换目录重建或改文件名不得改变 index_id。
MANIFEST_FIELDS = (
    "corpus_manifest_sha256",
    "fixtures_manifest_sha256",
    "source_content_sha256",          # source -> sha256（语料+fixture 合并后）
    "normalizer",
    "chunker",
    "dense_model",
    "sparse_model",
    "parent_count",
    "parent_content_sha256",
    "child_count",
    "child_content_sha256",
    "retrieval",
    "extra",
)


def compute_index_id(manifest: dict[str, Any]) -> str:
    """按 canonical JSON v1 计算 index_id。缺失必需字段或含 NaN 时抛 ValueError。"""
    missing = [k for k in ("parent_count", "parent_content_sha256", "child_count",
                           "child_content_sha256") if k not in manifest]
    if missing:
        raise ValueError(f"构建 manifest 缺少必需字段: {missing}")

    subset = {k: manifest.get(k) for k in MANIFEST_FIELDS if k in manifest}
    if not subset.get("source_content_sha256"):
        raise ValueError("构建 manifest 缺少 source_content_sha256（构建输入绑定）")

    canonical = json.dumps(subset, sort_keys=True, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":"))
    return INDEX_ID_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_valid_index_id(index_id: str) -> bool:
    return bool(_INDEX_ID_RE.match(index_id or ""))


def now_iso() -> str:
    """构建/校验时间戳（不参与摘要，仅审计）。"""
    return datetime.now().isoformat(timespec="seconds")


def _sorted_source_map(sources: dict[str, str]) -> dict[str, str]:
    return {k: sources[k] for k in sorted(sources)}


def normalize_manifest(manifest: dict[str, Any], *, sources: dict[str, str] | None) -> dict[str, Any]:
    """规范化外部提交的 manifest：只保留参与摘要的字段 + 审计字段。

    sources 为 {source: content_sha256}，调用方负责集合排序。缺失的构建输入/产物
    字段不在此校验（compute_index_id 负责必需字段），这里负责清洗非摘要字段。
    """
    cleaned = {k: manifest[k] for k in MANIFEST_FIELDS if k in manifest}
    if sources is not None:
        cleaned["source_content_sha256"] = _sorted_source_map(sources)
    return cleaned


class Snapshot:
    """已校验的快照视图：index_id + 关键绑定，供 L2 服务随响应返回。"""

    def __init__(self, index_id: str, manifest: dict[str, Any]):
        if not is_valid_index_id(index_id):
            raise ValueError(f"非法 index_id: {index_id!r}")
        self.index_id = index_id
        self.corpus_manifest_sha256 = manifest.get("corpus_manifest_sha256")
        self.fixtures_manifest_sha256 = manifest.get("fixtures_manifest_sha256")
        self.dense_model = manifest.get("dense_model")
        self.sparse_model = manifest.get("sparse_model")
        self.parent_count = manifest.get("parent_count")
        self.child_count = manifest.get("child_count")


def load_snapshot_manifest(manifest_path: Path) -> dict[str, Any]:
    raw = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if raw.get("manifest_version") != 1:
        raise ValueError(f"不支持的 manifest_version: {raw.get('manifest_version')!r}")
    return raw


def verify_snapshot(manifest: dict[str, Any], *, parent_dir: Path, qdrant_client, collection: str) -> Snapshot:
    """启动校验：manifest 自洽 + index_id 重算 + 本地 parent store 一致性。

    Qdrant 内容校验由调用方（bootstrap）按 point 数/子集采样执行；此函数专注
    manifest 与 parent store。任一不一致抛 RuntimeError → 服务不 ready。
    """
    expected_id = manifest.get("index_id")
    if not is_valid_index_id(expected_id):
        raise RuntimeError("快照 manifest 无有效 index_id")

    try:
        recomputed = compute_index_id(manifest)
    except ValueError as exc:
        raise RuntimeError(f"快照 manifest 不完整，无法重算 index_id: {exc}") from exc
    if recomputed != expected_id:
        raise RuntimeError(f"index_id 与 manifest 内容不符: 声明 {expected_id}，重算 {recomputed}")

    parent_count = int(manifest.get("parent_count", -1))
    parent_files = sorted(Path(parent_dir).glob("*.json"))
    if parent_count != len(parent_files):
        raise RuntimeError(
            f"parent store 数量不一致: manifest {parent_count}，磁盘 {len(parent_files)}"
        )

    h = hashlib.sha256()
    for path in parent_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        content = data.get("page_content", "")
        pid = data.get("metadata", {}).get("parent_id")
        h.update(pid.encode("utf-8") if pid else b"")
        h.update(b"\x00")
        h.update(hashlib.sha256(content.encode("utf-8")).digest())
    disk_hash = hashlib.sha256(h.digest()).hexdigest()
    declared = manifest.get("parent_content_sha256")
    if not declared or declared != disk_hash:
        raise RuntimeError(
            f"parent store 内容 hash 不一致: manifest {declared!r}，磁盘 {disk_hash!r}"
        )

    child_count = int(manifest.get("child_count", -1))
    try:
        points = qdrant_client.count(collection, exact=True).count
    except Exception as exc:
        raise RuntimeError(f"无法读取 Qdrant 集合 {collection!r} 点数: {exc}") from exc
    if child_count != points:
        raise RuntimeError(f"Qdrant point 数不一致: manifest {child_count}，集合 {points}")

    return Snapshot(expected_id, manifest)


def build_manifest(
    *,
    corpus_manifest_sha256: str,
    fixtures_manifest_sha256: str,
    source_content_sha256: dict[str, str],
    normalizer: str,
    chunker: dict[str, Any],
    dense_model: str,
    sparse_model: str,
    parent_count: int,
    parent_content_sha256: str,
    child_count: int,
    child_content_sha256: str,
    retrieval: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """离线 ingest 完成后构建 manifest；调用方先写文件再取 index_id。"""
    manifest = {
        "manifest_version": 1,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "fixtures_manifest_sha256": fixtures_manifest_sha256,
        "source_content_sha256": _sorted_source_map(source_content_sha256),
        "normalizer": normalizer,
        "chunker": chunker,
        "dense_model": dense_model,
        "sparse_model": sparse_model,
        "parent_count": int(parent_count),
        "parent_content_sha256": parent_content_sha256,
        "child_count": int(child_count),
        "child_content_sha256": child_content_sha256,
        "retrieval": retrieval,
        "extra": extra or {},
        "build_status": "complete",
        "built_at": now_iso(),
    }
    manifest["index_id"] = compute_index_id(manifest)
    return manifest
