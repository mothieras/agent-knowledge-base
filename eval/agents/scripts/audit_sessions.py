"""T6 会话审计：从 Claude Code 转录 JSONL 统计工具调用与用户提问。

用法: python3 audit_sessions.py <project-dir-slug> [session-uuid ...]
"""
import json
import sys
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".claude" / "projects"


def audit_file(path: Path):
    tools = {}
    asks = 0
    mcp = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        content = msg.get("content") or []
        if isinstance(content, str):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                name = c.get("name", "?")
                tools[name] = tools.get(name, 0) + 1
                if name.startswith("mcp__"):
                    mcp[name] = mcp.get(name, 0) + 1
        if msg.get("role") == "user":
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    text = str(c.get("text", ""))
                    # 向用户提问 = 用户消息中带问号的回复（agent 的问题以 user role 收尾）
                    if "?" in text or "？" in text:
                        asks += 1
    return tools, mcp, asks


def main():
    slug = sys.argv[1]
    uuids = sys.argv[2:]
    proj = BASE / slug
    if not proj.exists():
        print(f"no dir: {proj}")
        return 1
    files = sorted(proj.glob("*.jsonl"))
    if uuids:
        files = [f for f in files if f.stem in uuids]
    for f in files:
        tools, mcp, asks = audit_file(f)
        print(f"{f.name[:12]}: tools={tools} asks={asks}")
        if mcp:
            print(f"   mcp: {mcp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
