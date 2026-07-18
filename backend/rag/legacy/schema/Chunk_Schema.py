# -*- coding: utf-8 -*-
"""
rag_template/schema/Chunk_Schema.py
===================================

Chunk schema 构造层。

职责：
1. 保留 Mini-RAG 旧版 chunk schema 的 build_chunk()，兼容现有 chunker。
2. 实现企业级 Agent-RAG 的 chunk_v1 / parent_chunk_v1 / child_chunk_v1 构造。
3. 只负责“按 SchemaConfig 模板构造标准 dict”，不负责 Spark、文件读写、具体切分算法。
"""

from copy import deepcopy
import hashlib
from typing import Any, Dict, List, Optional

from rag.configs.SchemaConfig import (
    CHUNK_METADATA_TEMPLATE,
    CHUNK_V1_TEMPLATE,
    PARENT_CHUNK_V1_TEMPLATE,
    CHILD_CHUNK_V1_TEMPLATE,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_CHUNK_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_CHUNK_STRATEGY,
    DEFAULT_PARENT_CHUNK_STRATEGY,
    DEFAULT_CHILD_CHUNK_STRATEGY,
    current_time_str,
)


# =========================================================
# Common helpers
# =========================================================


def estimate_token_count(text: str) -> int:
    """轻量 token 估算。后续可替换为 util/token_utils.py 中的真实 tokenizer。"""
    if not text:
        return 0
    # 中文场景先用字符级粗估；保持简单、无额外依赖。
    return len(text)


def sha256_text(text: str) -> str:
    """Return stable sha256 hash for chunk text."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def safe_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _deepcopy_extra(extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return deepcopy(extra) if isinstance(extra, dict) else {}


# =========================================================
# Mini-RAG legacy chunk builder
# =========================================================


def build_chunk(*args, **kwargs) -> Dict[str, Any]:
    """兼容旧版 Mini-RAG chunk 构造。

    支持两种历史调用方式：

    1）schema/Chunk_Schema.py 旧调用：
        build_chunk(doc=document, chunk_text=text, chunk_index=idx, ...)

    2）util/schema_builder.py 旧调用：
        build_chunk(doc_id=doc_id, text=text, idx=idx, doc_metadata=metadata, ...)

    返回结构仍然是旧版：
        {"chunk_id", "doc_id", "text", "metadata"}
    """
    if args and isinstance(args[0], dict):
        # Positional old style: build_chunk(doc, chunk_text, chunk_index, ...)
        doc = args[0]
        chunk_text = args[1] if len(args) > 1 else kwargs.get("chunk_text", "")
        chunk_index = args[2] if len(args) > 2 else kwargs.get("chunk_index", 0)
        return _build_chunk_from_document(
            doc=doc,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            section=kwargs.get("section"),
            section_path=kwargs.get("section_path"),
            page=kwargs.get("page"),
            parent_chunk_id=kwargs.get("parent_chunk_id"),
            chunk_type=kwargs.get("chunk_type"),
        )

    if "doc" in kwargs:
        return _build_chunk_from_document(
            doc=kwargs["doc"],
            chunk_text=kwargs.get("chunk_text", ""),
            chunk_index=kwargs.get("chunk_index", 0),
            section=kwargs.get("section"),
            section_path=kwargs.get("section_path"),
            page=kwargs.get("page"),
            parent_chunk_id=kwargs.get("parent_chunk_id"),
            chunk_type=kwargs.get("chunk_type"),
        )

    # util/schema_builder.py style.
    return _build_chunk_from_fields(
        doc_id=kwargs.get("doc_id") if "doc_id" in kwargs else (args[0] if len(args) > 0 else None),
        text=kwargs.get("text") if "text" in kwargs else (args[1] if len(args) > 1 else ""),
        idx=kwargs.get("idx") if "idx" in kwargs else (args[2] if len(args) > 2 else 0),
        doc_metadata=kwargs.get("doc_metadata") if "doc_metadata" in kwargs else (args[3] if len(args) > 3 else {}),
        chunk_type=kwargs.get("chunk_type", "fixed"),
        page=kwargs.get("page"),
        section=kwargs.get("section"),
        section_path=kwargs.get("section_path"),
        parent_chunk_id=kwargs.get("parent_chunk_id"),
        start_char=kwargs.get("start_char"),
        end_char=kwargs.get("end_char"),
        heading_level=kwargs.get("heading_level"),
        semantic_score=kwargs.get("semantic_score"),
        token_count=kwargs.get("token_count"),
        extra=kwargs.get("extra"),
    )


def _build_chunk_from_document(
    *,
    doc: Dict[str, Any],
    chunk_text: str,
    chunk_index: int,
    section: Optional[str] = None,
    section_path: Optional[str] = None,
    page: Optional[int] = None,
    parent_chunk_id: Optional[str] = None,
    chunk_type: Optional[str] = None,
) -> Dict[str, Any]:
    doc_id = doc["doc_id"]
    doc_metadata = doc.get("metadata", {})

    metadata = deepcopy(CHUNK_METADATA_TEMPLATE)
    metadata.update({
        "source": doc_metadata.get("source"),
        "source_path": doc_metadata.get("source_path"),
        "doc_type": doc_metadata.get("doc_type"),
        "title": doc_metadata.get("title"),

        "chunk_index": chunk_index,
        "page": page,
        "section": section,
        "section_path": section_path,

        "created_at": current_time_str(),
        "updated_at": doc_metadata.get("updated_at"),

        "version": doc_metadata.get("version"),
        "status": doc_metadata.get("status", "active"),
        "is_latest": doc_metadata.get("is_latest", True),

        "department": doc_metadata.get("department"),
        "project_id": doc_metadata.get("project_id"),
        "project_name": doc_metadata.get("project_name"),
        "security_level": doc_metadata.get("security_level"),

        "char_count": len(chunk_text or ""),
        "token_count": None,

        "extra": {
            "parent_chunk_id": parent_chunk_id,
            "chunk_type": chunk_type,
        },
    })

    return {
        "chunk_id": f"{doc_id}_chunk_{chunk_index:04d}",
        "doc_id": doc_id,
        "text": chunk_text,
        "metadata": metadata,
    }


def _build_chunk_from_fields(
    *,
    doc_id: str,
    text: str,
    idx: int,
    doc_metadata: Dict[str, Any],
    chunk_type: str = "fixed",
    page: Optional[int] = None,
    section: Optional[str] = None,
    section_path: Optional[str] = None,
    parent_chunk_id: Optional[str] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    heading_level: Optional[int] = None,
    semantic_score: Optional[float] = None,
    token_count: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = deepcopy(CHUNK_METADATA_TEMPLATE)

    for key in metadata.keys():
        if key in doc_metadata and key != "extra":
            metadata[key] = doc_metadata[key]

    metadata["chunk_index"] = idx
    metadata["page"] = page
    metadata["section"] = section
    metadata["section_path"] = section_path
    metadata["created_at"] = doc_metadata.get("created_at") or current_time_str()
    metadata["updated_at"] = doc_metadata.get("updated_at")
    metadata["char_count"] = len(text or "")
    metadata["token_count"] = token_count

    inherited_extra = deepcopy(doc_metadata.get("extra", {})) if isinstance(doc_metadata.get("extra"), dict) else {}
    chunk_extra = {
        "chunk_type": chunk_type,
        "parent_chunk_id": parent_chunk_id,
        "start_char": start_char,
        "end_char": end_char,
        "heading_level": heading_level,
        "semantic_score": semantic_score,
    }
    if extra:
        chunk_extra.update(extra)

    metadata["extra"] = {
        "document_extra": inherited_extra,
        **chunk_extra,
    }

    return {
        "chunk_id": f"{doc_id}_chunk_{idx:04d}",
        "doc_id": doc_id,
        "text": text,
        "metadata": metadata,
    }


# =========================================================
# Enterprise Agent-RAG schema builders
# =========================================================


def build_chunk_v1(
    *,
    chunk_id: str,
    doc_id: str,
    text: str,
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
    record = deepcopy(CHUNK_V1_TEMPLATE)
    record.update({
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": text or "",
        "text_length": len(text or ""),
        "token_count": token_count if token_count is not None else estimate_token_count(text or ""),
        "text_hash": sha256_text(text or ""),
        "source_unit_ids": safe_list(source_unit_ids),
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
        "extra": _deepcopy_extra(extra),
    })
    return record


def build_parent_chunk_v1(
    *,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
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
    child_ids = safe_list(child_chunk_ids)

    record = deepcopy(PARENT_CHUNK_V1_TEMPLATE)
    record.update({
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": text or "",
        "text_length": len(text or ""),
        "token_count": token_count if token_count is not None else estimate_token_count(text or ""),
        "text_hash": sha256_text(text or ""),
        "source_unit_ids": safe_list(source_unit_ids),
        "child_chunk_ids": child_ids,
        "child_count": child_count if child_count is not None else len(child_ids),
        "title": title,
        "section": section,
        "section_level": section_level,
        "page_start": page_start,
        "page_end": page_end,
        "parent_chunk_index": parent_chunk_index,
        "parent_chunk_strategy": parent_chunk_strategy or DEFAULT_PARENT_CHUNK_STRATEGY,
        "cleaning_version": cleaning_version or DEFAULT_CLEANING_VERSION,
        "parent_chunk_version": parent_chunk_version or DEFAULT_PARENT_CHUNK_VERSION,
        "created_at": created_at or current_time_str(),
        "updated_at": None,
        "is_active": True,
        "extra": _deepcopy_extra(extra),
    })
    return record


def build_child_chunk_v1(
    *,
    child_chunk_id: str,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
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
    record = deepcopy(CHILD_CHUNK_V1_TEMPLATE)
    record.update({
        # chunk_id 是通用索引主键，语义上等于 child_chunk_id。
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "source_type": source_type or DEFAULT_SOURCE_TYPE,
        "text": text or "",
        "text_length": len(text or ""),
        "token_count": token_count if token_count is not None else estimate_token_count(text or ""),
        "text_hash": sha256_text(text or ""),
        "source_unit_ids": safe_list(source_unit_ids),
        "title": title,
        "section": section,
        "section_level": section_level,
        "page_start": page_start,
        "page_end": page_end,
        "child_chunk_index": child_chunk_index,
        "child_index_in_parent": child_index_in_parent,
        "child_chunk_strategy": child_chunk_strategy or DEFAULT_CHILD_CHUNK_STRATEGY,
        "char_start_in_parent": char_start_in_parent,
        "char_end_in_parent": char_end_in_parent,
        "cleaning_version": cleaning_version or DEFAULT_CLEANING_VERSION,
        "parent_chunk_version": parent_chunk_version or DEFAULT_PARENT_CHUNK_VERSION,
        "child_chunk_version": child_chunk_version or DEFAULT_CHILD_CHUNK_VERSION,
        "created_at": created_at or current_time_str(),
        "updated_at": None,
        "is_active": True,
        "extra": _deepcopy_extra(extra),
    })
    return record


# =========================================================
# Enterprise chunk_v1 normalizer / validator for embedding jobs
# =========================================================


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int = -1) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def _safe_nullable_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_chunk_v1(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw chunk-like dict to the chunk_v1 contract.

    兼容早期 chunk_unit_v1 输出，但标准化后的对象使用 chunk_v1 字段。
    """
    text = _safe_str(raw.get("text"))
    extra = _safe_dict(raw.get("extra"))

    token_count = raw.get("token_count")
    if token_count is None:
        token_count = len(text)

    text_length = raw.get("text_length")
    if text_length is None:
        text_length = len(text)

    return {
        "schema_version": CHUNK_V1_TEMPLATE["schema_version"],
        "input_schema_version": _safe_str(raw.get("schema_version"), CHUNK_V1_TEMPLATE["schema_version"]),
        "chunk_id": _safe_str(raw.get("chunk_id")),
        "doc_id": _safe_str(raw.get("doc_id")),
        "source_type": _safe_str(raw.get("source_type"), DEFAULT_SOURCE_TYPE),
        "text": text,
        "text_length": _safe_int(text_length, len(text)),
        "token_count": _safe_int(token_count, len(text)),
        "text_hash": _safe_str(raw.get("text_hash"), sha256_text(text)),
        "source_unit_ids": [_safe_str(x) for x in safe_list(raw.get("source_unit_ids"))],
        "title": raw.get("title"),
        "section": raw.get("section"),
        "section_level": _safe_nullable_int(raw.get("section_level")),
        "page_start": _safe_nullable_int(raw.get("page_start")),
        "page_end": _safe_nullable_int(raw.get("page_end")),
        "chunk_index": _safe_int(raw.get("chunk_index"), -1),
        "chunk_strategy": _safe_str(raw.get("chunk_strategy")),
        "cleaning_version": _safe_str(raw.get("cleaning_version")),
        "chunk_version": _safe_str(raw.get("chunk_version"), DEFAULT_CHUNK_VERSION),
        "created_at": _safe_str(raw.get("created_at"), current_time_str()),
        "extra": extra,
    }


def validate_chunk_v1_records(chunks: List[Dict[str, Any]]) -> None:
    if not chunks:
        raise ValueError("No chunk records loaded.")

    missing: List[str] = []
    for i, c in enumerate(chunks):
        if not c.get("chunk_id"):
            missing.append(f"idx={i}:chunk_id")
        if not c.get("doc_id"):
            missing.append(f"idx={i}:doc_id")
        if not c.get("text"):
            missing.append(f"idx={i}:text")
        if len(missing) >= 10:
            break

    if missing:
        raise ValueError("Invalid chunk_v1 records, missing required fields: " + ", ".join(missing))
