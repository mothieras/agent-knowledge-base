import os

# --- Directory Configuration ---
_BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# FastEmbed 稀疏模型缓存：默认在系统临时目录（macOS 周期清理，容器内不存在），
# 钉到项目内稳定路径；显式设置过则尊重外部值
os.environ.setdefault("FASTEMBED_CACHE_PATH", os.path.join(_BASE_DIR, ".fastembed_cache"))

MARKDOWN_DIR = os.path.join(_BASE_DIR, "markdown_docs")
# 数据目录支持环境变量覆盖（规模实测/容器挂载用独立快照，不污染质量索引）
PARENT_STORE_PATH = os.environ.get("PARENT_STORE_PATH", os.path.join(_BASE_DIR, "parent_store"))
QDRANT_DB_PATH = os.environ.get("QDRANT_DB_PATH", os.path.join(_BASE_DIR, "qdrant_db"))

# --- Qdrant Configuration ---
CHILD_COLLECTION = "document_child_chunks"
SPARSE_VECTOR_NAME = "sparse"

# --- Model Configuration ---
DENSE_MODEL = "Qwen/Qwen3-Embedding-0.6B"
SPARSE_MODEL = "Qdrant/bm25"
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek-chat")
LLM_TEMPERATURE = 0

# --- Retrieval Configuration ---
RETRIEVAL_SCORE_THRESHOLD = 0.4
DEFAULT_RETRIEVAL_K = 7
CHILD_CHUNK_SEPARATOR = "\n\n<CHILD_CHUNK_BOUNDARY>\n\n"

# --- Agent Configuration ---
MAX_TOOL_CALLS = 8
MAX_ITERATIONS = 10
# 递归上限是工程安全网，不是预算：预算耗尽的最坏路径（10 迭代 × 压缩/校验
# 超步 + 修复循环）约 45-48 超步，紧贴 50 会间歇触发 GraphRecursionError
GRAPH_RECURSION_LIMIT = 100
BASE_TOKEN_THRESHOLD = 2000
TOKEN_GROWTH_FACTOR = 0.9

# --- Generation / Budget Configuration (DESIGN 3.2，2026-09-07 冻结) ---
MAX_SUBQUESTIONS = 3          # 双图最多拆解出的子问题数
MAX_CITATION_REPAIRS = 1      # 引用校验失败最多修复次数
LLM_MAX_RETRIES = 1           # 网络瞬时失败重试次数（ChatOpenAI max_retries）
LLM_MAX_TOKENS = 2000         # 生成输出 token 上限（记入实验配置）
LLM_REQUEST_TIMEOUT_S = 100   # 模型单次网络调用超时，不超过剩余请求预算
ANSWER_TOTAL_TIMEOUT_S = 120  # 单次问答请求总预算
RAG_CONTEXT_MAX_CHARS = 24000  # rag 单图可复现的上下文截断上限（字符）

# deepseek-chat 定价（元/百万 token），与 eval/metrics.py 保持同源
PRICE_IN_PER_M = 0.28
PRICE_OUT_PER_M = 1.68

# --- Terminal Execution Logging ---
EXECUTION_LOGGING_ENABLED = False
EXECUTION_LOG_MAX_CHARS = 1200
EXECUTION_LOG_USE_COLOR = True

# --- Text Splitter Configuration ---
CHILD_CHUNK_SIZE = 500
CHILD_CHUNK_OVERLAP = 100
MIN_PARENT_SIZE = 2000
MAX_PARENT_SIZE = 4000
HEADERS_TO_SPLIT_ON = [
    ("#", "H1"),
    ("##", "H2"),
    ("###", "H3")
]

# --- Langfuse Observability ---
LANGFUSE_ENABLED = os.environ.get("LANGFUSE_ENABLED", "false").lower() == "true"
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

# --- Service Configuration ---
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
