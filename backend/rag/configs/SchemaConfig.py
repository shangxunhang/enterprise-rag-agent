# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：current_time_str。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
configs/SchemaConfig.py
=======================

Mini-RAG 的数据结构配置文件。

职责：
1. 统一定义 Document Schema
2. 统一定义 Chunk Schema
3. 统一定义 Chunk Meta Schema
4. 统一定义 Retrieval Result Schema
5. 提供标准结构构造函数，避免 reader / chunker / retriever 中硬编码字段

注意：
- 这里只定义“数据结构”和“默认字段”
- 不负责读取文件
- 不负责清洗文本
- 不负责切 chunk
- 不负责向量化
"""

from datetime import datetime
from copy import deepcopy
from typing import Dict, Any, Optional


# =========================
# 1. 通用默认值
# =========================

DEFAULT_NONE = None
DEFAULT_DOC_TYPE = "unknown"
DEFAULT_TITLE = None
DEFAULT_PAGE = None
DEFAULT_SECTION = None
DEFAULT_VERSION = None
DEFAULT_STATUS = "active"
DEFAULT_LANGUAGE = "zh"
DEFAULT_SECURITY_LEVEL = "internal"


# 阅读注释（函数）：处理 current time str 相关逻辑。
def current_time_str() -> str:
    """
    返回当前时间字符串。

    Returns:
        当前时间，格式：YYYY-MM-DD HH:MM:SS
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =========================
# 2. Document Metadata 默认结构
# =========================

DOCUMENT_METADATA_TEMPLATE: Dict[str, Any] = {
    # 来源信息
    "source": None,              # 原始文件名 / 来源表 / 来源系统
    "source_path": None,         # 文件完整路径，可选
    "doc_type": DEFAULT_DOC_TYPE, # txt / json / jsonl / pdf / docx / csv / mysql

    # 文档描述信息
    "title": DEFAULT_TITLE,
    "author": None,
    "language": DEFAULT_LANGUAGE,

    # 时间信息
    "created_at": None,
    "updated_at": None,
    "indexed_at": None,

    # 版本治理
    "version": DEFAULT_VERSION,
    "status": DEFAULT_STATUS,     # active / archived / deleted
    "is_latest": True,

    # 企业场景扩展
    "department": None,
    "project_id": None,
    "project_name": None,
    "security_level": DEFAULT_SECURITY_LEVEL,

    # 预留字段
    "extra": {},
}


# =========================
# 3. Document 默认结构
# =========================

DOCUMENT_TEMPLATE: Dict[str, Any] = {
    "doc_id": None,
    "text": "",
    "metadata": DOCUMENT_METADATA_TEMPLATE,
}


# =========================
# 4. Chunk Metadata 默认结构
# =========================

CHUNK_METADATA_TEMPLATE: Dict[str, Any] = {
    # 来源信息，通常从 document.metadata 继承
    "source": None,
    "source_path": None,
    "doc_type": DEFAULT_DOC_TYPE,
    "title": DEFAULT_TITLE,

    # chunk 位置信息
    "chunk_index": None,
    "page": DEFAULT_PAGE,
    "section": DEFAULT_SECTION,
    "section_path": None,

    # 时间信息
    "created_at": None,
    "updated_at": None,
    "indexed_at": None,

    # 版本治理
    "version": DEFAULT_VERSION,
    "status": DEFAULT_STATUS,
    "is_latest": True,

    # 企业场景扩展
    "department": None,
    "project_id": None,
    "project_name": None,
    "security_level": DEFAULT_SECURITY_LEVEL,

    # chunk 统计信息
    "char_count": None,
    "token_count": None,

    # 预留字段
    "extra": {},
}


# =========================
# 5. Chunk 默认结构
# =========================

CHUNK_TEMPLATE: Dict[str, Any] = {
    "chunk_id": None,
    "doc_id": None,
    "text": "",
    "metadata": CHUNK_METADATA_TEMPLATE,
}


# =========================
# 6. Retrieval Result 默认结构
# =========================

RETRIEVAL_RESULT_TEMPLATE: Dict[str, Any] = {
    "rank": None,
    "score": None,
    "rerank_score": None,

    "chunk_id": None,
    "doc_id": None,
    "text": "",

    "metadata": CHUNK_METADATA_TEMPLATE,
}

# =========================================================
# 7. Enterprise Agent-RAG Schema Versions / Defaults
# =========================================================
# 说明：
# - 上面的 DOCUMENT_TEMPLATE / CHUNK_TEMPLATE 保留给 Mini-RAG 旧流程使用。
# - 下面这些模板用于企业级 Agent-RAG 数据链路。
# - SchemaConfig 只定义模板、版本号和默认值；具体构造逻辑放在 schema/*.py。

CLEANED_TEXT_UNIT_SCHEMA_VERSION = "cleaned_text_unit_v1"

CHUNK_SCHEMA_VERSION = "chunk_v1"
PARENT_CHUNK_SCHEMA_VERSION = "parent_chunk_v1"
CHILD_CHUNK_SCHEMA_VERSION = "child_chunk_v1"

VECTOR_INDEX_RECORD_SCHEMA_VERSION = "vector_index_record_v1"
VECTOR_INDEX_RECORD_V2_SCHEMA_VERSION = "vector_index_record_v2"

RETRIEVAL_RESULT_SCHEMA_VERSION = "retrieval_result_v1"
RETRIEVAL_RESULT_V2_SCHEMA_VERSION = "retrieval_result_v2"

DEFAULT_SOURCE_TYPE = "offline"
DEFAULT_VECTOR_DB = "milvus"
DEFAULT_INDEXED_GRANULARITY = "child"

DEFAULT_CLEANING_VERSION = "cleaning_v1.0"
DEFAULT_CHUNK_VERSION = "chunk_v1.0"
DEFAULT_PARENT_CHUNK_VERSION = "parent_chunk_v1.0"
DEFAULT_CHILD_CHUNK_VERSION = "child_chunk_v1.0"
DEFAULT_EMBEDDING_VERSION = "embedding_v1.0"
DEFAULT_INDEX_VERSION = "index_v1.0"

DEFAULT_CHUNK_STRATEGY = "rule_based_by_doc_unit_order_v1"
DEFAULT_PARENT_CHUNK_STRATEGY = "small_to_big_parent_fixed_v1"
DEFAULT_CHILD_CHUNK_STRATEGY = "small_to_big_child_fixed_v1"


# =========================================================
# 8. Flat Chunk Schema: chunk_v1
# =========================================================

CHUNK_V1_TEMPLATE: Dict[str, Any] = {
    "schema_version": CHUNK_SCHEMA_VERSION,

    "chunk_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,

    "text": "",
    "text_length": 0,
    "token_count": 0,
    "text_hash": None,

    "source_unit_ids": [],

    "title": None,
    "section": None,
    "section_level": None,
    "page_start": None,
    "page_end": None,

    "chunk_index": 0,
    "chunk_strategy": DEFAULT_CHUNK_STRATEGY,

    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "chunk_version": DEFAULT_CHUNK_VERSION,

    "created_at": None,

    "extra": {},
}

CHUNK_V1_REQUIRED_FIELDS = [
    "schema_version",
    "chunk_id",
    "doc_id",
    "source_type",
    "text",
    "text_length",
    "token_count",
    "text_hash",
    "source_unit_ids",
    "chunk_index",
    "chunk_strategy",
    "cleaning_version",
    "chunk_version",
    "created_at",
]


# =========================================================
# 9. Parent Chunk Schema: parent_chunk_v1
# =========================================================

PARENT_CHUNK_V1_TEMPLATE: Dict[str, Any] = {
    "schema_version": PARENT_CHUNK_SCHEMA_VERSION,

    "parent_chunk_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,

    "text": "",
    "text_length": 0,
    "token_count": 0,
    "text_hash": None,

    "source_unit_ids": [],

    # Parent -> children reverse index.
    # 离线切分后回填，用于数据校验、审计和父子块血缘追踪；在线检索仍以 child -> parent 为主。
    "child_chunk_ids": [],
    "child_count": 0,

    "title": None,
    "section": None,
    "section_level": None,
    "page_start": None,
    "page_end": None,

    "parent_chunk_index": 0,
    "parent_chunk_strategy": DEFAULT_PARENT_CHUNK_STRATEGY,

    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "parent_chunk_version": DEFAULT_PARENT_CHUNK_VERSION,

    "created_at": None,
    "updated_at": None,
    "is_active": True,

    "extra": {},
}

PARENT_CHUNK_V1_REQUIRED_FIELDS = [
    "schema_version",
    "parent_chunk_id",
    "doc_id",
    "source_type",
    "text",
    "text_length",
    "token_count",
    "text_hash",
    "source_unit_ids",
    "child_chunk_ids",
    "child_count",
    "parent_chunk_index",
    "parent_chunk_strategy",
    "cleaning_version",
    "parent_chunk_version",
    "created_at",
    "is_active",
]


# =========================================================
# 10. Child Chunk Schema: child_chunk_v1
# =========================================================

CHILD_CHUNK_V1_TEMPLATE: Dict[str, Any] = {
    "schema_version": CHILD_CHUNK_SCHEMA_VERSION,

    # 通用索引/检索字段，必须等于 child_chunk_id。
    # 这样 flat chunk 与 child chunk 可以共用 chunk_id 作为向量库主键。
    "chunk_id": None,
    "child_chunk_id": None,
    "parent_chunk_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,

    "text": "",
    "text_length": 0,
    "token_count": 0,
    "text_hash": None,

    "source_unit_ids": [],

    "title": None,
    "section": None,
    "section_level": None,
    "page_start": None,
    "page_end": None,

    "child_chunk_index": 0,
    "child_index_in_parent": 0,
    "child_chunk_strategy": DEFAULT_CHILD_CHUNK_STRATEGY,

    "char_start_in_parent": None,
    "char_end_in_parent": None,

    "cleaning_version": DEFAULT_CLEANING_VERSION,
    "parent_chunk_version": DEFAULT_PARENT_CHUNK_VERSION,
    "child_chunk_version": DEFAULT_CHILD_CHUNK_VERSION,

    "created_at": None,
    "updated_at": None,
    "is_active": True,

    "extra": {},
}

CHILD_CHUNK_V1_REQUIRED_FIELDS = [
    "schema_version",
    "chunk_id",
    "child_chunk_id",
    "parent_chunk_id",
    "doc_id",
    "source_type",
    "text",
    "text_length",
    "token_count",
    "text_hash",
    "source_unit_ids",
    "child_chunk_index",
    "child_index_in_parent",
    "child_chunk_strategy",
    "cleaning_version",
    "parent_chunk_version",
    "child_chunk_version",
    "created_at",
    "is_active",
]


# =========================================================
# 11. Vector Index Record Schema: vector_index_record_v1 / v2
# =========================================================

VECTOR_INDEX_RECORD_V1_TEMPLATE: Dict[str, Any] = {
    "schema_version": VECTOR_INDEX_RECORD_SCHEMA_VERSION,

    "chunk_id": None,
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

VECTOR_INDEX_RECORD_V2_TEMPLATE: Dict[str, Any] = {
    "schema_version": VECTOR_INDEX_RECORD_V2_SCHEMA_VERSION,

    # Vector-store primary key. In the current child-indexing strategy:
    # vector_id == indexed_chunk_id == chunk_id == child_chunk_id.
    "vector_id": None,
    "indexed_chunk_id": None,
    "chunk_id": None,
    "child_chunk_id": None,
    "parent_chunk_id": None,
    "doc_id": None,
    "source_type": DEFAULT_SOURCE_TYPE,

    "indexed_granularity": DEFAULT_INDEXED_GRANULARITY,

    # Idempotency / lineage.
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


# =========================================================
# 12. Retrieval Result Schema: retrieval_result_v2
# =========================================================
# 父子块检索结果：分数来自 child，最终上下文通常回填 parent。

RETRIEVAL_RESULT_V2_TEMPLATE: Dict[str, Any] = {
    "schema_version": RETRIEVAL_RESULT_V2_SCHEMA_VERSION,

    "rank": None,
    "score": None,
    "rerank_score": None,

    # Matched vector record.
    "chunk_id": None,          # equals child_chunk_id
    "child_chunk_id": None,
    "parent_chunk_id": None,
    "doc_id": None,

    "matched_granularity": "child",
    "context_granularity": "parent",

    # Text fields.
    "child_text": "",
    "parent_text": "",
    "text": "",              # default context text for prompt; usually parent_text

    # Position / citation fields.
    "title": None,
    "section": None,
    "page_start": None,
    "page_end": None,
    "child_index_in_parent": None,

    # Versions / lineage.
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
