"""Canonical Gate V2 Target initial-Writer invocation boundary.

This module is the sole S11-15 owner for an already-committed NORMAL/FALLBACK
pre-write transition.  It consumes Router authority, locks one Evidence/context
snapshot, converges one logical execution through the existing execution store,
and dispatches the existing Writer capability only after durable DISPATCHED
intent.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping, Sequence

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.quality.budget import WorkflowBudgetExceeded
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetCapacityExceeded,
    CanonicalBudgetDeadlineExceeded,
    CanonicalBudgetDraining,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementResultSchema,
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionResultSchema,
    WriterExecutionStatus,
    WriterGenerationClass,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    EvidencePackageBindingSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanBindingSchema,
)
from schemas.context import ContextPackageSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from contracts.evidence import (
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from core.hashing import canonical_sha256
from core.model_input_accounting import ModelContextBudgetExceeded
from core.runtime.pre_write_execution_store import (
    PreWriteExecutionIntegrityError,
    PreWriteExecutionStore,
    WriterExecutionRecord,
    WriterExecutionState,
)
from core.runtime.pre_write_route_decision_store import PreWriteRouteDecisionStore
from schemas.common import ErrorSchema, ErrorSourceSchema
from schemas.failure import FailureCategory, FailureOrigin
from schemas.rag import EvidenceBundleSchema
from schemas.status import ExecutionStatus

from .pre_write_router import PreWriteRouter
from .prompt_service import (
    SectionPromptService,
    TargetInitialSectionPromptResult,
    TargetPromptContextIntegrityError,
    TargetTechnicalRecoveryIntegrityError,
    materialize_target_technical_recovery_context,
)
from .section_generation_service import (
    SectionGenerationService,
    TargetSectionContentResult,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
    TargetWriterContextIntegrityError,
)


class WriterInvocationIntegrityError(ValueError):
    """The proposed invocation does not represent one coherent frozen snapshot."""


class WriterInvocationInProgressError(RuntimeError):
    """A same-key execution is still active and has no canonical result yet."""


class WriterIndeterminateExternalExecutionError(RuntimeError):
    """DISPATCHED exists without a durable result and must never auto-redispatch."""


@dataclass(frozen=True)
class PreparedWriterInvocation:
    """Ephemeral preparation carrier; canonical truth remains the frozen schemas."""

    invocation: WriterGenerationInvocationSchema
    logical_execution_key: WriterLogicalExecutionKey
    target_prompt: TargetInitialSectionPromptResult
    semantic_fingerprint: str


def _route_ref(
    route_decision: PreWriteRouteDecisionSchema,
) -> PreWriteRouteDecisionRefSchema:
    return PreWriteRouteDecisionRefSchema(
        route_decision_id=route_decision.route_decision_id,
        route_decision_schema_version=route_decision.schema_version,
        route_decision_contract_version=route_decision.contract_version,
    )


def _logical_key_from_route(
    route_decision: PreWriteRouteDecisionSchema,
) -> WriterLogicalExecutionKey:
    return WriterLogicalExecutionKey(
        writer_subject_ref=route_decision.writer_subject_ref,
        write_instance_id=route_decision.write_instance_id,
        route_decision_ref=_route_ref(route_decision),
    )


def _decision_ref(
    decision: WriterAdmissionDecisionSchema,
) -> WriterAdmissionDecisionRefSchema:
    return WriterAdmissionDecisionRefSchema(
        decision_id=decision.decision_id,
        decision_schema_version=decision.schema_version,
        decision_contract_version=decision.contract_version,
    )


def _generation_class(route: PreWriteRoute) -> WriterGenerationClass:
    if route is PreWriteRoute.NORMAL:
        return WriterGenerationClass.NORMAL_WRITE
    if route is PreWriteRoute.FALLBACK:
        return WriterGenerationClass.FALLBACK_WRITE
    raise WriterInvocationIntegrityError(
        "RECOVERY route cannot form an initial Writer invocation"
    )


def _prompt_lock_payload(
    target_prompt: TargetInitialSectionPromptResult,
) -> dict[str, object]:
    recovery = target_prompt.technical_recovery_context
    return {
        "prompt_id": recovery.prompt_id,
        "prompt_version": recovery.prompt_version,
        "initial_prompt_text": recovery.initial_prompt_text,
        "context_package": target_prompt.context_package.model_dump(mode="json"),
        "context_package_id": recovery.context_package_id,
        "context_sha256": recovery.context_sha256,
        "writer_evidence_semantics_json": recovery.writer_evidence_semantics_json,
        "structured_writing_facts_json": recovery.structured_writing_facts_json,
        "citation_ids": list(recovery.citation_ids),
        "evidence_bundle_sha256": recovery.evidence_bundle_sha256,
    }


_CONTEXT_REPLAY_AUDIT_EPOCH = "1970-01-01T00:00:00+00:00"


def _context_package_semantic_snapshot(
    package: ContextPackageSchema,
) -> dict[str, object]:
    """Retain SIF-10 semantics/final input without audit-only timestamp churn."""

    return package.model_dump(mode="json", exclude={"created_at"})


def _thaw_json(value: Any) -> Any:
    """Convert immutable Writer JSON wrappers back to canonical JSON containers."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_thaw_json(item) for item in value]
    return value


def _restore_context_package_semantic_snapshot(
    value: Mapping[str, object],
) -> ContextPackageSchema:
    payload = _thaw_json(value)
    return ContextPackageSchema.model_validate(
        {
            **payload,
            # created_at is explicitly audit-only in SIF-10 package identity.
            "created_at": _CONTEXT_REPLAY_AUDIT_EPOCH,
        }
    )


def _unified_snapshot(
    *,
    shared_state: SharedStateSchema,
    project_input: ProjectInputSchema,
    writer_subject_ref: WriterSubjectRefSchema,
    section_title: str,
    section_order: int,
    structured_facts: Sequence[StructuredFactSchema],
    previous_sections: Sequence[SchemeSectionSchema],
    target_prompt: TargetInitialSectionPromptResult,
    target_context_package: ContextPackageSchema | None = None,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "task_id": shared_state.task_id,
        "run_id": shared_state.run_id,
        "document_id": writer_subject_ref.document_id,
        "section": {
            "section_id": writer_subject_ref.section_id,
            "section_title": section_title,
            "section_order": section_order,
        },
        "project_input": project_input.model_dump(mode="json"),
        "structured_writing_facts": [
            item.model_dump(mode="json") for item in structured_facts
        ],
        # M8 retires raw previous-section bytes on Target paths. Their nested
        # Writer inputs are audit history, not context; copying them here doubles
        # the snapshot each chapter. Authorized cross-section material is already
        # bound by target_context_package. Keep the compatibility path unchanged.
        "previous_sections": (
            [] if target_context_package is not None
            else [item.model_dump(mode="json") for item in previous_sections]
        ),
        "target_prompt_lock": _prompt_lock_payload(target_prompt),
    }
    if target_context_package is not None:
        snapshot["target_context_package"] = _context_package_semantic_snapshot(
            target_context_package
        )
    return snapshot


class WriterInvocationBuilder:
    """Deterministically lock one route-authorized Writer invocation."""

    def __init__(
        self,
        *,
        prompt_service: SectionPromptService,
        target_context_composer: TargetWriterContextComposer | None = None,
    ) -> None:
        self.prompt_service = prompt_service
        self.target_context_composer = target_context_composer

    def build(
        self,
        *,
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        evidence_bundle: EvidenceBundleSchema,
        section_title: str,
        section_order: int,
        structured_facts: Sequence[StructuredFactSchema],
        previous_sections: Sequence[SchemeSectionSchema],
        technical_execution_constraints: Mapping[str, object] | None = None,
        canonical_plan_binding: SectionPlanBindingSchema | None = None,
        active_evidence_package_binding: EvidencePackageBindingSchema | None = None,
        gate_binding: WriterGateBindingSchema | None = None,
    ) -> PreparedWriterInvocation:
        writer_subject_ref = route_decision.writer_subject_ref
        if route_decision.decision_ref != _decision_ref(decision):
            raise WriterInvocationIntegrityError(
                "RouteDecision does not reference the supplied current Decision"
            )
        evidence_hash = evidence_bundle_sha256(evidence_bundle)
        if decision.fact_assessment_ref.evidence_bundle_sha256 != evidence_hash:
            raise WriterInvocationIntegrityError(
                "canonical Evidence hash does not match route-authorizing Decision"
            )
        if shared_state.run_id != writer_subject_ref.run_id:
            raise WriterInvocationIntegrityError(
                "Writer subject run_id does not match shared state"
            )
        if writer_subject_ref.section_id.strip() == "":
            raise WriterInvocationIntegrityError("Writer subject section_id is required")

        target_prompt = self.prompt_service.render_target_initial_section_prompt(
            shared_state,
            project_input,
            section_id=writer_subject_ref.section_id,
            section_title=section_title,
            section_order=section_order,
            evidence_bundle=evidence_bundle,
            structured_facts=list(structured_facts),
            previous_sections=list(previous_sections),
        )
        target_context_package = None
        if self.target_context_composer is not None:
            if (
                canonical_plan_binding is None
                or active_evidence_package_binding is None
                or gate_binding is None
            ):
                raise WriterInvocationIntegrityError(
                    "Target Writer Context requires canonical Plan, EvidencePackage, and Gate bindings"
                )
            try:
                target_context_package = self.target_context_composer.build_package(
                    project_input=project_input,
                    plan_binding=canonical_plan_binding,
                    evidence_package_binding=active_evidence_package_binding,
                    gate_binding=gate_binding,
                    route_decision=route_decision,
                    decision=decision,
                    write_instance_id=route_decision.write_instance_id,
                )
            except TargetWriterContextIntegrityError as exc:
                raise WriterInvocationIntegrityError(str(exc)) from exc
        invocation = WriterGenerationInvocationSchema(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=route_decision.write_instance_id,
            route_decision_ref=_route_ref(route_decision),
            generation_class=_generation_class(route_decision.selected_route),
            canonical_evidence_input={
                **evidence_bundle.model_dump(mode="json"),
                "evidence_bundle_sha256": evidence_hash,
            },
            unified_writer_input_snapshot=_unified_snapshot(
                shared_state=shared_state,
                project_input=project_input,
                writer_subject_ref=writer_subject_ref,
                section_title=section_title,
                section_order=section_order,
                structured_facts=structured_facts,
                previous_sections=previous_sections,
                target_prompt=target_prompt,
                target_context_package=target_context_package,
            ),
            technical_execution_constraints=dict(
                technical_execution_constraints or {}
            ),
        )
        logical_execution_key = WriterLogicalExecutionKey(
            writer_subject_ref=invocation.writer_subject_ref,
            write_instance_id=invocation.write_instance_id,
            route_decision_ref=invocation.route_decision_ref,
        )
        fingerprint = canonical_sha256(invocation.model_dump(mode="json"))
        return PreparedWriterInvocation(
            invocation=invocation,
            logical_execution_key=logical_execution_key,
            target_prompt=target_prompt,
            semantic_fingerprint=fingerprint,
        )


class WriterInvocationIntegrityValidator:
    """Validate lineage and lock coherence without reassessing business truth."""

    @staticmethod
    def validate(
        prepared: PreparedWriterInvocation,
        *,
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        evidence_bundle: EvidenceBundleSchema,
        section_title: str,
        section_order: int,
        structured_facts: Sequence[StructuredFactSchema],
        previous_sections: Sequence[SchemeSectionSchema],
    ) -> WriterGenerationInvocationSchema:
        invocation = prepared.invocation
        expected_key = WriterLogicalExecutionKey(
            writer_subject_ref=invocation.writer_subject_ref,
            write_instance_id=invocation.write_instance_id,
            route_decision_ref=invocation.route_decision_ref,
        )
        if prepared.logical_execution_key != expected_key:
            raise WriterInvocationIntegrityError(
                "WriterLogicalExecutionKey does not match invocation lineage"
            )
        if prepared.semantic_fingerprint != canonical_sha256(
            invocation.model_dump(mode="json")
        ):
            raise WriterInvocationIntegrityError(
                "Writer invocation semantic fingerprint mismatch"
            )
        if invocation.route_decision_ref != _route_ref(route_decision):
            raise WriterInvocationIntegrityError("route_decision_ref mismatch")
        if invocation.writer_subject_ref != route_decision.writer_subject_ref:
            raise WriterInvocationIntegrityError("Writer subject lineage mismatch")
        if invocation.write_instance_id != route_decision.write_instance_id:
            raise WriterInvocationIntegrityError("write_instance_id lineage mismatch")
        if route_decision.decision_ref != _decision_ref(decision):
            raise WriterInvocationIntegrityError("Decision lineage mismatch")
        if invocation.generation_class is not _generation_class(
            route_decision.selected_route
        ):
            raise WriterInvocationIntegrityError(
                "generation_class does not match committed route"
            )

        evidence_hash = evidence_bundle_sha256(evidence_bundle)
        if invocation.canonical_evidence_input.evidence_bundle_sha256 != evidence_hash:
            raise WriterInvocationIntegrityError(
                "invocation Evidence hash does not match supplied canonical snapshot"
            )
        if decision.fact_assessment_ref.evidence_bundle_sha256 != evidence_hash:
            raise WriterInvocationIntegrityError(
                "invocation Evidence is not the route-authorizing snapshot"
            )
        locked_bundle = EvidenceBundleSchema.model_validate(
            invocation.canonical_evidence_input.model_dump(
                mode="json",
                exclude={"evidence_bundle_sha256"},
            )
        )
        if locked_bundle != evidence_bundle:
            raise WriterInvocationIntegrityError(
                "invocation Evidence value differs from supplied canonical snapshot"
            )

        target_prompt = prepared.target_prompt
        try:
            locked_package = materialize_target_technical_recovery_context(
                target_prompt.technical_recovery_context
            )
        except TargetTechnicalRecoveryIntegrityError as exc:
            raise WriterInvocationIntegrityError(str(exc)) from exc
        if locked_package != target_prompt.context_package:
            raise WriterInvocationIntegrityError(
                "Target Prompt context package differs from its technical lock"
            )
        if (
            target_prompt.prompt_result.rendered_text
            != target_prompt.technical_recovery_context.initial_prompt_text
        ):
            raise WriterInvocationIntegrityError(
                "Target Prompt text differs from its invocation lock"
            )
        projected_rag, _, projected_citations = project_evidence_bundle(
            evidence_bundle
        )
        if target_prompt.rag_context != projected_rag:
            raise WriterInvocationIntegrityError(
                "Target Prompt RAG projection is not derived from locked Evidence"
            )
        if tuple(projected_citations) != target_prompt.citations:
            raise WriterInvocationIntegrityError(
                "Target Prompt citations are not derived from locked Evidence"
            )

        actual_snapshot = invocation.model_dump(mode="json")[
            "unified_writer_input_snapshot"
        ]
        target_context_package = None
        raw_target_context = actual_snapshot.get("target_context_package")
        if raw_target_context is not None:
            try:
                target_context_package = (
                    _restore_context_package_semantic_snapshot(
                        raw_target_context
                    )
                )
            except Exception as exc:
                raise WriterInvocationIntegrityError(
                    "Target ContextPackage embedded in Writer invocation is invalid"
                ) from exc
            if (
                target_context_package.invocation_subject.run_id
                != shared_state.run_id
                or target_context_package.invocation_subject.attempt_id
                != invocation.write_instance_id
                or target_context_package.invocation_subject.consumer_scope_ref
                != f"section:{invocation.writer_subject_ref.section_id}"
            ):
                raise WriterInvocationIntegrityError(
                    "Target ContextPackage invocation lineage mismatch"
                )
        expected_snapshot = _unified_snapshot(
            shared_state=shared_state,
            project_input=project_input,
            writer_subject_ref=route_decision.writer_subject_ref,
            section_title=section_title,
            section_order=section_order,
            structured_facts=structured_facts,
            previous_sections=previous_sections,
            target_prompt=target_prompt,
            target_context_package=target_context_package,
        )
        if actual_snapshot != expected_snapshot:
            raise WriterInvocationIntegrityError(
                "unified Writer input snapshot is stale or incoherent"
            )
        if project_input.task_id != shared_state.task_id:
            raise WriterInvocationIntegrityError(
                "ProjectInput task_id does not match Writer task identity"
            )
        if route_decision.writer_subject_ref.run_id != shared_state.run_id:
            raise WriterInvocationIntegrityError(
                "Writer subject run_id does not match Writer run identity"
            )
        if target_prompt.section_id != route_decision.writer_subject_ref.section_id:
            raise WriterInvocationIntegrityError("section identity mismatch")
        return invocation


class PreWriteWriterInvocationService:
    """Only legal Target initial Writer dispatch owner for NORMAL/FALLBACK."""

    def __init__(
        self,
        *,
        builder: WriterInvocationBuilder,
        integrity_validator: WriterInvocationIntegrityValidator,
        execution_store: PreWriteExecutionStore,
        route_decision_store: PreWriteRouteDecisionStore,
        section_generation_service: SectionGenerationService,
        duplicate_wait_seconds: float = 5.0,
        duplicate_poll_seconds: float = 0.01,
    ) -> None:
        if duplicate_wait_seconds < 0:
            raise ValueError("duplicate_wait_seconds must be >= 0")
        if duplicate_poll_seconds <= 0:
            raise ValueError("duplicate_poll_seconds must be > 0")
        self.builder = builder
        self.integrity_validator = integrity_validator
        self.execution_store = execution_store
        self.route_decision_store = route_decision_store
        self.section_generation_service = section_generation_service
        self.duplicate_wait_seconds = float(duplicate_wait_seconds)
        self.duplicate_poll_seconds = float(duplicate_poll_seconds)

    @staticmethod
    def _terminal_result(
        record: WriterExecutionRecord,
    ) -> WriterExecutionResultSchema | None:
        if record.state in {
            WriterExecutionState.SUCCEEDED,
            WriterExecutionState.CONFIRMED_FAILED,
        }:
            if record.result is None:
                raise WriterInvocationIntegrityError(
                    "terminal Writer execution is missing its canonical result"
                )
            return record.result
        return None

    def _wait_for_existing_result(
        self,
        prepared: PreparedWriterInvocation,
    ) -> WriterExecutionResultSchema:
        deadline = time.monotonic() + self.duplicate_wait_seconds
        while True:
            record = self.execution_store.get_writer_execution(
                prepared.logical_execution_key
            )
            if record is None:
                raise WriterInvocationIntegrityError(
                    "reserved Writer execution disappeared from durable store"
                )
            if record.semantic_fingerprint != prepared.semantic_fingerprint:
                raise WriterInvocationIntegrityError(
                    "same Writer key resolved a conflicting semantic fingerprint"
                )
            terminal = self._terminal_result(record)
            if terminal is not None:
                return terminal
            if time.monotonic() >= deadline:
                if record.state is WriterExecutionState.DISPATCHED:
                    raise WriterIndeterminateExternalExecutionError(
                        "Writer is DISPATCHED without a durable canonical result; "
                        "automatic redispatch is forbidden"
                    )
                raise WriterInvocationInProgressError(
                    "Writer reservation has no canonical result yet"
                )
            time.sleep(self.duplicate_poll_seconds)

    @staticmethod
    def _generation_provenance(
        prepared: PreparedWriterInvocation,
    ) -> dict[str, object]:
        invocation = prepared.invocation
        recovery = prepared.target_prompt.technical_recovery_context
        return {
            "writer_subject_ref": invocation.writer_subject_ref.model_dump(
                mode="json"
            ),
            "write_instance_id": invocation.write_instance_id,
            "route_decision_ref": invocation.route_decision_ref.model_dump(
                mode="json"
            ),
            "generation_class": invocation.generation_class.value,
            "evidence_bundle_sha256": (
                invocation.canonical_evidence_input.evidence_bundle_sha256
            ),
            "prompt_id": recovery.prompt_id,
            "prompt_version": recovery.prompt_version,
            "context_package_id": recovery.context_package_id,
            "context_sha256": recovery.context_sha256,
        }

    @staticmethod
    def _model_call_ids(
        content_result: TargetSectionContentResult,
    ) -> list[str]:
        model_call_ids = [content_result.model_response.model_call_id]
        if content_result.recovery is not None:
            model_call_ids.extend(
                response.model_call_id
                for response in content_result.recovery.truncation_retry_responses
            )
            if content_result.recovery.compression_response is not None:
                model_call_ids.append(
                    content_result.recovery.compression_response.model_call_id
                )
        return model_call_ids

    @staticmethod
    def _success_artifact(
        prepared: PreparedWriterInvocation,
        content_result: TargetSectionContentResult,
    ) -> SchemeSectionSchema:
        target_prompt = prepared.target_prompt
        recovery = target_prompt.technical_recovery_context
        response = content_result.model_response
        model_call_ids = PreWriteWriterInvocationService._model_call_ids(
            content_result
        )
        return SchemeSectionSchema(
            section_id=recovery.section_id,
            section_title=recovery.section_title,
            section_order=recovery.section_order,
            input={
                "writer_generation_invocation": (
                    prepared.invocation.model_dump(mode="json")
                ),
                "llm_context_package": target_prompt.context_package.model_dump(
                    mode="json"
                ),
            },
            prompt=recovery.initial_prompt_text,
            model_output=response.content,
            content=content_result.content,
            status=ExecutionStatus.SUCCESS,
            citation_ids=[item.citation_id for item in target_prompt.citations],
            truncation=content_result.truncation,
            started_at=response.started_at or response.created_at,
            finished_at=response.finished_at or response.created_at,
            extra={
                **PreWriteWriterInvocationService._generation_provenance(prepared),
                "model_call_id": response.model_call_id,
                "model_call_ids": model_call_ids,
                "target_initial_writer": True,
            },
        )

    @staticmethod
    def _failure_kind(
        content_result: TargetSectionContentResult,
    ) -> WriterExecutionFailureKind:
        response = content_result.model_response
        if response.error is not None:
            if response.error.failure_category is FailureCategory.TIMEOUT:
                return WriterExecutionFailureKind.MODEL_TIMEOUT
            if response.error.failure_category is FailureCategory.AVAILABILITY:
                return WriterExecutionFailureKind.MODEL_OR_PROVIDER_UNAVAILABLE
            if response.error.failure_category is FailureCategory.RESOURCE_EXHAUSTED:
                return WriterExecutionFailureKind.RUNTIME_RESOURCE_FAILURE
        reason = (content_result.technical_failure_reason or "").lower()
        if "empty" in reason:
            return WriterExecutionFailureKind.EMPTY_OR_UNUSABLE_OUTPUT
        if "truncat" in reason:
            return WriterExecutionFailureKind.UNRECOVERABLE_TRUNCATION
        if "integrity" in reason or "context" in reason:
            return WriterExecutionFailureKind.CONTEXT_INTEGRITY_FAILURE
        return WriterExecutionFailureKind.MODEL_INVOCATION_FAILURE

    @staticmethod
    def _failure_error(
        content_result: TargetSectionContentResult,
    ) -> ErrorSchema:
        response = content_result.model_response
        if response.error is not None:
            return response.error
        message = (
            content_result.technical_failure_reason
            or response.error_message
            or "Target initial Writer execution failed technically"
        )
        return ErrorSchema(
            error_code="TARGET_WRITER_TECHNICAL_FAILURE",
            error_type="WriterTechnicalFailure",
            message=message,
            retryable=False,
            failure_category=FailureCategory.UNKNOWN,
            source=ErrorSourceSchema(
                component="PreWriteWriterInvocationService",
                step_name=content_result.model_response.model_call_id,
            ),
        )

    def _result_from_content(
        self,
        prepared: PreparedWriterInvocation,
        content_result: TargetSectionContentResult,
    ) -> WriterExecutionResultSchema:
        model_call_ids = self._model_call_ids(content_result)
        provenance = {
            **self._generation_provenance(prepared),
            "model_call_id": content_result.model_response.model_call_id,
            "model_call_ids": model_call_ids,
        }
        metadata = {
            "model_call_id": content_result.model_response.model_call_id,
            "model_call_ids": model_call_ids,
            "model_name": content_result.model_response.model_name,
            "finish_reason": content_result.model_response.finish_reason,
            "context_package_id": content_result.context_package_id,
            "context_sha256": content_result.context_sha256,
        }
        if content_result.success:
            return WriterExecutionResultSchema(
                logical_execution_key=prepared.logical_execution_key,
                status=WriterExecutionStatus.SUCCESS,
                artifact=self._success_artifact(prepared, content_result),
                generation_provenance=provenance,
                technical_execution_metadata=metadata,
            )
        return WriterExecutionResultSchema(
            logical_execution_key=prepared.logical_execution_key,
            status=WriterExecutionStatus.TECHNICAL_FAILURE,
            failure_kind=self._failure_kind(content_result),
            error=self._failure_error(content_result),
            generation_provenance=provenance,
            technical_execution_metadata=metadata,
        )

    @staticmethod
    def _integrity_failure_result(
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
        exc: Exception,
    ) -> WriterExecutionResultSchema:
        logical_execution_key = _logical_key_from_route(route_decision)
        return WriterExecutionResultSchema(
            logical_execution_key=logical_execution_key,
            status=WriterExecutionStatus.TECHNICAL_FAILURE,
            failure_kind=WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE,
            error=ErrorSchema(
                error_code="TARGET_WRITER_INVOCATION_INTEGRITY_FAILURE",
                error_type=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
                retryable=False,
                failure_category=FailureCategory.VALIDATION,
                source=ErrorSourceSchema(
                    component="PreWriteWriterInvocationService"
                ),
            ),
            generation_provenance={
                "writer_subject_ref": route_decision.writer_subject_ref.model_dump(
                    mode="json"
                ),
                "write_instance_id": route_decision.write_instance_id,
                "route_decision_ref": _route_ref(route_decision).model_dump(
                    mode="json"
                ),
                "generation_class": _generation_class(
                    route_decision.selected_route
                ).value,
                "evidence_bundle_sha256": (
                    decision.fact_assessment_ref.evidence_bundle_sha256
                ),
            },
            technical_execution_metadata={
                "exception_type": type(exc).__name__,
                "model_call_ids": [],
                "physical_model_dispatch_count": 0,
            },
        )

    def _exception_result(
        self,
        prepared: PreparedWriterInvocation,
        exc: Exception,
    ) -> WriterExecutionResultSchema:
        canonical_budget_failure = isinstance(
            exc,
            (
                CanonicalBudgetCapacityExceeded,
                CanonicalBudgetDeadlineExceeded,
                CanonicalBudgetDraining,
            ),
        )
        if isinstance(exc, WriterInvocationIntegrityError):
            failure_kind = (
                WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
            )
        elif isinstance(
            exc,
            (
                TargetPromptContextIntegrityError,
                TargetTechnicalRecoveryIntegrityError,
            ),
        ):
            failure_kind = WriterExecutionFailureKind.CONTEXT_INTEGRITY_FAILURE
        elif isinstance(exc, WorkflowBudgetExceeded) or canonical_budget_failure:
            failure_kind = WriterExecutionFailureKind.RUNTIME_RESOURCE_FAILURE
        else:
            failure_kind = WriterExecutionFailureKind.MODEL_INVOCATION_FAILURE
        # Classify at the typed producer boundary, before the immutable result is
        # persisted. Outer workflow/terminal readers must transport these facts.
        details: dict[str, object] = {}
        if canonical_budget_failure:
            details["failure_origin"] = FailureOrigin.CANONICAL_BUDGET.value
            if isinstance(exc, CanonicalBudgetCapacityExceeded):
                details.update(
                    resource=exc.resource,
                    limit=exc.limit,
                    remaining=exc.remaining,
                    required=exc.required,
                )
            elif isinstance(exc, CanonicalBudgetDeadlineExceeded):
                details["deadline_at"] = exc.deadline_at.isoformat()
        return WriterExecutionResultSchema(
            logical_execution_key=prepared.logical_execution_key,
            status=WriterExecutionStatus.TECHNICAL_FAILURE,
            failure_kind=failure_kind,
            error=ErrorSchema(
                error_code="TARGET_WRITER_EXECUTION_EXCEPTION",
                error_type=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
                retryable=False,
                failure_category=(
                    FailureCategory.VALIDATION
                    if isinstance(exc, ModelContextBudgetExceeded) or failure_kind
                    in {
                        WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE,
                        WriterExecutionFailureKind.CONTEXT_INTEGRITY_FAILURE,
                    }
                    else FailureCategory.RESOURCE_EXHAUSTED
                    if failure_kind
                    is WriterExecutionFailureKind.RUNTIME_RESOURCE_FAILURE
                    else FailureCategory.UNKNOWN
                ),
                source=ErrorSourceSchema(
                    component="PreWriteWriterInvocationService"
                ),
                details=details,
            ),
            generation_provenance=self._generation_provenance(prepared),
            technical_execution_metadata={
                "exception_type": type(exc).__name__,
                "model_call_ids": [],
            },
        )

    def execute_initial(
        self,
        route_decision: PreWriteRouteDecisionSchema,
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        applicability: BindingApplicabilityResult,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        evidence_bundle: EvidenceBundleSchema,
        section_title: str,
        section_order: int,
        structured_facts: Sequence[StructuredFactSchema],
        previous_sections: Sequence[SchemeSectionSchema],
        technical_execution_constraints: Mapping[str, object] | None = None,
        canonical_plan_binding: SectionPlanBindingSchema | None = None,
        active_evidence_package_binding: EvidencePackageBindingSchema | None = None,
    ) -> WriterExecutionResultSchema:
        """Validate current Route, lock E1, and execute at most once."""

        authorization_snapshot_sha256 = PreWriteRouter.authorization_snapshot_sha256(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        committed_route = self.route_decision_store.resolve_exact_for_snapshot(
            _route_ref(route_decision),
            authorization_snapshot_sha256=authorization_snapshot_sha256,
        )
        if committed_route != route_decision:
            raise WriterInvocationIntegrityError(
                "supplied RouteDecision disagrees with canonical store"
            )
        committed_route = PreWriteRouter.validate_commit(
            committed_route,
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        try:
            prepared = self.builder.build(
                route_decision=committed_route,
                decision=decision,
                shared_state=shared_state,
                project_input=project_input,
                evidence_bundle=evidence_bundle,
                section_title=section_title,
                section_order=section_order,
                structured_facts=structured_facts,
                previous_sections=previous_sections,
                technical_execution_constraints=technical_execution_constraints,
                canonical_plan_binding=canonical_plan_binding,
                active_evidence_package_binding=active_evidence_package_binding,
                gate_binding=binding,
            )
            self.integrity_validator.validate(
                prepared,
                route_decision=committed_route,
                decision=decision,
                shared_state=shared_state,
                project_input=project_input,
                evidence_bundle=evidence_bundle,
                section_title=section_title,
                section_order=section_order,
                structured_facts=structured_facts,
                previous_sections=previous_sections,
            )
        except WriterInvocationIntegrityError as exc:
            return self._integrity_failure_result(
                committed_route,
                decision,
                exc,
            )

        try:
            record = self.execution_store.get_writer_execution(
                prepared.logical_execution_key
            )
            if record is None:
                raise WriterInvocationIntegrityError(
                    "Writer execution must be RESERVED before hard dispatch"
                )
            if record.semantic_fingerprint != prepared.semantic_fingerprint:
                raise WriterInvocationIntegrityError(
                    "pre-existing Writer reservation fingerprint mismatch"
                )
            terminal = self._terminal_result(record)
            if terminal is not None:
                return terminal
            dispatched = self.execution_store.mark_writer_dispatched(
                prepared.logical_execution_key,
                semantic_fingerprint=prepared.semantic_fingerprint,
            )
        except (
            PreWriteExecutionIntegrityError,
            WriterInvocationIntegrityError,
        ) as exc:
            return self._integrity_failure_result(
                committed_route,
                decision,
                exc,
            )
        if not dispatched:
            try:
                return self._wait_for_existing_result(prepared)
            except WriterInvocationIntegrityError as exc:
                return self._integrity_failure_result(
                    committed_route,
                    decision,
                    exc,
                )

        try:
            canonical_messages = None
            canonical_context_package = None
            canonical_allowed_aliases = None
            composer = self.builder.target_context_composer
            raw_context_package = prepared.invocation.unified_writer_input_snapshot.get(
                "target_context_package"
            )
            if raw_context_package is not None:
                if composer is None:
                    raise WriterInvocationIntegrityError(
                        "Target ContextPackage is present without its production composer"
                    )
                canonical_context_package = (
                    _restore_context_package_semantic_snapshot(
                        raw_context_package
                    )
                )
                canonical_messages = composer.final_messages(
                    canonical_context_package,
                    project_input=project_input,
                )
                canonical_allowed_aliases = composer.locked_citation_aliases(
                    canonical_context_package
                )
            content_result = (
                self.section_generation_service.generate_from_pre_write_invocation(
                    prepared.invocation,
                    shared_state,
                    project_input=project_input,
                    target_prompt=prepared.target_prompt,
                    canonical_messages=canonical_messages,
                    canonical_allowed_aliases=canonical_allowed_aliases,
                    canonical_context_package=(
                        canonical_context_package.model_dump(mode="json")
                        if canonical_context_package is not None
                        else None
                    ),
                )
            )
            result = self._result_from_content(prepared, content_result)
        except Exception as exc:
            result = self._exception_result(prepared, exc)

        durable = self.execution_store.persist_writer_result(
            prepared.logical_execution_key,
            semantic_fingerprint=prepared.semantic_fingerprint,
            result=result,
        )
        if durable.result is None:
            raise WriterInvocationIntegrityError(
                "durable Writer terminal state is missing its canonical result"
            )
        return durable.result


__all__ = [
    "PreWriteWriterInvocationService",
    "PreparedWriterInvocation",
    "WriterIndeterminateExternalExecutionError",
    "WriterInvocationBuilder",
    "WriterInvocationInProgressError",
    "WriterInvocationIntegrityError",
    "WriterInvocationIntegrityValidator",
]
