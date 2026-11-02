"""Deterministic RunCompletionAssessment derivation and revalidation.

The builder is the SIF-11/Runtime integration owner for the derived completion
fact. It may consume already-resolved Post-write values, but it does not
persist the assessment, mutate Run lifecycle, or decide Release/publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Any, Callable

from pydantic import BaseModel

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunCompletionAssessmentRecordSchema,
    RunCompletionAssessmentRefSchema,
    RunCompletionExactRefEnvelopeSchema,
    RunCompletionLogicalOperationRefSchema,
    RunFinalizationBarrierSchema,
)
from apps.enterprise_document.schemas.post_write.control import (
    DocumentPostWriteControlInputManifestSchema,
    PostWriteControlDecisionSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveDocumentBindingProjectionSchema,
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    EnforcementAction,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
)
from core.hashing import canonical_sha256

from .run_completion_assessment_store import RunCompletionAssessmentStore


RUN_COMPLETION_ASSESSMENT_COMMIT = "RUN_COMPLETION_ASSESSMENT_COMMIT"


def build_run_finalization_barrier(*, run_id, final_control_ref):
    envelope = exact_ref_envelope(source_authority="POST_WRITE_CONTROL",
        source_kind="POST_WRITE_CONTROL_DECISION",
        source_scope_ref=final_control_ref.control_decision_id, exact_ref=final_control_ref)
    kwargs = dict(run_id=run_id, final_control_decision_ref=envelope, barrier_contract_version="v1")
    placeholder = RunFinalizationBarrierSchema.model_construct(**kwargs, barrier_sha256="0"*64)
    payload = placeholder.model_dump(mode="json")
    payload.pop("barrier_sha256")
    return RunFinalizationBarrierSchema(**kwargs, barrier_sha256=canonical_sha256(payload))


class RunCompletionAssessmentError(ValueError):
    pass


class RunCompletionAssessmentPrerequisiteError(
    RunCompletionAssessmentError
):
    pass


class RunCompletionAssessmentStaleError(
    RunCompletionAssessmentPrerequisiteError
):
    pass


def _canonical_payload(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("exact completion ref must be a Pydantic model or dict")


def exact_ref_envelope(
    *,
    source_authority: str,
    source_kind: str,
    source_scope_ref: str,
    exact_ref: BaseModel | dict[str, Any],
    exact_ref_schema_version: str | None = None,
) -> RunCompletionExactRefEnvelopeSchema:
    payload = _canonical_payload(exact_ref)
    schema_version = str(
        exact_ref_schema_version
        or payload.get("schema_version")
        or f"{source_kind.lower()}_ref_v1"
    ).strip()
    if not schema_version:
        raise ValueError("exact ref schema version is required")
    return RunCompletionExactRefEnvelopeSchema(
        source_authority=str(source_authority).strip(),
        source_kind=str(source_kind).strip(),
        source_scope_ref=str(source_scope_ref).strip(),
        exact_ref_payload=payload,
        exact_ref_schema_version=schema_version,
        exact_ref_sha256=canonical_sha256(payload),
    )


@dataclass(frozen=True)
class RunCompletionTerminalizationSnapshot:
    post_write_control_decision: PostWriteControlDecisionSchema
    fresh_document_currentness_proof: CurrentnessProofSchema
    active_repair_attempt_refs: tuple[str, ...] = ()


class RunCompletionAssessmentBuilder:
    """Build/revalidate exact SUCCESS completion facts.

    The caller remains responsible for resolving canonical Section completion
    refs and for presenting the fresh owner-supplied Document currentness proof.
    """

    def __init__(
        self,
        *,
        assessment_contract_version: str = "run_completion_assessment_v2",
    ) -> None:
        self.assessment_contract_version = str(
            assessment_contract_version or ""
        ).strip()
        if not self.assessment_contract_version:
            raise ValueError("assessment_contract_version is required")

    @staticmethod
    def _canonical_refs(
        refs: Iterable[RunCompletionExactRefEnvelopeSchema],
        *,
        required: bool,
        label: str,
    ) -> tuple[RunCompletionExactRefEnvelopeSchema, ...]:
        items = tuple(
            sorted(
                (
                    RunCompletionExactRefEnvelopeSchema.model_validate(item)
                    for item in refs
                ),
                key=lambda item: (
                    item.source_authority,
                    item.source_kind,
                    item.source_scope_ref,
                    item.exact_ref_sha256,
                ),
            )
        )
        keys = [
            (
                item.source_authority,
                item.source_kind,
                item.source_scope_ref,
                item.exact_ref_sha256,
            )
            for item in items
        ]
        if required and not items:
            raise RunCompletionAssessmentPrerequisiteError(
                f"{label} must contain exact completion refs"
            )
        if len(keys) != len(set(keys)):
            raise RunCompletionAssessmentPrerequisiteError(
                f"{label} must be unique"
            )
        return items

    @staticmethod
    def _validate_final_post_write_subject(
        *,
        run_id: str,
        document_artifact_ref: ArtifactStateRefSchema,
        document_post_write_entry_ref: PostWriteEntryRefSchema,
        post_write_control_decision: PostWriteControlDecisionSchema,
        fresh_document_currentness_proof: CurrentnessProofSchema,
        active_repair_attempt_refs: Iterable[str],
    ) -> None:
        artifact_ref = ArtifactStateRefSchema.model_validate(
            document_artifact_ref
        )
        entry_ref = PostWriteEntryRefSchema.model_validate(
            document_post_write_entry_ref
        )
        control = PostWriteControlDecisionSchema.model_validate(
            post_write_control_decision
        )
        proof = CurrentnessProofSchema.model_validate(
            fresh_document_currentness_proof
        )
        active_repairs = tuple(
            sorted(
                set(
                    str(item).strip()
                    for item in active_repair_attempt_refs
                    if str(item).strip()
                )
            )
        )

        if artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise RunCompletionAssessmentPrerequisiteError(
                "Run completion requires final DOCUMENT Artifact"
            )
        if not isinstance(
            control.input_manifest,
            DocumentPostWriteControlInputManifestSchema,
        ):
            raise RunCompletionAssessmentPrerequisiteError(
                "Run completion requires final DOCUMENT ControlDecision"
            )
        if (
            control.input_manifest.artifact_state_ref != artifact_ref
            or control.input_manifest.post_write_entry_ref != entry_ref
        ):
            raise RunCompletionAssessmentPrerequisiteError(
                "final Document Artifact/Entry/Control binding mismatch"
            )
        if proof.currentness_state is not CurrentnessState.CURRENT:
            raise RunCompletionAssessmentStaleError(
                "final Document Artifact/Entry is not CURRENT"
            )
        if (
            proof.historical_entry_ref != entry_ref
            or proof.expected_active_entry_identity.artifact_state_ref
            != artifact_ref
            or not isinstance(
                proof.active_projection,
                ActiveDocumentBindingProjectionSchema,
            )
        ):
            raise RunCompletionAssessmentStaleError(
                "fresh Document currentness proof targets another final subject"
            )
        if (
            proof.expected_active_entry_identity
            != control.fresh_currentness_proof.expected_active_entry_identity
        ):
            raise RunCompletionAssessmentStaleError(
                "final ControlDecision is stale against current Document binding"
            )
        if control.enforcement_action not in {
            EnforcementAction.OUTPUT,
            EnforcementAction.OUTPUT_BEST_EFFORT,
            EnforcementAction.BLOCK,
        }:
            raise RunCompletionAssessmentPrerequisiteError(
                "final ControlDecision remains mutation-capable CORRECT"
            )
        dispatch = control.document_correction_dispatch_projection
        if (
            dispatch is not None
            and dispatch.correction_mode.value != "NOT_ELIGIBLE"
        ):
            raise RunCompletionAssessmentPrerequisiteError(
                "recoverable Document correction dispatch remains open"
            )
        if active_repairs:
            raise RunCompletionAssessmentPrerequisiteError(
                "active MA repair Attempt remains for Run: "
                + ", ".join(active_repairs)
            )

        active_run = str(
            proof.expected_active_entry_identity.run_id
        ).strip()
        if active_run != str(run_id).strip():
            raise RunCompletionAssessmentPrerequisiteError(
                "final Document completion subject belongs to another Run"
            )

    def build_success(
        self,
        *,
        run_id: str,
        section_completion_refs: Iterable[
            RunCompletionExactRefEnvelopeSchema
        ],
        document_artifact_ref: ArtifactStateRefSchema,
        document_post_write_entry_ref: PostWriteEntryRefSchema,
        post_write_control_decision: PostWriteControlDecisionSchema,
        fresh_document_currentness_proof: CurrentnessProofSchema,
        post_write_mutation_closure_refs: Iterable[
            RunCompletionExactRefEnvelopeSchema
        ] = (),
        active_repair_attempt_refs: Iterable[str] = (),
    ) -> RunCompletionAssessmentRecordSchema:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id is required")

        self._validate_final_post_write_subject(
            run_id=normalized_run_id,
            document_artifact_ref=document_artifact_ref,
            document_post_write_entry_ref=document_post_write_entry_ref,
            post_write_control_decision=post_write_control_decision,
            fresh_document_currentness_proof=(
                fresh_document_currentness_proof
            ),
            active_repair_attempt_refs=active_repair_attempt_refs,
        )
        sections = self._canonical_refs(
            section_completion_refs,
            required=False,
            label="section_completion_refs",
        )
        mutations = self._canonical_refs(
            post_write_mutation_closure_refs,
            required=False,
            label="post_write_mutation_closure_refs",
        )

        artifact_ref = ArtifactStateRefSchema.model_validate(
            document_artifact_ref
        )
        entry_ref = PostWriteEntryRefSchema.model_validate(
            document_post_write_entry_ref
        )
        control = PostWriteControlDecisionSchema.model_validate(
            post_write_control_decision
        )
        control_ref = control.as_ref()

        artifact_envelope = exact_ref_envelope(
            source_authority="POST_WRITE_ARTIFACT_STATE",
            source_kind="DOCUMENT_ARTIFACT",
            source_scope_ref=artifact_ref.artifact_id,
            exact_ref=artifact_ref,
        )
        entry_envelope = exact_ref_envelope(
            source_authority="POST_WRITE_ENTRY",
            source_kind="DOCUMENT_POST_WRITE_ENTRY",
            source_scope_ref=entry_ref.entry_id,
            exact_ref=entry_ref,
            exact_ref_schema_version=entry_ref.entry_schema_version,
        )
        control_envelope = exact_ref_envelope(
            source_authority="POST_WRITE_CONTROL",
            source_kind="POST_WRITE_CONTROL_DECISION",
            source_scope_ref=control_ref.control_decision_id,
            exact_ref=control_ref,
        )

        identity_payload = {
            "run_id": normalized_run_id,
            "document_artifact_ref": artifact_envelope.model_dump(mode="json"),
            "document_post_write_entry_ref": entry_envelope.model_dump(
                mode="json"
            ),
            "post_write_control_decision_ref": control_envelope.model_dump(
                mode="json"
            ),
            "post_write_mutation_closure_refs": [
                item.model_dump(mode="json") for item in mutations
            ],
            "assessment_contract_version": self.assessment_contract_version,
        }
        assessment_id = (
            "run-completion-assessment://sha256/"
            + canonical_sha256(identity_payload)
        )

        semantic_payload = {
            "run_id": normalized_run_id,
            "aggregate_result": RunCompletionAggregateResult.SUCCESS.value,
            "section_completion_refs": [
                item.model_dump(mode="json") for item in sections
            ],
            "document_artifact_ref": artifact_envelope.model_dump(mode="json"),
            "document_post_write_entry_ref": entry_envelope.model_dump(
                mode="json"
            ),
            "post_write_control_decision_ref": control_envelope.model_dump(
                mode="json"
            ),
            "post_write_control_decision_hash": (
                control.control_decision_hash
            ),
            "post_write_mutation_closure_refs": [
                item.model_dump(mode="json") for item in mutations
            ],
            "assessment_contract_version": self.assessment_contract_version,
        }
        semantic_fingerprint = canonical_sha256(semantic_payload)

        operation_scope_ref = assessment_id
        operation_ref = RunCompletionLogicalOperationRefSchema(
            logical_operation_id=(
                "run-completion-op://sha256/"
                + canonical_sha256(
                    {
                        "operation_kind": (
                            RUN_COMPLETION_ASSESSMENT_COMMIT
                        ),
                        "run_id": normalized_run_id,
                        "operation_scope_ref": operation_scope_ref,
                    }
                )
            ),
            run_id=normalized_run_id,
            operation_scope_ref=operation_scope_ref,
        )

        kwargs = {
            "assessment_id": assessment_id,
            "run_id": normalized_run_id,
            "aggregate_result": RunCompletionAggregateResult.SUCCESS,
            "section_completion_refs": sections,
            "document_artifact_ref": artifact_envelope,
            "document_post_write_entry_ref": entry_envelope,
            "post_write_control_decision_ref": control_envelope,
            "post_write_control_decision_hash": (
                control.control_decision_hash
            ),
            "post_write_mutation_closure_refs": mutations,
            "assessment_contract_version": self.assessment_contract_version,
            "semantic_fingerprint": semantic_fingerprint,
            "committed_operation_ref": operation_ref,
        }
        placeholder = RunCompletionAssessmentRecordSchema.model_construct(
            **kwargs,
            assessment_sha256="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("assessment_sha256", None)
        return RunCompletionAssessmentRecordSchema(
            **kwargs,
            assessment_sha256=canonical_sha256(payload),
        )

    def revalidate_for_terminalization(
        self,
        record: RunCompletionAssessmentRecordSchema,
        *,
        post_write_control_decision: PostWriteControlDecisionSchema,
        fresh_document_currentness_proof: CurrentnessProofSchema,
        active_repair_attempt_refs: Iterable[str] = (),
    ) -> RunCompletionAssessmentRecordSchema:
        candidate = RunCompletionAssessmentRecordSchema.model_validate(record)
        control = PostWriteControlDecisionSchema.model_validate(
            post_write_control_decision
        )

        try:
            artifact_ref = ArtifactStateRefSchema.model_validate(
                candidate.document_artifact_ref.exact_ref_payload
            )
            entry_ref = PostWriteEntryRefSchema.model_validate(
                candidate.document_post_write_entry_ref.exact_ref_payload
            )
            control_ref = PostWriteControlDecisionRefSchema.model_validate(
                candidate.post_write_control_decision_ref.exact_ref_payload
            )
        except Exception as exc:
            raise RunCompletionAssessmentPrerequisiteError(
                "persisted completion exact refs cannot be resolved"
            ) from exc

        if control.as_ref() != control_ref:
            raise RunCompletionAssessmentStaleError(
                "final PostWriteControlDecision changed before terminalization"
            )
        self._validate_final_post_write_subject(
            run_id=candidate.run_id,
            document_artifact_ref=artifact_ref,
            document_post_write_entry_ref=entry_ref,
            post_write_control_decision=control,
            fresh_document_currentness_proof=(
                fresh_document_currentness_proof
            ),
            active_repair_attempt_refs=active_repair_attempt_refs,
        )
        return candidate


class RunCompletionAssessmentTerminalizationResolver:
    """Resolve committed assessment and revalidate live prerequisites.

    This is an integration adapter for the lifecycle resolver hook. It is not
    a second Run lifecycle owner and never performs the transition itself.
    """

    def __init__(
        self,
        *,
        store: RunCompletionAssessmentStore,
        builder: RunCompletionAssessmentBuilder,
        snapshot_provider: Callable[
            [RunCompletionAssessmentRecordSchema],
            RunCompletionTerminalizationSnapshot,
        ],
    ) -> None:
        if not callable(snapshot_provider):
            raise TypeError("snapshot_provider must be callable")
        self.store = store
        self.builder = builder
        self.snapshot_provider = snapshot_provider

    def resolve_exact(
        self,
        ref: RunCompletionAssessmentRefSchema,
    ) -> RunCompletionAssessmentRecordSchema:
        record = self.store.resolve_exact(ref)
        snapshot = self.snapshot_provider(record)
        if not isinstance(snapshot, RunCompletionTerminalizationSnapshot):
            raise RunCompletionAssessmentPrerequisiteError(
                "terminalization snapshot provider returned invalid value"
            )
        return self.builder.revalidate_for_terminalization(
            record,
            post_write_control_decision=(
                snapshot.post_write_control_decision
            ),
            fresh_document_currentness_proof=(
                snapshot.fresh_document_currentness_proof
            ),
            active_repair_attempt_refs=snapshot.active_repair_attempt_refs,
        )


__all__ = [
    "RUN_COMPLETION_ASSESSMENT_COMMIT",
    "RunCompletionAssessmentBuilder",
    "RunCompletionAssessmentError",
    "RunCompletionAssessmentPrerequisiteError",
    "RunCompletionAssessmentStaleError",
    "RunCompletionAssessmentTerminalizationResolver",
    "RunCompletionTerminalizationSnapshot",
    "exact_ref_envelope",
]
