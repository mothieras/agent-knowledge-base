"""T3 收料脚本：按 manifest.json 把 findjob 源文件收进 data/。

用法:
  SOURCES_ROOT=/path/to/findjob python3 data/sync_sources.py

产出:
  - copied      -> data/raw/<local_rel> + data/interview_docs/<local_rel>
  - code_wrapped -> data/raw/<local_rel> + data/processed/<local_rel>.md (包装版)

幂等: 已存在则覆盖更新。manifest 之外的文档不会进入仓库(隐私排除天然生效)。
"""
import json
import os
import shutil
import sys
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
    fence = "```"  # 用变量拼代码围栏，避免 f-string 内嵌反引号
    return (
        f"# 项目代码：{source_rel}\n\n"
        f"代码原文，供项目经历问答检索。\n\n"
        f"{fence}{lang}\n{content}\n{fence}\n"
    )


def main() -> int:
    sources_root = os.environ.get("SOURCES_ROOT")
    if not sources_root:
        print("错误: 未设置 SOURCES_ROOT（指向 findjob/ 目录）", file=sys.stderr)
        return 1
    root = Path(sources_root)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    copied = wrapped = failed = 0
    for doc in manifest["documents"]:
        local_rel = doc["local_rel"]
        src = root / local_rel
        if not src.exists():
            print(f"[FAIL] 源文件不存在: {src}")
            failed += 1
            continue
        raw_dst = REPO_ROOT / "data" / "raw" / local_rel
        raw_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, raw_dst)

        if doc["processing"] == "copied":
            dst = REPO_ROOT / "data" / "interview_docs" / local_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            copied += 1
            print(f"[COPIED] {local_rel}")
        elif doc["processing"] == "code_wrapped":
            wrapped_md = wrap_code(local_rel, src.read_text(encoding="utf-8"))
            dst = REPO_ROOT / "data" / "processed" / (local_rel + ".md")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(wrapped_md, encoding="utf-8")
            wrapped += 1
            print(f"[WRAPPED] {local_rel}")
        else:
            print(f"[SKIP] 未知 processing={doc['processing']}: {local_rel}")

    print(f"\n完成: copied={copied} wrapped={wrapped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
