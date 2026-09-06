import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

os.environ.pop("HF_ENDPOINT", None)  # 模型下载走官方 huggingface.co + 本机 SOCKS 代理（hf-mirror 经代理不可达，勿用）

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

import config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.service import router  # noqa: E402
from core.rag_system import RAGSystem  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic RAG Service", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


async def _lifespan(app: FastAPI):
    rag_system = RAGSystem()
    rag_system.initialize()
    app.state.rag_system = rag_system
    print(f"✓ RAGSystem ready (model={config.LLM_MODEL}, collection={config.CHILD_COLLECTION})")
    yield
    app.state.rag_system = None


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host=config.API_HOST, port=config.API_PORT)
