"""Post-write correction execution boundary.

Semantic prose repair is intentionally unreachable here. The only allowed work is
deterministic mechanical transformation whose exact input/output refs are supplied
by the caller. Writer/model repair is exclusively authorized through SIF-11
ParagraphRepairAuthorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Any

from core.hashing import canonical_sha256


class CorrectionExecutionError(RuntimeError):
    pass


class SemanticCorrectionExecutorForbidden(CorrectionExecutionError):
    """Raised when Post-write attempts to perform semantic/model prose repair."""


@dataclass(frozen=True)
class MechanicalCorrectionResult:
    repair_kind: str
    exact_input_refs: tuple[str, ...]
    output_payload: Mapping[str, Any]
    output_sha256: str


class CorrectionExecutor:
    """Deterministic mechanical-only executor.

    The class deliberately has no model/retrieval/tool dependencies and no method
    that accepts ArtifactCorrectionAuthorization as semantic dispatch authority.
    """

    def execute_mechanical(
        self,
        *,
        repair_kind: str,
        exact_input_refs: tuple[str, ...],
        transform: Callable[[], Mapping[str, Any]],
    ) -> MechanicalCorrectionResult:
        normalized_kind = str(repair_kind or "").strip()
        if not normalized_kind:
            raise ValueError("repair_kind is required")
        refs = tuple(exact_input_refs)
        if refs != tuple(sorted(set(refs))):
            raise ValueError("exact_input_refs must be canonical ordered-unique")
        if not callable(transform):
            raise TypeError("mechanical transform must be callable")
        payload = dict(transform())
        return MechanicalCorrectionResult(
            repair_kind=normalized_kind,
            exact_input_refs=refs,
            output_payload=payload,
            output_sha256=canonical_sha256(payload),
        )

    def execute_semantic(self, *args, **kwargs):
        raise SemanticCorrectionExecutorForbidden(
            "semantic correction is owned by ParagraphRepairAuthorization + Writer REPAIR"
        )


class CorrectionInputBuilder:
    """Mechanical-only exact-input normalizer; no semantic authority inference."""

    @staticmethod
    def canonical_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(item).strip() for item in refs)
        if any(not item for item in normalized):
            raise ValueError("mechanical correction refs cannot be blank")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("mechanical correction refs must be ordered-unique")
        return normalized


class CorrectionTechnicalRetryPolicy:
    """Finite technical retry helper for mechanical operations only."""

    def __init__(self, max_attempts: int) -> None:
        self.max_attempts = int(max_attempts)
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

    def allows_attempt(self, attempt_ordinal: int) -> bool:
        ordinal = int(attempt_ordinal)
        return 1 <= ordinal <= self.max_attempts


__all__ = [
    "CorrectionExecutionError",
    "CorrectionExecutor",
    "CorrectionInputBuilder",
    "CorrectionTechnicalRetryPolicy",
    "MechanicalCorrectionResult",
    "SemanticCorrectionExecutorForbidden",
]
