# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：sha256_text、preview_text、_safe_value、bounded_summary、canonical_sha256、model_request_summary、model_response_summary、tool_call_summary、_rag_contract_summary、tool_result_summary等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stable, bounded summaries for Trace v2 events.

Trace is an observability product, not a second copy of prompts, documents and
model outputs.  These helpers keep the process facts while avoiding unbounded
JSONL growth and accidental secret capture.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping


_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
)


# 阅读注释（函数）：处理 sha256 文本 相关逻辑。
def sha256_text(value: str) -> str:
    """处理 sha256 文本 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：hexdigest, hashlib.sha256, value.encode。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# 阅读注释（函数）：处理 preview 文本 相关逻辑。
def preview_text(value: Any, limit: int = 240) -> str:
    """处理 preview 文本 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        limit: limit，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：strip, replace, str, len。
    """
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


# 阅读注释（函数）：处理 safe value 相关逻辑。
def _safe_value(value: Any, *, depth: int, max_depth: int, max_items: int) -> Any:
    """处理 safe value 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        depth: depth，具体约束请结合类型标注和调用方确认。
        max_depth: max depth，具体约束请结合类型标注和调用方确认。
        max_items: max 数据项集合，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：isinstance, len, sha256_text, preview_text, enumerate, value.items, max, str。
    """
    if depth >= max_depth:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return {
            "chars": len(value),
            "sha256": sha256_text(value),
            "preview": preview_text(value),
        }
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["__truncated_keys__"] = max(0, len(value) - max_items)
                break
            key_text = str(key)
            if any(marker in key_text.lower() for marker in _SECRET_MARKERS):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _safe_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return {
            "count": len(items),
            "items": [
                _safe_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
                for item in items[:max_items]
            ],
            "truncated": len(items) > max_items,
        }
    if hasattr(value, "model_dump"):
        return _safe_value(
            value.model_dump(mode="json"),
            depth=depth,
            max_depth=max_depth,
            max_items=max_items,
        )
    return preview_text(repr(value))


# 阅读注释（函数）：处理 bounded summary 相关逻辑。
def bounded_summary(value: Any, *, max_depth: int = 4, max_items: int = 20) -> Any:
    """处理 bounded summary 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        max_depth: max depth，具体约束请结合类型标注和调用方确认。
        max_items: max 数据项集合，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：_safe_value。
    """
    return _safe_value(value, depth=0, max_depth=max_depth, max_items=max_items)


# 阅读注释（函数）：处理 canonical sha256 相关逻辑。
def canonical_sha256(value: Any) -> str:
    """处理 canonical sha256 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：json.dumps, sha256_text。
    """
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_text(payload)


# 阅读注释（函数）：处理 模型 请求 summary 相关逻辑。
def model_request_summary(request: Any, model_name: str) -> Dict[str, Any]:
    """处理 模型 请求 summary 相关逻辑。

    参数:
        request: 当前请求对象。
        model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：str, getattr, dict, extra.get, len, sha256_text, preview_text, bounded_summary。
    """
    prompt = str(getattr(request, "prompt", "") or "")
    system_prompt = str(getattr(request, "system_prompt", "") or "")
    extra = dict(getattr(request, "extra", {}) or {})
    return {
        "model_call_id": getattr(request, "model_call_id", None),
        "model_name": model_name,
        "caller_agent": getattr(request, "caller_agent", None),
        "call_purpose": extra.get("call_purpose"),
        "prompt_chars": len(prompt),
        "prompt_sha256": sha256_text(prompt),
        "prompt_preview": preview_text(prompt),
        "system_prompt_chars": len(system_prompt),
        "message_count": len(getattr(request, "messages", []) or []),
        "temperature": getattr(request, "temperature", None),
        "max_tokens": getattr(request, "max_tokens", None),
        "prompt_id": extra.get("prompt_id"),
        "prompt_version": extra.get("prompt_version"),
        "section_id": extra.get("section_id"),
        "section_title": extra.get("section_title"),
        "llm_context": bounded_summary(
            extra.get("llm_context_summary") or {"managed": False},
            max_depth=4,
            max_items=30,
        ),
    }


# 阅读注释（函数）：处理 模型 响应 summary 相关逻辑。
def model_response_summary(response: Any) -> Dict[str, Any]:
    """处理 模型 响应 summary 相关逻辑。

    参数:
        response: 下游返回的响应对象。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：str, getattr, hasattr, usage.model_dump, dict, bool, len, sha256_text。
    """
    content = str(getattr(response, "content", "") or "")
    usage = getattr(response, "token_usage", None)
    usage_dict = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else dict(usage or {})
    error = getattr(response, "error", None)
    return {
        "model_call_id": getattr(response, "model_call_id", None),
        "model_name": getattr(response, "model_name", None),
        "success": bool(getattr(response, "success", False)),
        "content_chars": len(content),
        "content_sha256": sha256_text(content),
        "content_preview": preview_text(content),
        "finish_reason": getattr(response, "finish_reason", None),
        "latency_ms": getattr(response, "latency_ms", None),
        "token_usage": usage_dict,
        "error_code": getattr(error, "error_code", None) if error else None,
        "error_type": getattr(error, "error_type", None) if error else None,
    }


# 阅读注释（函数）：处理 工具 call summary 相关逻辑。
def tool_call_summary(tool_call: Any) -> Dict[str, Any]:
    """处理 工具 call summary 相关逻辑。

    参数:
        tool_call: 工具 call，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：dict, getattr, str, tool_input.get, sorted, len, sha256_text, preview_text。
    """
    tool_input = dict(getattr(tool_call, "tool_input", {}) or {})
    query = str(tool_input.get("query") or "")
    return {
        "tool_call_id": getattr(tool_call, "tool_call_id", None),
        "tool_name": getattr(tool_call, "tool_name", None),
        "caller_agent": getattr(tool_call, "caller_agent", None),
        "step_id": getattr(tool_call, "step_id", None),
        "step_name": getattr(tool_call, "step_name", None),
        "input_keys": sorted(tool_input),
        "query_chars": len(query),
        "query_sha256": sha256_text(query) if query else None,
        "query_preview": preview_text(query) if query else None,
        "retrieval_mode": tool_input.get("retrieval_mode") or tool_input.get("retrieval_strategy"),
        "top_k": tool_input.get("top_k"),
        "max_context_chars": tool_input.get("max_context_chars"),
    }


# 阅读注释（函数）：处理 RAG contract summary 相关逻辑。
def _rag_contract_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """处理 RAG contract summary 相关逻辑。

    参数:
        result: 待处理的结果对象。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：result.get, evidence.get, canonical_sha256, len, str, context.get, assessment.get, lineage.get。
    """
    evidence = result.get("evidence") or {}
    lineage = evidence.get("lineage") or {}
    assessment = evidence.get("assessment") or {}
    context = evidence.get("context") or result.get("context") or {}
    return {
        "schema_version": evidence.get("schema_version"),
        "contract_sha256": canonical_sha256(evidence) if evidence else None,
        "selected_evidence_count": len(evidence.get("selected_evidence_ids") or []),
        "dropped_evidence_count": len(evidence.get("dropped_evidence_ids") or []),
        "citation_count": len(evidence.get("citations") or result.get("citations") or []),
        "context_chars": len(str(context.get("context_text") or "")),
        "assessment_status": assessment.get("status"),
        "index_version": lineage.get("index_version"),
        "dataset_version": lineage.get("dataset_version"),
        "embedding_model": lineage.get("embedding_model"),
        "retrieval_plan_id": (
            lineage.get("retrieval_plan_id")
            or lineage.get("retrieval_strategy")
        ),
    }


# 阅读注释（函数）：处理 工具 结果 summary 相关逻辑。
def tool_result_summary(result: Any) -> Dict[str, Any]:
    """处理 工具 结果 summary 相关逻辑。

    参数:
        result: 待处理的结果对象。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：dict, getattr, bool, get, payload.get, _rag_contract_summary。
    """
    payload = dict(getattr(result, "result", {}) or {})
    error = getattr(result, "error", None)
    summary = {
        "tool_call_id": getattr(result, "tool_call_id", None),
        "tool_name": getattr(result, "tool_name", None),
        "success": bool(getattr(result, "success", False)),
        "status": getattr(getattr(result, "status", None), "value", getattr(result, "status", None)),
        "latency_ms": getattr(result, "latency_ms", None),
        "output_schema": (getattr(result, "metadata", {}) or {}).get("output_schema"),
        "error_code": getattr(error, "error_code", None) if error else None,
        "error_type": getattr(error, "error_type", None) if error else None,
    }
    if payload.get("evidence") is not None or payload.get("schema_version") == "rag_tool_output_v1":
        summary["rag_evidence"] = _rag_contract_summary(payload)
    return summary


# 阅读注释（函数）：提取 工具 lineage。
def extract_tool_lineage(result: Any) -> Dict[str, Any]:
    """提取 工具 lineage。

    参数:
        result: 待处理的结果对象。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：dict, getattr, payload.get, evidence.get, lineage.get, trace.get, canonical_sha256。
    """
    payload = dict(getattr(result, "result", {}) or {})
    evidence = payload.get("evidence") or {}
    lineage = evidence.get("lineage") or {}
    trace = payload.get("trace") or {}
    return {
        "index_version": lineage.get("index_version") or trace.get("index_version"),
        "dataset_version": lineage.get("dataset_version"),
        "embedding_model": lineage.get("embedding_model") or trace.get("embedding_model"),
        "embedding_version": lineage.get("embedding_version") or trace.get("embedding_version"),
        "reranker_model": lineage.get("reranker_model") or trace.get("reranker_model"),
        "retrieval_plan_id": (
            lineage.get("retrieval_plan_id")
            or lineage.get("retrieval_strategy")
            or trace.get("retrieval_mode")
        ),
        "static_retrieval_spec_id": lineage.get("static_retrieval_spec_id"),
        "evidence_contract_sha256": canonical_sha256(evidence) if evidence else None,
    }
