"""T1 冒烟测试：无生成模型时验证检索，有生成模型时跑 rag + agentic 单次问答。

前提: 语料已由 ingest_corpus.py 入库（生成模型凭据可选）。
运行: cd src && uv run --python ../.venv/bin/python smoke_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# 模型下载走官方 huggingface.co + 本机 SOCKS 代理（hf-mirror 经代理不可达，勿用）
os.environ.pop("HF_ENDPOINT", None)

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from core.app_service import build_app_service
from schema.dto import AnswerRequest, SearchRequest

QUESTION = "什么是 HyDE？"

svc = build_app_service()
print(f"[ready] index_id={svc.index_id[:16]}… generation={'configured' if svc.llm_configured else 'disabled'}")

search = svc.retrieval.search(SearchRequest(query="HyDE 查询改写", k=3))
print(f"[search] returned_k={search.returned_k} truncated={search.truncated}")
for hit in search.hits[:3]:
    print(f"  - {hit.source} @ {hit.span.start}:{hit.span.end} ({hit.evidence_id})")

if not svc.llm_configured:
    print("[answer] 未配置生成模型，跳过问答（仅检索冒烟通过）")
    sys.exit(0)

for mode in ("rag", "agentic"):
    resp = svc.generation.invoke(AnswerRequest(message=QUESTION, mode=mode))
    print(f"[{mode}] decision={resp.decision} citations={len(resp.citations)} "
          f"tokens={resp.usage.input_tokens}/{resp.usage.output_tokens} "
          f"cost={resp.usage.estimated_cost_cny}")
    print(f"  answer: {resp.answer[:120]}{'…' if len(resp.answer) > 120 else ''}")
    for c in resp.citations:
        print(f"  cite {c.citation_id} -> {c.evidence_id} span={c.span.start}:{c.span.end} quote={c.quote[:40]!r}")
