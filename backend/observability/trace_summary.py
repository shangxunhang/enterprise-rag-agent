"""为 Trace v2 事件生成稳定、有界且脱敏的摘要和血缘。

Trace 是可观测性产品，不应成为提示词、文档和模型输出的第二份完整副本。本模块保留
调用身份、长度、哈希、有限预览、模型参数、证据契约摘要和版本血缘，同时限制递归
深度、集合数量并按键名清除常见敏感字段。
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


def sha256_text(value: str) -> str:
    """计算文本 UTF-8 字节的 SHA-256 摘要。

    Args:
        value: 要绑定内容身份的完整文本。

    Returns:
        64 位十六进制摘要。
    """

    # 对 UTF-8 字节计算哈希；该摘要用于内容关联，不替代访问控制或数字签名。
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preview_text(value: Any, limit: int = 240) -> str:
    """把任意值转换为单行、长度受限的文本预览。

    Args:
        value: 要预览的值；空值转换为空字符串。
        limit: 最多保留的字符数。

    Returns:
        去除首尾空白并替换换行后的单行文本；超限时追加省略号。
    """

    # 第一步：统一转为文本并消除换行，避免一条 JSONL 摘要形成多行视觉内容。
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()

    # 第二步：未超限时完整返回，超限时只保留前缀并明确标记截断。
    return text if len(text) <= limit else f"{text[:limit]}…"


def _safe_value(value: Any, *, depth: int, max_depth: int, max_items: int) -> Any:
    """递归把任意值转换为有界、可序列化且按键脱敏的摘要。

    Args:
        value: 当前待摘要值。
        depth: 当前递归深度。
        max_depth: 允许展开的最大深度。
        max_items: 每个映射或集合最多展开的项目数。

    Returns:
        由标量、字典和列表构成的安全摘要。
    """
    # 第一步：达到最大深度时停止展开，防止深层结构造成无限增长。
    if depth >= max_depth:
        return "<max_depth>"

    # 第二步：空值和简单数值类型可以直接保留。
    if value is None or isinstance(value, (bool, int, float)):
        return value

    # 第三步：字符串只记录长度、哈希和有限预览，不复制完整正文。
    if isinstance(value, str):
        return {
            "chars": len(value),
            "sha256": sha256_text(value),
            "preview": preview_text(value),
        }
    # 第四步：映射按项目上限展开，并根据键名清除常见敏感值。
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
    # 第五步：集合统一转换为包含总数、有限项目和截断标记的摘要。
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
    # 第六步：Pydantic 模型先转为 JSON 数据，再复用相同限制与脱敏规则。
    if hasattr(value, "model_dump"):
        return _safe_value(
            value.model_dump(mode="json"),
            depth=depth,
            max_depth=max_depth,
            max_items=max_items,
        )
    # 第七步：未知对象只保留 repr 的有限预览，不遍历任意属性。
    return preview_text(repr(value))


def bounded_summary(value: Any, *, max_depth: int = 4, max_items: int = 20) -> Any:
    """生成具有统一深度、数量和敏感键限制的摘要。

    Args:
        value: 任意 JSON-like、Pydantic 或普通 Python 值。
        max_depth: 最大递归展开深度。
        max_items: 每层映射或集合最多展开的项目数。

    Returns:
        可安全进入 Trace JSONL 的有界摘要。
    """
    # 从根深度开始应用统一递归、数量和敏感键限制。
    return _safe_value(value, depth=0, max_depth=max_depth, max_items=max_items)


def canonical_sha256(value: Any) -> str:
    """为任意可序列化值计算键顺序无关的规范哈希。

    Args:
        value: 要绑定内容身份的结构化值。

    Returns:
        按键排序 JSON 文本的 SHA-256 摘要。
    """
    # 第一步：按键排序生成确定性 JSON；default=str 只兼容少量扩展对象。
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    # 第二步：复用文本哈希函数绑定规范结构内容。
    return sha256_text(payload)


def model_request_summary(request: Any, model_name: str) -> Dict[str, Any]:
    """提取模型请求身份、提示词摘要和主要生成参数。

    Args:
        request: ``ModelRequestSchema`` 或满足相同属性约定的对象。
        model_name: 网关实际解析出的模型名称。

    Returns:
        不包含完整提示词和消息正文的结构化摘要。
    """
    # 第一步：读取提示词文本，仅用于长度、哈希和有限预览。
    prompt = str(getattr(request, "prompt", "") or "")
    system_prompt = str(getattr(request, "system_prompt", "") or "")

    # 第二步：复制扩展字段并提取统一生成参数子字典。
    extra = dict(getattr(request, "extra", {}) or {})
    generation_params = dict(extra.get("generation_params") or {})

    # 第三步：返回足以定位调用和复现参数、但不复制完整上下文的摘要。
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
        "top_p": generation_params.get("top_p"),
        "do_sample": generation_params.get("do_sample"),
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


def model_response_summary(response: Any) -> Dict[str, Any]:
    """提取模型响应状态、内容摘要、用量和错误类别。

    Args:
        response: ``ModelResponseSchema`` 或满足相同属性约定的对象。

    Returns:
        不包含完整模型输出和原始供应商响应的结构化摘要。
    """
    # 第一步：读取规范文本，只用于长度、哈希和有限预览。
    content = str(getattr(response, "content", "") or "")

    # 第二步：把 token_usage 模型或映射统一转为普通字典。
    usage = getattr(response, "token_usage", None)
    usage_dict = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else dict(usage or {})

    # 第三步：结构化错误只暴露稳定错误码和类型。
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


def tool_call_summary(tool_call: Any) -> Dict[str, Any]:
    """提取工具调用身份、输入键和检索类常用参数。

    Args:
        tool_call: ``ToolCallSchema`` 或满足相同属性约定的对象。

    Returns:
        不包含完整工具输入的结构化摘要。
    """
    # 第一步：复制工具输入，并单独识别可能体积较大的检索查询。
    tool_input = dict(getattr(tool_call, "tool_input", {}) or {})
    query = str(tool_input.get("query") or "")

    # 第二步：只记录输入键、查询摘要和常用检索预算参数。
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


def _rag_contract_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """从 RAG 工具结果提取证据规模、评估状态和版本血缘。

    Args:
        result: RAG 工具的结构化 ``result`` 字典。

    Returns:
        不包含证据正文的规范证据契约摘要。
    """
    # 第一步：规范 evidence 嵌套优先，旧 context 投影只作为迁移期回退。
    evidence = result.get("evidence") or {}
    lineage = evidence.get("lineage") or {}
    assessment = evidence.get("assessment") or {}
    context = evidence.get("context") or result.get("context") or {}

    # 第二步：用完整契约哈希绑定内容，只暴露数量、评估和版本字段。
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


def tool_result_summary(result: Any) -> Dict[str, Any]:
    """提取工具结果状态、耗时、错误和可选 RAG 证据摘要。

    Args:
        result: ``ToolResultSchema`` 或满足相同属性约定的对象。

    Returns:
        不包含完整工具输出的结构化摘要。
    """
    # 第一步：复制标准 result 字典，并读取结构化错误。
    payload = dict(getattr(result, "result", {}) or {})
    error = getattr(result, "error", None)

    # 第二步：构造所有工具共用的状态、耗时和错误摘要。
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
    # 第三步：结果明确携带证据或 RAG 输出 Schema 时，追加专用证据摘要。
    if payload.get("evidence") is not None or payload.get("schema_version") == "rag_tool_output_v1":
        summary["rag_evidence"] = _rag_contract_summary(payload)

    return summary


def extract_tool_lineage(result: Any) -> Dict[str, Any]:
    """从工具结果提取 RAG 索引、模型、计划和证据契约血缘。

    Args:
        result: ``ToolResultSchema`` 或满足相同属性约定的对象。

    Returns:
        对非 RAG 工具通常大部分为空的稳定血缘字典。
    """
    # 第一步：规范 evidence.lineage 优先，旧 trace 字段作为迁移期回退。
    payload = dict(getattr(result, "result", {}) or {})
    evidence = payload.get("evidence") or {}
    lineage = evidence.get("lineage") or {}
    trace = payload.get("trace") or {}

    # 第二步：返回版本与模型血缘，并以哈希关联完整证据契约而不复制正文。
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
