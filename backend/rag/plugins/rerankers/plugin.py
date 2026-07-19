# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_build_context_dict、_resource_pool、_normalize_text_field、BGEParentCrossEncoderRerankerPlugin、NoOpParentRerankerPlugin。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Configuration-driven parent-candidate reranker plugins.

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


# 阅读注释（函数）：构建 上下文 字典。
def _build_context_dict(build_context: Any) -> dict[str, Any]:
    """构建 上下文 字典。

    参数:
        build_context: build 上下文，具体约束请结合类型标注和调用方确认。

    返回:
        dict[str, Any]

    阅读提示:
        主要直接调用：isinstance。
    """
    return build_context if isinstance(build_context, dict) else {}


# 阅读注释（函数）：处理 resource pool 相关逻辑。
def _resource_pool(build_context: Any) -> Any:
    """处理 resource pool 相关逻辑。

    参数:
        build_context: build 上下文，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：get, _build_context_dict, ValueError。
    """
    pool = _build_context_dict(build_context).get("resource_pool")
    if pool is None:
        raise ValueError("reranker plugin requires build_context['resource_pool']")
    return pool


# 阅读注释（函数）：规范化 文本 field。
def _normalize_text_field(value: str) -> str:
    """规范化 文本 field。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：strip, str, join, sorted, ValueError。
    """
    field = str(value or "").strip()
    if field not in _ALLOWED_TEXT_FIELDS:
        allowed = ", ".join(sorted(_ALLOWED_TEXT_FIELDS))
        raise ValueError(f"unsupported rerank text_field {field!r}; allowed: {allowed}")
    return field


# 阅读注释（类）：封装 bgeparent cross encoder reranker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class BGEParentCrossEncoderRerankerPlugin:
    """Rerank parent candidates with the configured cross-encoder resource."""

    # 阅读注释（函数）：初始化 BGEParentCrossEncoderRerankerPlugin，保存运行所需的依赖、配置或状态。
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
        """初始化 BGEParentCrossEncoderRerankerPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            text_field: 文本 field，具体约束请结合类型标注和调用方确认。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            device: device，具体约束请结合类型标注和调用方确认。
            batch_size: batch size，具体约束请结合类型标注和调用方确认。
            max_length: max length，具体约束请结合类型标注和调用方确认。
            local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, _normalize_text_field, get_parent_reranker, _resource_pool, str, getattr, bool。
        """
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

    # 阅读注释（函数）：对 BGEParentCrossEncoderRerankerPlugin 重新排序。
    def rerank(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对 BGEParentCrossEncoderRerankerPlugin 重新排序。

        参数:
            query: 当前检索或生成查询。
            results: 待处理的结果集合。

        返回:
            list[dict[str, Any]]

        阅读提示:
            主要直接调用：self.backend.rerank。
        """
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> dict[str, Any]:
        """处理 execution 元数据 相关逻辑。

        返回:
            dict[str, Any]
        """
        return {
            "top_k": self.top_k,
            "text_field": self.text_field,
            "model_name": self.model_name,
            "device": self.device,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "local_files_only": self.local_files_only,
        }


# 阅读注释（类）：封装 no op 父块 reranker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class NoOpParentRerankerPlugin:
    """Explicit static-spec no-op reranker for smoke/evaluation runs."""

    # 阅读注释（函数）：初始化 NoOpParentRerankerPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 5,
        text_field: str = "parent_text",
    ) -> None:
        """初始化 NoOpParentRerankerPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            text_field: 文本 field，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, _normalize_text_field, NoOpParentChildReranker。
        """
        del build_context
        from rag.reranker.parent_child_reranker import NoOpParentChildReranker

        self.top_k = max(1, int(top_k))
        self.text_field = _normalize_text_field(text_field)
        self.backend = NoOpParentChildReranker()

    # 阅读注释（函数）：对 NoOpParentRerankerPlugin 重新排序。
    def rerank(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对 NoOpParentRerankerPlugin 重新排序。

        参数:
            query: 当前检索或生成查询。
            results: 待处理的结果集合。

        返回:
            list[dict[str, Any]]

        阅读提示:
            主要直接调用：self.backend.rerank。
        """
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> dict[str, Any]:
        """处理 execution 元数据 相关逻辑。

        返回:
            dict[str, Any]
        """
        return {
            "top_k": self.top_k,
            "text_field": self.text_field,
            "model_name": None,
            "mode": "noop",
        }
