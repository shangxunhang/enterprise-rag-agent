"""Focused P1 contract tests for the Gate V2 Target schema foundation."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from apps.enterprise_document.schemas import (
    pre_write_admission,
    pre_write_binding,
    pre_write_control,
    pre_write_writer,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceFactSchema,
    CriterionEvidenceSupportState,
    EvidenceConflictFactSchema,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
    RequirementEvidenceFactSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionContractSchema,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionFactAssessmentSchema,
    WriterAdmissionOutcome,
    WriterAdmissionResolutionState,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    EnforcementResultSchema,
    EnforcementSnapshotRefSchema,
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
    RecoveryControlSnapshotRefSchema,
    RecoveryExhaustionReason,
    RecoveryRoundOutcome,
    RecoveryRuntimeStateSchema,
    RecoveryTargetProjection,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionResultSchema,
    WriterExecutionStatus,
    WriterGenerationClass,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


SHA_A = "a" * 64
SHA_B = "b" * 64


def subject() -> WriterSubjectRefSchema:
    return WriterSubjectRefSchema(run_id="run-1", document_id="doc-1", section_id="s-1")


def decision_ref() -> WriterAdmissionDecisionRefSchema:
    return WriterAdmissionDecisionRefSchema(decision_id="decision-1")


def binding_ref() -> WriterGateBindingRefSchema:
    return WriterGateBindingRefSchema(binding_id="binding-1")


def assessment_ref() -> WriterAdmissionFactAssessmentRefSchema:
    return WriterAdmissionFactAssessmentRefSchema(
        assessment_id="assessment-1",
        requirements_schema_version="evidence_requirements_snapshot_v1",
        requirements_contract_version="evidence_requirement_semantics_v1",
        requirements_sha256=SHA_A,
        evidence_bundle_schema_version="rag_evidence_contract_v1",
        evidence_bundle_sha256=SHA_B,
    )


def route_ref() -> PreWriteRouteDecisionRefSchema:
    return PreWriteRouteDecisionRefSchema(route_decision_id="route-1")


def execution_key() -> WriterLogicalExecutionKey:
    return WriterLogicalExecutionKey(
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
        route_decision_ref=route_ref(),
    )


def evidence_bundle() -> EvidenceBundleSchema:
    return EvidenceBundleSchema(
        query="locked query",
        context=RAGContextSchema(context_text="", used_context_chars=0),
    )


def error() -> ErrorSchema:
    return ErrorSchema(
        error_code="MODEL_TIMEOUT",
        error_type="TimeoutError",
        message="provider timed out",
        failure_category=FailureCategory.TIMEOUT,
    )


def section() -> SchemeSectionSchema:
    return SchemeSectionSchema(
        section_id="s-1",
        section_title="Section",
        section_order=1,
        content="generated",
    )


def writer_invocation(
    *,
    semantic_snapshot: object | None = None,
    technical_constraints: object | None = None,
    canonical_evidence: object | None = None,
) -> WriterGenerationInvocationSchema:
    return WriterGenerationInvocationSchema(
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
        route_decision_ref=route_ref(),
        generation_class=WriterGenerationClass.NORMAL_WRITE,
        canonical_evidence_input=canonical_evidence
        or {
            **evidence_bundle().model_dump(mode="json"),
            "evidence_bundle_sha256": SHA_B,
        },
        unified_writer_input_snapshot=semantic_snapshot
        or {"section_contract": "v1", "nested": {"value": 1}},
        technical_execution_constraints=technical_constraints
        or {"limit": 3, "nested": {"retry": 1}},
    )


def test_frozen_symbol_surface_matches_step_11_authority() -> None:
    expected = {
        pre_write_admission: {
            "EvidenceCriterionSchema", "EvidenceRequirementSchema",
            "EvidenceRequirementsSnapshotSchema", "CriterionEvidenceSupportState",
            "CriterionEvidenceFactSchema", "RequirementEvidenceFactSchema",
            "EvidenceConflictFactSchema", "WriterAdmissionAssessmentState",
            "WriterAdmissionFactAssessmentSchema", "WriterAdmissionFactAssessmentRefSchema",
            "WriterAdmissionContractSchema", "WriterAdmissionResolutionState",
            "WriterAdmissionOutcome", "WriterAdmissionDecisionSchema",
            "WriterAdmissionDecisionRefSchema",
        },
        pre_write_binding: {
            "WriterSubjectRefSchema", "WriterGateBindingSchema",
            "WriterGateBindingRefSchema", "BindingApplicabilityState",
            "BindingApplicabilityResult",
        },
        pre_write_control: {
            "EnforcementBasis", "EnforcementAction", "EnforcementResultSchema",
            "RecoveryRoundOutcome", "RecoveryExhaustionReason",
            "RecoveryRuntimeStateSchema", "RecoveryTargetProjection",
            "RecoveryControlFactsSchema", "EnforcementSnapshotRefSchema",
            "RecoveryControlSnapshotRefSchema", "PreWriteRoute",
            "PreWriteRouteDecisionSchema", "PreWriteRouteDecisionRefSchema",
        },
        pre_write_writer: {
            "WriterGenerationClass", "WriterGenerationInvocationSchema",
            "WriterGenerationInvocationRefSchema", "WriterLogicalExecutionKey",
            "WriterExecutionStatus", "WriterExecutionFailureKind",
            "WriterExecutionRefSchema", "WriterExecutionResultSchema",
        },
    }
    for module, names in expected.items():
        assert set(module.__all__) == names
        assert all(hasattr(module, name) for name in names)


def test_requirements_snapshot_is_strict_frozen_and_round_trips() -> None:
    snapshot = EvidenceRequirementsSnapshotSchema(
        requirements_sha256=SHA_A,
        source_profile_ref="profile:v1",
        source_task_ref="task:1",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="r1",
                criteria=(EvidenceCriterionSchema(criterion_id="c1", criterion="Fact"),),
            ),
        ),
    )
    restored = EvidenceRequirementsSnapshotSchema.model_validate_json(
        snapshot.model_dump_json()
    )
    assert restored == snapshot
    assert snapshot.schema_version == "evidence_requirements_snapshot_v1"
    with pytest.raises(ValidationError):
        EvidenceRequirementsSnapshotSchema.model_validate(
            {**snapshot.model_dump(), "unknown": True}
        )
    with pytest.raises(ValidationError):
        snapshot.requirements_sha256 = SHA_B


def test_requirements_reject_empty_duplicate_and_invalid_hash() -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirementSchema(requirement_id="r1", criteria=())
    duplicate = EvidenceCriterionSchema(criterion_id="c1", criterion="Fact")
    with pytest.raises(ValidationError):
        EvidenceRequirementsSnapshotSchema(
            requirements_sha256="bad",
            source_profile_ref="p",
            source_task_ref="t",
            requirements=(
                EvidenceRequirementSchema(requirement_id="r1", criteria=(duplicate,)),
                EvidenceRequirementSchema(requirement_id="r2", criteria=(duplicate,)),
            ),
        )


def test_fact_domains_and_pending_lifecycle_are_structural() -> None:
    assert {item.value for item in CriterionEvidenceSupportState} == {
        "supported", "not_supported", "indeterminate"
    }
    with pytest.raises(ValidationError):
        CriterionEvidenceFactSchema(
            fact_id="f1", requirement_id="r1", criterion_id="c1",
            support_state="supported", evidence_ids=(),
        )


def test_requirement_fact_rejects_empty_duplicate_and_invalid_domain() -> None:
    with pytest.raises(ValidationError):
        RequirementEvidenceFactSchema(
            fact_id="f1",
            requirement_id="",
            support_state="supported",
            criterion_fact_ids=("c-fact-1",),
        )
    with pytest.raises(ValidationError):
        RequirementEvidenceFactSchema(
            fact_id="f1",
            requirement_id="r1",
            support_state="supported",
            criterion_fact_ids=("c-fact-1", "c-fact-1"),
        )
    with pytest.raises(ValidationError):
        RequirementEvidenceFactSchema(
            fact_id="f1",
            requirement_id="r1",
            support_state="conflicted",
            criterion_fact_ids=("c-fact-1",),
        )


def test_evidence_conflict_rejects_too_few_or_duplicate_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        EvidenceConflictFactSchema(
            conflict_id="conflict-1",
            criterion_id="criterion-1",
            evidence_ids=("e1",),
        )
    with pytest.raises(ValidationError):
        EvidenceConflictFactSchema(
            conflict_id="conflict-1",
            criterion_id="criterion-1",
            evidence_ids=("e1", "e1"),
        )


def test_writer_admission_contract_rejects_nonfrozen_literals() -> None:
    contract = WriterAdmissionContractSchema()
    assert contract.required_support_state == "supported"
    assert contract.unresolved_conflict_allowed is False
    with pytest.raises(ValidationError):
        WriterAdmissionContractSchema(required_support_state="not_supported")
    with pytest.raises(ValidationError):
        WriterAdmissionContractSchema(unresolved_conflict_allowed=True)
    pending = WriterAdmissionFactAssessmentSchema(
        assessment_id="a1",
        assessment_state=WriterAdmissionAssessmentState.PENDING,
        requirements_schema_version="evidence_requirements_snapshot_v1",
        requirements_contract_version="evidence_requirement_semantics_v1",
        requirements_sha256=SHA_A,
        evidence_bundle_schema_version="rag_evidence_contract_v1",
        evidence_bundle_sha256=SHA_B,
    )
    with pytest.raises(ValidationError):
        WriterAdmissionFactAssessmentSchema.model_validate(
            {**pending.model_dump(), "criterion_facts": [{
                "fact_id": "f1", "requirement_id": "r1", "criterion_id": "c1",
                "support_state": "not_supported",
            }]}
        )


def test_decision_control_states_and_identity_are_coherent() -> None:
    unresolved = WriterAdmissionDecisionSchema(
        decision_id="d1",
        resolution_state=WriterAdmissionResolutionState.UNRESOLVED,
        admission_outcome=None,
        fact_assessment_ref=assessment_ref(),
        reason_codes=("assessment_pending",),
    )
    assert unresolved.policy_id == "writer_admission_policy"
    with pytest.raises(ValidationError):
        WriterAdmissionDecisionSchema.model_validate(
            {**unresolved.model_dump(), "admission_outcome": "allow"}
        )
    with pytest.raises(ValidationError):
        WriterAdmissionDecisionSchema(
            decision_id="d1", supersedes_decision_id="d1",
            resolution_state="resolved", admission_outcome="deny",
            fact_assessment_ref=assessment_ref(),
            reason_codes=("requirement_not_fully_supported",),
        )


def test_binding_versions_shape_and_immutability() -> None:
    binding = WriterGateBindingSchema(
        binding_id="b1", decision_ref=decision_ref(),
        writer_subject_ref=subject(), write_instance_id="attempt-1",
    )
    assert binding.schema_version == "writer_gate_binding_v3"
    assert binding.contract_version == "writer_gate_binding_control_v1"
    assert set(WriterGateBindingSchema.model_fields) == {
        "schema_version", "contract_version", "binding_id", "decision_ref",
        "writer_subject_ref", "write_instance_id",
    }
    with pytest.raises(ValidationError):
        binding.write_instance_id = "attempt-2"


def test_binding_applicability_is_a_typed_integrity_carrier() -> None:
    decision = WriterAdmissionDecisionSchema(
        decision_id="decision-1", resolution_state="unresolved",
        admission_outcome=None, fact_assessment_ref=assessment_ref(),
        reason_codes=("assessment_pending",),
    )
    binding = WriterGateBindingSchema(
        binding_id="b1", decision_ref=decision_ref(),
        writer_subject_ref=subject(), write_instance_id="attempt-1",
    )
    result = BindingApplicabilityResult(
        state=BindingApplicabilityState.APPLICABLE,
        decision=decision,
        binding=binding,
    )
    assert result.state is BindingApplicabilityState.APPLICABLE
    with pytest.raises(ValidationError):
        BindingApplicabilityResult(state="applicable")


def test_enforcement_versions_domains_and_integrity_fail_closed() -> None:
    result = EnforcementResultSchema(
        mode="shadow", action=EnforcementAction.BLOCK,
        would_block_in_strict=True,
        enforcement_basis=EnforcementBasis.TECHNICAL_INTEGRITY,
    )
    assert result.schema_version == "writer_admission_enforcement_result_v1"
    assert result.contract_version == "writer_admission_enforcement_v1"
    with pytest.raises(ValidationError):
        EnforcementResultSchema(
            mode="shadow", action="continue", would_block_in_strict=True,
            enforcement_basis="technical_integrity",
        )
    with pytest.raises(ValidationError):
        EnforcementResultSchema(
            mode="strict", action="continue", would_block_in_strict=False,
            enforcement_basis="business_admission",
        )


def test_recovery_runtime_state_validates_only_counter_structure() -> None:
    state = RecoveryRuntimeStateSchema(
        writer_subject_ref=subject(), write_instance_id="attempt-1",
        recovery_state_revision=0, recovery_rounds_used=0,
        recovery_rounds_limit=2, consecutive_no_progress_rounds=0,
        no_progress_rounds_limit=1,
        last_round_outcome=RecoveryRoundOutcome.NOT_STARTED,
    )
    assert "schema_version" not in RecoveryRuntimeStateSchema.model_fields
    with pytest.raises(ValidationError):
        RecoveryRuntimeStateSchema.model_validate(
            {**state.model_dump(), "consecutive_no_progress_rounds": 1}
        )


def test_recovery_control_versions_and_exhaustion_coherence() -> None:
    facts = RecoveryControlFactsSchema(
        writer_subject_ref=subject(), write_instance_id="attempt-1",
        decision_ref=decision_ref(), binding_ref=binding_ref(),
        recovery_state_revision=2, recovery_available=False,
        recovery_exhausted=True, recovery_rounds_used=2,
        recovery_rounds_limit=2, consecutive_no_progress_rounds=0,
        no_progress_rounds_limit=1,
        last_round_outcome=RecoveryRoundOutcome.TECHNICAL_FAILURE,
        exhaustion_reason=RecoveryExhaustionReason.ROUND_BUDGET_EXHAUSTED,
    )
    assert facts.schema_version == "writer_recovery_control_facts_v1"
    assert facts.contract_version == "writer_recovery_control_v1"
    with pytest.raises(ValidationError):
        RecoveryControlFactsSchema.model_validate(
            {**facts.model_dump(), "recovery_available": True}
        )


def test_recovery_target_rejects_invalid_identity_and_duplicate_projection() -> None:
    target = {
        "writer_subject_ref": subject(),
        "write_instance_id": "attempt-1",
        "decision_ref": decision_ref(),
        "binding_ref": binding_ref(),
        "fact_assessment_ref": assessment_ref(),
        "requirements_sha256": SHA_A,
        "expected_recovery_state_revision": 0,
        "blocking_requirement_ids": ("r1",),
        "blocking_criterion_ids": ("c1",),
        "unresolved_conflict_criterion_ids": (),
        "indeterminate_requirement_ids": (),
        "indeterminate_criterion_ids": (),
    }
    assert RecoveryTargetProjection.model_validate(target).write_instance_id == "attempt-1"
    with pytest.raises(ValidationError):
        RecoveryTargetProjection.model_validate({**target, "write_instance_id": ""})
    with pytest.raises(ValidationError):
        RecoveryTargetProjection.model_validate(
            {**target, "blocking_criterion_ids": ("c1", "c1")}
        )


def test_route_decision_versions_enum_ref_and_round_trip() -> None:
    route = PreWriteRouteDecisionSchema(
        route_decision_id="route-1", writer_subject_ref=subject(),
        write_instance_id="attempt-1", decision_ref=decision_ref(),
        binding_ref=binding_ref(),
        enforcement_ref=EnforcementSnapshotRefSchema(mode="strict"),
        recovery_control_ref=RecoveryControlSnapshotRefSchema(
            recovery_state_revision=0
        ),
        selected_route=PreWriteRoute.RECOVERY,
    )
    assert route.contract_version == "pre_write_routing_v1"
    assert {item.value for item in PreWriteRoute} == {
        "normal", "recovery", "fallback"
    }
    assert PreWriteRouteDecisionSchema.model_validate_json(
        route.model_dump_json()
    ) == route
    with pytest.raises(ValidationError):
        PreWriteRouteDecisionSchema.model_validate(
            {**route.model_dump(), "selected_route": "terminate"}
        )


def test_writer_invocation_identity_has_no_invocation_id_or_recovery_class() -> None:
    invocation = writer_invocation()
    assert "writer_invocation_id" not in WriterGenerationInvocationSchema.model_fields
    assert set(WriterLogicalExecutionKey.model_fields) == {
        "writer_subject_ref", "write_instance_id", "route_decision_ref"
    }
    assert {item.value for item in WriterGenerationClass} == {
        "normal_write", "fallback_write"
    }
    assert invocation.schema_version == "writer_generation_invocation_v1"
    assert invocation.contract_version == "writer_generation_execution_v1"
    assert invocation.canonical_evidence_input.evidence_bundle_sha256 == SHA_B


def test_writer_invocation_top_level_assignment_is_frozen() -> None:
    invocation = writer_invocation()
    with pytest.raises(ValidationError):
        invocation.write_instance_id = "attempt-2"


def test_writer_invocation_immutable_json_keys_remain_structurally_strict() -> None:
    with pytest.raises(ValidationError):
        writer_invocation(semantic_snapshot={"   ": "invalid"})
    normalized = writer_invocation(semantic_snapshot={" section_contract ": "v1"})
    assert tuple(normalized.unified_writer_input_snapshot) == ("section_contract",)
    with pytest.raises(ValidationError):
        writer_invocation(semantic_snapshot={" section_contract ": "v1", "section_contract": "v2"})


def test_writer_invocation_nested_semantic_snapshot_is_deeply_immutable() -> None:
    invocation = writer_invocation(
        semantic_snapshot={"x": {"y": 1}, "items": [{"value": "locked"}]}
    )
    with pytest.raises((AttributeError, TypeError)):
        invocation.unified_writer_input_snapshot["x"]["y"] = 2
    with pytest.raises((AttributeError, TypeError)):
        invocation.unified_writer_input_snapshot["items"][0]["value"] = "changed"
    assert invocation.unified_writer_input_snapshot["x"]["y"] == 1
    assert invocation.unified_writer_input_snapshot["items"][0]["value"] == "locked"


def test_writer_invocation_technical_constraints_are_deeply_immutable() -> None:
    invocation = writer_invocation()
    with pytest.raises((AttributeError, TypeError)):
        invocation.technical_execution_constraints["limit"] = 9
    with pytest.raises((AttributeError, TypeError)):
        invocation.technical_execution_constraints["nested"]["retry"] = 8
    assert invocation.technical_execution_constraints["limit"] == 3
    assert invocation.technical_execution_constraints["nested"]["retry"] == 1


def test_writer_invocation_canonical_evidence_and_hash_cannot_drift() -> None:
    invocation = writer_invocation()
    original_hash = invocation.canonical_evidence_input.evidence_bundle_sha256
    original_context = invocation.canonical_evidence_input.context.context_text
    with pytest.raises((AttributeError, TypeError)):
        invocation.canonical_evidence_input.context.context_text = "E2"
    with pytest.raises((AttributeError, TypeError)):
        invocation.canonical_evidence_input.context["context_text"] = "E2"
    assert invocation.canonical_evidence_input.context.context_text == original_context
    assert invocation.canonical_evidence_input.evidence_bundle_sha256 == original_hash


def test_writer_invocation_source_alias_mutation_cannot_change_locked_values() -> None:
    semantic_source = {"nested": {"value": 1}}
    constraints_source = {"limit": 3, "nested": {"retry": 1}}
    source_context = RAGContextSchema(context_text="E1", used_context_chars=2)
    evidence_source = {
        **evidence_bundle().model_dump(mode="python"),
        "context": source_context,
        "evidence_bundle_sha256": SHA_B,
    }
    invocation = writer_invocation(
        semantic_snapshot=semantic_source,
        technical_constraints=constraints_source,
        canonical_evidence=evidence_source,
    )

    semantic_source["nested"]["value"] = 2
    constraints_source["nested"]["retry"] = 8
    source_context.context_text = "E2"

    assert invocation.unified_writer_input_snapshot["nested"]["value"] == 1
    assert invocation.technical_execution_constraints["nested"]["retry"] == 1
    assert invocation.canonical_evidence_input.context.context_text == "E1"
    assert invocation.canonical_evidence_input.evidence_bundle_sha256 == SHA_B


def test_writer_invocation_json_round_trip_preserves_and_relocks_semantics() -> None:
    invocation = writer_invocation()
    python_dump = invocation.model_dump()
    json_dump = invocation.model_dump(mode="json")
    json_text = invocation.model_dump_json()

    assert python_dump == json_dump
    assert json.loads(json_text) == json_dump
    restored = WriterGenerationInvocationSchema.model_validate(json_dump)
    restored_from_json = WriterGenerationInvocationSchema.model_validate_json(json_text)
    assert restored == invocation
    assert restored_from_json == invocation
    with pytest.raises((AttributeError, TypeError)):
        restored.unified_writer_input_snapshot["nested"]["value"] = 99
    with pytest.raises((AttributeError, TypeError)):
        restored.canonical_evidence_input.context.context_text = "E2"


def test_writer_invocation_supports_validation_and_serialization_json_schema() -> None:
    validation_schema = WriterGenerationInvocationSchema.model_json_schema()
    explicit_validation_schema = WriterGenerationInvocationSchema.model_json_schema(
        mode="validation"
    )
    serialization_schema = WriterGenerationInvocationSchema.model_json_schema(
        mode="serialization"
    )
    expected_fields = {
        "schema_version",
        "contract_version",
        "writer_subject_ref",
        "write_instance_id",
        "route_decision_ref",
        "generation_class",
        "canonical_evidence_input",
        "unified_writer_input_snapshot",
        "technical_execution_constraints",
    }

    assert validation_schema == explicit_validation_schema
    assert set(validation_schema["properties"]) == expected_fields
    assert set(serialization_schema["properties"]) == expected_fields
    assert validation_schema["properties"]["unified_writer_input_snapshot"]["type"] == "object"
    assert validation_schema["properties"]["technical_execution_constraints"]["type"] == "object"
    assert serialization_schema["properties"]["unified_writer_input_snapshot"]["type"] == "object"
    assert serialization_schema["properties"]["technical_execution_constraints"]["type"] == "object"
    rendered = json.dumps(
        {"validation": validation_schema, "serialization": serialization_schema},
        sort_keys=True,
    )
    assert "_FrozenJsonMap" not in rendered
    assert "_FrozenJsonList" not in rendered


def test_every_exported_p1_pydantic_model_generates_json_schema() -> None:
    checked: list[str] = []
    failures: list[tuple[str, str]] = []
    for module in (
        pre_write_admission,
        pre_write_binding,
        pre_write_control,
        pre_write_writer,
    ):
        for name in module.__all__:
            exported = getattr(module, name)
            if inspect.isclass(exported) and issubclass(exported, BaseModel):
                checked.append(f"{module.__name__}.{name}")
                for mode in ("validation", "serialization"):
                    try:
                        exported.model_json_schema(mode=mode)
                    except Exception as exc:  # pragma: no cover - assertion diagnostic
                        failures.append((f"{module.__name__}.{name}:{mode}", repr(exc)))

    assert checked
    assert failures == []


def test_writer_logical_key_cannot_hide_semantic_snapshot_mutation() -> None:
    invocation = writer_invocation()
    key_before = WriterLogicalExecutionKey(
        writer_subject_ref=invocation.writer_subject_ref,
        write_instance_id=invocation.write_instance_id,
        route_decision_ref=invocation.route_decision_ref,
    )
    semantic_before = invocation.model_dump(mode="json")
    with pytest.raises((AttributeError, TypeError)):
        invocation.unified_writer_input_snapshot["nested"]["value"] = 2
    key_after = WriterLogicalExecutionKey(
        writer_subject_ref=invocation.writer_subject_ref,
        write_instance_id=invocation.write_instance_id,
        route_decision_ref=invocation.route_decision_ref,
    )
    assert key_after == key_before
    assert invocation.model_dump(mode="json") == semantic_before


def test_writer_result_is_a_disjoint_success_or_technical_failure() -> None:
    success = WriterExecutionResultSchema(
        logical_execution_key=execution_key(), status=WriterExecutionStatus.SUCCESS,
        artifact=section(), generation_provenance={"generation_class": "normal_write"},
    )
    assert success.contract_version == "writer_execution_result_control_v1"
    failed = WriterExecutionResultSchema(
        logical_execution_key=execution_key(), status="technical_failure",
        failure_kind=WriterExecutionFailureKind.MODEL_TIMEOUT,
        error=error(), generation_provenance={"generation_class": "normal_write"},
    )
    assert failed.artifact is None
    with pytest.raises(ValidationError):
        WriterExecutionResultSchema(
            logical_execution_key=execution_key(), status="technical_failure",
            artifact=section(), failure_kind="model_timeout", error=error(),
            generation_provenance={"generation_class": "normal_write"},
        )


def test_writer_result_historical_state_is_deeply_immutable() -> None:
    result = WriterExecutionResultSchema(
        logical_execution_key=execution_key(),
        status=WriterExecutionStatus.SUCCESS,
        artifact=section(),
        generation_provenance={
            "generation_class": "normal_write",
            "context": {"package": {"id": "ctx-1"}},
        },
        technical_execution_metadata={"nested": {"x": 1}},
    )

    with pytest.raises((AttributeError, TypeError)):
        result.generation_provenance["generation_class"] = "fallback_write"
    with pytest.raises((AttributeError, TypeError)):
        result.generation_provenance["context"]["package"]["id"] = "ctx-2"
    with pytest.raises((AttributeError, TypeError)):
        result.technical_execution_metadata["nested"]["x"] = 2
    with pytest.raises((AttributeError, TypeError)):
        result.artifact.content = "changed"

    assert result.generation_provenance["generation_class"] == "normal_write"
    assert result.generation_provenance["context"]["package"]["id"] == "ctx-1"
    assert result.technical_execution_metadata["nested"]["x"] == 1
    assert result.artifact.content == "generated"


def test_writer_result_source_aliases_cannot_change_historical_success() -> None:
    provenance = {"generation_class": "normal_write", "nested": {"x": 1}}
    metadata = {"nested": {"x": 1}}
    source_artifact = section()
    source_artifact.input = {"nested": {"x": 1}}
    result = WriterExecutionResultSchema(
        logical_execution_key=execution_key(),
        status=WriterExecutionStatus.SUCCESS,
        artifact=source_artifact,
        generation_provenance=provenance,
        technical_execution_metadata=metadata,
    )

    provenance["generation_class"] = "fallback_write"
    provenance["nested"]["x"] = 2
    metadata["nested"]["x"] = 2
    source_artifact.content = "source changed"
    source_artifact.input["nested"]["x"] = 2

    assert result.generation_provenance["generation_class"] == "normal_write"
    assert result.generation_provenance["nested"]["x"] == 1
    assert result.technical_execution_metadata["nested"]["x"] == 1
    assert result.artifact.content == "generated"
    assert result.artifact.input["nested"]["x"] == 1


def test_writer_result_json_round_trip_preserves_historical_immutability() -> None:
    result = WriterExecutionResultSchema(
        logical_execution_key=execution_key(),
        status=WriterExecutionStatus.SUCCESS,
        artifact=section(),
        generation_provenance={"generation_class": "normal_write"},
        technical_execution_metadata={"nested": {"x": 1}},
    )
    python_dump = result.model_dump()
    json_dump = result.model_dump(mode="json")
    json_text = result.model_dump_json()
    restored = WriterExecutionResultSchema.model_validate(python_dump)
    restored_from_json = WriterExecutionResultSchema.model_validate_json(json_text)

    assert python_dump == json_dump
    assert json.loads(json_text) == json_dump
    assert restored == result
    assert restored_from_json == result
    with pytest.raises((AttributeError, TypeError)):
        restored.generation_provenance["generation_class"] = "fallback_write"
    with pytest.raises((AttributeError, TypeError)):
        restored.technical_execution_metadata["nested"]["x"] = 2
    with pytest.raises((AttributeError, TypeError)):
        restored.artifact.content = "changed"


def test_writer_failure_error_is_a_result_owned_immutable_snapshot() -> None:
    source_error = error()
    failed = WriterExecutionResultSchema(
        logical_execution_key=execution_key(),
        status=WriterExecutionStatus.TECHNICAL_FAILURE,
        failure_kind=WriterExecutionFailureKind.MODEL_TIMEOUT,
        error=source_error,
        generation_provenance={"generation_class": "normal_write"},
    )
    source_error.message = "source changed"

    assert failed.error.message == "provider timed out"
    with pytest.raises((AttributeError, TypeError)):
        failed.error.message = "result changed"
    restored = WriterExecutionResultSchema.model_validate_json(failed.model_dump_json())
    assert restored == failed
    with pytest.raises((AttributeError, TypeError)):
        restored.error.message = "restored changed"


def test_schema_modules_do_not_import_or_call_business_authorities() -> None:
    forbidden_imports = {
        "writer_admission", "writer_gate_binding", "pre_write_recovery",
        "pre_write_router", "writer_invocation", "gate_enforcement",
    }
    forbidden_calls = {
        "WriterAdmissionPolicy", "WriterGateApplicabilityValidator",
        "PreWriteRecoveryController", "PreWriteRouter",
        "PreWriteWriterInvocationService",
    }
    for module in (
        pre_write_admission, pre_write_binding, pre_write_control, pre_write_writer
    ):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        imported = {
            node.module.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not imported & forbidden_imports
        assert not called & forbidden_calls
        assert "computed_field" not in source


def test_target_schema_files_do_not_modify_runtime_modules() -> None:
    schema_dir = Path(pre_write_admission.__file__).parent
    assert {path.name for path in schema_dir.glob("pre_write_*.py")} == {
        "pre_write_admission.py", "pre_write_binding.py",
        "pre_write_control.py", "pre_write_writer.py",
    }
