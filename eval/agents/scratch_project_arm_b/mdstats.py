"""mdstats：统计 Markdown 文件行数与词数。

CLI 约定见 README：位置参数 + 可选 --json，零第三方依赖。
"""
import json
import sys


def stats(path):
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    lines = text.splitlines()
    words = text.split()
    return len(lines), len(words)


def main(argv):
    if not argv:
        print("用法: python mdstats.py <file> [--json] [--lines-only]", file=sys.stderr)
        return 2
    path = argv[0]
    flags = argv[1:]
    use_json = "--json" in flags
    lines_only = "--lines-only" in flags
    lines, words = stats(path)
    if use_json:
        data = {"file": path, "lines": lines}
        if not lines_only:
            data["words"] = words
        print(json.dumps(data))
    elif lines_only:
        print(f"{lines} 行")
    else:
        print(f"{path}: {lines} 行, {words} 词")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
