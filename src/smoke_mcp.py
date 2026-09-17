"""MCP 协议冒烟：工具发现 → search_knowledge → get_context 回查 →（可选）ask_knowledge → 条目工具闭环。

验证发布清单的 MCP 侧：Streamable HTTP 握手、Bearer、工具 schema 发现、
结构化输出与证据回查闭环。服务端生成能力启用时追问一次 ask_knowledge。
条目工具（PHASE2 T2）走 save→get→revise→冲突错误通道→lifecycle→历史全链路。
客户端走 mcp 2.x ClientSessionGroup（与 verify_protocol_equivalence.py 同模式）。

运行: cd src && ../.venv/bin/python smoke_mcp.py [--url http://127.0.0.1:8000/mcp] [--token TOKEN] [--query "HyDE 查询改写"]
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid

from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    ap.add_argument("--token", default=os.environ.get("DEMO_API_TOKEN", ""))
    ap.add_argument("--query", default="HyDE 查询改写")
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else None
    ok = True

    group = ClientSessionGroup()
    async with group:
        await group.connect_to_server(
            StreamableHttpParameters(url=args.url, headers=headers, timeout=60.0))

        names = sorted(group.tools)  # {工具名: Tool}，connect 时完成发现
        print(f"[2] 工具发现: {names}")
        need = {"search_knowledge", "get_context"}
        if not need <= set(names):
            print(f"    失败: 缺少 {need - set(names)}")
            ok = False

        res = await group.call_tool("search_knowledge", {"query": args.query, "k": 3})
        hits = (res.structured_content or {}).get("hits", [])
        index_id = (res.structured_content or {}).get("index_id", "")
        print(f"[3] search_knowledge: returned_k={(res.structured_content or {}).get('returned_k')} "
              f"hits={len(hits)} index={index_id[:16]}…")
        if not hits:
            print("    失败: 零命中")
            ok = False

        if hits:
            ev = hits[0]
            content_ok = ev["content_hash"] == "sha256:" + hashlib.sha256(
                ev["content"].encode("utf-8")).hexdigest()
            print(f"    命中[0] {ev['source']} span={ev['span']['start']}:{ev['span']['end']} "
                  f"content_hash 自洽={content_ok}")

            win = await group.call_tool("get_context", {
                "evidence_id": ev["evidence_id"], "offset": 0, "limit": 2000})
            w = win.structured_content or {}
            win_ok = (w.get("index_id") == index_id
                      and w.get("content_hash") == "sha256:" + hashlib.sha256(
                          w.get("content", "").encode("utf-8")).hexdigest()
                      and ev["content"] in w.get("content", ""))
            print(f"[4] get_context: {w.get('source')} 窗口 {w.get('offset')}+"
                  f"{len(w.get('content', ''))}/{w.get('total_length')}，"
                  f"index 一致且可含命中原文={win_ok}")
            if not win_ok:
                ok = False

        if "ask_knowledge" in names:
            ans = await group.call_tool("ask_knowledge", {
                "message": "什么是 HyDE？", "mode": "rag"})
            if ans.is_error:
                print(f"[5] ask_knowledge: tool 报错 — {ans.content[0].text[:120]}")
                ok = False
            else:
                a = ans.structured_content or {}
                cites = a.get("citations", [])
                print(f"[5] ask_knowledge: decision={a.get('decision')} citations={len(cites)} "
                      f"usage={a.get('usage', {}).get('input_tokens')}/"
                      f"{a.get('usage', {}).get('output_tokens')} tok")
                if a.get("decision") not in ("answered", "refused", "clarification_required"):
                    ok = False
        else:
            print("[5] ask_knowledge 未发布（生成模型未配置），跳过")

        entry_names = {"save_entry", "get_entry", "revise_entry",
                       "entry_lifecycle", "list_entry_revisions"}
        if not entry_names <= set(names):
            print(f"[6] 条目工具: 失败，缺少 {entry_names - set(names)}")
            ok = False
        else:
            ok = await _smoke_entries(group, ok)

    print("[smoke] " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _error_code(result) -> str | None:
    """从 tool 错误文本载荷解析 code（可能带 SDK 前缀，取首个 JSON 对象）。"""
    text = result.content[0].text if result.content else ""
    start = text.find("{")
    try:
        return (json.loads(text[start:]) or {}).get("code") if start >= 0 else None
    except json.JSONDecodeError:
        return None


async def _smoke_entries(group, ok: bool) -> bool:
    """[6] 条目工具闭环：save → get → revise → 冲突通道 → lifecycle → 历史。"""
    key = f"smoke-{uuid.uuid4().hex[:12]}"
    saved = await group.call_tool("save_entry", {
        "type": "knowledge",
        "body": "MCP 冒烟：共享条目可写入并回读（自动化验证正文）。",
        "author": "smoke-mcp/1.0",
        "scope": {"kind": "projects", "projects": ["Smoke "]},
        "idempotency_key": key,
    })
    if saved.is_error:
        print(f"[6] save_entry: 报错 — {saved.content[0].text[:120]}")
        return False
    entry = saved.structured_content
    dual = json.loads(saved.content[0].text) == entry  # 双通道同载荷
    print(f"[6] save_entry: id={entry['id'][:18]}… rev={entry['revision']} "
          f"scope={entry['scope']['projects']} 双通道同载荷={dual}")
    ok = ok and dual and entry["scope"]["projects"] == ["smoke"]

    got = (await group.call_tool("get_entry", {"entry_id": entry["id"]})).structured_content
    ok = ok and got["id"] == entry["id"] and got["body"] == entry["body"]

    revised = await group.call_tool("revise_entry", {
        "entry_id": entry["id"], "expected_revision": 1, "author": "smoke-mcp/1.0",
        "updates": {"body": "MCP 冒烟：修订后的正文。", "expires_at": None},
    })
    ok = ok and revised.structured_content["revision"] == 2
    ok = ok and revised.structured_content["expires_at"] is None  # null=清除语义

    stale = await group.call_tool("revise_entry", {
        "entry_id": entry["id"], "expected_revision": 1, "author": "smoke-mcp/1.0",
        "updates": {"body": "应当冲突"},
    })
    conflict_ok = stale.is_error and _error_code(stale) == "revision_conflict"
    has_current = conflict_ok and "current" in (stale.content[0].text if stale.content else "")
    print(f"    冲突通道: code=revision_conflict={conflict_ok} 携带current={has_current}")
    ok = ok and conflict_ok and has_current

    archived = await group.call_tool("entry_lifecycle", {
        "entry_id": entry["id"], "op": "archive",
        "expected_revision": 2, "author": "smoke-mcp/1.0",
    })
    ok = ok and archived.structured_content["status"] == "archived"

    history = await group.call_tool("list_entry_revisions", {"entry_id": entry["id"]})
    ops = [h["op"] for h in history.structured_content["revisions"]]
    print(f"    闭环: get/revise/lifecycle OK，历史 ops={ops}")
    ok = ok and ops == ["create", "update", "archive"]
    return ok


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))