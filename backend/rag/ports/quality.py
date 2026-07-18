"""Stable ports for retrieval-evidence and generation-quality plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class EvidenceCorrectionPlan:
    """Conditional re-retrieval decision emitted by an evidence grader.

    The retrieval pipeline treats this as a generic plan. It does not inspect
    concrete grader names such as CRAG, which keeps the corrective loop
    configuration-driven and reusable by future evidence graders.
    """

    required: bool = False
    queries: list[str] = field(default_factory=list)
    reason: str = ""
    max_rounds: int = 1
    merge_original_candidates: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_queries(self, *, original_query: str = "") -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        original_key = str(original_query or "").strip().lower()
        for item in self.queries:
            if isinstance(item, dict):
                raw_text = next(
                    (
                        item.get(key)
                        for key in ("query", "text", "rewritten_query", "value")
                        if isinstance(item.get(key), str) and item.get(key).strip()
                    ),
                    "",
                )
            else:
                raw_text = item
            text = str(raw_text or "").strip()
            if not text:
                continue
            key = text.lower()
            if key == original_key or key in seen:
                continue
            seen.add(key)
            output.append(text)
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": bool(self.required),
            "queries": list(self.queries),
            "reason": str(self.reason or ""),
            "max_rounds": max(0, int(self.max_rounds)),
            "merge_original_candidates": bool(self.merge_original_candidates),
            "metadata": dict(self.metadata),
        }


@dataclass
class EvidenceGradeOutput:
    """Result of applying one configured evidence grader."""

    results: list[dict[str, Any]]
    report: dict[str, Any] | None
    correction: EvidenceCorrectionPlan | None = None


@dataclass
class RepairOutput:
    """Result of applying one configured answer/section repair strategy."""

    answer: str
    repaired: bool
    report: dict[str, Any]


class EvidenceGraderPort(Protocol):
    def grade(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceGradeOutput: ...

    def execution_metadata(self) -> dict[str, Any]: ...


class GenerationCheckerPort(Protocol):
    def check(
        self,
        *,
        query: str,
        answer: str | None,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    def execution_metadata(self) -> dict[str, Any]: ...


class RepairStrategyPort(Protocol):
    def repair(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]],
        check_result: dict[str, Any] | None,
        runtime_context: dict[str, Any] | None = None,
    ) -> RepairOutput: ...

    def execution_metadata(self) -> dict[str, Any]: ...
