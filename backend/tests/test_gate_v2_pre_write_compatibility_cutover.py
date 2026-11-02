"""T12-11: P5 compatibility / cutover / legacy-authority fences."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionContractSchema,
    WriterAdmissionOutcome,
)
from apps.enterprise_document.schemas.pre_write_control import EnforcementAction
from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import (
    LegacyEvidenceAdapter,
    SUPPORTED_CANONICAL_EVIDENCE_VERSION,
    SUPPORTED_LEGACY_RAG_OUTPUT_VERSION,
    SUPPORTED_LEGACY_TOOL_RESULT_VERSION,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    WriterAdmissionFactAssessor,
    WriterAdmissionPolicy,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from bootstrap.supervisor_factory import SupervisorFactory
from schemas.tool import ToolResultSchema
from backend.tests import test_gate_v2_pre_write_binding_enforcement as binding_tests
from backend.tests import test_gate_v2_pre_write_checkpoint_replay as checkpoint_tests
from backend.tests.test_rag_interface_v2_phase4d_verification import _legacy_shared_state


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _versioned_legacy_result(*, payload_version=SUPPORTED_LEGACY_RAG_OUTPUT_VERSION):
    return ToolResultSchema(
        schema_version=SUPPORTED_LEGACY_TOOL_RESULT_VERSION,
        tool_call_id="legacy-p5-call",
        task_id="task_phase4d_legacy",
        run_id="run_phase4d_legacy",
        tool_name="LegacyRAGTool",
        success=True,
        result={
            "schema_version": payload_version,
            "query": "项目事实",
            "retrieved_chunks": [],
            "citations": [],
        },
        created_at="2026-08-23T00:00:00+08:00",
    )


def test_target_admission_versions_and_legacy_decision_fence() -> None:
    snapshot = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(),
        task_contract=scheme_generation_minimal_task_contract(),
    )
    assessment = WriterAdmissionFactAssessor(
        assessment_id_factory=lambda: "p5-zero-assessment"
    ).assess(
        requirements=snapshot,
        evidence_bundle=checkpoint_tests._evidence_bundle(),
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=lambda: "p5-zero-decision"
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    assert snapshot.schema_version == "evidence_requirements_snapshot_v1"
    assert assessment.schema_version == "writer_admission_fact_assessment_v1"
    assert decision.schema_version == "writer_admission_decision_v1"
    assert decision.admission_outcome is WriterAdmissionOutcome.DENY

    for relative in (
        "backend/bootstrap/supervisor_factory.py",
        "backend/apps/enterprise_document/services/scheme_writer/writer_admission.py",
    ):
        source = _source(relative)
        assert "GateDecisionSchema(" not in source
        assert "import GateDecisionSchema" not in source
        assert "EvidenceGatePolicy(" not in source
        assert "import EvidenceGatePolicy" not in source
        assert "ClaimRiskLevel." not in source
        assert "DeliveryMode." not in source


def test_checkpoint_v2_reject_and_restore_never_reconstructs_authority() -> None:
    checkpoint_tests.test_checkpoint_v2_reject_and_restore_never_reconstructs_authority()
    checkpoint_tests.test_top_level_checkpoint_v2_and_unknown_versions_are_explicitly_rejected()


def test_running_invocation_recovery_and_archived_runtime_are_not_gate_recovery_authority() -> None:
    checkpoint_tests.test_running_invocation_recovery_and_archived_runtime_are_not_gate_recovery_authority()
    supervisor_source = _source("backend/bootstrap/supervisor_factory.py")
    for forbidden in (
        "agent_rag_v1.runtime_factory",
        "agent_rag_v1.workflow_orchestrator",
        "ScenarioAgentReasoner",
        "RunningInvocationRecovery",
    ):
        assert forbidden not in supervisor_source


def test_shadow_validation_is_isolated_and_post_v3_legacy_rollback_is_fenced() -> None:
    binding_tests.test_business_enforcement_matrix(
        "deny",
        "strict",
        EnforcementAction.BLOCK,
        True,
    )
    binding_tests.test_business_enforcement_matrix(
        "deny",
        "shadow",
        EnforcementAction.CONTINUE,
        True,
    )
    checkpoint_tests.test_checkpoint_v2_reject_and_restore_never_reconstructs_authority()
    source = _source("backend/bootstrap/supervisor_factory.py")
    assert "gate_enforcement_mode=settings.gate_enforcement_mode" in source
    assert "LEGACY_GATE_FALLBACK" not in source
    assert "try Target except Legacy" not in source


def test_legacy_evidence_adapter_is_one_way_and_archived_runtime_cannot_authorize_target() -> None:
    shared_state = _legacy_shared_state()
    accepted = LegacyEvidenceAdapter.upgrade(
        shared_state,
        _versioned_legacy_result(),
    )
    assert accepted.schema_version == SUPPORTED_CANONICAL_EVIDENCE_VERSION
    assert accepted.extra["compatibility_upgrade"] is True

    for payload_version in (None, "rag_tool_output_v0", 7, ""):
        result = _versioned_legacy_result(payload_version=payload_version)
        with pytest.raises(ValueError, match="schema_version"):
            LegacyEvidenceAdapter.upgrade(shared_state, result)

    bad_tool_result = _versioned_legacy_result().model_copy(
        update={"schema_version": "tool_result_v0"}
    )
    with pytest.raises(ValueError, match="ToolResult schema_version"):
        LegacyEvidenceAdapter.upgrade(shared_state, bad_tool_result)

    canonical_payload = _versioned_legacy_result()
    canonical_payload.result["evidence"] = {
        "schema_version": "rag_evidence_contract_v0"
    }
    with pytest.raises(ValueError, match="canonical Evidence schema_version"):
        LegacyEvidenceAdapter.upgrade(shared_state, canonical_payload)

    assert not hasattr(LegacyEvidenceAdapter, "downgrade")
    assert not hasattr(LegacyEvidenceAdapter, "to_legacy")
    assert not hasattr(LegacyEvidenceAdapter, "roundtrip")

    archived = (
        "backend/apps/enterprise_document/runtime/agent_rag_v1/workflow_orchestrator.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/standalone_adapters.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/runtime_factory.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/application_service.py",
    )
    for relative in archived:
        assert "LEGACY_FIXTURE_ONLY = True" in _source(relative)
    supervisor_source = _source("backend/bootstrap/supervisor_factory.py")
    assert "apps.enterprise_document.runtime.agent_rag_v1" not in supervisor_source


def test_no_gate_feature_flag_no_dual_authority_no_silent_version_fallback() -> None:
    source = inspect.getsource(SupervisorFactory.build)
    assert source.count("attach_pre_write_services") == 1
    for forbidden in (
        "ENABLE_GATE_V2",
        "LEGACY_GATE_FALLBACK",
        "TARGET_GATE_ENABLED",
        "USE_LEGACY_GATE",
        "PREWRITE_V2_ENABLED",
        "EvidenceGatePolicy",
        "GateDecisionSchema",
        "legacy_gate",
        "fallback_to_legacy",
    ):
        assert forbidden not in source

    checkpoint_tests.test_checkpoint_v2_reject_and_restore_never_reconstructs_authority()
    checkpoint_tests.test_top_level_checkpoint_v2_and_unknown_versions_are_explicitly_rejected()

    target_sources = "\n".join(
        _source(path)
        for path in (
            "backend/bootstrap/supervisor_factory.py",
            "backend/apps/enterprise_document/services/scheme_writer/writer_admission.py",
            "backend/apps/enterprise_document/services/scheme_writer/pre_write_recovery.py",
            "backend/apps/enterprise_document/services/scheme_writer/pre_write_router.py",
            "backend/apps/enterprise_document/services/scheme_writer/writer_invocation.py",
        )
    )
    assert "EvidenceGatePolicy(" not in target_sources
    assert "import EvidenceGatePolicy" not in target_sources
    assert "GateDecisionSchema(" not in target_sources
    assert "import GateDecisionSchema" not in target_sources
