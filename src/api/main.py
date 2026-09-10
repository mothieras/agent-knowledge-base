"""L3 HTTP 适配：路由、鉴权与 lifespan。只依赖 L2 公共接口与 schema.dto。

lifespan 同时管理 MCP session manager（DESIGN 3.1）：检索资源、可选问答资源
与 MCP 会话在同一生命周期内起停。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

os.environ.pop("HF_ENDPOINT", None)  # 模型下载走官方 huggingface.co + 本机 SOCKS 代理（hf-mirror 经代理不可达，勿用）

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

import config  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from api.mcp_app import build_mcp_asgi, create_mcp_server  # noqa: E402
from api.routes import router  # noqa: E402
from core.app_service import build_app_service  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic RAG Service", lifespan=_lifespan)
    app.include_router(router)
    return app


async def _lifespan(app: FastAPI):
    app_service = build_app_service()
    app.state.app_service = app_service

    # MCP 工具从 lifespan context 拿 app_service；mount 在 lifespan 外（create_app），
    # 会话由 session manager 在 lifespan 内运行（2.x 要求，否则握手 500）。
    # ask_knowledge 仅生成能力启用时发布（DESIGN 6.2）。
    mcp_server = create_mcp_server(include_ask=app_service.llm_configured)
    app.mount("/mcp", build_mcp_asgi(mcp_server))
    async with mcp_server.session_manager.run():
        print(
            f"✓ 检索就绪 (index={app_service.index_id[:16]}…, points={app_service.snapshot.child_count}), "
            f"生成模型={'已配置' if app_service.llm_configured else '未配置（仅检索）'}"
        )
        yield
    app.state.app_service = None


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host=config.API_HOST, port=config.API_PORT)
