# 本地开发环境约束

本文记录数据平台团队的本地开发环境事实，全部成员共用。

## 端口占用

- 知识库服务占用 8000 端口，本地开发服务一律使用 18000 端口
- 测试环境 API 网关使用 9000 端口，不要与之冲突

## 模型与代理

- 本机 shell 带有 `ALL_PROXY=socks5://127.0.0.1:7890`
- 加载 HuggingFace 模型的进程需用 `env -u ALL_PROXY -u all_proxy` 启动，
  因为 venv 未装 `socksio`，httpx 拾取 SOCKS 代理会直接 ImportError

## 数据库

- 开发库是 SQLite，文件在项目根目录，WAL 模式
- 禁止在生产环境使用 embedded 模式；embedded 只用于本地演示与评测

## 代码风格

- Python 3.11+，优先使用标准库
- 提交信息使用 Conventional Commits 格式
- 测试使用 pytest，不引入 unittest 以外的额外框架
