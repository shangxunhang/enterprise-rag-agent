"""集中定义 RAG 跨阶段 Schema 的默认版本和缺省值。

这些常量不是运行时调参，而是序列化协议的默认血缘。Reader、Chunker、Embedding、
Vector Store、Retriever 和 Reranker 写出记录时会引用它们；修改版本常量意味着下游
兼容性边界发生变化，应同步提供迁移器或重新构建索引。
"""

# 文档和分块协议版本。
DEFAULT_DOCUMENT_SCHEMA_VERSION = "document_v1"
DEFAULT_CHUNK_SCHEMA_VERSION = "chunk_v1"
DEFAULT_PARENT_CHUNK_SCHEMA_VERSION = "parent_chunk_v1"
DEFAULT_CHILD_CHUNK_SCHEMA_VERSION = "child_chunk_v1"

# 向量索引记录和检索结果协议版本。
DEFAULT_VECTOR_INDEX_RECORD_SCHEMA_VERSION = "vector_index_record_v1"
DEFAULT_VECTOR_INDEX_RECORD_V2_SCHEMA_VERSION = "vector_index_record_v2"
DEFAULT_RETRIEVAL_RESULT_SCHEMA_VERSION = "retrieval_result_v1"
DEFAULT_RETRIEVAL_RESULT_V2_SCHEMA_VERSION = "retrieval_result_v2"

# Reader / Cleaner 的默认实现版本。
DEFAULT_READER_NAME = "generic_reader"
DEFAULT_READER_VERSION = "reader_v1.0"
DEFAULT_CLEANER_NAME = "generic_cleaner"
DEFAULT_CLEANER_VERSION = "cleaner_v1.0"

# 普通 Chunker 与父子块 Chunker 的默认身份。
DEFAULT_CHUNKER_NAME = "fixed_size_chunker"
DEFAULT_CHUNKER_VERSION = "chunker_v1.0"
DEFAULT_PARENT_CHILD_CHUNKER_NAME = "fixed_parent_child_chunker_v1"
DEFAULT_PARENT_CHILD_CHUNKER_VERSION = "parent_child_chunker_v1.0"

# Embedding 组件和向量协议默认值。
DEFAULT_EMBEDDING_MODEL = "unknown_embedding_model"
DEFAULT_EMBEDDING_VERSION = "embedding_v1.0"
DEFAULT_EMBEDDING_DIM = None

# 向量数据库与索引默认身份。None 表示记录创建时尚未进入具体数据库。
DEFAULT_VECTOR_DB = None
DEFAULT_INDEX_NAME = None
DEFAULT_INDEX_VERSION = None

# 检索与重排默认组件身份。
DEFAULT_RETRIEVER_NAME = "generic_retriever"
DEFAULT_RETRIEVER_VERSION = "retriever_v1.0"
DEFAULT_RERANKER_NAME = "generic_reranker"
DEFAULT_RERANKER_VERSION = "reranker_v1.0"

# 通用记录默认业务状态和数据安全等级。
DEFAULT_STATUS = "active"
DEFAULT_SECURITY_LEVEL = "internal"

# 时间字段在未提供真实时间时保持 None；不使用导入时当前时间破坏可重复性。
DEFAULT_CREATED_AT = None
DEFAULT_UPDATED_AT = None

# Chunk 的默认逻辑层级和语言。父子块实现会显式覆盖 chunk_level。
DEFAULT_CHUNK_LEVEL = 0
DEFAULT_LANGUAGE = "zh"

# 检索结果中的分数类型缺省为 unknown，调用方必须根据具体 Retriever 解释数值方向。
DEFAULT_SCORE_TYPE = "unknown"
DEFAULT_RERANK_SCORE_TYPE = None

# 匹配粒度与上下文粒度默认适配普通扁平 Chunk；父子块检索会覆盖为 child/parent。
DEFAULT_MATCHED_GRANULARITY = "chunk"
DEFAULT_CONTEXT_GRANULARITY = "chunk"

# 检索来源列表默认为空，真正的 dense/keyword/rerank 来源由运行链追加。
DEFAULT_RETRIEVAL_SOURCES = []

# Schema 扩展字段必须通过 default_factory 创建；该常量只用于历史调用方只读兼容，
# 新 dataclass/Pydantic 字段不应直接把可变字典作为默认值。
DEFAULT_EXTRA = {}
