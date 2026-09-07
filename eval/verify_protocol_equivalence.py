"""Day 2 协议等价性验证：同一查询经 HTTP /search 与 MCP search_knowledge
返回的裁剪后公共结果必须逐字段一致（DESIGN 5：HTTP/MCP 共用同一预算与结果）。

用法（服务运行中）: cd eval && ../.venv/bin/python verify_protocol_equivalence.py
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

QUERIES = [
    ("RAG 的核心定义是什么？", 3),
    ("向量数据库的作用", 5),
    ("星辰科技开放平台当前有效的免费层分钟限额是多少？", 7),
    ("知识图谱与 RAG 结合有什么优势", 4),
]


def http_search(query: str, k: int) -> dict:
    resp = httpx.post("http://localhost:8000/search", json={"query": query, "k": k}, timeout=60)
    resp.raise_for_status()
    return resp.json()


async def mcp_search(query: str, k: int) -> dict:
    from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters

    group = ClientSessionGroup()
    async with group:
        await group.connect_to_server(StreamableHttpParameters(url="http://localhost:8000/mcp"))
        result = await group.call_tool("search_knowledge", {"query": query, "k": k})
        return result.structured_content


def canonical(resp: dict) -> dict:
    """裁剪后公共结果（debug_artifact 属诊断通道，不参与等价比较）。"""
    return {
        "requested_k": resp["requested_k"],
        "returned_k": resp["returned_k"],
        "truncated": resp["truncated"],
        "hits": [
            {
                "evidence_id": h["evidence_id"],
                "source": h["source"],
                "version": h["version"],
                "span": h["span"],
                "content": h["content"],
                "content_hash": h["content_hash"],
                "score": h.get("score"),
            }
            for h in resp["hits"]
        ],
    }


def main():
    failures = 0
    for query, k in QUERIES:
        http_resp = http_search(query, k)
        mcp_resp = asyncio.run(mcp_search(query, k))
        http_c = canonical(http_resp)
        mcp_c = canonical(mcp_resp)
        same = http_c == mcp_c
        print(f"[{'PASS' if same else 'FAIL'}] {query[:40]} (k={k})")
        if not same:
            failures += 1
            h1 = json.dumps(http_c, ensure_ascii=False, sort_keys=True)
            h2 = json.dumps(mcp_c, ensure_ascii=False, sort_keys=True)
            print(f"  http: {hashlib.sha256(h1.encode()).hexdigest()[:16]}")
            print(f"  mcp : {hashlib.sha256(h2.encode()).hexdigest()[:16]}")
    print(f"\n等价性: {len(QUERIES) - failures}/{len(QUERIES)} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
