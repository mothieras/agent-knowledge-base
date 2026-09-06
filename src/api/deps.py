from fastapi import HTTPException, Request

from core.rag_system import RAGSystem


def get_rag_system(request: Request) -> RAGSystem:
    rag_system = getattr(request.app.state, "rag_system", None)
    if rag_system is None or rag_system.agent_graph is None:
        raise HTTPException(status_code=503, detail="RAGSystem not initialized")
    return rag_system
