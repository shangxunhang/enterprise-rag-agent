"""Map a public evidence request to the retrieval runtime input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.common.coercion import as_int, as_str_list
from schemas.rag import RAGToolInputSchema, RetrievalAccessScopeSchema


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

        request_data = {
            **request.model_dump(),
            **dict(request.extra or {}),
        }
        max_context_chars = as_int(request_data.get("max_context_chars"), 6000)
        max_context_items = as_int(request_data.get("max_context_items"), 3)

        filters = request_data.get("filters") or {}
        legacy_doc_ids = as_str_list(filters.get("doc_ids") or request_data.get("doc_ids"))
        legacy_file_ids = as_str_list(filters.get("file_ids") or request_data.get("file_ids"))
        requested_filter_expr = str(request_data.get("filter_expr") or "").strip()

        access_scope = request.access_scope
        # Backward-compatible bridge: callers that already send tenant_id +
        # kb_ids through the legacy contract are upgraded to a strict scope.
        if access_scope is None:
            tenant_id = str(filters.get("tenant_id") or "").strip()
            legacy_kb_ids = as_str_list(request.kb_ids)
            if tenant_id and legacy_kb_ids:
                access_scope = RetrievalAccessScopeSchema(
                    tenant_id=tenant_id,
                    authorized_kb_ids=legacy_kb_ids,
                    allowed_file_ids=legacy_file_ids,
                    allowed_doc_ids=legacy_doc_ids,
                )
            elif not self.allow_legacy_unscoped:
                raise ValueError(
                    "retrieval access scope is required; mandatory tenant/KB "
                    "boundaries cannot be disabled by request payloads"
                )

        keyword_scope: dict[str, Any] = {}
        if access_scope is not None:
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
            if requested_filter_expr:
                filter_expr = f"({filter_expr}) and ({requested_filter_expr})"
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
            filter_expr = requested_filter_expr
            if not filter_expr and allowed_doc_ids:
                filter_expr = _in_expr("doc_id", allowed_doc_ids)
            keyword_scope = {"doc_ids": allowed_doc_ids}

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
        )
