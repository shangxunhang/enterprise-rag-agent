"""T12-05 frozen Gate V2 Target routing/fallback acceptance proofs.

This P6 suite closes the final Step12 topology without changing production
semantics.  The older router-focused suite remains historical regression
coverage; this file owns the frozen T12-05 oracle names and the explicit
route-to-Writer/Fallback boundary proofs required by AV-S12-032～038.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.tests import test_gate_v2_pre_write_router as router_cases
from backend.tests import test_gate_v2_pre_write_writer_invocation as writer_cases


@pytest.mark.parametrize(
    "kind,mode,rounds_limit,expected",
    [
        ("allow", "strict", 2, router_cases.PreWriteRoute.NORMAL),
        ("allow", "shadow", 2, router_cases.PreWriteRoute.NORMAL),
        ("deny", "shadow", 2, router_cases.PreWriteRoute.NORMAL),
        ("deny", "shadow", 0, router_cases.PreWriteRoute.NORMAL),
        ("deny", "strict", 2, router_cases.PreWriteRoute.RECOVERY),
        ("deny", "strict", 0, router_cases.PreWriteRoute.FALLBACK),
    ],
)
def test_prewrite_router_exact_deterministic_matrix(
    kind: str,
    mode: str,
    rounds_limit: int,
    expected: router_cases.PreWriteRoute,
) -> None:
    """AV-S12-032: the frozen Router matrix is exact and deterministic."""

    router_cases.test_prewrite_router_exact_deterministic_matrix(
        kind,
        mode,
        rounds_limit,
        expected,
    )


def test_route_decision_identity_converges_for_same_effective_snapshot() -> None:
    """AV-S12-033: replay of one effective snapshot converges on one ID."""

    router_cases.test_route_decision_identity_converges_for_same_effective_snapshot()


def test_route_commit_revalidates_current_applicability_and_expected_route() -> None:
    """AV-S12-034: commit revalidates current lineage and expected route."""

    router_cases.test_route_commit_revalidates_current_snapshot_and_expected_route()
    router_cases.test_router_rejects_stale_applicability_lineage_even_when_state_says_applicable()
    router_cases.test_route_identity_preimage_excludes_selected_route()


def test_normal_route_projects_normal_write_only(tmp_path) -> None:
    """AV-S12-035: committed NORMAL projects NORMAL_WRITE through one Writer."""

    calls: list[dict[str, object]] = []
    chain = writer_cases._current_chain(allow=True)
    assert chain.route_decision.selected_route is router_cases.PreWriteRoute.NORMAL

    service = writer_cases._service(
        store=writer_cases.SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=writer_cases._section_service(
            calls,
            responses=[writer_cases._success_response(call_id="t12_05_normal")],
        ),
    )
    result = writer_cases._execute(service, chain)

    assert result.status is writer_cases.WriterExecutionStatus.SUCCESS
    assert result.generation_provenance.generation_class == (
        writer_cases.WriterGenerationClass.NORMAL_WRITE.value
    )
    assert len(calls) == 1


def test_recovery_route_cannot_form_writer_invocation(tmp_path) -> None:
    """AV-S12-036: RECOVERY has no Writer generation class or model dispatch."""

    base = writer_cases._current_chain(allow=False)
    recovery = base.recovery_control.model_copy(
        update={
            "recovery_available": True,
            "recovery_exhausted": False,
            "recovery_rounds_limit": 2,
            "exhaustion_reason": None,
        }
    )
    route = router_cases.PreWriteRouter.evaluate(
        writer_subject_ref=base.route_decision.writer_subject_ref,
        write_instance_id=base.route_decision.write_instance_id,
        decision=base.decision,
        binding=base.binding,
        applicability=base.applicability,
        enforcement=base.enforcement,
        recovery_control=recovery,
    )
    assert route.selected_route is router_cases.PreWriteRoute.RECOVERY
    chain = replace(base, recovery_control=recovery, route_decision=route)

    calls: list[dict[str, object]] = []
    service = writer_cases._service(
        store=writer_cases.SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=writer_cases._section_service(
            calls,
            responses=[writer_cases._success_response(call_id="must_not_dispatch")],
        ),
    )

    with pytest.raises(
        writer_cases.WriterInvocationIntegrityError,
        match="RECOVERY route cannot form an initial Writer invocation",
    ):
        writer_cases._execute(service, chain)
    assert calls == []


def test_fallback_route_requires_frozen_exhaustion_and_uses_same_writer(tmp_path) -> None:
    """AV-S12-037: exhausted DENY/BLOCK FALLBACK uses the canonical Writer."""

    calls: list[dict[str, object]] = []
    chain = writer_cases._current_chain(allow=False)
    assert chain.decision.admission_outcome.value == "deny"
    assert chain.enforcement.action is router_cases.EnforcementAction.BLOCK
    assert chain.recovery_control.recovery_exhausted is True
    assert chain.route_decision.selected_route is router_cases.PreWriteRoute.FALLBACK

    service = writer_cases._service(
        store=writer_cases.SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=writer_cases._section_service(
            calls,
            responses=[writer_cases._success_response(call_id="t12_05_fallback")],
        ),
    )
    result = writer_cases._execute(service, chain)

    assert result.status is writer_cases.WriterExecutionStatus.SUCCESS
    assert result.generation_provenance.generation_class == (
        writer_cases.WriterGenerationClass.FALLBACK_WRITE.value
    )
    assert "fallback" not in result.artifact.content.lower()
    assert len(calls) == 1


def test_shadow_deny_can_route_normal_without_becoming_fallback() -> None:
    """AV-S12-038: SHADOW changes action, never DENY truth or Fallback class."""

    router_cases.test_shadow_deny_normal_is_not_vetoed_by_recovery_specific_technical_condition()
