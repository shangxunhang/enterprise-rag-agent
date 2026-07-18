"""Map public RAG requests to the legacy tool payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from schemas.rag import RAGToolInputSchema
from .coercion import as_bool, as_int, as_str_list


@dataclass(frozen=True)
class LegacyInvocation:
    query: str
    payload: Dict[str, Any]
    tool_input: Dict[str, Any]
    retrieval_strategy: str
    max_context_chars: int
    max_context_items: int
    generate_answer: bool


class LegacyRAGRequestMapper:
    def __init__(
        self,
        *,
        default_generate_answer: bool = False,
    ) -> None:
        self.default_generate_answer = default_generate_answer

    def map(self, request: RAGToolInputSchema) -> LegacyInvocation:
        query = request.query.strip()
        if not query:
            raise ValueError("LegacyRAGService requires a non-empty query.")
        tool_input: Dict[str, Any] = {**request.model_dump(), **request.extra}
        max_context_chars = as_int(tool_input.get("max_context_chars"), 6000)
        max_context_items = as_int(tool_input.get("max_context_items"), 3)
        retrieval_strategy = str(
            tool_input.get("retrieval_strategy")
            or tool_input.get("retrieval_mode")
            or "hybrid"
        ).strip() or "hybrid"
        generate_answer = as_bool(
            tool_input.get("generate_answer"),
            default=self.default_generate_answer,
        )
        # Deprecated request switches are retained only for compatibility and
        # audit. They are never inferred from the legacy strategy string; the
        # selected online profile is the sole source of plugin behavior.
        enable_crag = as_bool(tool_input.get("enable_crag"), default=False)
        enable_self_rag = as_bool(
            tool_input.get("enable_self_rag"), default=False
        )
        enable_hyde = as_bool(tool_input.get("enable_hyde"), default=False)
        filters = tool_input.get("filters") or {}
        allowed_doc_ids = as_str_list(
            filters.get("doc_ids") or tool_input.get("doc_ids")
        )
        filter_expr = str(tool_input.get("filter_expr") or "").strip()
        if not filter_expr and allowed_doc_ids:
            escaped = [
                item.replace("\\", "\\\\").replace('"', '\\"')
                for item in allowed_doc_ids
            ]
            filter_expr = "doc_id in [" + ", ".join(
                f'"{item}"' for item in escaped
            ) + "]"
        payload = {
            "query": query,
            "generate_answer": generate_answer,
            "need_citation": as_bool(tool_input.get("need_citation"), default=True),
            "retrieval_strategy": retrieval_strategy,
            "num_rewrites": as_int(tool_input.get("num_rewrites"), 3),
            "enable_hyde": enable_hyde,
            "enable_crag": enable_crag,
            "enable_self_rag": enable_self_rag,
            "crag_max_judge_chunks": as_int(
                tool_input.get("crag_max_judge_chunks"), 8
            ),
            "crag_drop_irrelevant": as_bool(
                tool_input.get("crag_drop_irrelevant"), default=True
            ),
            "dense_top_k": as_int(tool_input.get("dense_top_k"), 10),
            "keyword_top_k": as_int(tool_input.get("keyword_top_k"), 10),
            "candidate_top_k": as_int(tool_input.get("candidate_top_k"), 10),
            "rerank_top_k": as_int(tool_input.get("rerank_top_k"), 5),
            "max_context_chars": max_context_chars,
            "max_context_items": max_context_items,
            # Preserve Agent/workflow context across the public RAG boundary.
            # C-RAG's section-aware corrective planner depends on fields such as
            # document_title, required_sections and citation_required_sections.
            # Dropping this metadata silently degrades the planner to generic
            # fallback queries even though the ToolCall contained the context.
            "extra_metadata": dict(tool_input.get("extra_metadata") or {}),
            "filter_expr": filter_expr or None,
            "keyword_doc_ids": allowed_doc_ids,
            "return_full_record": True,
        }
        return LegacyInvocation(
            query=query,
            payload=payload,
            tool_input=tool_input,
            retrieval_strategy=retrieval_strategy,
            max_context_chars=max_context_chars,
            max_context_items=max_context_items,
            generate_answer=generate_answer,
        )
