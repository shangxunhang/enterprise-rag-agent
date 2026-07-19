"""Map a public evidence request to the retrieval runtime input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.common.coercion import as_int, as_str_list
from schemas.rag import RAGToolInputSchema


@dataclass(frozen=True)
class RAGInvocation:
    """Normalized data needed by the runtime and result mapper."""

    query: str
    payload: dict[str, Any]
    request_data: dict[str, Any]
    max_context_chars: int
    max_context_items: int


class RAGRequestMapper:
    """Keep the runtime's dictionary boundary private to the RAG package."""

    def map(self, request: RAGToolInputSchema) -> RAGInvocation:
        query = request.query.strip()
        if not query:
            raise ValueError("RAGService requires a non-empty query")

        request_data = {
            **request.model_dump(),
            **dict(request.extra or {}),
        }
        max_context_chars = as_int(request_data.get("max_context_chars"), 6000)
        max_context_items = as_int(request_data.get("max_context_items"), 3)

        filters = request_data.get("filters") or {}
        allowed_doc_ids = as_str_list(
            filters.get("doc_ids") or request_data.get("doc_ids")
        )
        filter_expr = str(request_data.get("filter_expr") or "").strip()
        if not filter_expr and allowed_doc_ids:
            escaped = [
                item.replace("\\", "\\\\").replace('"', '\\"')
                for item in allowed_doc_ids
            ]
            filter_expr = "doc_id in [" + ", ".join(
                f'"{item}"' for item in escaped
            ) + "]"

        planner_context = dict(request_data.get("extra_metadata") or {})
        planner_context.setdefault("need_citation", bool(request.need_citation))
        context_requirements = dict(
            planner_context.get("context_requirements") or {}
        )
        context_requirements.setdefault("max_context_chars", max_context_chars)
        context_requirements.setdefault("max_evidence_items", max_context_items)
        planner_context["context_requirements"] = context_requirements
        payload = {
            "query": query,
            "max_context_chars": max_context_chars,
            "max_context_items": max_context_items,
            "extra_metadata": planner_context,
            "filter_expr": filter_expr or None,
            "keyword_doc_ids": allowed_doc_ids,
            "return_full_record": True,
        }
        return RAGInvocation(
            query=query,
            payload=payload,
            request_data=request_data,
            max_context_chars=max_context_chars,
            max_context_items=max_context_items,
        )
