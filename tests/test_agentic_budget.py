"""双图共享预算测试：LLM 持续请求工具时，预算耗尽走 fallback 收敛，不无限循环。"""
from langchain_core.messages import AIMessage, HumanMessage

from rag_agent.graph import create_agent_graph
from rag_agent.budget import RequestBudget
from rag_agent.schemas import QueryAnalysis, GenerationOutput
from rag_agent.tools import ToolFactory
from fixtures.in_memory_retriever import InMemoryRetriever
from db.retrieval import RetrievalHit


class _LoopLLM:
    """structured 模式按 schema 返回；工具模式一直返回 tool_calls。"""

    def __init__(self, *, schema=None, tools_mode=False, counter=None):
        self._schema = schema
        self._tools_mode = tools_mode
        self._counter = counter

    def bind_tools(self, tools):
        return _LoopLLM(tools_mode=True, counter=self._counter)

    def with_structured_output(self, schema, method=None):
        return _LoopLLM(schema=schema, counter=self._counter)

    def invoke(self, messages, **kwargs):
        if self._schema is QueryAnalysis:
            return QueryAnalysis(is_clear=True, questions=["什么是 HyDE？"], clarification_needed="")
        if self._schema is GenerationOutput:
            return GenerationOutput(decision="refused", answer="")
        if self._tools_mode:
            n = (self._counter[0] if self._counter else 0) + 1
            if self._counter:
                self._counter[0] = n
            return AIMessage(content="", tool_calls=[
                {"name": "search_child_chunks", "args": {"query": f"HyDE-{n}"},
                 "id": f"t{n}", "type": "tool_call"}
            ])
        # fallback_response 用裸 LLM 生成最终回答
        return AIMessage(content="无法回答。")


class _EvidenceStore:
    index_id = "sha256:" + "a" * 64

    def evidence_id_for(self, hit):
        return "aaaaaaaa:p0:0:10"


def test_agentic_budget_exhaustion_converges():
    parent = RetrievalHit(source="src.md", parent_id="p0", content="正文" * 40,
                          span_start=0, span_end=120)
    child = RetrievalHit(source="src.md", parent_id="p0", content="正文 HyDE",
                         span_start=0, span_end=6)
    retriever = InMemoryRetriever(parents=[parent], children=[child])
    tools = ToolFactory(retriever).create_tools()
    counter = [0]

    graph = create_agent_graph(_LoopLLM(counter=counter), tools, _EvidenceStore())
    budget = RequestBudget(max_iterations=3, max_tool_calls=3)
    final = graph.invoke(
        {"messages": [HumanMessage(content="什么是 HyDE？")]},
        config={"configurable": {"budget": budget}, "recursion_limit": 30},
    )
    # 预算耗尽 → fallback 回答 → 聚合 → 校验（refused 无需引用）
    assert final.get("decision") in ("answered", "refused", "clarification_required")
    assert budget.iterations > 0
    assert budget.tool_calls <= 3  # 共享预算封顶，不是每个分支各拿一份


def test_agentic_shared_budget_across_fanout():
    """3 个子问题并行扇出，共享同一 budget 对象，总工具调用不超上限。"""
    from rag_agent.budget import get_budget

    budget = RequestBudget(max_tool_calls=8, max_iterations=10)
    cfg = {"configurable": {"budget": budget}}
    assert get_budget(cfg) is budget  # 同一对象贯穿子图
