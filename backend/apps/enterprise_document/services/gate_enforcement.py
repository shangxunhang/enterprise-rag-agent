"""Centralized enforcement policy for business gate decisions.

The gate decision itself remains authoritative.  This module only decides whether a
failed decision is allowed to continue in the current runtime mode.  Technical and
integrity failures are never shadowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class GateEnforcementMode(str, Enum):
    STRICT = "strict"
    SHADOW = "shadow"


class GateEnforcementAction(str, Enum):
    CONTINUE = "continue"
    BLOCK = "block"


class GateFailureKind(str, Enum):
    BUSINESS = "business"
    TECHNICAL = "technical"


@dataclass(frozen=True)
class GateEnforcementDecision:
    """Execution consequence of one already-computed gate decision."""

    mode: GateEnforcementMode
    action: GateEnforcementAction
    gate_passed: bool
    failure_kind: GateFailureKind
    would_block_in_strict: bool

    @property
    def should_continue(self) -> bool:
        return self.action is GateEnforcementAction.CONTINUE

    @property
    def should_block(self) -> bool:
        return self.action is GateEnforcementAction.BLOCK

    def as_metadata(self, *, gate_id: str | None = None) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "enforcement_mode": self.mode.value,
            "enforcement_action": self.action.value,
            "would_block_in_strict": self.would_block_in_strict,
            "failure_kind": self.failure_kind.value,
            "gate_passed": self.gate_passed,
        }
        if gate_id:
            metadata["gate_id"] = gate_id
        return metadata


class GateEnforcementController:
    """Resolve BLOCK/CONTINUE without changing the underlying gate result."""

    def __init__(self, mode: str | GateEnforcementMode = GateEnforcementMode.STRICT):
        try:
            self.mode = GateEnforcementMode(str(getattr(mode, "value", mode)).strip().lower())
        except ValueError as exc:
            raise ValueError(
                "gate enforcement mode must be one of: strict, shadow"
            ) from exc

    @property
    def is_shadow(self) -> bool:
        return self.mode is GateEnforcementMode.SHADOW

    def resolve(
        self,
        *,
        gate_passed: bool,
        failure_kind: GateFailureKind = GateFailureKind.BUSINESS,
    ) -> GateEnforcementDecision:
        if gate_passed:
            action = GateEnforcementAction.CONTINUE
        elif failure_kind is GateFailureKind.TECHNICAL:
            action = GateEnforcementAction.BLOCK
        elif self.mode is GateEnforcementMode.SHADOW:
            action = GateEnforcementAction.CONTINUE
        else:
            action = GateEnforcementAction.BLOCK
        return GateEnforcementDecision(
            mode=self.mode,
            action=action,
            gate_passed=bool(gate_passed),
            failure_kind=failure_kind,
            would_block_in_strict=not bool(gate_passed),
        )


__all__ = [
    "GateEnforcementAction",
    "GateEnforcementController",
    "GateEnforcementDecision",
    "GateEnforcementMode",
    "GateFailureKind",
]
