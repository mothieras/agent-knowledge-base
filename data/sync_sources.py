"""按 manifest.json 同步公开语料，并清除不再受清单管理的旧文件。

用法:
  python3 data/sync_sources.py
  SOURCES_ROOT=/path/to/all-in-rag python3 data/sync_sources.py
"""
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "data" / "manifest.json"

LANG_BY_SUFFIX = {
    ".java": "java",
    ".ts": "typescript",
    ".xml": "xml",
    ".py": "python",
    ".js": "javascript",
}


def wrap_code(source_rel: str, content: str) -> str:
    lang = LANG_BY_SUFFIX.get(Path(source_rel).suffix, "text")
    fence = "```"
    return (
        f"# 项目代码：{source_rel}\n\n"
        f"代码原文，供项目经历问答检索。\n\n"
        f"{fence}{lang}\n{content}\n{fence}\n"
    )


def safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"local_rel 必须是安全的相对路径: {value}")
    return path


def download_url(repository_url: str, revision: str, local_rel: str) -> str:
    repo_path = repository_url.removesuffix(".git").removeprefix("https://github.com/")
    return f"https://raw.githubusercontent.com/{repo_path}/{revision}/{local_rel}"


def load_source(doc: dict, manifest: dict, sources_root: Path | None) -> bytes:
    local_rel = safe_relative_path(doc["local_rel"])
    if sources_root is not None:
        source_path = sources_root / local_rel
        if not source_path.is_file():
            raise FileNotFoundError(f"源文件不存在: {source_path}")
        return source_path.read_bytes()

    repository = manifest["source_repository"]
    url = download_url(repository["url"], repository["revision"], local_rel.as_posix())
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def verify_digest(doc: dict, payload: bytes) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    expected = doc.get("sha256")
    if not expected:
        raise ValueError(f"manifest 缺少 sha256: {doc['local_rel']}")
    if actual != expected:
        raise ValueError(
            f"SHA-256 不匹配: {doc['local_rel']} expected={expected} actual={actual}"
        )


def prune_unmanaged(root: Path, expected: set[Path]) -> int:
    if not root.exists():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(root) not in expected:
            path.unlink()
            removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sources_root_value = os.environ.get("SOURCES_ROOT")
    sources_root = Path(sources_root_value).expanduser().resolve() if sources_root_value else None
    if sources_root is not None and not sources_root.is_dir():
        print(f"错误: SOURCES_ROOT 不是目录: {sources_root}", file=sys.stderr)
        return 1

    payloads = []
    try:
        for doc in manifest["documents"]:
            processing = doc["processing"]
            if processing not in {"copied", "code_wrapped"}:
                raise ValueError(f"未知 processing={processing}: {doc['local_rel']}")
            payload = load_source(doc, manifest, sources_root)
            verify_digest(doc, payload)
            payloads.append((doc, payload))
    except Exception as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1

    expected_raw = {safe_relative_path(doc["local_rel"]) for doc, _ in payloads}
    expected_interview = {
        safe_relative_path(doc["local_rel"])
        for doc, _ in payloads
        if doc["processing"] == "copied"
    }
    expected_processed = {
        Path(doc["local_rel"] + ".md")
        for doc, _ in payloads
        if doc["processing"] == "code_wrapped"
    }

    roots = {
        "raw": REPO_ROOT / "data" / "raw",
        "interview": REPO_ROOT / "data" / "interview_docs",
        "processed": REPO_ROOT / "data" / "processed",
    }
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)

    removed = sum((
        prune_unmanaged(roots["raw"], expected_raw),
        prune_unmanaged(roots["interview"], expected_interview),
        prune_unmanaged(roots["processed"], expected_processed),
    ))

    copied = wrapped = 0
    for doc, payload in payloads:
        local_rel = safe_relative_path(doc["local_rel"])
        raw_dst = roots["raw"] / local_rel
        raw_dst.parent.mkdir(parents=True, exist_ok=True)
        raw_dst.write_bytes(payload)

        if doc["processing"] == "copied":
            dst = roots["interview"] / local_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(payload)
            copied += 1
            print(f"[COPIED] {local_rel}")
        else:
            wrapped_md = wrap_code(doc["local_rel"], payload.decode("utf-8"))
            dst = roots["processed"] / (doc["local_rel"] + ".md")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(wrapped_md, encoding="utf-8")
            wrapped += 1
            print(f"[WRAPPED] {local_rel}")

    print(f"\n完成: copied={copied} wrapped={wrapped} removed_stale={removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
