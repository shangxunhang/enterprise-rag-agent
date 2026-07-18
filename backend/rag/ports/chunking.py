"""Stable contracts for offline chunk building."""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from rag.chunker.ChildParentChunker import ParentChildChunkResult


class ChunkerPort(Protocol):
    """Convert cleaned_text_unit_v1 records into parent/child chunks."""

    def chunk_records(
        self,
        records: Iterable[dict[str, Any]],
    ) -> ParentChildChunkResult: ...

    def execution_metadata(self) -> dict[str, Any]: ...
