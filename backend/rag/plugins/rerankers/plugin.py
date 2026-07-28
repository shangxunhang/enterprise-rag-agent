"""配置驱动的父级候选重排插件。

The static retrieval specification owns ranking behaviour such as ``top_k`` and the
candidate text field. Deployment-specific model paths/devices remain in the
runtime resource configuration and are resolved through ``ParentChildResourcePool``.
"""

from __future__ import annotations

from typing import Any


_ALLOWED_TEXT_FIELDS = {
    "parent_text",
    "text",
    "child_text",
}


def _build_context_dict(build_context: Any) -> dict[str, Any]:
    """把可选组合根上下文规范为普通字典。"""
    return build_context if isinstance(build_context, dict) else {}


def _resource_pool(build_context: Any) -> Any:
    """读取必需资源池，避免插件自行重复加载 Cross-Encoder。"""
    pool = _build_context_dict(build_context).get("resource_pool")
    if pool is None:
        raise ValueError("reranker plugin requires build_context['resource_pool']")
    return pool


def _normalize_text_field(value: str) -> str:
    """限制重排正文来源为父块、通用正文或子块三个明确字段。"""
    field = str(value or "").strip()
    if field not in _ALLOWED_TEXT_FIELDS:
        allowed = ", ".join(sorted(_ALLOWED_TEXT_FIELDS))
        raise ValueError(f"unsupported rerank text_field {field!r}; allowed: {allowed}")
    return field


class BGEParentCrossEncoderRerankerPlugin:
    """Rerank parent candidates with the configured cross-encoder resource."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 5,
        text_field: str = "parent_text",
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        max_length: int | None = None,
        local_files_only: bool | None = None,
    ) -> None:
        """解析静态排序参数，并从资源池取得匹配资源配置的 Cross-Encoder。"""
        self.top_k = max(1, int(top_k))
        self.text_field = _normalize_text_field(text_field)
        self.backend = _resource_pool(build_context).get_parent_reranker(
            model_name=model_name,
            device=device,
            batch_size=batch_size,
            max_length=max_length,
            local_files_only=local_files_only,
        )
        self.model_name = str(getattr(self.backend, "model_name", model_name or ""))
        self.device = str(getattr(self.backend, "device", device or ""))
        self.batch_size = int(getattr(self.backend, "batch_size", batch_size or 0))
        self.max_length = int(getattr(self.backend, "max_length", max_length or 0))
        self.local_files_only = bool(
            getattr(
                self.backend,
                "local_files_only",
                True if local_files_only is None else local_files_only,
            )
        )

    def rerank(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按配置正文和 top-k 委托共享父级 Cross-Encoder 重排。"""
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    def execution_metadata(self) -> dict[str, Any]:
        """返回本次实际模型资源与排序参数，供 Trace 和评测记录。"""
        return {
            "top_k": self.top_k,
            "text_field": self.text_field,
            "model_name": self.model_name,
            "device": self.device,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "local_files_only": self.local_files_only,
        }


class NoOpParentRerankerPlugin:
    """Explicit static-spec no-op reranker for smoke/evaluation runs."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 5,
        text_field: str = "parent_text",
    ) -> None:
        """构造显式无模型基线，并保留与真实插件相同的 top-k/正文配置。"""
        del build_context
        from rag.reranker.parent_child_reranker import NoOpParentChildReranker

        self.top_k = max(1, int(top_k))
        self.text_field = _normalize_text_field(text_field)
        self.backend = NoOpParentChildReranker()

    def rerank(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """不调用模型，按原顺序投影兼容 ``rerank_score``。"""
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    def execution_metadata(self) -> dict[str, Any]:
        """明确标记 ``noop``，防止评测把该路径误认为真实模型重排。"""
        return {
            "top_k": self.top_k,
            "text_field": self.text_field,
            "model_name": None,
            "mode": "noop",
        }
