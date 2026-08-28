import uuid
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import config
from db.vector_db_manager import VectorDbManager
from db.parent_store_manager import ParentStoreManager
from db.retrieval import QdrantRetriever, RetrievalHit
from document_chunker import DocumentChunker
from rag_agent.tools import ToolFactory
from rag_agent.graph import create_agent_graph
from core.observability import Observability

class RAGSystem:

    def __init__(self, collection_name=config.CHILD_COLLECTION):
        self.collection_name = collection_name
        self.vector_db = VectorDbManager()
        self.parent_store = ParentStoreManager()
        self.chunker = DocumentChunker()
        self.observability = Observability()
        # RetrievalHit lives in checkpointed state (AgentState.retrieved_contexts);
        # register it with the msgpack serde so it round-trips as a typed object
        # (not a dict) and survives strict mode / future PostgresSaver.
        self.checkpointer = InMemorySaver(
            serde=JsonPlusSerializer(allowed_msgpack_modules=[RetrievalHit])
        )
        self.agent_graph = None
        self.thread_id = str(uuid.uuid4())
        self.recursion_limit = config.GRAPH_RECURSION_LIMIT

    def initialize(self):
        self.vector_db.create_collection(self.collection_name)
        collection = self.vector_db.get_collection(self.collection_name)
        retriever = QdrantRetriever(collection, self.parent_store)

        llm = ChatOpenAI(
            model=config.LLM_MODEL,
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            temperature=config.LLM_TEMPERATURE,
        )
        tools = ToolFactory(retriever).create_tools()
        self.agent_graph = create_agent_graph(llm, tools, self.checkpointer)

    def get_config(self, thread_id=None, **configurable):
        configurable_fields = {"thread_id": thread_id or self.thread_id}
        configurable_fields.update(configurable)
        cfg = {"configurable": configurable_fields, "recursion_limit": self.recursion_limit}
        handler = self.observability.get_handler()
        if handler:
            cfg["callbacks"] = [handler]
        return cfg

    def reset_thread(self):
        try:
            self.agent_graph.checkpointer.delete_thread(self.thread_id)
        except Exception as e:
            print(f"Warning: Could not delete thread {self.thread_id}: {e}")
        self.thread_id = str(uuid.uuid4())
