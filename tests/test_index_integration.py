"""真实本地索引集成测试（ROADMAP M1 证据契约）。

前置：本机已运行 `project/ingest_corpus.py`（qdrant_db/ + parent_store/ 存在）。
fresh clone 无索引时整模块 skip。验证的是真实 embedding + 真实 Qdrant payload
→ RetrievalHit 的全链路 metadata 传播，不打桩。
"""
import os
from datetime import date
from pathlib import Path

import pytest

# 本机 shell 带 SOCKS 代理而 venv 无 socksio：HF embedding 加载前须摘掉，
# 与 ingest/eval 入口同一 gotcha（见 AGENTS.md）
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)
os.environ.pop("HF_ENDPOINT", None)

import config
from db.parent_store_manager import ParentStoreManager
from db.retrieval import QdrantRetriever
from db.vector_db_manager import VectorDbManager

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_READY = Path(config.QDRANT_DB_PATH).exists() and Path(config.PARENT_STORE_PATH).exists()

pytestmark = pytest.mark.skipif(
    not INDEX_READY, reason="本地索引未构建：先运行 cd project && python ingest_corpus.py"
)


@pytest.fixture(scope="module")
def retriever():
    vdb = VectorDbManager()
    collection = vdb.get_collection(config.CHILD_COLLECTION)
    return QdrantRetriever(collection, ParentStoreManager())


def test_corpus_hits_carry_full_provenance(retriever):
    hits = retriever.search("检索增强生成的基本流程", k=7)

    assert hits, "公开语料查询应有命中"
    for h in hits:
        assert h.source.startswith("all-in-rag/")
        assert h.version == "04b8ea2"
        assert h.retrieval_channel == "hybrid"
        assert h.chunk_id and h.chunk_id.startswith(f"{h.parent_id}_c")
        assert isinstance(h.span_start, int) and isinstance(h.span_end, int)
        assert 0 <= h.span_start < h.span_end


def test_span_slices_parent_content_exactly(retriever):
    hits = retriever.search("向量数据库的作用", k=7)
    assert hits

    for h in hits[:3]:
        parent = retriever.get_parent(h.parent_id)
        assert parent is not None
        # span 精确反查：父文本切片即 child 命中内容
        assert parent.content[h.span_start:h.span_end] == h.content
        assert parent.retrieval_channel == "parent_store"
        assert parent.chunk_id == parent.parent_id
        assert (parent.span_start, parent.span_end) == (0, len(parent.content))
        assert parent.version == h.version


def test_expired_fixture_version_metadata_propagates(retriever):
    # “限额临时下调百分之五十” 只出现在 v1（v2 已改为熔断机制），融合分远高于阈值
    hits = retriever.search("限额临时下调百分之五十", k=7)
    v1 = [h for h in hits if h.source == "fixtures/api_rate_limit_policy_v1.md"]
    assert v1, "v1 专属措辞应命中过期版本"
    for h in v1:
        assert h.version == "v1"
        assert h.effective_date == date(2026, 1, 1)
        assert h.expired_date == date(2026, 6, 30)
        assert h.priority == 10


def test_active_fixture_version_metadata_propagates(retriever):
    # “熔断 10 分钟” 只出现在 v2
    hits = retriever.search("星辰科技 限流 熔断 10 分钟", k=7)
    v2 = [h for h in hits if h.source == "fixtures/api_rate_limit_policy_v2.md"]
    assert v2, "v2 专属措辞应命中当前有效版本"
    for h in v2:
        assert h.version == "v2"
        assert h.effective_date == date(2026, 7, 1)
        assert h.expired_date is None
        assert h.priority == 20

    # 数据保留政策对：审计日志 365 天是 v2 独有数值
    hits = retriever.search("星辰科技 审计日志保留 365 天", k=7)
    retention_v2 = [h for h in hits if h.source == "fixtures/data_retention_policy_v2.md"]
    assert retention_v2
    for h in retention_v2:
        assert h.version == "v2"
        assert h.priority == 20
