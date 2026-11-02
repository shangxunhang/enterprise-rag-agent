"""集中定义 RAG 跨阶段 Schema 的默认版本和缺省值。

这些常量不是运行时调参，而是序列化协议的默认血缘。Reader、Chunker、Embedding、
Vector Store、Retriever 和 Reranker 写出记录时会引用它们；修改版本常量意味着下游
兼容性边界发生变化，应同步提供迁移器或重新构建索引。
"""

from datetime import datetime

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

# ``c7171b7`` 保留了使用企业父子分块/索引协议的调用方，却误删了它们引用的
# 版本常量和模板。以下定义是仍在生产 RAG 模块中使用的兼容契约，不是运行时配置。
DEFAULT_SOURCE_TYPE = "offline"
DEFAULT_INDEXED_GRANULARITY = "child"
DEFAULT_CLEANING_VERSION = "cleaning_v1.0"
DEFAULT_CHUNK_VERSION = "chunk_v1.0"
DEFAULT_PARENT_CHUNK_VERSION = "parent_chunk_v1.0"
DEFAULT_CHILD_CHUNK_VERSION = "child_chunk_v1.0"
DEFAULT_CHUNK_STRATEGY = "rule_based_by_doc_unit_order_v1"
DEFAULT_PARENT_CHUNK_STRATEGY = "small_to_big_parent_fixed_v1"
DEFAULT_CHILD_CHUNK_STRATEGY = "small_to_big_child_fixed_v1"


def current_time_str() -> str:
    """Return the historical serialized timestamp format."""

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


CHUNK_METADATA_TEMPLATE = {
    "source": None,
    "source_path": None,
    "doc_type": "unknown",
    "title": None,
    "chunk_index": None,
    "page": None,
    "section": None,
    "section_path": None,
    "created_at": None,
    "updated_at": None,
    "indexed_at": None,
    "version": None,
    "status": DEFAULT_STATUS,
    "is_latest": True,
    "department": None,
    "project_id": None,
    "project_name": None,
    "security_level": DEFAULT_SECURITY_LEVEL,
    "char_count": None,
    "token_count": None,
    "extra": {},
}

RETRIEVAL_RESULT_TEMPLATE = {
    "rank": None,
    "score": None,
    "rerank_score": None,
    "chunk_id": None,
    "doc_id": None,
    "text": "",
    "metadata": CHUNK_METADATA_TEMPLATE,
}

RETRIEVAL_RESULT_V2_TEMPLATE = {
    "schema_version": DEFAULT_RETRIEVAL_RESULT_V2_SCHEMA_VERSION,
    "rank": None,
    "score": None,
    "rerank_score": None,
    "chunk_id": None,
    "child_chunk_id": None,
    "parent_chunk_id": None,
    "doc_id": None,
    "matched_granularity": "child",
    "context_granularity": "parent",
    "child_text": "",
    "parent_text": "",
    "text": "",
    "title": None,
    "section": None,
    "page_start": None,
    "page_end": None,
    "child_index_in_parent": None,
    "source_type": DEFAULT_SOURCE_TYPE,
    "source_unit_ids": [],
    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "parent_chunk_version": DEFAULT_PARENT_CHUNK_VERSION,
    "child_chunk_version": DEFAULT_CHILD_CHUNK_VERSION,
    "embedding_model": None,
    "embedding_version": DEFAULT_EMBEDDING_VERSION,
    "index_name": None,
    "vector_db": DEFAULT_VECTOR_DB,
    "metadata": {},
    "extra": {},
}

VECTOR_INDEX_RECORD_V1_TEMPLATE = {
    "schema_version": DEFAULT_VECTOR_INDEX_RECORD_SCHEMA_VERSION,
    "chunk_id": None,
    "tenant_id": "default",
    "kb_id": "default",
    "file_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,
    "embedding_model": None,
    "embedding_dim": 0,
    "index_name": None,
    "vector_db": DEFAULT_VECTOR_DB,
    "title": None,
    "section": None,
    "page_start": None,
    "page_end": None,
    "source_unit_ids": [],
    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "chunk_version": DEFAULT_CHUNK_VERSION,
    "embedding_version": DEFAULT_EMBEDDING_VERSION,
    "created_at": None,
    "extra": {},
}

VECTOR_INDEX_RECORD_V2_TEMPLATE = {
    "schema_version": DEFAULT_VECTOR_INDEX_RECORD_V2_SCHEMA_VERSION,
    "vector_id": None,
    "indexed_chunk_id": None,
    "chunk_id": None,
    "child_chunk_id": None,
    "parent_chunk_id": None,
    "tenant_id": "default",
    "kb_id": "default",
    "file_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,
    "indexed_granularity": DEFAULT_INDEXED_GRANULARITY,
    "text_hash": None,
    "embedding_key": None,
    "embedding_model": None,
    "embedding_dim": 0,
    "embedding_version": DEFAULT_EMBEDDING_VERSION,
    "index_name": None,
    "index_version": DEFAULT_INDEX_VERSION,
    "vector_db": DEFAULT_VECTOR_DB,
    "title": None,
    "section": None,
    "section_level": None,
    "page_start": None,
    "page_end": None,
    "child_index_in_parent": None,
    "source_unit_ids": [],
    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "parent_chunk_version": DEFAULT_PARENT_CHUNK_VERSION,
    "child_chunk_version": DEFAULT_CHILD_CHUNK_VERSION,
    "is_active": True,
    "created_at": None,
    "updated_at": None,
    "deleted_at": None,
    "extra": {},
}
