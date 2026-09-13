"""embedding 并发串行化回归（MPS 后端并发 encode 会 OOM/死锁，见 vector_db_manager）。

修复前 Darwin/MPS 上并发 embed_query 挂死或崩溃；本测试用有限超时把死锁
转成显式失败。仅验证并发安全，不依赖本地索引。
"""
import os
from concurrent.futures import ThreadPoolExecutor

# 本机 SOCKS 代理 gotcha：HF embedding 加载前须摘掉（见 AGENTS.md）
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)
os.environ.pop("HF_ENDPOINT", None)

import config
from db.vector_db_manager import _SerializedEmbeddings


def test_concurrent_embed_queries_serialize():
    emb = _SerializedEmbeddings(model_name=config.DENSE_MODEL)
    queries = [
        "什么是 RAG？",
        "HyDE 是什么？",
        "混合检索融合策略",
        "评估框架对比",
        "文档分块策略",
        "元数据版本过滤",
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(emb.embed_query, q) for q in queries * 3]
        vectors = [f.result(timeout=180) for f in futures]
    assert all(len(v) == len(vectors[0]) for v in vectors)
