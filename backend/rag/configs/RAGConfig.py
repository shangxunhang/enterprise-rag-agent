"""集中定义旧版 Mini-RAG 的分块、Embedding、检索、重排和 Milvus 默认参数。

这些常量服务历史脚本和兼容实现。当前配置驱动主链优先使用静态检索规范和插件
注册表；修改本文件仍会影响 ``init_mini_rag.py``、旧版 Index/Reader/Prompt 和部分
评测脚本，因此应把参数变更视为索引或实验版本变化，而不是普通运行时调参。
"""

from rag.configs.BaseConfig import MODEL_ROOT_DIR


# -----------------------------------------------------------------------------
# 1. 旧版扁平 Chunk 配置
# -----------------------------------------------------------------------------

# 默认目标块大小和相邻块重叠量；单位由 CHUNK_UNIT 指定。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# fixed 表示固定窗口分块，token 表示大小按 Token 估算。
CHUNK_STRATEGY = "fixed"
CHUNK_UNIT = "token"

# Tokenizer 只从本地模型目录加载，避免离线构建时隐式联网。
CHUNK_TOKENIZER_LOCAL_FILES_ONLY = True


# -----------------------------------------------------------------------------
# 2. Parent-Child Chunk 配置
# -----------------------------------------------------------------------------

# 父块用于生成上下文，尺寸更大以保留完整语义边界。
PARENT_CHUNK_SIZE = 1500
PARENT_CHUNK_OVERLAP = 150

# 子块用于向量匹配，尺寸更小以提高召回定位精度。
CHILD_CHUNK_SIZE = 500
CHILD_CHUNK_OVERLAP = 50

# 当前父块和子块都使用固定窗口策略，并共享同一计量单位。
PARENT_CHUNK_STRATEGY = "fixed"
CHILD_CHUNK_STRATEGY = "fixed"
PARENT_CHILD_CHUNK_UNIT = CHUNK_UNIT

# Chunk 协议版本和 Chunker 名称进入索引 Manifest/metadata，用于重建与兼容判断。
PARENT_CHUNK_VERSION = "parent_chunk_v1"
CHILD_CHUNK_VERSION = "child_chunk_v1"
PARENT_CHILD_CHUNKER_NAME = "fixed_parent_child_chunker_v1"


# -----------------------------------------------------------------------------
# 3. Embedding 模型
# -----------------------------------------------------------------------------

# 保留多个本地模型目录，当前默认使用 m3e-base。
EMBEDDING_MODEL_DIR = (
    MODEL_ROOT_DIR
    / "embedding"
    / "paraphrase-multilingual-MiniLM-L12-v2"
)
BGE_M3_MODEL_DIR = MODEL_ROOT_DIR / "embedding" / "bge-m3"
M3E_BASE_MODEL_DIR = MODEL_ROOT_DIR / "embedding" / "m3e-base"

# TextEmbedder 接收字符串路径；Chunk Tokenizer 默认与 Embedding 模型一致。
EMBEDDING_MODEL_NAME = str(M3E_BASE_MODEL_DIR)
CHUNK_TOKENIZER_MODEL_NAME = EMBEDDING_MODEL_NAME

# 默认在 CUDA 上批量编码；无 GPU 环境需要由调用方覆盖。
EMBEDDING_DEVICE = "cuda"
EMBEDDING_BATCH_SIZE = 32


# -----------------------------------------------------------------------------
# 4. 旧版检索与 Prompt
# -----------------------------------------------------------------------------

# 旧版单阶段 Retriever 最终返回的默认结果数。
TOP_K = 3

# Prompt Router 默认选择严格基于证据回答的模板。
PROMPT_TEMPLATE = "strict_qa"


# -----------------------------------------------------------------------------
# 5. Reranker
# -----------------------------------------------------------------------------

# 开启 Cross-Encoder 重排；先召回更多候选，再截断为最终上下文结果。
USE_RERANKER = True
RETRIEVAL_TOP_K = 10
FINAL_TOP_K = 3

# 默认本地 BGE Reranker 模型及运行设备。
RERANKER_MODEL_DIR = (
    MODEL_ROOT_DIR
    / "reranker"
    / "bge-reranker-v2-m3"
)
RERANKER_MODEL_NAME = str(RERANKER_MODEL_DIR)
RERANKER_DEVICE = "cuda"
RERANKER_BATCH_SIZE = 16


# -----------------------------------------------------------------------------
# 6. 旧版 Milvus Lite
# -----------------------------------------------------------------------------

# 扁平 Chunk 集合名称和向量字段协议。
MILVUS_COLLECTION_NAME = "rag_chunks"
MILVUS_DIM = 768
MILVUS_VECTOR_FIELD = "vector"
MILVUS_ID_FIELD = "id"

# Milvus 原始查询候选数；可与最终 TOP_K 不同。
MILVUS_TOP_K = 10


# -----------------------------------------------------------------------------
# 7. Parent-Child Retrieval P1
# -----------------------------------------------------------------------------

# P0 离线构建产物默认位置。路径相对项目根，脚本可显式覆盖。
PARENT_CHILD_MILVUS_DB_FILE = (
    "data/processed/vector_store/milvus_parent_child.db"
)
PARENT_CHILD_MILVUS_COLLECTION_NAME = "rag_child_chunks"
PARENT_CHUNKS_FILE = (
    "data/processed/parent_child_chunks/parent_chunks.jsonl"
)
CHILD_CHUNKS_FILE = (
    "data/processed/parent_child_chunks/child_chunks.jsonl"
)
PARENT_CHILD_VECTOR_INDEX_RECORD_V2_FILE = (
    "data/processed/vector_index_record/vector_index_record_v2.jsonl"
)

# 子块向量检索使用 COSINE，命中后回填父块作为上下文。
PARENT_CHILD_SEARCH_METRIC_TYPE = "COSINE"
PARENT_CHILD_RETRIEVAL_TOP_K = 5
PARENT_CHILD_DENSE_TOP_K = 10
PARENT_CHILD_CONTEXT_GRANULARITY = "parent"


# -----------------------------------------------------------------------------
# 8. Parent-Child Hybrid Retrieval P2
# -----------------------------------------------------------------------------

# P2 流程：Dense 子块召回 + BM25 子块召回 + RRF 融合 + 父块回填。
PARENT_CHILD_KEYWORD_TOP_K = 10
PARENT_CHILD_HYBRID_DENSE_TOP_K = 10
PARENT_CHILD_HYBRID_KEYWORD_TOP_K = 10
PARENT_CHILD_HYBRID_FINAL_TOP_K = 5

# RRF 常数越大，排名差异的影响越平缓。
PARENT_CHILD_RRF_K = 60

# 最终上下文按 parent_chunk_id 去重，避免同一父块因多个子块命中重复出现。
PARENT_CHILD_DEDUP_PARENT = True


# -----------------------------------------------------------------------------
# 9. P3 Rerank、Context Packing 与 Eval
# -----------------------------------------------------------------------------

# 历史 P3 配置显式覆盖同名 RERANKER_MODEL_NAME；保持原值以兼容已有脚本。
RERANKER_MODEL_NAME = (
    r"D:\models\huggingface\reranker\bge-reranker-v2-m3"
)
RERANKER_DEVICE = "cuda"
RERANKER_BATCH_SIZE = 16

# 重排父级上下文文本，并限制 Cross-Encoder 最大输入长度。
PARENT_CHILD_RERANK_TOP_K = 5
PARENT_CHILD_RERANK_TEXT_FIELD = "parent_text"
PARENT_CHILD_RERANK_MAX_LENGTH = 512
PARENT_CHILD_RERANK_LOCAL_FILES_ONLY = True

# Context Packer 同时限制总字符数和上下文项数。
PARENT_CHILD_MAX_CONTEXT_CHARS = 6000
PARENT_CHILD_MAX_CONTEXT_ITEMS = 3
PARENT_CHILD_CONTEXT_TEXT_FIELD = "text"

# 旧版离线评测使用的 top-k 窗口。
PARENT_CHILD_EVAL_TOP_K = 5
