"""Unified runtime envelopes for retrieval plugin composition.

The candidate payload remains a dictionary during the migration so existing
citation/rerank code keeps its exact semantics. The envelope itself is stable
and framework-independent, which lets retrievers, fusion plugins and candidate
enrichers compose without knowing each other's concrete classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    filter_expr: str | None = None
    doc_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.query or "").strip():
            raise ValueError("retrieval query cannot be empty")


@dataclass
class CandidateSet:
    query: str
    source_name: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def copy_with(
        self,
        *,
        source_name: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CandidateSet":
        return CandidateSet(
            query=self.query,
            source_name=source_name or self.source_name,
            candidates=list(self.candidates if candidates is None else candidates),
            metadata=dict(self.metadata if metadata is None else metadata),
        )
