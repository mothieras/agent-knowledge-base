"""mdstats：统计 Markdown 文件行数与词数。

CLI 约定见 README：位置参数 + 可选 --json，零第三方依赖。
"""
import json
import sys


def stats(path):
    # utf-8-sig：有 BOM 则剥除，无 BOM 时与 utf-8 行为一致
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
    use_json = "--json" in argv[1:]
    lines_only = "--lines-only" in argv[1:]
    lines, words = stats(path)
    if lines_only and use_json:
        print(json.dumps({"file": path, "lines": lines}))
    elif lines_only:
        print(f"{lines} 行")
    elif use_json:
        print(json.dumps({"file": path, "lines": lines, "words": words}))
    else:
        print(f"{path}: {lines} 行, {words} 词")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
