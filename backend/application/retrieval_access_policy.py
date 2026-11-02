"""Application-owned retrieval authorization policy over neutral catalog facts."""

from __future__ import annotations

from collections.abc import Sequence

from schemas.document_catalog import DocumentAccessDescriptorSchema
from schemas.rag import RetrievalAccessScopeSchema


def narrow_retrieval_access_scope(
    *,
    descriptors: Sequence[DocumentAccessDescriptorSchema],
    current_project_id: str,
    retrieval_access_scope: RetrievalAccessScopeSchema,
) -> RetrievalAccessScopeSchema:
    """Narrow trusted retrieval authority while preserving the frozen V1 semantics."""

    project_id = str(current_project_id or "").strip()
    if not project_id:
        raise ValueError("current_project_id is required for project evidence scope")

    authorized_kb_ids = set(retrieval_access_scope.authorized_kb_ids)
    eligible_doc_ids: list[str] = []
    for descriptor in descriptors:
        if not descriptor.indexable:
            continue
        if descriptor.tenant_id != retrieval_access_scope.tenant_id:
            continue
        if descriptor.kb_id not in authorized_kb_ids:
            continue
        if (
            descriptor.kb_id == "project_fact_kb"
            and descriptor.project_id != project_id
        ):
            continue
        eligible_doc_ids.append(descriptor.document_id)

    existing_allowed = list(retrieval_access_scope.allowed_doc_ids)
    if existing_allowed:
        eligible = set(eligible_doc_ids)
        effective_allowed = [
            document_id
            for document_id in existing_allowed
            if document_id in eligible
        ]
    else:
        effective_allowed = eligible_doc_ids

    if not effective_allowed:
        raise ValueError(
            "current project retrieval scope resolved to no authorized documents"
        )

    return retrieval_access_scope.model_copy(
        update={"allowed_doc_ids": effective_allowed}
    )


__all__ = ["narrow_retrieval_access_scope"]
