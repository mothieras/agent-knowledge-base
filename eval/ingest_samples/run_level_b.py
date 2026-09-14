"""T1 Level B：ingest 样例集的 scratch 索引端到端验证（本地手动跑，不进 CI）。

样例逐个以独立 add_documents 调用提交，捕获每步的返回计数与 print 输出，
输出逐样本显式终态表（ok / failed:<观察> / silent_drop）与批量计数一致性（门槛 1），
并记录 S7/S8/S9 在 store 侧的实际行为（指纹/文件清单/source 列表）。

与质量索引严格隔离：qdrant/parent_store 走 env 覆盖，markdown 输出目录
（config 无 env 入口）在导入 config 后直接改写。重复运行前会 clear_all 重建。

运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python ingest_samples/run_level_b.py
产出: eval/ingest_artifacts/{qdrant_db/, parent_store/, markdown_docs/, report.json}
"""
import hashlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVAL_DIR.parent
ARTIFACTS = EVAL_DIR / "ingest_artifacts"
SAMPLES = REPO_ROOT / "data" / "ingest_samples"

os.environ["QDRANT_DB_PATH"] = str(ARTIFACTS / "qdrant_db")
os.environ["PARENT_STORE_PATH"] = str(ARTIFACTS / "parent_store")
os.environ.pop("HF_ENDPOINT", None)
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import config  # noqa: E402

config.MARKDOWN_DIR = str(ARTIFACTS / "markdown_docs")

from db.parent_store_manager import ParentStoreManager  # noqa: E402
from db.vector_db_manager import VectorDbManager  # noqa: E402
from document_chunker import DocumentChunker  # noqa: E402
from core.document_manager import DocumentManager  # noqa: E402

# 正样：当前实现就必须成功（失败 = harness 或管道破坏，硬失败）
MUST_OK = {"S1", "S2", "S3", "S4", "S7#1", "S8a#1", "S8b#1"}

# 冲突对指纹：断言胜者入库、败者无痕迹（静默跳过的 store 侧证据）
FINGERPRINTS = {
    "S8a": ("FIRST-FILE-CONTENT-A", "SECOND-FILE-CONTENT-B"),
    "S8b": ("LEFT-NOTE-CONTENT-L", "RIGHT-NOTE-CONTENT-R"),
}


def _p(sample_file):
    return str(SAMPLES / sample_file)


def run_plan():
    """(步骤名, 路径列表, source_names)——每步独立提交，终态才可逐样本观测。"""
    s8a_first, s8a_second = _p("s8a_with_source/first.md"), _p("s8a_with_source/second.md")
    s8b_left = _p("s8b_no_source/left/shared_note.md")
    s8b_right = _p("s8b_no_source/right/shared_note.md")
    return [
        ("S1", [_p("s1_structure.md")], "samples/s1_structure.md"),
        ("S2", [_p("s2_plain_text.txt")], "samples/s2_plain_text.txt"),
        # PDF 走无 source_names 提交：带标识时 slug 与转换器 stem 输出不一致，必然
        # FileNotFoundError（行为表记录的管道缺陷，T3 处置）
        ("S3", [_p("s3_pdf_text_layer.pdf")], None),
        ("S4", [_p("s4_pdf_multipage.pdf")], None),
        ("S5a", [_p("s5a_empty.md")], "samples/s5a_empty.md"),
        ("S5b", [_p("s5b_whitespace.txt")], "samples/s5b_whitespace.txt"),
        ("S7#1", [_p("s7_duplicate.md")], "samples/s7_duplicate.md"),
        ("S7#2", [_p("s7_duplicate.md")], "samples/s7_duplicate.md"),
        ("S8a#1", [s8a_first], "samples/s8a/shared.md"),
        ("S8a#2", [s8a_second], "samples/s8a/shared.md"),
        ("S8b#1", [s8b_left], None),
        ("S8b#2", [s8b_right], None),
        ("S9", [_p("s9_unsupported.docx")], "samples/s9_unsupported.docx"),
        ("S10", [_p("s10_gbk.txt")], "samples/s10_gbk.txt"),
    ]


def classify(added, skipped, captured):
    if added == 1:
        return "ok"
    if skipped == 1:
        reason = " ".join(captured.split())[:120] or "skipped（无任何输出）"
        return f"failed:{reason}"
    return "silent_drop（后缀过滤剔除，added/skipped 均不计）"


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    vector_db = VectorDbManager()
    parent_store = ParentStoreManager()
    dm = DocumentManager(DocumentChunker(), parent_store, vector_db, config.CHILD_COLLECTION)
    print("[clear] 重建 scratch collection 与目录...")
    dm.clear_all()

    steps = []
    for name, paths, source in run_plan():
        buf = io.StringIO()
        with redirect_stdout(buf):
            added, skipped = dm.add_documents(paths, source_names={paths[0]: source} if source else None)
        state = classify(added, skipped, buf.getvalue())
        steps.append({
            "step": name, "file": Path(paths[0]).name, "added": added, "skipped": skipped,
            "state": state, "print": buf.getvalue().strip()[:400],
        })
        print(f"  {name:<7} {Path(paths[0]).name:<28} -> {state}")

    # ---- store 侧证据 ----
    md_files = sorted(p.name for p in Path(config.MARKDOWN_DIR).glob("*.md"))
    sources = parent_store.list_sources()
    parent_files = sorted(p.name for p in Path(config.PARENT_STORE_PATH).glob("*.json"))
    store_text = "\n".join(p.read_text(encoding="utf-8") for p in Path(config.PARENT_STORE_PATH).glob("*.json"))

    client = vector_db.client
    points, offset = [], None
    while True:
        batch, offset = client.scroll(config.CHILD_COLLECTION, limit=200, offset=offset,
                                      with_payload=True, with_vectors=False)
        points.extend(batch)
        if offset is None:
            break

    fingerprints = {
        pair: {"winner_present": win in store_text, "loser_present": lose in store_text}
        for pair, (win, lose) in FINGERPRINTS.items()
    }

    checks = []
    checks.append(("正样全部 ok（当前实现的行为底线）",
                   all(s["state"] == "ok" for s in steps if s["step"] in MUST_OK)))
    checks.append(("批量计数一致：sum(added)+sum(skipped) == 逐样本终态可计数行",
                   sum(s["added"] for s in steps) + sum(s["skipped"] for s in steps) == len(steps) - 1))  # S9 两计数皆 0
    checks.append(("store source 列表 == 正样来源集（7 项）", len(sources) == len(MUST_OK)))
    for pair, fp in fingerprints.items():
        checks.append((f"{pair} 冲突对：胜者入库、败者无痕迹",
                       fp["winner_present"] and not fp["loser_present"]))

    report = {
        "kind": "ingest_samples_level_b",
        "note": "S6（无文本层 PDF）随 T2 补入",
        "steps": steps,
        "store": {
            "markdown_docs": md_files,
            "sources": sources,
            "parent_files": len(parent_files),
            "child_points": len(points),
            "fingerprints": fingerprints,
        },
        "checks": [{"check": c, "pass": ok} for c, ok in checks],
    }
    (ARTIFACTS / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[store] markdown_docs:", md_files)
    print("[store] sources:", sources)
    print(f"[store] parents={len(parent_files)} child_points={len(points)}")
    print("[store] fingerprints:", json.dumps(fingerprints, ensure_ascii=False))
    print()
    failed = [c for c, ok in checks if not ok]
    for c, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {c}")
    print(f"\n[report] {ARTIFACTS / 'report.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
