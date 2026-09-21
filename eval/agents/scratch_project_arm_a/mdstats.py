"""mdstats：统计 Markdown 文件行数与词数。

CLI 约定见 README：位置参数 + 可选 --json / --lines-only，零第三方依赖。
"""
import json
import sys


def read_text(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def stats(path):
    text = read_text(path)
    lines = text.splitlines()
    words = text.split()
    return len(lines), len(words)


def main(argv):
    if not argv:
        print("用法: python mdstats.py <file> [--json] [--lines-only]", file=sys.stderr)
        return 2
    path = argv[0]
    use_json = "--json" in argv[1:]
    lines_only = "--lines-only" in argv[1:]
    lines, words = stats(path)
    if use_json:
        data = {"file": path, "lines": lines}
        if not lines_only:
            data["words"] = words
        print(json.dumps(data))
    elif lines_only:
        print(f"{path}: {lines} 行")
    else:
        print(f"{path}: {lines} 行, {words} 词")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
