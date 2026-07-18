"""Configuration-driven parent-candidate reranker plugins.

The online pipeline profile owns ranking behaviour such as ``top_k`` and the
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
    return build_context if isinstance(build_context, dict) else {}


def _resource_pool(build_context: Any) -> Any:
    pool = _build_context_dict(build_context).get("resource_pool")
    if pool is None:
        raise ValueError("reranker plugin requires build_context['resource_pool']")
    return pool


def _normalize_text_field(value: str) -> str:
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
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    def execution_metadata(self) -> dict[str, Any]:
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
    """Explicit profile-selected no-op reranker for smoke/evaluation runs."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 5,
        text_field: str = "parent_text",
    ) -> None:
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
        return self.backend.rerank(
            query=query,
            results=results,
            top_k=self.top_k,
            text_field=self.text_field,
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "text_field": self.text_field,
            "model_name": None,
            "mode": "noop",
        }
