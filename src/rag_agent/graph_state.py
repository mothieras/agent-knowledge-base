from typing import List, Annotated, Set
from langgraph.graph import MessagesState

from db.retrieval import RetrievalHit
from rag_agent.schemas import GenerationOutput


def accumulate_or_reset(existing: List[dict], new: List[dict]) -> List[dict]:
    if new and any(item.get('__reset__') for item in new):
        return []
    return existing + new

def set_union(a: Set[str], b: Set[str]) -> Set[str]:
    return a | b

def append_unique(existing: List[RetrievalHit], new: List[RetrievalHit]) -> List[RetrievalHit]:
    return list(dict.fromkeys(existing + new))


class State(MessagesState):
    """Main graph state（单次请求语义：无会话历史、无 pending/interrupt）。"""

    questionIsClear: bool = False
    originalQuery: str = ""
    rewrittenQuestions: List[str] = []
    agent_answers: Annotated[List[dict], accumulate_or_reset] = []
    # 单次终态（decision 协议）
    generation: GenerationOutput | None = None
    evidence_map: dict[str, RetrievalHit] = {}
    context_text: str = ""
    repair_feedback: str = ""
    decision: str = ""
    answer: str = ""
    clarification_question: str = ""
    limitations: List[str] = []
    citations: List[dict] = []


class AgentState(MessagesState):
    """Individual agent subgraph state."""

    question: str = ""
    question_index: int = 0
    context_summary: str = ""
    retrieval_keys: Annotated[Set[str], set_union] = set()
    retrieved_contexts: Annotated[List[RetrievalHit], append_unique] = []
    final_answer: str = ""
    agent_answers: List[dict] = []
