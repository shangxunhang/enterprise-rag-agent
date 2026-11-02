"""构造普通 Chunk、父块和子块的标准字典协议。

本模块位于历史 dataclass 体系，返回普通字典而不是 Pydantic 对象。所有构建函数都
从 ``extra`` 创建新字典，避免调用方可变对象被后续修改；父子块函数显式记录层级、
父级身份、Chunker 版本和字符边界，供索引、检索和引用血缘复用。
"""

from datetime import datetime
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from rag.configs.SchemaConfig import *


def _now_iso() -> str:
    """返回当前本地时间 ISO 字符串，保留旧版无时区行为。"""

    return datetime.now().isoformat()


def build_chunk(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build modern chunks and both historical Mini-RAG call shapes."""

    if args and isinstance(args[0], dict):
        kwargs = {
            **kwargs,
            "doc": args[0],
            "chunk_text": (
                args[1] if len(args) > 1 else kwargs.get("chunk_text", "")
            ),
            "chunk_index": (
                args[2] if len(args) > 2 else kwargs.get("chunk_index", 0)
            ),
        }
    if "doc" in kwargs:
        document = dict(kwargs["doc"])
        return _build_legacy_chunk(
            doc_id=str(document.get("doc_id") or ""),
            text=str(kwargs.get("chunk_text") or ""),
            index=int(kwargs.get("chunk_index") or 0),
            metadata=dict(document.get("metadata") or {}),
            chunk_type=str(kwargs.get("chunk_type") or "fixed"),
            page=kwargs.get("page"),
            section=kwargs.get("section"),
            section_path=kwargs.get("section_path"),
            parent_chunk_id=kwargs.get("parent_chunk_id"),
            start_char=kwargs.get("start_char"),
            end_char=kwargs.get("end_char"),
            heading_level=kwargs.get("heading_level"),
            token_count=kwargs.get("token_count"),
            extra=kwargs.get("extra"),
        )
    if "idx" in kwargs or "doc_metadata" in kwargs:
        return _build_legacy_chunk(
            doc_id=str(kwargs.get("doc_id") or ""),
            text=str(kwargs.get("text") or ""),
            index=int(kwargs.get("idx") or 0),
            metadata=dict(kwargs.get("doc_metadata") or {}),
            chunk_type=str(kwargs.get("chunk_type") or "fixed"),
            page=kwargs.get("page"),
            section=kwargs.get("section"),
            section_path=kwargs.get("section_path"),
            parent_chunk_id=kwargs.get("parent_chunk_id"),
            start_char=kwargs.get("start_char"),
            end_char=kwargs.get("end_char"),
            heading_level=kwargs.get("heading_level"),
            token_count=kwargs.get("token_count"),
            extra=kwargs.get("extra"),
        )
    return _build_modern_chunk(*args, **kwargs)


def _build_legacy_chunk(
    *,
    doc_id: str,
    text: str,
    index: int,
    metadata: Dict[str, Any],
    chunk_type: str,
    page: Optional[int] = None,
    section: Optional[str] = None,
    section_path: Any = None,
    parent_chunk_id: Optional[str] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    heading_level: Optional[int] = None,
    token_count: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    output_metadata = deepcopy(CHUNK_METADATA_TEMPLATE)
    output_metadata.update(metadata)
    output_metadata.update(
        {
            "chunk_index": index,
            "page": page,
            "section": section,
            "section_path": section_path,
            "parent_chunk_id": parent_chunk_id,
            "chunk_type": chunk_type,
            "start_char": start_char,
            "end_char": end_char,
            "heading_level": heading_level,
            "token_count": token_count,
            "char_count": len(text),
            "extra": dict(extra or {}),
        }
    )
    return {
        "chunk_id": f"{doc_id}_chunk_{index:04d}",
        "doc_id": doc_id,
        "text": text,
        "metadata": output_metadata,
    }


def _build_modern_chunk(
    chunk_id: str,
    doc_id: str,
    text: str,
    chunk_index: int,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunk_level: int = DEFAULT_CHUNK_LEVEL,
    parent_chunk_id: Optional[str] = None,
    chunker_name: str = DEFAULT_CHUNKER_NAME,
    chunker_version: str = DEFAULT_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一个普通扁平 Chunk 字典。

    Args:
        chunk_id: 当前块全局身份。
        doc_id: 来源文档身份。
        text: 当前块正文。
        chunk_index: 当前块在文档中的 0-based 顺序。
        source: 可读来源名称。
        source_path: 原始文件路径。
        title: 文档标题。
        section: 当前章节名称。
        page_num: 可选页码。
        start_char: 在来源文本中的起始字符偏移。
        end_char: 结束字符偏移。
        token_count: 可选 Token 数。
        chunk_level: 逻辑层级，普通块默认 0。
        parent_chunk_id: 可选父块身份。
        chunker_name: 生成该块的 Chunker 名称。
        chunker_version: Chunker 版本。
        created_at: 显式创建时间；省略时使用当前本地时间。
        language: 文本语言。
        status: 业务状态。
        security_level: 数据安全等级。
        extra: 扩展 metadata。

    Returns:
        符合 ``chunk_v1`` 字段布局的普通字典。
    """

    # 统一构造完整字段，即使可选值为 None 也保留键，便于旧调用方直接访问。
    return {
        "schema_version": DEFAULT_CHUNK_SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": chunk_index,
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunk_level": chunk_level,
        "parent_chunk_id": parent_chunk_id,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }


def build_parent_chunk(
    *,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    parent_index: int,
    child_chunk_ids: Optional[List[str]] = None,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunker_name: str = DEFAULT_PARENT_CHILD_CHUNKER_NAME,
    chunker_version: str = DEFAULT_PARENT_CHILD_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造父级上下文 Chunk。

    父块用于生成上下文，不直接参与细粒度匹配。``child_chunk_ids`` 保存当前父块下的
    子块顺序；调用方可先构造父块，再在子块生成完成后回填该列表。
    """

    return {
        "schema_version": DEFAULT_PARENT_CHUNK_SCHEMA_VERSION,
        "chunk_id": parent_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": parent_index,
        "parent_index": parent_index,
        "chunk_level": 0,
        "child_chunk_ids": list(child_chunk_ids or []),
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }


def build_child_chunk(
    *,
    child_chunk_id: str,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    child_index: int,
    parent_index: int,
    global_chunk_index: int,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunker_name: str = DEFAULT_PARENT_CHILD_CHUNKER_NAME,
    chunker_version: str = DEFAULT_PARENT_CHILD_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造参与向量匹配的子级 Chunk。

    Args:
        child_chunk_id: 子块身份。
        parent_chunk_id: 所属父块身份。
        doc_id: 来源文档身份。
        text: 子块匹配文本。
        child_index: 在当前父块中的 0-based 顺序。
        parent_index: 父块在文档中的 0-based 顺序。
        global_chunk_index: 子块在整篇文档中的全局顺序。

    Returns:
        同时保留 ``chunk_id`` 和 ``child_chunk_id`` 的 ``child_chunk_v1`` 字典，兼容
        通用 Chunk 调用方和父子块专用调用方。
    """

    return {
        "schema_version": DEFAULT_CHILD_CHUNK_SCHEMA_VERSION,
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": global_chunk_index,
        "global_chunk_index": global_chunk_index,
        "child_index": child_index,
        "parent_index": parent_index,
        "chunk_level": 1,
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }


def _enterprise_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_chunk_v1(
    *,
    chunk_id: str,
    doc_id: str,
    text: str,
    tenant_id: str = "default",
    kb_id: str = "default",
    file_id: Optional[str] = None,
    source_type: str = DEFAULT_SOURCE_TYPE,
    source_unit_ids: Optional[List[str]] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    section_level: Optional[int] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    chunk_index: int = 0,
    chunk_strategy: Optional[str] = None,
    cleaning_version: str = DEFAULT_CLEANING_VERSION,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
    token_count: Optional[int] = None,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the enterprise flat-chunk contract consumed by offline indexing."""

    value = text or ""
    return {
        "schema_version": DEFAULT_CHUNK_SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "tenant_id": tenant_id or "default",
        "kb_id": kb_id or "default",
        "file_id": file_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": value,
        "text_length": len(value),
        "token_count": token_count if token_count is not None else len(value),
        "text_hash": _enterprise_text_hash(value),
        "source_unit_ids": list(source_unit_ids or []),
        "title": title,
        "section": section,
        "section_level": section_level,
        "page_start": page_start,
        "page_end": page_end,
        "chunk_index": chunk_index,
        "chunk_strategy": chunk_strategy or DEFAULT_CHUNK_STRATEGY,
        "cleaning_version": cleaning_version or DEFAULT_CLEANING_VERSION,
        "chunk_version": chunk_version or DEFAULT_CHUNK_VERSION,
        "created_at": created_at or current_time_str(),
        "extra": dict(extra or {}),
    }


def build_parent_chunk_v1(
    *,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    tenant_id: str = "default",
    kb_id: str = "default",
    file_id: Optional[str] = None,
    source_type: str = DEFAULT_SOURCE_TYPE,
    source_unit_ids: Optional[List[str]] = None,
    child_chunk_ids: Optional[List[str]] = None,
    child_count: Optional[int] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    section_level: Optional[int] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    parent_chunk_index: int = 0,
    parent_chunk_strategy: Optional[str] = None,
    cleaning_version: str = DEFAULT_CLEANING_VERSION,
    parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
    token_count: Optional[int] = None,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the enterprise parent-chunk contract."""

    value = text or ""
    child_ids = list(child_chunk_ids or [])
    return {
        "schema_version": DEFAULT_PARENT_CHUNK_SCHEMA_VERSION,
        "parent_chunk_id": parent_chunk_id,
        "tenant_id": tenant_id or "default",
        "kb_id": kb_id or "default",
        "file_id": file_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": value,
        "text_length": len(value),
        "token_count": token_count if token_count is not None else len(value),
        "text_hash": _enterprise_text_hash(value),
        "source_unit_ids": list(source_unit_ids or []),
        "child_chunk_ids": child_ids,
        "child_count": child_count if child_count is not None else len(child_ids),
        "title": title,
        "section": section,
        "section_level": section_level,
        "page_start": page_start,
        "page_end": page_end,
        "parent_chunk_index": parent_chunk_index,
        "parent_chunk_strategy": (
            parent_chunk_strategy or DEFAULT_PARENT_CHUNK_STRATEGY
        ),
        "cleaning_version": cleaning_version or DEFAULT_CLEANING_VERSION,
        "parent_chunk_version": (
            parent_chunk_version or DEFAULT_PARENT_CHUNK_VERSION
        ),
        "created_at": created_at or current_time_str(),
        "updated_at": None,
        "is_active": True,
        "extra": dict(extra or {}),
    }


def build_child_chunk_v1(
    *,
    child_chunk_id: str,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    tenant_id: str = "default",
    kb_id: str = "default",
    file_id: Optional[str] = None,
    source_type: str = DEFAULT_SOURCE_TYPE,
    source_unit_ids: Optional[List[str]] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    section_level: Optional[int] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    child_chunk_index: int = 0,
    child_index_in_parent: int = 0,
    child_chunk_strategy: Optional[str] = None,
    char_start_in_parent: Optional[int] = None,
    char_end_in_parent: Optional[int] = None,
    cleaning_version: str = DEFAULT_CLEANING_VERSION,
    parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
    child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
    token_count: Optional[int] = None,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the enterprise child-chunk contract."""

    value = text or ""
    return {
        "schema_version": DEFAULT_CHILD_CHUNK_SCHEMA_VERSION,
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "tenant_id": tenant_id or "default",
        "kb_id": kb_id or "default",
        "file_id": file_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": value,
        "text_length": len(value),
        "token_count": token_count if token_count is not None else len(value),
        "text_hash": _enterprise_text_hash(value),
        "source_unit_ids": list(source_unit_ids or []),
        "title": title,
        "section": section,
        "section_level": section_level,
        "page_start": page_start,
        "page_end": page_end,
        "child_chunk_index": child_chunk_index,
        "child_index_in_parent": child_index_in_parent,
        "child_chunk_strategy": (
            child_chunk_strategy or DEFAULT_CHILD_CHUNK_STRATEGY
        ),
        "char_start_in_parent": char_start_in_parent,
        "char_end_in_parent": char_end_in_parent,
        "cleaning_version": cleaning_version or DEFAULT_CLEANING_VERSION,
        "parent_chunk_version": (
            parent_chunk_version or DEFAULT_PARENT_CHUNK_VERSION
        ),
        "child_chunk_version": child_chunk_version or DEFAULT_CHILD_CHUNK_VERSION,
        "created_at": created_at or current_time_str(),
        "updated_at": None,
        "is_active": True,
        "extra": dict(extra or {}),
    }
