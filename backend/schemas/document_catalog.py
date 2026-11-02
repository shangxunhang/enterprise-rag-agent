"""Neutral public facts required for retrieval authorization decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentAccessDescriptorSchema(BaseModel):
    """Minimum catalog facts exposed to the trusted Application boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    document_id: str
    tenant_id: str
    kb_id: str
    indexable: bool
    project_relation: Literal[
        "current_project",
        "other_project",
        "general",
        "unknown",
    ]
    project_id: str | None = None


__all__ = ["DocumentAccessDescriptorSchema"]
