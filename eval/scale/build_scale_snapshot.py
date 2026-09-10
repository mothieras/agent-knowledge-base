"""规模实测·合成语料快照构建：生成 → 入库 → 快照 manifest → 查询集。

受控合成规模（PLAN 收尾一步）：约 100 文档 / 1 万 child chunks，与质量语料
完全隔离——语料、parent store、Qdrant 全部落在 eval/scale_artifacts/ 下，
通过 QDRANT_DB_PATH / PARENT_STORE_PATH 环境变量在进程启动前覆盖（见
src/config.py），不触碰质量索引。

合成语料确定性生成（固定 seed），查询集从语料真实句子采样（命中型）+ 站外
干扰句（未命中型），供 run_scale_load.py 压测。内容为 RAG 邻域词模板句，
只用于规模/并发实测，不进入任何质量评测。

运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python scale/build_scale_snapshot.py
产出: eval/scale_artifacts/{corpus/, qdrant_db/, parent_store/, scale_manifest.json,
      queries.json, build_meta.json}
"""
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVAL_DIR.parent
ARTIFACTS = EVAL_DIR / "scale_artifacts"
CORPUS_DIR = ARTIFACTS / "corpus"
SEED = 20260910
N_DOCS = 100
SECTIONS_PER_DOC = 12
SECTION_TARGET_CHARS = 3400   # 每 H2 节目标字符数：落入 parent 2000-4000 区间
N_HIT_QUERIES = 84
N_MISS_QUERIES = 21

# 在 import config 之前覆盖数据目录（config 在模块导入时读取环境变量）
os.environ["QDRANT_DB_PATH"] = str(ARTIFACTS / "qdrant_db")
os.environ["PARENT_STORE_PATH"] = str(ARTIFACTS / "parent_store")
os.environ.pop("HF_ENDPOINT", None)
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import config  # noqa: E402
from db.parent_store_manager import ParentStoreManager  # noqa: E402
from db.vector_db_manager import VectorDbManager  # noqa: E402
from document_chunker import DocumentChunker  # noqa: E402
from core.document_manager import DocumentManager  # noqa: E402
from ingest_corpus import _child_store_hash, _parent_store_hash, _source_sha256  # noqa: E402

SUBJECTS = ["检索增强", "向量索引", "文本分块", "混合检索", "查询改写", "重排序", "上下文压缩",
            "证据引用", "召回评测", "知识库治理", "文档解析", "嵌入模型"]
VERBS = ["决定了", "依赖于", "影响着", "约束着", "反作用于", "支撑起", "校准着", "界定着"]
OBJECTS = ["召回率的上下限", "延迟分布的尾部", "证据链的可信度", "索引体积的增长",
           "检索结果的稳定性", "生成答案的可核验性", "离线评测的可复现性", "并发吞吐的饱和点"]
QUALIFIERS = ["在受控语料上", "在固定预算下", "在单次请求语义内", "在快照一致的前提下",
              "在混合稀疏通道下", "在父块窗口边界内", "在阈值过滤生效后", "在引用校验通过后"]


def _sentence(rng: random.Random, doc_idx: int, sec_idx: int, sent_idx: int) -> str:
    rng_local = random.Random(f"{SEED}:{doc_idx}:{sec_idx}:{sent_idx}")  # str 种子跨进程确定
    parts = [
        f"{rng_local.choice(SUBJECTS)}的{rng_local.choice(['参数', '策略', '约束', '配置'])}"
        f"{rng_local.choice(VERBS)}{rng_local.choice(OBJECTS)}",
        f"{rng_local.choice(QUALIFIERS)}，该结论记为命题 S{doc_idx:03d}-{sec_idx:02d}-{sent_idx:02d}",
        f"量纲-{'%03d' % doc_idx} 调校参数 K{sec_idx:02d}-{rng_local.randint(100, 999)}"
        f"与样本数 {rng_local.randint(64, 4096)} 的组合",
    ]
    rng_local.shuffle(parts)
    return "，".join(parts) + "。"


def _section_body(rng: random.Random, doc_idx: int, sec_idx: int) -> str:
    out = []
    n = 0
    while n < SECTION_TARGET_CHARS:
        s = _sentence(rng, doc_idx, sec_idx, n // 120)
        out.append(s)
        n += len(s)
    return "".join(out)


def generate_corpus() -> tuple[list[str], list[dict]]:
    """确定性生成 N_DOCS 篇 Markdown：H1 + 12 个 H2 节。返回 (doc dicts, 全部句子)。"""
    rng = random.Random(SEED)
    docs, all_sentences = [], []
    for doc_idx in range(N_DOCS):
        lines = [f"# 规模实测合成文档 {doc_idx:03d}：{rng.choice(SUBJECTS)}专题",
                 "", f"主题词：量程-量纲-{doc_idx:03d}。", ""]
        for sec_idx in range(1, SECTIONS_PER_DOC + 1):
            body = _section_body(rng, doc_idx, sec_idx)
            lines += [f"## 第{sec_idx:02d}节 {rng.choice(SUBJECTS)}{rng.choice(OBJECTS)}的量纲-{'%03d' % doc_idx}分析",
                      "", body, ""]
            for piece in body.split("。"):
                piece = piece.strip()
                if len(piece) >= 20:
                    all_sentences.append(piece + "。")
        docs.append({"name": f"scale_doc_{doc_idx:03d}.md", "text": "\n".join(lines)})
    return docs, all_sentences


def write_corpus(docs: list[dict]) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        (CORPUS_DIR / doc["name"]).write_text(doc["text"], encoding="utf-8")


def build_queries(all_sentences: list[str]) -> list[dict]:
    """命中型查询 = 语料真实句子；未命中型 = 语料外句子（仍应为合法响应）。"""
    rng = random.Random(SEED + 1)
    hits = rng.sample(all_sentences, min(N_HIT_QUERIES, len(all_sentences)))
    miss_pool = [
        "红烧肉的火候如何掌握，肥瘦比例多少合适。",
        "从成都出发自驾川西的路线有哪些推荐的垭口。",
        "小区业委会换届投票的法定程序和公示要求是什么。",
        "手冲咖啡的水温和研磨度对萃取率的影响如何量化。",
    ]
    misses = [miss_pool[i % len(miss_pool)] + f"（干扰句 {i:02d}）" for i in range(N_MISS_QUERIES)]
    queries = [{"id": f"q{i:03d}", "kind": "hit", "query": s} for i, s in enumerate(hits)]
    queries += [{"id": f"q{len(hits) + i:03d}", "kind": "miss", "query": s}
                for i, s in enumerate(misses)]
    return queries


def main() -> int:
    t0 = time.time()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print(f"[gen] 生成 {N_DOCS} 篇合成文档（seed={SEED}）...")
    docs, all_sentences = generate_corpus()
    write_corpus(docs)
    total_chars = sum(len(d["text"]) for d in docs)
    print(f"[gen] 语料 {total_chars / 1e6:.2f} MB，句子 {len(all_sentences)} 条")

    # 合成语料 manifest（绑定用）：文件名 → sha256
    scale_manifest = {
        "kind": "scale_test_synthetic",
        "seed": SEED,
        "n_docs": len(docs),
        "documents": [{"file": d["name"], "sha256": hashlib.sha256(d["text"].encode("utf-8")).hexdigest()}
                      for d in docs],
    }
    scale_manifest_path = ARTIFACTS / "scale_manifest.json"
    scale_manifest_path.write_text(json.dumps(scale_manifest, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    paths = [str(CORPUS_DIR / d["name"]) for d in docs]
    source_names = {p: f"scale/{d['name']}" for p, d in zip(paths, docs)}
    doc_meta = {p: {"version": "scale-v1", "priority": 1, "topic": "scale_test",
                    "doc_type": "synthetic"} for p in paths}

    print("[ingest] 分块与入库（本机嵌入模型，可能需要数分钟）...")
    t_ingest = time.time()
    vector_db = VectorDbManager()
    parent_store = ParentStoreManager()
    chunker = DocumentChunker()
    dm = DocumentManager(chunker, parent_store, vector_db, config.CHILD_COLLECTION)
    dm.clear_all()
    added, skipped = dm.add_documents(paths, source_names=source_names, doc_meta=doc_meta)
    if added != len(paths) or skipped:
        print(f"错误: 入库不完整 added={added} skipped={skipped}", file=sys.stderr)
        return 1
    print(f"[ingest] added={added} 耗时 {time.time() - t_ingest:.1f}s")

    parent_count, parent_hash = _parent_store_hash()
    child_count, child_hash = _child_store_hash(vector_db.client, config.CHILD_COLLECTION)
    print(f"[chunks] parent={parent_count} child={child_count}")

    from db.snapshot import build_manifest
    manifest = build_manifest(
        corpus_manifest_sha256=hashlib.sha256(scale_manifest_path.read_bytes()).hexdigest(),
        fixtures_manifest_sha256=hashlib.sha256(b"no-fixtures").hexdigest(),
        source_content_sha256=_source_sha256(paths, source_names),
        normalizer="synthetic/markdown-generator-seed20260910",
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

    queries = build_queries(all_sentences)
    (ARTIFACTS / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "seed": SEED,
        "n_docs": len(docs),
        "corpus_chars": total_chars,
        "parent_count": parent_count,
        "child_count": child_count,
        "index_id": manifest["index_id"],
        "ingest_seconds": round(time.time() - t_ingest, 1),
        "dense_model": config.DENSE_MODEL,
        "sparse_model": config.SPARSE_MODEL,
        "n_queries": len(queries),
        "n_hit_queries": sum(1 for q in queries if q["kind"] == "hit"),
        "n_miss_queries": sum(1 for q in queries if q["kind"] == "miss"),
    }
    (ARTIFACTS / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {json.dumps(meta, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())