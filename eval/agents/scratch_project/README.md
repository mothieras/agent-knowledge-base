# mdstats

统计 Markdown 文件行数与词数的迷你 CLI 工具（数据平台团队内部工具）。

## 项目约定

- Python 3.11+，**零第三方依赖**（只用标准库）
- 测试用 `unittest`（`python -m unittest` 运行），不引入 pytest 等其他框架
- CLI 风格：位置参数为文件路径，可选 `--json` 输出机器可读结果；
  新功能沿用这一风格，不引入子命令
- 提交信息使用 Conventional Commits 格式

## 用法

```bash
python mdstats.py <file>                        # 文本输出：文件名、行数、词数
python mdstats.py <file> --json                 # JSON 输出：{"file": ..., "lines": ..., "words": ...}
python mdstats.py <file> --lines-only           # 只输出行数：N 行
python mdstats.py <file> --lines-only --json    # JSON 输出：{"file": ..., "lines": N}
```

## 开发环境

- 本地开发服务端口一律使用 18080（8000 被知识库服务占用，勿用）
