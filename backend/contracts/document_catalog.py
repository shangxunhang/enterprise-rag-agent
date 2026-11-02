"""Public narrow fact-reading port for the document catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from schemas.document_catalog import DocumentAccessDescriptorSchema


class DocumentCatalogPort(Protocol):
    """Read authorization-relevant catalog facts without making authorization decisions."""

    def list_access_descriptors(
        self,
        *,
        project_root: Path,
    ) -> Sequence[DocumentAccessDescriptorSchema]: ...


__all__ = ["DocumentCatalogPort"]
