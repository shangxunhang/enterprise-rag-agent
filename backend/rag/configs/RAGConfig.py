from rag.configs.BaseConfig import (
    MODEL_ROOT_DIR,
)

# =========================
# 1. Flat Chunk Config  Chunk 参数
# =========================

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

CHUNK_STRATEGY = "fixed"
CHUNK_UNIT = "token"
CHUNK_TOKENIZER_LOCAL_FILES_ONLY = True


# ===== Parent-Child Chunk Config =====

PARENT_CHUNK_SIZE = 1500
PARENT_CHUNK_OVERLAP = 150

CHILD_CHUNK_SIZE = 500
CHILD_CHUNK_OVERLAP = 50

PARENT_CHUNK_STRATEGY = "fixed"
CHILD_CHUNK_STRATEGY = "fixed"

PARENT_CHILD_CHUNK_UNIT = CHUNK_UNIT

PARENT_CHUNK_VERSION = "parent_chunk_v1"
CHILD_CHUNK_VERSION = "child_chunk_v1"

PARENT_CHILD_CHUNKER_NAME = "fixed_parent_child_chunker_v1"



# =========================
# 2. Embedding 模型
# =========================

EMBEDDING_MODEL_DIR = (
    MODEL_ROOT_DIR
    / "embedding"
    / "paraphrase-multilingual-MiniLM-L12-v2"
)

BGE_M3_MODEL_DIR = MODEL_ROOT_DIR / "embedding" / "bge-m3"
M3E_BASE_MODEL_DIR = MODEL_ROOT_DIR / "embedding" / "m3e-base"

EMBEDDING_MODEL_NAME = str(M3E_BASE_MODEL_DIR)

CHUNK_TOKENIZER_MODEL_NAME = EMBEDDING_MODEL_NAME

EMBEDDING_DEVICE = "cuda"
EMBEDDING_BATCH_SIZE = 32

# =========================
# 3. 检索参数
# =========================

TOP_K = 3

# =========================
# 4. Prompt 模板
# =========================

PROMPT_TEMPLATE = "strict_qa"

# =========================
# 5. Reranker 配置
# =========================

USE_RERANKER = True

RETRIEVAL_TOP_K = 10
FINAL_TOP_K = 3

RERANKER_MODEL_DIR = (
    MODEL_ROOT_DIR
    / "reranker"
    / "bge-reranker-v2-m3"
)

RERANKER_MODEL_NAME = str(RERANKER_MODEL_DIR)
RERANKER_DEVICE = "cuda"
RERANKER_BATCH_SIZE = 16

# =========================
# 6. Milvus Lite 配置
# =========================

MILVUS_COLLECTION_NAME = "rag_chunks"

MILVUS_DIM = 768

MILVUS_VECTOR_FIELD = "vector"
MILVUS_ID_FIELD = "id"

MILVUS_TOP_K = 10

# =========================
# 7. Parent-Child Retrieval P1 配置
# =========================

# P0 产物默认路径。Windows / PyCharm 下也可以在脚本参数里覆盖。
PARENT_CHILD_MILVUS_DB_FILE = "data/processed/vector_store/milvus_parent_child.db"
PARENT_CHILD_MILVUS_COLLECTION_NAME = "rag_child_chunks"
PARENT_CHUNKS_FILE = "data/processed/parent_child_chunks/parent_chunks.jsonl"
CHILD_CHUNKS_FILE = "data/processed/parent_child_chunks/child_chunks.jsonl"
PARENT_CHILD_VECTOR_INDEX_RECORD_V2_FILE = "data/processed/vector_index_record/vector_index_record_v2.jsonl"

PARENT_CHILD_SEARCH_METRIC_TYPE = "COSINE"
PARENT_CHILD_RETRIEVAL_TOP_K = 5
PARENT_CHILD_DENSE_TOP_K = 10
PARENT_CHILD_CONTEXT_GRANULARITY = "parent"

# =========================
# 8. Parent-Child Hybrid Retrieval P2 配置
# =========================

# P2 = dense child retrieval + BM25 child retrieval + RRF fusion + parent backfill
PARENT_CHILD_KEYWORD_TOP_K = 10
PARENT_CHILD_HYBRID_DENSE_TOP_K = 10
PARENT_CHILD_HYBRID_KEYWORD_TOP_K = 10
PARENT_CHILD_HYBRID_FINAL_TOP_K = 5
PARENT_CHILD_RRF_K = 60
PARENT_CHILD_DEDUP_PARENT = True


# ===== P3 Rerank + Context Packing + Eval Config =====

RERANKER_MODEL_NAME = r"D:\models\huggingface\reranker\bge-reranker-v2-m3"
RERANKER_DEVICE = "cuda"
RERANKER_BATCH_SIZE = 16


PARENT_CHILD_RERANK_TOP_K = 5
PARENT_CHILD_RERANK_TEXT_FIELD = "parent_text"
PARENT_CHILD_RERANK_MAX_LENGTH = 512
PARENT_CHILD_RERANK_LOCAL_FILES_ONLY = True

PARENT_CHILD_MAX_CONTEXT_CHARS = 6000
PARENT_CHILD_MAX_CONTEXT_ITEMS = 3
PARENT_CHILD_CONTEXT_TEXT_FIELD = "text"

PARENT_CHILD_EVAL_TOP_K = 5

