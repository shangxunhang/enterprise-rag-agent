from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.gate_enforcement import (
    GateEnforcementAction,
    GateEnforcementController,
    GateFailureKind,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from stage_e_integrity_test_fixtures import make_binding


def test_passed_gate_continues_in_strict_and_shadow() -> None:
    for mode in ("strict", "shadow"):
        decision = GateEnforcementController(mode).resolve(gate_passed=True)
        assert decision.action is GateEnforcementAction.CONTINUE
        assert decision.would_block_in_strict is False
        assert decision.gate_passed is True


def test_business_failure_blocks_in_strict_but_continues_in_shadow() -> None:
    strict = GateEnforcementController("strict").resolve(gate_passed=False)
    shadow = GateEnforcementController("shadow").resolve(gate_passed=False)

    assert strict.action is GateEnforcementAction.BLOCK
    assert shadow.action is GateEnforcementAction.CONTINUE
    assert shadow.gate_passed is False
    assert shadow.would_block_in_strict is True


def test_technical_failure_is_never_shadowed() -> None:
    for mode in ("strict", "shadow"):
        decision = GateEnforcementController(mode).resolve(
            gate_passed=False,
            failure_kind=GateFailureKind.TECHNICAL,
        )
        assert decision.action is GateEnforcementAction.BLOCK
        assert decision.would_block_in_strict is True


def test_writer_business_block_is_shadowable_but_integrity_gap_is_not() -> None:
    shadow_controller = GateEnforcementController("shadow")
    coordinator = SectionExecutionCoordinator(
        evidence_service=SimpleNamespace(),
        query_builder=SimpleNamespace(),
        section_generation_service=SimpleNamespace(
            gate_enforcement_controller=shadow_controller
        ),
        runtime_support=SimpleNamespace(),
        generation_quality_metadata={},
    )

    blocked_business = make_binding(mode=DeliveryMode.BLOCKED)
    incomplete_integrity = make_binding(
        mode=DeliveryMode.GROUNDED,
        evidence_hash=None,
    )

    business = coordinator.resolve_writer_gate_enforcement(blocked_business)
    integrity = coordinator.resolve_writer_gate_enforcement(incomplete_integrity)

    assert business.gate_passed is False
    assert business.action is GateEnforcementAction.CONTINUE
    assert business.failure_kind is GateFailureKind.BUSINESS
    assert integrity.action is GateEnforcementAction.BLOCK
    assert integrity.failure_kind is GateFailureKind.TECHNICAL


def test_invalid_mode_fails_fast() -> None:
    with pytest.raises(ValueError, match="strict, shadow"):
        GateEnforcementController("permissive")
