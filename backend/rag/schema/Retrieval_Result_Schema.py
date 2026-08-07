# -*- coding: utf-8 -*-
"""兼容检索结果字典的标准构造层。

``retrieval_result_v1`` 服务 Mini-RAG 平面 Chunk；``v2`` 表达父子检索：子块承担命中与
评分，父块承担默认生成上下文。该模块只规范字段，不执行向量召回、重排或 Parent Store
查询。
"""

from copy import deepcopy
from typing import Any, Dict, Optional

from rag.configs.SchemaConfig import (
    RETRIEVAL_RESULT_TEMPLATE,
    RETRIEVAL_RESULT_V2_TEMPLATE,
    CHUNK_METADATA_TEMPLATE,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_VECTOR_DB,
    DEFAULT_EMBEDDING_VERSION,
)
from rag.schema.structured_evidence import build_structured_evidence


def _safe_str(value: Any, default: str = "") -> str:
    """把可选标量规范为字符串，``None`` 使用显式默认值。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _safe_nullable_int(value: Any) -> Optional[int]:
    """尽力转换页码或位置索引；空值和非法值都保留为 ``None``。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_list(value: Any) -> list:
    """把空值、元组或单个值统一为列表，便于来源 ID 序列化。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def build_retrieval_result(
    chunk: Dict[str, Any],
    rank: int,
    score: float,
    rerank_score: Optional[float] = None,
) -> Dict[str, Any]:
    """从一个平面 Chunk 构造 Mini-RAG ``retrieval_result_v1``。"""
    result = deepcopy(RETRIEVAL_RESULT_TEMPLATE)
    result["rank"] = rank
    result["score"] = score
    result["rerank_score"] = rerank_score
    result["chunk_id"] = chunk.get("chunk_id")
    result["doc_id"] = chunk.get("doc_id")
    result["text"] = chunk.get("text", "")
    result["metadata"] = chunk.get("metadata", deepcopy(CHUNK_METADATA_TEMPLATE))
    return result


def _nested_dict(record: Dict[str, Any], key: str) -> Dict[str, Any]:
    direct = record.get(key)
    if isinstance(direct, dict):
        return deepcopy(direct)
    extra = record.get("extra")
    if isinstance(extra, dict) and isinstance(extra.get(key), dict):
        return deepcopy(extra[key])
    return {}


def build_retrieval_result_v2(
    *,
    child_chunk: Dict[str, Any],
    parent_chunk: Optional[Dict[str, Any]] = None,
    rank: int,
    score: float,
    rerank_score: Optional[float] = None,
    embedding_model: Optional[str] = None,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    index_name: Optional[str] = None,
    vector_db: str = DEFAULT_VECTOR_DB,
    context_granularity: str = "parent",
    metadata: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造父子 RAG ``retrieval_result_v2``。

    ``chunk_id`` 固定等于实际命中的 ``child_chunk_id``，用于兼容向量库主键和评测命中；
    ``text`` 则按 ``context_granularity`` 优先使用父块正文，父块缺失时回退到子块。评分和
    embedding/index 血缘保持命中侧语义，不因上下文回填而改变。
    """
    parent_chunk = parent_chunk or {}
    child_chunk_id = _safe_str(child_chunk.get("chunk_id") or child_chunk.get("child_chunk_id"))
    parent_chunk_id = _safe_str(
        child_chunk.get("parent_chunk_id") or parent_chunk.get("parent_chunk_id")
    )

    child_text = _safe_str(child_chunk.get("text"))
    parent_text = _safe_str(parent_chunk.get("text"))
    context_text = parent_text if context_granularity == "parent" and parent_text else child_text

    document_profile = (
        _nested_dict(child_chunk, "document_profile")
        or _nested_dict(parent_chunk, "document_profile")
    )
    type_aware_chunking = (
        _nested_dict(child_chunk, "type_aware_chunking")
        or _nested_dict(parent_chunk, "type_aware_chunking")
    )
    structured_evidence = build_structured_evidence(child_chunk).model_dump(
        mode="json", exclude_none=True
    )
    effective_metadata = dict(metadata or {})
    effective_extra = dict(extra or {})
    if document_profile:
        effective_metadata["document_profile"] = deepcopy(document_profile)
        effective_extra["document_profile"] = deepcopy(document_profile)
    if type_aware_chunking:
        effective_metadata["type_aware_chunking"] = deepcopy(type_aware_chunking)
        effective_extra["type_aware_chunking"] = deepcopy(type_aware_chunking)
    effective_metadata["structured_evidence"] = deepcopy(structured_evidence)
    effective_extra["structured_evidence"] = deepcopy(structured_evidence)

    result = deepcopy(RETRIEVAL_RESULT_V2_TEMPLATE)
    result.update({
        "tenant_id": _safe_str(child_chunk.get("tenant_id") or parent_chunk.get("tenant_id")),
        "kb_id": _safe_str(child_chunk.get("kb_id") or parent_chunk.get("kb_id")),
        "file_id": _safe_str(child_chunk.get("file_id") or parent_chunk.get("file_id")) or None,
        "document_type": _safe_str(
            child_chunk.get("document_type")
            or parent_chunk.get("document_type")
            or document_profile.get("document_type")
        ),
        "evidence_role": _safe_str(
            child_chunk.get("evidence_role")
            or parent_chunk.get("evidence_role")
            or document_profile.get("evidence_role")
        ),
        "project_relation": _safe_str(
            child_chunk.get("project_relation")
            or parent_chunk.get("project_relation")
            or (
                document_profile.get("project_relation", {}).get("relation")
                if isinstance(document_profile.get("project_relation"), dict)
                else document_profile.get("project_relation")
            )
        ),
        "project_id": _safe_str(
            child_chunk.get("project_id")
            or parent_chunk.get("project_id")
            or document_profile.get("project_id")
            or (
                document_profile.get("project_relation", {}).get("project_id")
                if isinstance(document_profile.get("project_relation"), dict)
                else None
            )
        ) or None,
        "structured_evidence": deepcopy(structured_evidence),
        "authority_level": _safe_str(
            child_chunk.get("authority_level")
            or parent_chunk.get("authority_level")
            or (
                document_profile.get("authority", {}).get("level")
                if isinstance(document_profile.get("authority"), dict)
                else None
            )
        ),
        "rank": rank,
        "score": score,
        "rerank_score": rerank_score,
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": _safe_str(child_chunk.get("doc_id") or parent_chunk.get("doc_id")),
        "matched_granularity": "child",
        "context_granularity": context_granularity,
        "child_text": child_text,
        "parent_text": parent_text,
        "text": context_text,
        "title": child_chunk.get("title") or parent_chunk.get("title"),
        "section": child_chunk.get("section") or parent_chunk.get("section"),
        "page_start": _safe_nullable_int(child_chunk.get("page_start") or parent_chunk.get("page_start")),
        "page_end": _safe_nullable_int(child_chunk.get("page_end") or parent_chunk.get("page_end")),
        "child_index_in_parent": _safe_nullable_int(child_chunk.get("child_index_in_parent")),
        "source_type": _safe_str(child_chunk.get("source_type") or parent_chunk.get("source_type"), DEFAULT_SOURCE_TYPE),
        "source_unit_ids": [
            _safe_str(x)
            for x in _safe_list(child_chunk.get("source_unit_ids") or parent_chunk.get("source_unit_ids"))
        ],
        "cleaning_version": _safe_str(child_chunk.get("cleaning_version") or parent_chunk.get("cleaning_version")),
        "parent_chunk_version": _safe_str(child_chunk.get("parent_chunk_version") or parent_chunk.get("parent_chunk_version")),
        "child_chunk_version": _safe_str(child_chunk.get("child_chunk_version")),
        "embedding_model": embedding_model,
        "embedding_version": embedding_version,
        "index_name": index_name,
        "vector_db": vector_db,
        "metadata": effective_metadata,
        "extra": effective_extra,
    })
    if document_profile:
        try:
            from rag.schema.retrieval_candidate_v2 import build_retrieval_candidate_v2

            candidate = build_retrieval_candidate_v2(result)
            result["metadata"]["retrieval_candidate_v2"] = candidate.model_dump(
                mode="json",
                exclude_none=True,
            )
        except Exception as exc:
            result["metadata"]["retrieval_candidate_v2_error"] = {
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
    else:
        result["metadata"]["retrieval_candidate_v2_error"] = {
            "error_type": "MissingDocumentProfile",
            "message": "retrieval candidate has no Stage C document_profile",
        }
    return result
