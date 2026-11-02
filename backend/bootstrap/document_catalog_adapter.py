"""Composition-layer adapter from the RAG catalog to neutral public access facts."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from rag.catalog.document_profile_registry import DocumentProfileRegistry
from schemas.document_catalog import DocumentAccessDescriptorSchema


class DocumentProfileCatalogAdapter:
    """Project RAG-internal document profiles into public authorization facts only."""

    def list_access_descriptors(
        self,
        *,
        project_root: Path,
    ) -> Sequence[DocumentAccessDescriptorSchema]:
        catalog_path = project_root / "data/catalog/document_profiles_v1.jsonl"
        registry = DocumentProfileRegistry.from_jsonl(catalog_path)
        return [
            DocumentAccessDescriptorSchema(
                document_id=profile.document_id,
                tenant_id=profile.tenant_id,
                kb_id=profile.kb_id,
                indexable=profile.metadata.get("should_index") is True,
                project_relation=profile.project_relation.relation.value,
                project_id=profile.project_relation.project_id,
            )
            for profile in registry.all()
        ]


__all__ = ["DocumentProfileCatalogAdapter"]
