"""T1 冒烟测试：ingest 一篇自有笔记 → 提一个问题 → 打印最终回答。

前提: project/.env 已配置 DEEPSEEK_API_KEY。
运行: cd project && uv run --python ../.venv/bin/python smoke_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# 模型下载走官方 huggingface.co + 本机 SOCKS 代理（hf-mirror 经代理不可达，勿用）
os.environ.pop("HF_ENDPOINT", None)

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from langchain_core.messages import HumanMessage
from core.rag_system import RAGSystem
from core.document_manager import DocumentManager

TEST_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw", "Redis-缓存三连.md")
QUESTION = "缓存穿透是什么？怎么解决？"

rs = RAGSystem()
rs.initialize()
dm = DocumentManager(rs)
added, skipped = dm.add_documents([TEST_DOC])
print(f"[ingest] added={added} skipped={skipped}")

state = rs.agent_graph.invoke(
    {"messages": [HumanMessage(content=QUESTION)]},
    config=rs.get_config(),
)

pending = rs.agent_graph.get_state(rs.get_config()).next
if pending:
    print(f"[interrupt] 图在节点挂起等待输入: {list(pending)}")

final = state["messages"][-1]
print(f"[answer] {final.content}")
