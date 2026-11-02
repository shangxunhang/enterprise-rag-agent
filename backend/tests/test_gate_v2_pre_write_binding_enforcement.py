"""T12-03 focused Binding, identity, and typed Enforcement acceptance tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionOutcome,
    WriterAdmissionResolutionState,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
)
from apps.enterprise_document.schemas.project_input_schema import (
    GenerationRequirementsSchema,
    OutputSchemaDefinition,
    ProjectInputSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanSchema
from apps.enterprise_document.schemas.scheme_writer.state_projection import (
    AttemptStateSchema,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import (
    MainlineReplanAttempt,
)
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    DecisionReferenceIntegrityError,
    WriterGateApplicabilityValidator,
    WriterGateBinder,
    resolve_decision_ref,
)
from core.runtime.ids import TimestampedUuidIdGenerator
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


SHA_E1 = "a" * 64
SHA_E2 = "b" * 64
SHA_R1 = "c" * 64
SHA_R2 = "d" * 64


def assessment_ref(*, evidence_sha: str = SHA_E1, requirements_sha: str = SHA_R1):
    return WriterAdmissionFactAssessmentRefSchema(
        assessment_id="assessment-1",
        requirements_schema_version="evidence_requirements_snapshot_v1",
        requirements_contract_version="evidence_requirement_semantics_v1",
        requirements_sha256=requirements_sha,
        evidence_bundle_schema_version="rag_evidence_contract_v1",
        evidence_bundle_sha256=evidence_sha,
    )


def decision(kind: str, *, decision_id: str = "decision-1") -> WriterAdmissionDecisionSchema:
    if kind == "allow":
        return WriterAdmissionDecisionSchema(
            decision_id=decision_id,
            resolution_state=WriterAdmissionResolutionState.RESOLVED,
            admission_outcome=WriterAdmissionOutcome.ALLOW,
            fact_assessment_ref=assessment_ref(),
            reason_codes=("normal_admission_satisfied",),
        )
    if kind == "deny":
        return WriterAdmissionDecisionSchema(
            decision_id=decision_id,
            resolution_state=WriterAdmissionResolutionState.RESOLVED,
            admission_outcome=WriterAdmissionOutcome.DENY,
            fact_assessment_ref=assessment_ref(),
            blocking_requirement_ids=("r1",),
            reason_codes=("requirement_not_fully_supported",),
        )
    if kind == "unresolved":
        return WriterAdmissionDecisionSchema(
            decision_id=decision_id,
            resolution_state=WriterAdmissionResolutionState.UNRESOLVED,
            admission_outcome=None,
            fact_assessment_ref=assessment_ref(),
            reason_codes=("assessment_pending",),
        )
    raise AssertionError(kind)


def subject(*, run_id: str = "run-1", section_id: str = "section-1"):
    return WriterSubjectRefSchema(
        run_id=run_id,
        document_id="document-1",
        section_id=section_id,
    )


def binding_ref(binding):
    return WriterGateBindingRefSchema(
        binding_id=binding.binding_id,
        binding_schema_version=binding.schema_version,
        binding_contract_version=binding.contract_version,
    )


def applicable(kind: str = "allow"):
    canonical_decision = decision(kind)
    binding = WriterGateBinder.bind(
        decision=canonical_decision,
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
    )
    result = WriterGateApplicabilityValidator().validate(
        binding=binding,
        current_writer_subject_ref=subject(),
        current_write_instance_id="attempt-1",
        current_decision=canonical_decision,
        current_active_evidence_bundle_sha256=SHA_E1,
        current_active_requirements_sha256=SHA_R1,
        current_active_binding_ref=binding_ref(binding),
    )
    assert result.state is BindingApplicabilityState.APPLICABLE
    return canonical_decision, binding, result


def test_binder_is_deterministic_and_binds_all_legal_decision_states() -> None:
    for kind in ("allow", "deny", "unresolved"):
        canonical_decision = decision(kind)
        first = WriterGateBinder.bind(
            decision=canonical_decision,
            writer_subject_ref=subject(),
            write_instance_id="attempt-1",
        )
        second = WriterGateBinder.bind(
            decision=canonical_decision,
            writer_subject_ref=subject(),
            write_instance_id="attempt-1",
        )
        assert first == second
        assert first.binding_id == second.binding_id
        assert first.decision_ref.decision_id == canonical_decision.decision_id


def test_binding_identity_changes_with_decision_or_write_instance() -> None:
    base = WriterGateBinder.bind(
        decision=decision("allow"),
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
    )
    new_decision = WriterGateBinder.bind(
        decision=decision("allow", decision_id="decision-2"),
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
    )
    new_attempt = WriterGateBinder.bind(
        decision=decision("allow"),
        writer_subject_ref=subject(),
        write_instance_id="attempt-2",
    )
    assert len({base.binding_id, new_decision.binding_id, new_attempt.binding_id}) == 3


def test_resolve_decision_ref_uses_only_independently_active_decision() -> None:
    canonical_decision = decision("allow")
    ref = WriterAdmissionDecisionRefSchema(decision_id=canonical_decision.decision_id)
    assert resolve_decision_ref(ref, active_decision=canonical_decision) == canonical_decision
    with pytest.raises(DecisionReferenceIntegrityError):
        resolve_decision_ref(ref, active_decision=None)
    with pytest.raises(DecisionReferenceIntegrityError):
        resolve_decision_ref(ref, active_decision=decision("allow", decision_id="other"))


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"current_writer_subject_ref": subject(section_id="section-2")}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_write_instance_id": "attempt-2"}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_decision": decision("allow", decision_id="decision-2")}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_active_evidence_bundle_sha256": SHA_E2}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_active_requirements_sha256": SHA_R2}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_active_binding_ref": WriterGateBindingRefSchema(binding_id="old-binding")}, BindingApplicabilityState.NOT_APPLICABLE),
        ({"current_decision": None}, BindingApplicabilityState.REFERENCE_INTEGRITY_FAILURE),
        ({"current_active_binding_ref": None}, BindingApplicabilityState.REFERENCE_INTEGRITY_FAILURE),
    ],
)
def test_applicability_stale_and_reference_integrity_matrix(override, expected) -> None:
    canonical_decision = decision("allow")
    binding = WriterGateBinder.bind(
        decision=canonical_decision,
        writer_subject_ref=subject(),
        write_instance_id="attempt-1",
    )
    inputs = {
        "binding": binding,
        "current_writer_subject_ref": subject(),
        "current_write_instance_id": "attempt-1",
        "current_decision": canonical_decision,
        "current_active_evidence_bundle_sha256": SHA_E1,
        "current_active_requirements_sha256": SHA_R1,
        "current_active_binding_ref": binding_ref(binding),
    }
    inputs.update(override)
    result = WriterGateApplicabilityValidator().validate(**inputs)
    assert result.state is expected
    if expected is not BindingApplicabilityState.APPLICABLE:
        assert result.decision is None
        assert result.binding is None


@pytest.mark.parametrize(
    "kind,mode,action,would_block",
    [
        ("allow", "strict", EnforcementAction.CONTINUE, False),
        ("allow", "shadow", EnforcementAction.CONTINUE, False),
        ("deny", "strict", EnforcementAction.BLOCK, True),
        ("deny", "shadow", EnforcementAction.CONTINUE, True),
        ("unresolved", "strict", EnforcementAction.BLOCK, True),
        ("unresolved", "shadow", EnforcementAction.CONTINUE, True),
    ],
)
def test_business_enforcement_matrix(kind, mode, action, would_block) -> None:
    canonical_decision, binding, applicability = applicable(kind)
    before_decision = canonical_decision.model_dump(mode="json")
    before_binding = binding.model_dump(mode="json")
    result = WriterAdmissionEnforcement().evaluate(
        applicability=applicability,
        decision=canonical_decision,
        mode=mode,
    )
    assert result.enforcement_basis is EnforcementBasis.BUSINESS_ADMISSION
    assert result.action is action
    assert result.would_block_in_strict is would_block
    assert canonical_decision.model_dump(mode="json") == before_decision
    assert binding.model_dump(mode="json") == before_binding


@pytest.mark.parametrize("mode", ["strict", "shadow"])
def test_technical_integrity_always_blocks_even_in_shadow(mode) -> None:
    canonical_decision, _, _ = applicable("allow")
    technical = WriterGateApplicabilityValidator().validate(
        binding=WriterGateBinder.bind(
            decision=canonical_decision,
            writer_subject_ref=subject(),
            write_instance_id="attempt-1",
        ),
        current_writer_subject_ref=None,
        current_write_instance_id="attempt-1",
        current_decision=canonical_decision,
        current_active_evidence_bundle_sha256=SHA_E1,
        current_active_requirements_sha256=SHA_R1,
        current_active_binding_ref=None,
    )
    result = WriterAdmissionEnforcement().evaluate(
        applicability=technical,
        decision=canonical_decision,
        mode=mode,
    )
    assert result.enforcement_basis is EnforcementBasis.TECHNICAL_INTEGRITY
    assert result.action is EnforcementAction.BLOCK
    assert result.would_block_in_strict is True
    assert result.decision_ref is None
    assert result.binding_ref is None


def test_enforcement_rejects_invalid_explicit_mode_without_valid_result() -> None:
    canonical_decision, _, applicability = applicable("allow")
    for mode in ("STRICT", "warn", "", None):
        with pytest.raises(ValueError, match="strict or shadow"):
            WriterAdmissionEnforcement().evaluate(
                applicability=applicability,
                decision=canonical_decision,
                mode=mode,
            )


def task(*, task_id: str, run_id: str, user_input: str = "write") -> TaskSchema:
    return TaskSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input=user_input,
        project_input={"user_query": user_input},
        created_at="2026-08-22T00:00:00+00:00",
    )


def test_task_store_claims_run_id_for_one_exact_immutable_identity(tmp_path) -> None:
    manager = JsonlTaskManager(output_dir=tmp_path)
    original = task(task_id="task-1", run_id="run-1")
    first = manager.create_task(original)
    second = manager.create_task(original)
    assert first.task_id == second.task_id == "task-1"
    assert manager._read_run_identity("run-1").task_id == "task-1"

    with pytest.raises(ValueError, match="run_id immutable"):
        manager.create_task(task(task_id="task-2", run_id="run-1"))


def test_mainline_default_run_generator_has_random_component_under_same_clock() -> None:
    class FixedClock:
        def now_iso(self):
            return "2026-08-22T05:00:00+00:00"

    service = MainlineApplicationService(clock=FixedClock())
    assert isinstance(service.id_generator, TimestampedUuidIdGenerator)
    first = service.id_generator.new_id("run")
    second = service.id_generator.new_id("run")
    assert first != second
    assert first.startswith("run_20260822T050000Z_")
    assert second.startswith("run_20260822T050000Z_")


@dataclass
class SequenceIdGenerator:
    values: list[str]

    def new_id(self, prefix: str) -> str:
        assert prefix == "section"
        return self.values.pop(0)


def test_true_new_section_allocator_is_independent_of_title_and_order() -> None:
    generator = SequenceIdGenerator(["section-new-1", "section-new-2"])
    first = DocumentPlanningService.allocate_new_section_id(id_generator=generator)
    second = DocumentPlanningService.allocate_new_section_id(id_generator=generator)
    assert first == "section-new-1"
    assert second == "section-new-2"
    assert first != second


def test_local_replan_retains_section_identity_but_creates_new_persisted_attempt() -> None:
    section_plan = SectionPlanSchema(
        section_id="section-1",
        section_title="Section",
        section_order=1,
    )
    project_input = ProjectInputSchema(
        task_id="task-1",
        task_type="scheme_generation",
        user_query="write",
        generation_requirements=GenerationRequirementsSchema(
            required_sections=["Section"]
        ),
        output_schema=OutputSchemaDefinition(required_sections=["Section"]),
    )
    section_state = SectionStateSchema(
        section_id="section-1",
        section_title="Section",
        section_order=1,
        attempt=AttemptStateSchema(
            attempt_id="run-1:section-1:attempt:1",
            attempt_number=1,
            input_snapshot_version="snapshot-1",
        ),
    )
    payload = SectionExecutionGraphPayloadSchema(
        task_id="task-1",
        run_id="run-1",
        document_id="document-1",
        project_input=project_input,
        section_plan=section_plan,
        shared_state_payload={},
        section_id="section-1",
        section_title="Section",
        section_order=1,
        document_evidence_bundle=EvidenceBundleSchema(
            query="document evidence",
            context=RAGContextSchema(context_text="", used_context_chars=0),
        ),
        section_state=section_state,
        generation_attempt=7,
    )
    revised = section_plan.model_copy(
        update={"metadata": {"replan_reason": "local_replan"}},
        deep=True,
    )
    replanned = MainlineReplanAttempt.prepare(payload=payload, revised_plan=revised)
    assert replanned.section_id == "section-1"
    assert replanned.section_state is not None
    assert replanned.section_state.section_id == "section-1"
    assert replanned.section_state.attempt.attempt_id != section_state.attempt.attempt_id
    assert replanned.section_state.attempt.attempt_id == "run-1:section-1:attempt:8"
    assert replanned.section_state.attempt.attempt_number == 8
    assert replanned.generation_attempt == 8
    assert str(replanned.generation_attempt) != replanned.section_state.attempt.attempt_id


def test_project_input_publication_is_write_once_and_equal_replay_is_idempotent() -> None:
    context = ContextBundleSchema(
        user=UserContextSchema(user_query="write"),
        task=TaskContextSchema(
            task_id="task-1",
            run_id="run-1",
            task_type="scheme_generation",
        ),
    )
    state = SharedStateSchema(
        task_id="task-1",
        run_id="run-1",
        task_type="scheme_generation",
        user_input="write",
        context_bundle=context,
        created_at="2026-08-22T00:00:00+00:00",
    )
    writer = SharedStateWriter()
    project_input = {"b": 2, "a": {"value": 1}}
    kwargs = dict(
        state=state,
        table_agent_output={},
        structured_facts=[],
        source_materials=[],
        missing_information=[],
        conflicting_information=[],
        manual_boundaries=[],
    )
    writer.set_project_input_normalization(project_input=project_input, **kwargs)
    project_input["a"]["value"] = 99
    assert state.context_bundle.business.project_input["a"]["value"] == 1

    writer.set_project_input_normalization(
        project_input={"a": {"value": 1}, "b": 2},
        **kwargs,
    )
    with pytest.raises(ValueError, match="cannot change"):
        writer.set_project_input_normalization(
            project_input={"a": {"value": 2}, "b": 2},
            **kwargs,
        )
