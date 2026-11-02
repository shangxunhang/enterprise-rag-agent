"""Map a public evidence request to the retrieval runtime input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.common.coercion import as_int, as_str_list
from schemas.rag import RAGToolInputSchema


def _escape_filter_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _in_expr(field: str, values: list[str]) -> str:
    escaped = [_escape_filter_value(item) for item in values]
    return f"{field} in [" + ", ".join(f'"{item}"' for item in escaped) + "]"


@dataclass(frozen=True)
class RAGInvocation:
    """Normalized data needed by the runtime and result mapper."""

    query: str
    payload: dict[str, Any]
    request_data: dict[str, Any]
    max_context_chars: int
    max_context_items: int
    max_context_tokens: int | None


class RAGRequestMapper:
    """Keep the runtime's dictionary boundary private to the RAG package.

    Legacy unscoped retrieval is an internal composition policy used only to
    keep local/demo indexes readable during migration. It is never controlled
    by request payload fields.
    """

    def __init__(self, *, allow_legacy_unscoped: bool = False) -> None:
        self.allow_legacy_unscoped = bool(allow_legacy_unscoped)

    def map(self, request: RAGToolInputSchema) -> RAGInvocation:
        query = request.query.strip()
        if not query:
            raise ValueError("RAGService requires a non-empty query")

        # Keep the public extension bag nested. Promoting arbitrary caller
        # keys into the normalized request would let untrusted values impersonate
        # authorization-sensitive fields such as a raw Milvus filter.
        request_data = request.model_dump()
        max_context_chars = as_int(request_data.get("max_context_chars"), 6000)
        max_context_items = as_int(request_data.get("max_context_items"), 3)
        max_context_tokens = (
            int(request.max_context_tokens)
            if request.max_context_tokens is not None
            else None
        )

        filters = request.filters
        legacy_doc_ids = as_str_list(filters.doc_ids)
        legacy_file_ids = as_str_list(filters.file_ids)
        requested_tenant_id = str(filters.tenant_id or "").strip()

        access_scope = request.access_scope
        if access_scope is None and not self.allow_legacy_unscoped:
            raise ValueError(
                "retrieval access scope is required; mandatory tenant/KB "
                "boundaries must be supplied by the trusted application boundary"
            )

        keyword_scope: dict[str, Any] = {}
        if access_scope is not None:
            if (
                requested_tenant_id
                and requested_tenant_id != access_scope.tenant_id
            ):
                raise ValueError(
                    "requested tenant_id conflicts with the authorized retrieval scope"
                )
            requested_kb_ids = as_str_list(request.kb_ids)
            effective_kb_ids = list(access_scope.authorized_kb_ids)
            if requested_kb_ids:
                requested_kb_set = set(requested_kb_ids)
                effective_kb_ids = [
                    item for item in effective_kb_ids if item in requested_kb_set
                ]
                if not effective_kb_ids:
                    raise ValueError("requested kb_ids are outside the authorized retrieval scope")

            allowed_doc_ids = list(access_scope.allowed_doc_ids)
            allowed_file_ids = list(access_scope.allowed_file_ids)
            # Legacy/request filters may only narrow an already-authorized scope.
            if legacy_doc_ids:
                if allowed_doc_ids:
                    requested_doc_set = set(legacy_doc_ids)
                    narrowed = [item for item in allowed_doc_ids if item in requested_doc_set]
                    if not narrowed:
                        raise ValueError("requested doc_ids are outside the authorized retrieval scope")
                    allowed_doc_ids = narrowed
                else:
                    allowed_doc_ids = legacy_doc_ids
            if legacy_file_ids:
                if allowed_file_ids:
                    requested_file_set = set(legacy_file_ids)
                    narrowed = [item for item in allowed_file_ids if item in requested_file_set]
                    if not narrowed:
                        raise ValueError("requested file_ids are outside the authorized retrieval scope")
                    allowed_file_ids = narrowed
                else:
                    allowed_file_ids = legacy_file_ids
            scope_parts = [
                f'tenant_id == "{_escape_filter_value(access_scope.tenant_id)}"',
                _in_expr("kb_id", effective_kb_ids),
            ]
            if allowed_file_ids:
                scope_parts.append(_in_expr("file_id", allowed_file_ids))
            if allowed_doc_ids:
                scope_parts.append(_in_expr("doc_id", allowed_doc_ids))
            filter_expr = " and ".join(scope_parts)
            keyword_scope = {
                "tenant_id": access_scope.tenant_id,
                "kb_ids": effective_kb_ids,
                "file_ids": allowed_file_ids,
                "doc_ids": allowed_doc_ids,
            }
        else:
            # Legacy local/demo mode remains supported until its index is rebuilt
            # with tenant/KB metadata.  It is intentionally not advertised as a
            # security boundary.
            allowed_doc_ids = legacy_doc_ids
            filter_expr = (
                _in_expr("doc_id", allowed_doc_ids)
                if allowed_doc_ids
                else ""
            )
            keyword_scope = {"doc_ids": allowed_doc_ids}

        # Public typed request -> RAG internal runtime projection. ``extra`` is
        # retained only under a diagnostic namespace and cannot override these
        # canonical values.
        planner_context: dict[str, Any] = {
            "task_id": request.task_id,
            "run_id": request.run_id,
            "workflow_run_id": request.run_id,
            "caller_agent": request.agent_name,
            "retrieval_trace_id": request.retrieval_trace_id,
            "retrieval_scope": request.retrieval_scope,
            "task_type": request.task_type,
            "need_citation": bool(request.need_citation),
        }
        if request.evidence_requirements is not None:
            planner_context["evidence_requirements"] = (
                request.evidence_requirements.model_dump(mode="json")
            )
        if request.document_context is not None:
            planner_context["document_context"] = request.document_context.model_dump(
                mode="json"
            )
        if request.extra:
            planner_context["request_metadata"] = dict(request.extra)

        context_requirements = {
            "max_context_chars": max_context_chars,
            "max_context_items": max_context_items,
        }
        if max_context_tokens is not None:
            context_requirements["max_context_tokens"] = max_context_tokens
        planner_context["context_requirements"] = context_requirements
        payload = {
            "query": query,
            "max_context_chars": max_context_chars,
            "max_context_items": max_context_items,
            "max_context_tokens": max_context_tokens,
            "extra_metadata": planner_context,
            "filter_expr": filter_expr or None,
            "keyword_doc_ids": allowed_doc_ids,
            "keyword_scope": keyword_scope,
            "access_scope_enforced": access_scope is not None,
            "return_full_record": True,
        }
        return RAGInvocation(
            query=query,
            payload=payload,
            request_data=request_data,
            max_context_chars=max_context_chars,
            max_context_items=max_context_items,
            max_context_tokens=max_context_tokens,
        )
