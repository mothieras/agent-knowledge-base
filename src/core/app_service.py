"""L2 应用组装：bootstrap（检索先行、生成可选）与运行生命周期。

检索资源（Qdrant + parent store + 快照校验）就绪即可 ready；生成资源只在
LLM_API_KEY 显式存在时构建。启动顺序与关闭见 DESIGN 3.1。

并发槽：最多 MAX_CONCURRENT_QUERIES 个在途应用查询，超出返回 busy 错误
（不排队、不无界等待）。槽位由嵌入在槽内的实际执行释放。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import config
from db.evidence_store import EvidenceStore
from db.parent_store_manager import ParentStoreManager
from db.retrieval import QdrantRetriever
from db.snapshot import Snapshot, verify_snapshot
from db.vector_db_manager import VectorDbManager
from core.retrieval_service import RetrievalService, ServiceError

MAX_CONCURRENT_QUERIES = 3
SLOT_TIMEOUT_S = 30.0


class AppService:
    """composition root：装配并持有 L0/L1 资源与 L2 服务。

    ``retrieval_service`` 始终可用（否则不 ready）；``invoke``/``stream`` 属
    可选生成能力，未配置模型时抛 llm_not_configured。
    """

    def __init__(self, *, snapshot: Snapshot, retrieval_service: RetrievalService,
                 generation=None, entry_service=None, doc_service=None):
        self.snapshot = snapshot
        self.retrieval = retrieval_service
        self.generation = generation
        self.entries = entry_service
        self.documents = doc_service
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

    @property
    def index_id(self) -> str:
        return self.snapshot.index_id

    @property
    def llm_configured(self) -> bool:
        return self.generation is not None

    @asynccontextmanager
    async def acquire_slot(self):
        """申请一个在途查询槽位；没有可用槽位时明确报 busy。"""
        acquired = False
        try:
            async with asyncio.timeout(SLOT_TIMEOUT_S):
                acquired = await self._slots.acquire()
        except TimeoutError:
            raise ServiceError("busy", "并发查询已满，请稍后重试") from None
        if not acquired:
            raise ServiceError("busy", "并发查询已满，请稍后重试")
        try:
            yield
        finally:
            self._slots.release()

    def metadata(self) -> dict:
        return {
            "index_id": self.index_id,
            "corpus_manifest_sha256": self.snapshot.corpus_manifest_sha256,
            "collection": config.CHILD_COLLECTION,
            "dense_model": self.snapshot.dense_model,
            "sparse_model": self.snapshot.sparse_model,
            "generation": self.generation.metadata() if self.generation else None,
            "modes": list(self.generation.modes()) if self.generation else [],
        }


def _load_snapshot_manifest():
    from pathlib import Path

    path = Path(config.QDRANT_DB_PATH) / "snapshot_manifest.json"
    if not path.exists():
        raise RuntimeError(
            "快照 manifest 不存在。先运行: cd src && uv run --python ../.venv/bin/python ingest_corpus.py"
        )
    from db.snapshot import load_snapshot_manifest
    return load_snapshot_manifest(path)


def _source_uris_from_manifests() -> dict[str, str]:
    """{source: source_url}，来自语料 manifest（含 fixtures）。"""
    import json
    from pathlib import Path

    uris = {}
    root = Path(__file__).resolve().parent.parent.parent
    for manifest_path in (root / "data" / "manifest.json",
                          root / "data" / "fixtures" / "manifest.json"):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for doc in data.get("documents", []):
            if doc.get("source") and doc.get("source_url"):
                uris[doc["source"]] = doc["source_url"]
    return uris


def build_app_service(*, snapshot_path=None, vector_db=None, parent_store=None,
                      build_generation=True) -> AppService:
    """装配完整检索运行时；生成能力可选（未配置 LLM_API_KEY 时仅检索）。"""
    manifest = _load_snapshot_manifest() if snapshot_path is None else snapshot_path
    vdb = vector_db or VectorDbManager()
    collection = vdb.get_collection(config.CHILD_COLLECTION)
    store = parent_store or ParentStoreManager()
    retriever = QdrantRetriever(collection, store)

    # embedded 单进程只允许一个 client：复用 VectorDbManager 已持有的实例
    client = vdb.client
    snapshot = verify_snapshot(manifest, parent_dir=config.PARENT_STORE_PATH,
                               qdrant_client=client, collection=config.CHILD_COLLECTION)

    evidence = EvidenceStore(store, snapshot)
    source_uris = _source_uris_from_manifests()
    retrieval_service = RetrievalService(retriever, evidence, source_uris=source_uris)

    # 条目服务（PHASE2 §6.1/6.2）：独立 SQLite 文件，不触碰 Qdrant/语料快照
    from db.doc_store import DocStore
    from db.entry_store import EntryStore
    from core.doc_service import DocService
    from core.entry_service import EntryService

    # 文档注册表（D6/PHASE2-T67 §4.3）：source.document 机械校验与来源回查
    doc_store = DocStore(config.DOCS_DB_PATH)
    doc_service = DocService(doc_store)
    entry_service = EntryService(EntryStore(config.ENTRIES_DB_PATH), doc_store=doc_store)

    generation = None
    if build_generation and config.LLM_API_KEY:
        from langchain_openai import ChatOpenAI

        from core.answer_service import AnswerService

        llm = ChatOpenAI(
            model=config.LLM_MODEL,
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            temperature=config.LLM_TEMPERATURE,
            max_retries=config.LLM_MAX_RETRIES,
            timeout=config.LLM_REQUEST_TIMEOUT_S,
            max_tokens=config.LLM_MAX_TOKENS,
        )
        generation = AnswerService(llm, retriever, evidence, source_uris)

    return AppService(snapshot=snapshot, retrieval_service=retrieval_service,
                      generation=generation, entry_service=entry_service,
                      doc_service=doc_service)
