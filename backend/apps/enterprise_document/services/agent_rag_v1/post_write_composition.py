"""Wave-13 synchronized Target Post-write composition.

This module composes already-frozen Post-write owners.  It does not add a new
semantic truth store: every returned value is either an exact durable owner ref
or a passive projection over those refs.
"""

from __future__ import annotations

from .terminal_outcome_transport import TargetPostWriteInterrupted, TargetPostWriteCompositionError

import base64
from dataclasses import dataclass, replace
from functools import wraps
import json
from pathlib import Path
from typing import Callable
from core.diagnostics import current_diagnostics

from apps.enterprise_document.config.quality_delivery import (
    SemanticQualityDeliveryConfig,
    load_semantic_quality_delivery_config,
)
from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetStateSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAssessmentRefSchema,
    RunLifecycleRecordSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveComparisonMode,
    ActiveDocumentBindingProjectionSchema,
    ActiveSectionBindingProjectionSchema,
    CurrentnessProofSchema,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblySectionEntrySchema,
    AssemblySelectionState,
)
from apps.enterprise_document.schemas.post_write.entry import (
    PostWriteEntryRefSchema,
    PostWriteEvidenceBindingKind,
    PostWriteEvidenceBindingRef,
    entry_ref_from_identity,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    ArtifactValidatedOutputBindingSchema,
    DeliveryGateDecisionRefSchema,
    EvidenceSnapshotRefSchema,
    ExpectedItemExecutionAccountingSchema,
    ExpectedItemExecutionState,
    InitialWriterArtifactOriginSchema,
    StageDescriptorSchema,
    StagePlanSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationCompletionRefSchema,
    ValidationRunState,
    ValidationScope,
    ValidationTargetSelectorSchema,
    ValidatorExecutionRecordSchema,
    ValidatorTerminalExecutionState,
)
from apps.enterprise_document.schemas.post_write.gate import (
    RequiredDimensionConditionSchema,
)
from apps.enterprise_document.schemas.post_write.quality import (
    CoverageRelation,
    RequirementDomainCompletenessState,
)
from apps.enterprise_document.schemas.post_write.release import (
    PostWritePublishedReleaseSchema,
    PublicationReuseState,
)
from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementEntrySchema,
    RequirementRefSchema,
)
from apps.enterprise_document.schemas.post_write.control import (
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.section_execution import (
    SectionExecutionResultSchema,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    BudgetManager,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_assessment_store import (
    RunCompletionAssessmentStore,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_runtime import (
    RunCompletionAssessmentBuilder,
    RunCompletionAssessmentTerminalizationResolver,
    RunCompletionTerminalizationSnapshot,
    exact_ref_envelope,
    build_run_finalization_barrier,
)
from apps.enterprise_document.services.agent_rag_v1.research_runtime import (
    ResearchRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import TargetPostWriteCitationIntegrity
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_sources import normalize_requirement_entries
from apps.enterprise_document.services.post_write.artifact_state import (
    ArtifactCanonicalPayloadMaterializer,
    ArtifactOutputConformanceVerifier,
    ArtifactStateService,
    ArtifactValidationViewResolver,
    OutputConformanceRegistry,
)
from apps.enterprise_document.services.post_write.binding import (
    DocumentPostWriteEntryAssembler,
    PostWriteEntryIdentityBuilder,
    SectionPostWriteEntryAssembler,
)
from apps.enterprise_document.services.post_write.control import (
    ArtifactUsabilityIntegrityResolver,
    CanonicalRepairBudgetProjectionAdapter,
    PostWriteControlInputIntegrityGuard,
    PostWriteControlService,
)
from apps.enterprise_document.services.post_write.currentness import (
    CurrentnessResolver,
)
from apps.enterprise_document.services.post_write.document_lifecycle import (
    AssemblyInputManifestBuilder,
    DocumentEvidenceSnapshotProjector,
    DocumentLifecycleCoordinator,
    DocumentLifecycleResult,
    PostWriteDocumentAssembler,
    SectionAssemblyEligibilityResolver,
    active_selection_from_manifest_entry,
)
from apps.enterprise_document.services.post_write.execution import (
    ValidationCompletionBuilder,
    ValidationIntermediateRuntime,
    ValidationRunFactory,
    ValidationRunLifecycleService,
    ValidationSubjectKeyBuilder,
    ValidatorInputEnvelopeBuilder,
)
from apps.enterprise_document.services.post_write.gate import (
    DeliveryGateService,
    GateInputIntegrityGuard,
)
from apps.enterprise_document.services.post_write.persistence import (
    ArtifactStateStore,
    ControlDecisionStore,
    CorrectionPolicyStore,
    DocumentBindingStore,
    GateDecisionStore,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
    PostWriteReleaseCommitFence,
    PostWriteSqliteDatabase,
    QualityDeliveryContractStore,
    RequirementStore,
    ValidationStore,
)
from apps.enterprise_document.services.post_write.coverage import (
    CoverageProjector,
    CoverageRequirementTargetMaterializer,
    CoverageStructuralEvaluator,
)
from apps.enterprise_document.services.post_write.shared_requirement import (
    CoverageTargetProjector,
    SharedRequirementArtifactCandidateDomainBuilder,
)
from apps.enterprise_document.services.post_write.policy import (
    CorrectionPolicyRegistry,
    CorrectionPolicyResolver,
    QualityDeliveryContractRegistry,
)
from apps.enterprise_document.services.post_write.release import (
    PostWriteReleaseInputIntegrityGuard,
    PostWriteReleasePublicationService,
)
from apps.enterprise_document.services.post_write.requirements import (
    RequirementMaterializer,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore


from apps.enterprise_document.schemas.post_write.verification_generation import (
    VerificationGenerationScopeRefV1, VerificationGenerationManifestV1,
)
from .post_write_pipeline_identity import (
    pipeline_identity_material, semantic_pipeline_sha256, validation_run_identity,
)

BudgetManagerProvider = Callable[[str, str], BudgetManager]


class _DeterministicValidationIds:
    def __init__(self, seed: str) -> None:
        self.seed = str(seed).strip()

    def new_id(self, prefix: str) -> str:
        return (
            f"{prefix}://sha256/"
            + canonical_sha256({"prefix": prefix, "seed": self.seed})
        )


class _DeterministicValidationClock:
    def now_epoch(self) -> float:
        # Validation occurrence identity/replay must not depend on wall-clock.
        return 0.0


@dataclass(frozen=True)
class TargetSectionPostWriteResult:
    section_id: str
    artifact_state_ref: ArtifactStateRefSchema
    post_write_entry_ref: PostWriteEntryRefSchema
    currentness_proof: CurrentnessProofSchema
    validation_completion_ref: ValidationCompletionRefSchema
    delivery_gate_decision_ref: DeliveryGateDecisionRefSchema
    control_decision_ref: PostWriteControlDecisionRefSchema


@dataclass(frozen=True)
class TargetPostWriteMaterialization:
    requirement_ref: RequirementRefSchema
    section_results: tuple[TargetSectionPostWriteResult, ...]
    document: DocumentLifecycleResult
    # Ephemeral exact source handles; consumers re-resolve canonical owners.
    source_sections: tuple[SectionExecutionResultSchema, ...] = ()


@dataclass(frozen=True)
class TargetFinalDocumentResult:
    artifact_state_ref: ArtifactStateRefSchema
    post_write_entry_ref: PostWriteEntryRefSchema
    active_projection: ActiveDocumentBindingProjectionSchema
    currentness_proof: CurrentnessProofSchema
    validation_completion_ref: ValidationCompletionRefSchema
    delivery_gate_decision_ref: DeliveryGateDecisionRefSchema
    control_decision_ref: PostWriteControlDecisionRefSchema
    output_binding: ArtifactValidatedOutputBindingSchema


@dataclass(frozen=True)
class TargetPostWriteCompletion:
    materialization: TargetPostWriteMaterialization
    final_document: TargetFinalDocumentResult
    run_completion_assessment_ref: RunCompletionAssessmentRefSchema
    terminal_run: RunLifecycleRecordSchema
    published_release: PostWritePublishedReleaseSchema | None
    # Non-authoritative, transient transport of a post-terminal publication
    # failure. The committed quality/Control/Run and Artifact remain unchanged.
    publication_failure: dict[str, str] | None = None


@dataclass(frozen=True)
class RequiredValidationItemDeclaration:
    """Pure identity declaration; it neither runs nor validates a producer."""

    dimension: str
    expected_validation_item_ref: str
    producer: str
    channel: str = "dimension"
    required: bool = True


def declare_required_validation_items(*, required_dimensions, artifact_hash: str,
                                     has_claim_projection: bool):
    """The composition's exact ordered item declaration, without side effects.

    Attachment controls the shared projection item; dimensions retain their
    supplied order.  Callers must not execute producers to obtain this plan.
    """
    declarations = tuple(RequiredValidationItemDeclaration(
        dimension=dimension,
        expected_validation_item_ref=f"{dimension}:item:{artifact_hash}",
        producer=("faithfulness_fth03" if dimension == "faithfulness"
                  and has_claim_projection else dimension),
    ) for dimension in required_dimensions)
    if has_claim_projection:
        declarations += (RequiredValidationItemDeclaration(
            dimension="faithfulness", expected_validation_item_ref="faithfulness:shared:" + artifact_hash,
            producer="claim_quality_projection", channel="shared"),)
    return declarations + (RequiredValidationItemDeclaration(
        dimension="citation", expected_validation_item_ref="citation:integrity:" + artifact_hash,
        producer="citation_integrity", channel="integrity"),)


_DIAGNOSTIC_REF_FIELDS = (
    "schema_version", "run_id", "task_id", "document_id", "artifact_id", "artifact_version", "artifact_hash",
    "artifact_scope", "entry_id", "entry_sha256", "validation_run_id", "subject_hash",
    "completion_id", "completion_sha256", "delivery_gate_decision_id", "decision_hash",
    "control_decision_id", "control_decision_hash", "assessment_id", "assessment_hash",
    "lifecycle_revision", "workflow_status", "output_format", "output_content_hash",
    "binding_sha256", "publication_id", "publication_manifest_sha256",
    "expected_validation_item_ref", "required", "state", "action", "scope", "target_ref",
    "entry_ref", "artifact_ref", "artifact_state_ref", "control_decision_ref", "output_binding",
    "run_completion_assessment_ref", "terminal_run", "published_release", "fact_refs",
    "validation_scope", "requirement_ref", "assembly_input_manifest_ref", "assessment_ref",
    "quality_contract_ref", "completion_ref", "gate_ref", "gate_input_manifest",
    "status", "aggregate_result", "semantic_fingerprint", "assessment_sha256", "assessment_contract_version",
    "request", "manifest", "release_id", "request_sha256", "release_contract_version", "run_lifecycle_revision",
    "document_artifact_ref", "post_write_control_decision_ref", "release_mode", "enforcement_action_passthrough",
    "quality_state_passthrough", "overall_quality_state", "publication_target_id", "validated_output_binding_sha256",
    "release_payload_artifact", "release_payload_sha256", "release_input_proof_artifact", "release_input_proof_sha256",
    "manifest_payload_sha256", "manifest_artifact", "artifact_ref", "content_hash", "reuse_state", "verified_after_write",
)


def _diagnostic_ref(value, depth=0):
    """Explicit small identity projection; never dump an arbitrary owner DTO."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 4:
        return {"capture": "CAPTURE_INCOMPLETE", "reason": "REF_DEPTH_LIMIT"}
    if isinstance(value, (tuple, list)):
        items = [_diagnostic_ref(item, depth + 1) for item in value[:16]]
        return items if len(value) <= 16 else {"capture": "CAPTURE_INCOMPLETE", "declared_count": len(value), "items": items}
    return {key: _diagnostic_ref(value.get(key) if isinstance(value, dict)
        else getattr(value, key), depth + 1) for key in _DIAGNOSTIC_REF_FIELDS
        if (key in value if isinstance(value, dict) else hasattr(value, key))}


def _observe_postwrite(stage):
    def decorate(method):
        @wraps(method)
        def invoke(self, *args, **kwargs):
            observer = current_diagnostics()
            if not observer.enabled:
                return method(self, *args, **kwargs)
            identity = {"stage": stage, "unit_id": "E11" if stage in
                {"S-ASSESS", "S-PUBLISH"} else "E03", **_diagnostic_ref(kwargs)}
            observer.emit({**identity, "event_type": stage + "-BEGIN"})
            try:
                with observer.span(stage, "WORKFLOW", **identity):
                    result = method(self, *args, **kwargs)
            except BaseException as error:
                observer.emit({**identity, "event_type": stage + "-ERROR",
                    "exception_type": type(error).__name__})
                raise
            observer.emit({**identity, "event_type": stage + "-RESULT",
                "result": _diagnostic_ref(result)})
            return result
        return invoke
    return decorate


def _run_write_context(method):
    """Thread an explicit orchestration Run into store-local commit checks."""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.database.run_write_guard_context(kwargs["run_id"]):
            return method(self, *args, **kwargs)
    return invoke


class TargetPostWriteComposition:
    """One application-local composition over the frozen Target Post-write owners."""

    REQUIRED_DIMENSIONS = (
        "faithfulness",
        "citation",
        "relevancy",
        "coverage",
        "consistency",
    )
    _MARKDOWN_HEADER_PREFIX = "<!-- target-document-visible-v1:"
    _MARKDOWN_HEADER_SUFFIX = " -->"

    @classmethod
    def _render_markdown_visible_payload(cls, visible_payload: dict) -> bytes:
        payload = dict(visible_payload)
        if payload.get("payload_kind") != "document":
            raise TargetPostWriteCompositionError(
                "Target markdown renderer requires Document visible payload"
            )
        included = payload.get("included_sections")
        if not isinstance(included, list) or not included:
            raise TargetPostWriteCompositionError(
                "Target markdown renderer requires included Sections"
            )
        body_parts: list[str] = []
        for index, raw in enumerate(included, start=1):
            if not isinstance(raw, dict):
                raise TargetPostWriteCompositionError(
                    "Target markdown Section projection is malformed"
                )
            section = raw.get("visible_payload")
            if not isinstance(section, dict) or section.get("payload_kind") != "section":
                raise TargetPostWriteCompositionError(
                    "Target markdown requires exact Section visible payload"
                )
            title = str(section.get("section_title") or "").strip()
            content = str(section.get("content") or "")
            section_id = str(section.get("section_id") or "").strip()
            if not title or not section_id:
                raise TargetPostWriteCompositionError(
                    "Target markdown Section identity is incomplete"
                )
            body_parts.append(f"{index}、{title}\n{content}")
        metadata = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.b64encode(metadata).decode("ascii")
        rendered = (
            cls._MARKDOWN_HEADER_PREFIX
            + encoded
            + cls._MARKDOWN_HEADER_SUFFIX
            + "\n\n"
            + "\n\n".join(body_parts)
        )
        return rendered.encode("utf-8")

    @classmethod
    def _extract_markdown_visible_payload(cls, raw: bytes) -> dict:
        text = raw.decode("utf-8")
        header, separator, _body = text.partition("\n\n")
        if not separator or not header.startswith(cls._MARKDOWN_HEADER_PREFIX):
            raise ValueError("Target markdown visible metadata header is missing")
        if not header.endswith(cls._MARKDOWN_HEADER_SUFFIX):
            raise ValueError("Target markdown visible metadata header is malformed")
        encoded = header[
            len(cls._MARKDOWN_HEADER_PREFIX) : -len(cls._MARKDOWN_HEADER_SUFFIX)
        ]
        payload = json.loads(base64.b64decode(encoded.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("Target markdown visible metadata must decode to dict")
        if cls._render_markdown_visible_payload(payload) != raw:
            raise ValueError("Target markdown bytes differ from canonical rendering")
        return payload

    def __init__(
        self,
        *,
        database: PostWriteSqliteDatabase,
        artifact_service: ArtifactStateService,
        artifact_store: ArtifactStateStore,
        entry_store: PostWriteEntryStore,
        requirement_store: RequirementStore,
        validation_store: ValidationStore,
        contract_store: QualityDeliveryContractStore,
        gate_store: GateDecisionStore,
        correction_policy_store: CorrectionPolicyStore,
        control_store: ControlDecisionStore,
        binding_store: DocumentBindingStore,
        runs_dir: Path,
        research_runtime: ResearchRuntime,
        writer_execution_store: SQLitePreWriteExecutionStore,
        budget_manager_provider: BudgetManagerProvider,
        semantic_quality_config: SemanticQualityDeliveryConfig,
        lifecycle_service: RunAttemptLifecycleService | None = None,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.database = database
        self.artifact_service = artifact_service
        self.artifact_store = artifact_store
        self.entry_store = entry_store
        self.requirement_store = requirement_store
        self.validation_store = validation_store
        self.contract_store = contract_store
        self.gate_store = gate_store
        self.correction_policy_store = correction_policy_store
        self.control_store = control_store
        self.binding_store = binding_store
        self.research_runtime = research_runtime
        self.writer_execution_store = writer_execution_store
        self.budget_manager_provider = budget_manager_provider
        self.semantic_quality_config = SemanticQualityDeliveryConfig.model_validate(
            semantic_quality_config
        )
        self.lifecycle_service = lifecycle_service
        self.claim_quality_projection = None
        self.faithfulness_fth03 = None
        self.requirement_adherence = None
        self.coverage_composition = None
        self.consistency_composition = None

        self.output_store = FileArtifactStore(
            self.runs_dir / "target_validated_output"
        )
        self.release_store = FileArtifactStore(
            self.runs_dir / "target_release"
        )
        self.output_conformance_registry = OutputConformanceRegistry()
        self.output_conformance_registry.register(
            output_format="json",
            adapter_contract_version="json-visible-v1",
            adapter=lambda raw: json.loads(raw.decode("utf-8")),
        )
        self.output_conformance_registry.register(
            output_format="markdown",
            adapter_contract_version="target-markdown-visible-v1",
            adapter=self._extract_markdown_visible_payload,
        )
        self.output_conformance_verifier = ArtifactOutputConformanceVerifier(
            artifact_store=self.output_store,
            registry=self.output_conformance_registry,
        )
        self.validation_view_resolver = ArtifactValidationViewResolver(
            artifact_service
        )
        self.validation_intermediate_runtime = ValidationIntermediateRuntime(
            validation_store
        )
        self.assessment_builder = RunCompletionAssessmentBuilder()
        self.assessment_store = RunCompletionAssessmentStore(
            self.runs_dir / "run_completion_assessments.jsonl"
        )

        self.identity_builder = PostWriteEntryIdentityBuilder()
        self.requirement_materializer = RequirementMaterializer(requirement_store)
        self.section_entry_assembler = SectionPostWriteEntryAssembler(
            store=entry_store,
            identity_builder=self.identity_builder,
        )
        self.document_entry_assembler = DocumentPostWriteEntryAssembler(
            store=entry_store,
            identity_builder=self.identity_builder,
        )
        self.currentness_resolver = CurrentnessResolver(
            entry_store=entry_store,
            identity_builder=self.identity_builder,
            document_binding_store=binding_store,
        )
        self.usability_resolver = ArtifactUsabilityIntegrityResolver(artifact_store)
        self.quality_registry = QualityDeliveryContractRegistry(contract_store)
        self.correction_registry = CorrectionPolicyRegistry(correction_policy_store)
        self.validation_lifecycle = ValidationRunLifecycleService(
            store=validation_store,
            clock=_DeterministicValidationClock(),
        )
        self.gate_service = DeliveryGateService(
            input_guard=GateInputIntegrityGuard(
                validation_store=validation_store,
                entry_store=entry_store,
                contract_store=contract_store,
            ),
            decision_store=gate_store,
            clock=lambda: 0.0,
        )
        self.control_service = PostWriteControlService(
            integrity_guard=PostWriteControlInputIntegrityGuard(
                gate_store=gate_store,
                correction_policy_resolver=CorrectionPolicyResolver(
                    correction_policy_store
                ),
            ),
            control_store=control_store,
        )
        self.section_eligibility = SectionAssemblyEligibilityResolver(
            control_store=control_store,
            entry_store=entry_store,
        )
        self.manifest_builder = AssemblyInputManifestBuilder(store=binding_store)
        self.citation_integrity = TargetPostWriteCitationIntegrity(self)
        self.document_lifecycle = DocumentLifecycleCoordinator(
            manifest_builder=self.manifest_builder,
            evidence_projector=DocumentEvidenceSnapshotProjector(
                binding_store=binding_store,
                entry_store=entry_store,
                evidence_bundle_resolver=self._resolve_section_evidence_snapshot,
            ),
            document_assembler=PostWriteDocumentAssembler(
                artifact_service=artifact_service,
                binding_store=binding_store,
                document_payload_projector=self.citation_integrity.assemble_projection,
                committed_document_resolver=self.citation_integrity.committed_assembly,
            ),
            entry_assembler=self.document_entry_assembler,
        )
        self.terminalization_resolver = (
            RunCompletionAssessmentTerminalizationResolver(
                store=self.assessment_store,
                builder=self.assessment_builder,
                snapshot_provider=self._terminalization_snapshot,
            )
        )
        if self.lifecycle_service is not None:
            self.lifecycle_service.bind_run_completion_assessment_resolver(
                self.assessment_store.resolve_exact
            )
        self.release_service = (
            PostWriteReleasePublicationService(
                publication_store=self.release_store,
                input_guard=PostWriteReleaseInputIntegrityGuard(
                    artifact_state_store=self.artifact_store,
                    control_store=self.control_store,
                    currentness_resolver=self.currentness_resolver,
                    artifact_usability_resolver=self.usability_resolver,
                    output_conformance_verifier=self.output_conformance_verifier,
                    run_completion_assessment_resolver=(
                        self.assessment_store.resolve_exact
                    ),
                    run_lifecycle_revision_resolver=(
                        self._resolve_run_lifecycle_revision
                    ),
                ),
                release_commit_fence=PostWriteReleaseCommitFence(self.database),
            )
        )

    @classmethod
    def create(
        cls,
        *,
        runs_dir: str | Path,
        research_runtime: ResearchRuntime,
        writer_execution_store: SQLitePreWriteExecutionStore,
        budget_manager_provider: BudgetManagerProvider,
        lifecycle_service: RunAttemptLifecycleService | None = None,
        require_run_commit_guard: bool = False,
    ) -> "TargetPostWriteComposition":
        root = Path(runs_dir)
        database = PostWriteSqliteDatabase(
            root / "post_write.sqlite3",
            run_commit_guard=(lifecycle_service.assert_run_committable
                              if require_run_commit_guard and lifecycle_service is not None else None),
            require_run_commit_guard=require_run_commit_guard,
        )
        artifact_store = ArtifactStateStore(database)
        semantic_quality_config = load_semantic_quality_delivery_config(
            Path(__file__).resolve().parents[2] / "config" / "quality_delivery_v1.yaml"
        )
        return cls(
            runs_dir=root,
            database=database,
            artifact_service=ArtifactStateService(store=artifact_store),
            artifact_store=artifact_store,
            entry_store=PostWriteEntryStore(database),
            requirement_store=RequirementStore(database),
            validation_store=ValidationStore(database),
            contract_store=QualityDeliveryContractStore(database),
            gate_store=GateDecisionStore(database),
            correction_policy_store=CorrectionPolicyStore(database),
            control_store=ControlDecisionStore(database),
            binding_store=DocumentBindingStore(database),
            research_runtime=research_runtime,
            writer_execution_store=writer_execution_store,
            budget_manager_provider=budget_manager_provider,
            semantic_quality_config=semantic_quality_config,
            lifecycle_service=lifecycle_service,
        )

    def _resolve_section_evidence_snapshot(self, ref: EvidenceSnapshotRefSchema):
        exact = EvidenceSnapshotRefSchema.model_validate(ref)
        subject = (
            exact.writer_invocation_ref.logical_execution_key.writer_subject_ref
        )
        binding = self.research_runtime.resolve_current_evidence_package_binding(
            run_id=subject.run_id,
            section_id=subject.section_id,
        )
        if binding is None:
            raise TargetPostWriteCompositionError(
                "Section EvidenceSnapshot has no current canonical EvidencePackage"
            )
        content = self.research_runtime.validate_evidence_package_binding_current(
            binding
        )
        bundle = self.research_runtime.resolve_evidence_bundle(
            content.evidence_bundle_binding
        )
        if evidence_bundle_sha256(bundle) != exact.evidence_bundle_sha256:
            raise TargetPostWriteCompositionError(
                "Section EvidenceSnapshot hash differs from current canonical Evidence"
            )
        return bundle

    @_run_write_context
    def _materialize_requirement(
        self,
        *,
        project_input: ProjectInputSchema,
        run_id: str,
    ) -> RequirementRefSchema:
        requirements = tuple(
            sorted(
                (
                    *normalize_requirement_entries(project_input),
                    self.semantic_quality_config.requirement_entry(),
                ),
                key=lambda item: item.requirement_id,
            )
        )
        return self.requirement_materializer.materialize(
            semantic_contract_version="post-write-requirements-v2",
            source_business_ref=f"project-input://{project_input.task_id}",
            source_task_ref=f"task://{project_input.task_id}/run/{run_id}",
            requirements=requirements,
        )

    def attach_requirement_adherence(self, *, model_gateway) -> None:
        if self.requirement_adherence is not None:
            raise TargetPostWriteCompositionError("Post-write Requirement Adherence is already attached")
        from .post_write_requirement_adherence import TargetPostWriteRequirementAdherence
        self.requirement_adherence = TargetPostWriteRequirementAdherence(
            post_write=self,
            model_gateway=model_gateway,
        )

    def attach_coverage(self, *, model_gateway) -> None:
        if self.coverage_composition is not None:
            raise TargetPostWriteCompositionError("Post-write Coverage is already attached")
        from .post_write_coverage_composition import TargetPostWriteCoverageComposition
        self.coverage_composition = TargetPostWriteCoverageComposition(
            post_write=self,
            model_gateway=model_gateway,
        )

    def attach_consistency(self, *, model_gateway) -> None:
        if self.consistency_composition is not None:
            raise TargetPostWriteCompositionError("Post-write Consistency is already attached")
        from .post_write_consistency_composition import TargetPostWriteConsistency
        self.consistency_composition = TargetPostWriteConsistency(
            post_write=self,
            model_gateway=model_gateway,
        )

    def attach_claim_quality_projection(self, projection) -> None:
        if self.claim_quality_projection is not None and self.claim_quality_projection is not projection:
            raise TargetPostWriteCompositionError("Post-write Claim projection is already attached")
        if projection.paragraph_runtime.artifact_runtime is not self.research_runtime.artifact_runtime:
            raise TargetPostWriteCompositionError("Post-write Claim projection uses another Artifact owner")
        self.claim_quality_projection = projection
        from .post_write_citation_grounding import TargetPostWriteCitationGrounding
        from .post_write_faithfulness_composition import TargetPostWriteFaithfulnessFTH03
        self.citation_grounding = TargetPostWriteCitationGrounding(post_write=self,
            claim_projection=projection, support_composition=projection.support,
            model_gateway=projection.support.model_gateway)
        self.faithfulness_fth03 = TargetPostWriteFaithfulnessFTH03(
            post_write=self,
            claim_projection=projection,
            support_composition=projection.support,
        )

    def _quality_contract_ref(self, requirement_ref: RequirementRefSchema):
        procedure_input = self.semantic_quality_config.procedure_input(
            requirement_ref=requirement_ref
        )
        return self.quality_registry.register(
            contract_id="quality-delivery://multi-agent-v3/target-v1",
            contract_version="v1",
            required_dimensions=tuple(
                RequiredDimensionConditionSchema(dimension_id=dimension)
                for dimension in self.REQUIRED_DIMENSIONS
            ),
            contract_metadata={
                "authority": "target_post_write",
                "missing_semantic_execution": "UNRESOLVED",
                "semantic_quality_config_ref": self.semantic_quality_config.source_ref,
            },
            consistency_procedure_input=procedure_input,
        )

    def _correction_policy_ref(self):
        # W13 does not invent semantic correction eligibility.  Exact correction
        # rules may be registered later by the frozen policy owner; absent an
        # exact rule, Control must remain non-correcting.
        return self.correction_registry.register(
            policy_id="correction-policy://multi-agent-v3/w13-no-inference",
            policy_version="v1",
            rules=(),
            automatic_correction_enabled=False,
            technical_retry_limit=1,
        )

    @staticmethod
    def _coverage_stage_plan() -> StagePlanSchema:
        stage = StageDescriptorSchema(
            stage_ref="post-write-coverage-structural-stage:v1",
            stage_version="v1",
            sequence_index=0,
            stage_input_schema_ref="schema://post-write/coverage-structural-input/v1",
            stage_output_schema_ref="schema://post-write/coverage-fact/v1",
            execution_policy_ref="policy://post-write/coverage-structural-deterministic/v1",
            execution_policy_version="v1",
            timeout_seconds=10.0,
            max_attempts=1,
            retryable_failure_categories=(),
        )
        base = {
            "schema_version": "validator_stage_plan_v1",
            "stage_plan_ref": "stage-plan:post-write-coverage-structural:v1",
            "validator_id": CoverageProjector.VALIDATOR_ID,
            "validator_version": CoverageProjector.VALIDATOR_VERSION,
            "stage_plan_version": "v1",
            "stages": [stage.model_dump(mode="json")],
        }
        return StagePlanSchema(
            stage_plan_ref=base["stage_plan_ref"],
            validator_id=base["validator_id"],
            validator_version=base["validator_version"],
            stage_plan_version=base["stage_plan_version"],
            stages=(stage,),
            stage_plan_hash=canonical_sha256(base),
        )

    @staticmethod
    def _required_section_unit_ref(
        requirement_ref: RequirementRefSchema,
        section_title: str,
    ) -> str:
        title = str(section_title or "").strip()
        if not title:
            raise TargetPostWriteCompositionError(
                "required Section title cannot be blank"
            )
        return (
            "required-section://sha256/"
            + canonical_sha256(
                {
                    "requirement_ref": requirement_ref.model_dump(mode="json"),
                    "section_title": title,
                }
            )
        )

    def _document_structural_coverage_item(
        self,
        *,
        validation_run_id: str,
        subject_hash: str,
        artifact_ref: ArtifactStateRefSchema,
        document_id: str,
        requirement_ref: RequirementRefSchema,
        assembly_input_manifest_ref,
        committed_only: bool = False,
    ) -> ValidationCompletionItemSchema:
        snapshot = self.requirement_store.resolve_exact(requirement_ref)
        matching_requirements = tuple(
            item
            for item in snapshot.requirements
            if item.requirement_id == "task-request"
        )
        if len(matching_requirements) != 1:
            raise TargetPostWriteCompositionError(
                "Document structural Coverage requires exact task-request Requirement"
            )
        requirement = matching_requirements[0]
        required_titles = tuple(
            str(item).strip()
            for item in requirement.constraints.get("required_sections", ())
            if str(item).strip()
        )
        if not required_titles or len(required_titles) != len(set(required_titles)):
            raise TargetPostWriteCompositionError(
                "canonical required_sections are missing or duplicated"
            )

        manifest = self.binding_store.resolve_assembly_manifest(
            assembly_input_manifest_ref
        )
        actual_titles: set[str] = set()
        for entry in manifest.ordered_section_entries:
            candidate = entry.section_assembly_candidate
            if candidate is None:
                continue
            section_record = self.artifact_store.resolve_exact(
                candidate.section_artifact_ref
            )
            payload = ArtifactCanonicalPayloadMaterializer.business_payload(
                section_record.canonical_payload_ref
            )
            title = str(payload.get("section_title") or "").strip()
            if not title:
                raise TargetPostWriteCompositionError(
                    "assembled Section Artifact lacks canonical section_title"
                )
            actual_titles.add(title)

        unit_ref_by_title = {
            title: self._required_section_unit_ref(requirement_ref, title)
            for title in required_titles
        }
        complete_units = tuple(sorted(unit_ref_by_title.values()))
        matched_units = tuple(
            sorted(
                unit_ref_by_title[title]
                for title in required_titles
                if title in actual_titles
            )
        )
        provenance_refs = (
            manifest.manifest_id,
            requirement_ref.requirement_snapshot_id,
        )
        domain_builder = SharedRequirementArtifactCandidateDomainBuilder(
            self.validation_intermediate_runtime
        )
        domain = domain_builder.build(
            artifact_state_ref=artifact_ref,
            validation_scope=ValidationScope.DOCUMENT.value,
            subject_selector_ref=document_id,
            complete_applicable_domain_refs=complete_units,
            prioritized_candidate_refs=complete_units,
            consumer_requirements=(requirement.requirement_id,),
            completeness_state=RequirementDomainCompletenessState.COMPLETE,
            provenance_refs=provenance_refs,
        )
        coverage_materializer = CoverageRequirementTargetMaterializer(
            self.validation_intermediate_runtime
        )
        target = coverage_materializer.materialize(
            requirement_ref=requirement_ref,
            requirement=requirement,
            artifact_state_ref=artifact_ref,
            validation_scope=ValidationScope.DOCUMENT.value,
            target_specs=(
                {
                    "target_kind": "STRUCTURAL_REQUIRED_SECTION_SET",
                    "canonical_subject_selector": "document.required_sections",
                    "normalized_presence_constraint": (
                        "all_canonical_required_sections_present"
                    ),
                    "required_count": len(required_titles),
                    "source_requirement_group_ref": (
                        requirement_ref.requirement_snapshot_id
                    ),
                    "provenance_refs": provenance_refs,
                },
            ),
            provenance_refs=provenance_refs,
        )[0]
        coverage_target_projector = CoverageTargetProjector(
            self.validation_intermediate_runtime
        )
        match_set = coverage_target_projector.project(
            coverage_target=target,
            candidate_domain=domain,
            matched_unit_refs=matched_units,
            match_state=(
                "MATCH_OBSERVED" if matched_units else "NO_MATCH_OBSERVED"
            ),
            complete_domain=True,
            provenance_refs=provenance_refs,
        )
        relation = CoverageStructuralEvaluator.evaluate(
            target=target,
            match_target_set=match_set,
        )
        fact = CoverageProjector.project(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            target=target,
            match_target_set=match_set,
            relation=relation,
            observed_count=len(matched_units),
            observation_codes=(
                "DOCUMENT_REQUIRED_SECTION_STRUCTURAL_ACCOUNTING",
            ),
            related_refs=provenance_refs,
        )

        expected_item_ref = f"coverage:item:{artifact_ref.artifact_hash}"
        stage_plan = self._coverage_stage_plan()
        input_envelope = ValidatorInputEnvelopeBuilder(
            validation_view_resolver=self.validation_view_resolver
        ).build(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_state_ref=artifact_ref,
            stage_plan=stage_plan,
            applicable_expected_validation_item_refs=(expected_item_ref,),
            exact_authority_refs=provenance_refs,
            execution_contract_version="validator-execution-v1",
        )
        execution_ref = (
            "validator-execution:coverage:"
            + canonical_sha256(
                {
                    "validation_run_id": validation_run_id,
                    "input_hash": input_envelope.input_hash,
                    "expected_item_ref": expected_item_ref,
                }
            )
        )
        existing = self.validation_store.find_validator_execution_for_item(
            validation_run_id=validation_run_id,
            validator_id=stage_plan.validator_id,
            expected_item_ref=expected_item_ref,
        )
        if existing is not None:
            if (
                existing.validation_run_id != validation_run_id
                or existing.subject_hash != subject_hash
                or existing.validator_id != stage_plan.validator_id
                or existing.validator_version != stage_plan.validator_version
                or existing.stage_plan_ref != stage_plan.stage_plan_ref
                or existing.stage_plan_version != stage_plan.stage_plan_version
                or existing.input_hash != input_envelope.input_hash
                or existing.terminal_execution_state
                is not ValidatorTerminalExecutionState.COMPLETED
                or len(existing.produced_fact_refs) != 1
            ):
                raise TargetPostWriteCompositionError(
                    "replayed Coverage ValidatorExecution differs from exact input"
                )
            fact_ref = existing.produced_fact_refs[0]
            replay_fact = self.validation_store.resolve_fact(fact_ref)
            if replay_fact != fact:
                raise TargetPostWriteCompositionError(
                    "replayed Coverage Fact differs from deterministic projection"
                )
        else:
            if committed_only:
                raise TargetPostWriteCompositionError("terminal replay is missing its exact Coverage execution")
            domain_ref = domain_builder.commit(
                validation_run_id=validation_run_id,
                domain=domain,
            )
            target_ref = coverage_materializer.commit(
                validation_run_id=validation_run_id,
                target=target,
            )
            match_ref = coverage_target_projector.commit(
                validation_run_id=validation_run_id,
                target_set=match_set,
            )
            fact_ref = self.validation_store.put_fact_if_absent_or_same(fact)
            with self.database.read_transaction() as connection:
                sequence = self.validation_store._next_event_sequence(
                    connection,
                    validation_run_id,
                )
            event_id = (
                "validator-terminal:coverage:"
                + canonical_sha256({"validator_execution_ref": execution_ref})
            )
            terminal_event = self.validation_store._event(
                validation_run_id=validation_run_id,
                event_id=event_id,
                sequence=sequence,
                event_kind="VALIDATOR_COMPLETED",
                payload={"validator_execution_ref": execution_ref},
                occurred_at_epoch=0.0,
            )
            accounting = ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref=expected_item_ref,
                execution_state=ExpectedItemExecutionState.COMPLETED,
                fact_refs=(fact_ref,),
                provenance_refs=tuple(sorted(provenance_refs)),
            )
            record_base = {
                "schema_version": "validator_execution_record_v1",
                "validator_execution_ref": execution_ref,
                "validation_run_id": validation_run_id,
                "subject_hash": subject_hash,
                "validator_id": stage_plan.validator_id,
                "validator_version": stage_plan.validator_version,
                "stage_plan_ref": stage_plan.stage_plan_ref,
                "stage_plan_version": stage_plan.stage_plan_version,
                "input_hash": input_envelope.input_hash,
                "terminal_event_id": terminal_event.event_id,
                "terminal_execution_state": (
                    ValidatorTerminalExecutionState.COMPLETED.value
                ),
                "expected_item_accounting": [
                    accounting.model_dump(mode="json")
                ],
                "produced_fact_refs": [fact_ref.model_dump(mode="json")],
                "intermediate_refs": [
                    item.model_dump(mode="json")
                    for item in (domain_ref, target_ref, match_ref)
                ],
            }
            record = ValidatorExecutionRecordSchema(
                **record_base,
                record_sha256=canonical_sha256(record_base),
            )
            self.validation_store.finalize_validator_execution(
                terminal_event,
                record,
                stage_plan=stage_plan,
                input_envelope=input_envelope,
            )

        state = {
            CoverageRelation.PRESENT: ValidationCompletionItemState.PASS,
            CoverageRelation.MISSING: ValidationCompletionItemState.FAIL,
            CoverageRelation.PARTIAL: ValidationCompletionItemState.FAIL,
            CoverageRelation.INDETERMINATE: (
                ValidationCompletionItemState.INDETERMINATE
            ),
        }[relation]
        return ValidationCompletionItemSchema(
            expected_validation_item_ref=expected_item_ref,
            required=True,
            state=state,
            fact_refs=(fact_ref,),
        )

    def _verification_generation(self, *, run_id, artifact_ref, scope, target_ref,
                                 source_sections, assembly_input_manifest_ref, strict_currentness=False):
        if self.claim_quality_projection is None:
            if any(section.canonical_claim_verification_refs or section.canonical_claim_coverage_proof_refs
                   for section in source_sections):
                raise TargetPostWriteCompositionError("Claim sources require canonical Claim projection; empty generation forbidden")
            return VerificationGenerationManifestV1.build(VerificationGenerationScopeRefV1(
                run_id=run_id, validation_scope=scope, target_ref=target_ref))
        snapshot = self.claim_quality_projection.resolve_sources(post_write=self,
            artifact_ref=artifact_ref, scope=scope, source_sections=source_sections,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
            generation_identity_only=True)
        return self.claim_quality_projection.build_verification_generation(snapshot=snapshot,
            run_id=run_id, scope=scope, target_ref=target_ref, strict_currentness=strict_currentness)

    def _assert_pre_gate_verification_current(self, *, generation, run_id,
            validation_run_id, subject_hash, artifact_ref, scope, target_ref,
            source_sections, assembly_input_manifest_ref):
        """Read-only RUNNING freshness guard; I8 owns terminal atomicity."""
        if self.claim_quality_projection is not None:
            committed = self.claim_quality_projection.resolve_committed_source_snapshot(
                post_write=self, validation_run_id=validation_run_id,
                subject_hash=subject_hash, artifact_ref=artifact_ref, scope=scope)
            fresh = self.claim_quality_projection.resolve_sources(post_write=self,
                artifact_ref=artifact_ref, scope=scope, source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
                citation_validation_run_id=validation_run_id, citation_subject_hash=subject_hash)
            if committed != fresh:
                raise TargetPostWriteCompositionError("pre-Gate Claim snapshot is stale")
            current = self.claim_quality_projection.build_verification_generation(snapshot=fresh,
                run_id=run_id, scope=scope, target_ref=target_ref)
        else:
            current = self._verification_generation(run_id=run_id, artifact_ref=artifact_ref,
                scope=scope, target_ref=target_ref, source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref)
        if current != generation:
            raise TargetPostWriteCompositionError("pre-Gate Verification generation is stale")

    @_observe_postwrite("S-VALIDATION")
    def _complete_validation(
        self,
        *,
        run_id: str,
        entry_ref: PostWriteEntryRefSchema,
        artifact_ref: ArtifactStateRefSchema,
        scope: ValidationScope,
        target_ref: str,
        quality_contract_ref,
        currentness_proof: CurrentnessProofSchema,
        requirement_ref: RequirementRefSchema | None = None,
        assembly_input_manifest_ref=None,
        source_sections: tuple[SectionExecutionResultSchema, ...] = (),
        evidence_snapshot_ref=None,
        committed_gate_ref: DeliveryGateDecisionRefSchema | None = None,
        budget_continuation=None,
    ) -> tuple[ValidationCompletionRefSchema, DeliveryGateDecisionRefSchema]:
        if scope is ValidationScope.DOCUMENT and assembly_input_manifest_ref is not None:
            manifest = self.binding_store.resolve_assembly_manifest(assembly_input_manifest_ref)
            included = {entry.section_key for entry in manifest.ordered_section_entries
                if entry.section_assembly_candidate is not None}
            source_sections = tuple(source for source in source_sections if source.section.section_id in included)
        selector = ValidationTargetSelectorSchema(
            selector_kind=scope.value.lower(),
            target_ref=target_ref,
            selector_contract_version="target-selector-v1",
        )
        subject = ValidationSubjectKeyBuilder.build(
            post_write_entry_ref=entry_ref,
            validation_scope=scope,
            target_selector=selector,
            subject_contract_version="target-subject-v1",
        )
        generation = self._verification_generation(run_id=run_id, artifact_ref=artifact_ref,
            scope=scope, target_ref=target_ref, source_sections=source_sections,
            assembly_input_manifest_ref=assembly_input_manifest_ref)
        pipeline_hash = semantic_pipeline_sha256(pipeline_identity_material(self, scope))
        validation_run_id = validation_run_identity(subject_hash=subject.subject_hash,
            quality_contract_ref=quality_contract_ref, semantic_pipeline_sha256=pipeline_hash,
            verification_generation_sha256=generation.verification_generation_sha256)
        try:
            run = self.validation_store.resolve_run_exact(validation_run_id)
        except PostWritePersistenceIntegrityError:
            if committed_gate_ref is not None:
                raise TargetPostWriteCompositionError("terminal replay is missing its exact ValidationRun")
            if self.claim_quality_projection is not None and self.claim_quality_projection.is_terminal_replay(source_sections):
                raise TargetPostWriteCompositionError("terminal Run requires its existing ValidationRun; new execution is forbidden")
            run = ValidationRunFactory(
                id_generator=_DeterministicValidationIds(subject.subject_hash),
                clock=_DeterministicValidationClock(),
            ).create(
                subject_key=subject,
                creation_command_id=(
                    "validation-create://sha256/"
                    + canonical_sha256({"validation_run_id": validation_run_id})
                ),
            )
            # The deterministic factory id must be the exact composition id.
            run = run.model_copy(
                update={"validation_run_id": validation_run_id},
                deep=True,
            )
            run = self.validation_lifecycle.create(run)

        if committed_gate_ref is not None and run.execution_state is not ValidationRunState.COMPLETED:
            raise TargetPostWriteCompositionError("terminal replay requires completed validation")
        if run.subject_key != subject:
            raise TargetPostWriteCompositionError(
                "ValidationRun deterministic identity resolved another subject"
            )
        if (self.claim_quality_projection is not None
                and self.claim_quality_projection.is_terminal_replay(source_sections)
                and run.execution_state is not ValidationRunState.COMPLETED):
            raise TargetPostWriteCompositionError("terminal Run cannot finish an incomplete ValidationRun")
        if run.execution_state is ValidationRunState.CREATED:
            run = self.validation_lifecycle.start(
                run.validation_run_id,
                expected_state_version=run.state_version,
                transition_command_id=(
                    "validation-start://sha256/"
                    + canonical_sha256({"validation_run_id": run.validation_run_id})
                ),
            )

        required_declarations = declare_required_validation_items(
            required_dimensions=self.REQUIRED_DIMENSIONS,
            artifact_hash=artifact_ref.artifact_hash,
            has_claim_projection=self.claim_quality_projection is not None,
        )
        observer = current_diagnostics()
        if observer.enabled:
            observer.emit({"event_type": "S-PLAN", "unit_id": "E03", "run_id": run_id,
                "validation_run_id": run.validation_run_id, "subject_hash": subject.subject_hash,
                "scope": scope.value, "artifact_ref": _diagnostic_ref(artifact_ref),
                "representation_kind": "DERIVED_REQUIRED_DECLARATION",
                "items": [{"expected_validation_item_ref": item.expected_validation_item_ref,
                    "required": item.required, "dimension": item.dimension,
                    "producer": item.producer, "channel": item.channel} for item in required_declarations]})
        items_by_dimension = {
            declaration.dimension: ValidationCompletionItemSchema(
                expected_validation_item_ref=declaration.expected_validation_item_ref,
                required=True,
                state=ValidationCompletionItemState.NOT_RUN,
            )
            for declaration in required_declarations
            if declaration.channel == "dimension"
        }
        if requirement_ref is None:
            raise TargetPostWriteCompositionError("Validation requires exact canonical RequirementRef")
        if self.requirement_adherence is not None:
            items_by_dimension["relevancy"] = self._observe_validation_producer("adherence", self.requirement_adherence.complete_item,
                validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                requirement_ref=requirement_ref,
                source_sections=source_sections,
            )
        if scope is ValidationScope.DOCUMENT and assembly_input_manifest_ref is None:
            raise TargetPostWriteCompositionError(
                "Document Validation requires exact Requirement/assembly refs"
            )
        # Coverage citation-presence consumes the already-committed Citation
        # occurrence accounting.  Integrity remains the sole validity owner.
        citation_item = self._observe_validation_producer("integrity", self.citation_integrity.complete_item,
            validation_run_id=run.validation_run_id, subject_hash=subject.subject_hash,
            artifact_ref=artifact_ref, scope=scope, source_sections=source_sections,
            evidence_snapshot_ref=evidence_snapshot_ref,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
        )
        if self.coverage_composition is not None:
            items_by_dimension["coverage"] = self._observe_validation_producer("coverage", self.coverage_composition.complete_item,
                validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                requirement_ref=requirement_ref,
                source_sections=source_sections,
                evidence_snapshot_ref=evidence_snapshot_ref,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
            )
        elif scope is ValidationScope.DOCUMENT:
            # Compatibility path for narrow owner tests that intentionally do not
            # attach the 347 production Coverage composition.
            items_by_dimension["coverage"] = self._observe_validation_producer("structural_coverage", self._document_structural_coverage_item,
                validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref,
                document_id=target_ref,
                requirement_ref=requirement_ref,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
                committed_only=committed_gate_ref is not None,
            )
        projected_items = ()
        if self.claim_quality_projection is not None:
            projected_items = (self._observe_validation_producer("shared_projection", self.claim_quality_projection.complete_item,
                post_write=self, validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash, artifact_ref=artifact_ref, scope=scope,
                source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
            ),)
            if self.faithfulness_fth03 is None:
                raise TargetPostWriteCompositionError(
                    "Post-write Claim projection lacks required FTH-03 composition"
                )
            items_by_dimension["faithfulness"] = self._observe_validation_producer("FTH03", self.faithfulness_fth03.complete_item,
                validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                requirement_ref=requirement_ref,
                source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
            )
        if self.consistency_composition is not None:
            items_by_dimension["consistency"] = self._observe_validation_producer("consistency", self.consistency_composition.complete_item,
                validation_run_id=run.validation_run_id,
                subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                quality_contract_ref=quality_contract_ref,
                source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
            )
        if self.claim_quality_projection is not None:
            items_by_dimension["citation"] = self._observe_validation_producer("grounding", self.citation_grounding.complete_item,
                validation_run_id=run.validation_run_id, subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref, scope=scope, source_sections=source_sections,
                evidence_snapshot_ref=evidence_snapshot_ref,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
                **({"budget_continuation":budget_continuation} if budget_continuation is not None else {}))
        # FTH-01/02/04 remain in the shared Result/body item; FTH-03 owns
        # the original required faithfulness:item through exact certainty receipts.
        # Grounding independently accounts the citation item; Integrity stays separate.
        items = tuple(
            items_by_dimension[declaration.dimension]
            for declaration in required_declarations
            if declaration.channel == "dimension"
        ) + projected_items + (citation_item,)
        completion = ValidationCompletionBuilder.build(
            validation_run_id=run.validation_run_id,
            subject_hash=subject.subject_hash,
            items=items,
        )
        if committed_gate_ref is not None:
            self._assert_pre_gate_verification_current(generation=generation, run_id=run_id,
                validation_run_id=run.validation_run_id, subject_hash=subject.subject_hash,
                artifact_ref=artifact_ref, scope=scope, target_ref=target_ref,
                source_sections=source_sections, assembly_input_manifest_ref=assembly_input_manifest_ref)
            gate = self.gate_store.resolve_exact(committed_gate_ref)
            manifest = gate.gate_input_manifest
            completion_ref = manifest.validation_completion_ref
            if (self.validation_store.resolve_completion(completion_ref) != completion
                    or gate.validation_run_id != run.validation_run_id or gate.subject_key != subject
                    or manifest.artifact_state_ref != artifact_ref or manifest.post_write_entry_ref != entry_ref
                    or manifest.quality_delivery_contract_ref != quality_contract_ref
                    or gate.currentness_proof != currentness_proof):
                raise TargetPostWriteCompositionError("terminal replay Gate/completion/input binding differs")
            resolved = self.gate_service.input_guard.resolve(validation_run_id=run.validation_run_id,
                validation_completion_ref=completion_ref,quality_delivery_contract_ref=quality_contract_ref,
                currentness_proof=currentness_proof)
            if resolved.manifest != manifest or resolved.quality_policy_projection != gate.quality_policy_projection:
                raise TargetPostWriteCompositionError("terminal Gate exact input manifest changed")
            return completion_ref, committed_gate_ref
        completion_ref = self.validation_store.put_completion_if_absent_or_same(
            completion
        )
        if run.execution_state is ValidationRunState.RUNNING:
            run = self.validation_lifecycle.complete(
                run.validation_run_id,
                expected_state_version=run.state_version,
                transition_command_id=(
                    "validation-complete://sha256/"
                    + canonical_sha256({"validation_run_id": run.validation_run_id})
                ),
            )
        if run.execution_state is not ValidationRunState.COMPLETED:
            raise TargetPostWriteCompositionError(
                "ValidationRun did not reach COMPLETED accounting state"
            )
        self._assert_pre_gate_verification_current(generation=generation, run_id=run_id,
            validation_run_id=run.validation_run_id, subject_hash=subject.subject_hash,
            artifact_ref=artifact_ref, scope=scope, target_ref=target_ref,
            source_sections=source_sections, assembly_input_manifest_ref=assembly_input_manifest_ref)
        gate = self._observe_validation_producer("gate", self.gate_service.evaluate,
            validation_run_id=run.validation_run_id,
            validation_completion_ref=completion_ref,
            quality_delivery_contract_ref=quality_contract_ref,
            currentness_proof=currentness_proof,
        )
        return completion_ref, gate.as_ref()

    def _observe_validation_producer(self, producer, method, *args, **kwargs):
        observer = current_diagnostics()
        if not observer.enabled:
            return method(*args, **kwargs)
        stage = {"assessment": "S-ASSESS", "terminal": "S-TERMINAL", "publication": "S-PUBLISH", "gate": "S-GATE"}.get(producer, "S-ITEM")
        event_prefix = stage if stage != "S-ITEM" else "S-PRODUCER"
        unit = {"adherence": "E06", "integrity": "E05", "grounding": "E05", "coverage": "E07",
            "structural_coverage": "E07", "shared_projection": "E04", "FTH03": "E04", "consistency": "E08",
            "gate": "E09", "assessment": "E11", "terminal": "E11", "publication": "E11"}.get(producer, "E03")
        identity = {"stage": stage, "unit_id": unit, "producer": producer,
            **_diagnostic_ref(kwargs)}
        if args:
            identity["input_refs"] = _diagnostic_ref(args)
        observer.emit({**identity, "event_type": event_prefix + "-BEGIN"})
        try:
            with observer.span("postwrite:" + producer, "WORKFLOW", **identity):
                result = method(*args, **kwargs)
        except BaseException as error:
            observer.emit({**identity, "event_type": event_prefix + "-ERROR",
                "exception_type": type(error).__name__})
            raise
        observer.emit({**identity, "event_type": event_prefix + "-RESULT",
            "result": _diagnostic_ref(result)})
        return result

    @_run_write_context
    @_observe_postwrite("S-CONTROL")
    def _control(
        self,
        *,
        task_id: str,
        run_id: str,
        artifact_ref: ArtifactStateRefSchema,
        gate_ref: DeliveryGateDecisionRefSchema,
        currentness: CurrentnessProofSchema,
        generation_class=None,
        assembly_input_manifest_ref=None,
    ) -> PostWriteControlDecisionRefSchema:
        budget = self.budget_manager_provider(task_id, run_id)
        state = CanonicalBudgetStateSchema.model_validate(budget.canonical_state())
        if state.task_id != task_id or state.run_id != run_id:
            raise TargetPostWriteCompositionError(
                "canonical Budget account belongs to another task/run"
            )
        lineage = CanonicalRepairBudgetProjectionAdapter.project(
            target_artifact_ref=artifact_ref,
            budget_state=state,
        )
        usability = self.usability_resolver.resolve(
            artifact_state_ref=artifact_ref,
            currentness_proof=currentness,
        )
        return self.control_service.decide(
            delivery_gate_decision_ref=gate_ref,
            artifact_state_ref=artifact_ref,
            correction_policy_ref=self._correction_policy_ref(),
            fresh_currentness_proof=currentness,
            artifact_usability_integrity_proof=usability,
            correction_lineage_projection=lineage,
            generation_class=generation_class,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
            created_at_epoch=0.0,
        )

    def _resolve_run_lifecycle_revision(
        self,
        run_id: str,
        lifecycle_revision: int,
    ) -> RunLifecycleRecordSchema:
        if self.lifecycle_service is None:
            raise TargetPostWriteCompositionError(
                "Target Release requires canonical Run lifecycle owner"
            )
        return self.lifecycle_service.load_run_lifecycle_revision(
            run_id,
            lifecycle_revision,
        )

    def _append_or_reuse_exact_document_output_version(
        self,
        *,
        previous_ref: ArtifactStateRefSchema,
        projection,
        output_binding: ArtifactValidatedOutputBindingSchema,
        conformance_proof,
    ) -> ArtifactStateRefSchema:
        previous = self.artifact_service.resolve_exact(previous_ref)
        candidate = self.artifact_service.materializer.materialize(
            artifact_id=previous_ref.artifact_id,
            artifact_version=previous_ref.artifact_version + 1,
            artifact_scope=ArtifactScope.DOCUMENT,
            canonical_payload_ref=previous.canonical_payload_ref,
            origin=previous.origin,
            parent_artifact_ref=previous_ref,
            visible_projection_ref=projection.as_ref(),
            visible_projection=projection,
            validated_output_bindings=(output_binding,),
            output_conformance_proofs=(conformance_proof,),
        )
        try:
            existing = self.artifact_store.resolve_exact(candidate.artifact_ref)
        except PostWritePersistenceIntegrityError:
            return self.artifact_store.append_next_version(
                candidate,
                expected_parent_ref=previous_ref,
            )
        if existing != candidate:
            raise TargetPostWriteCompositionError(
                "deterministic final Document Artifact ref resolves conflicting content"
            )
        return candidate.artifact_ref

    @_run_write_context
    @_observe_postwrite("S-ASSEMBLY")
    def _finalize_document_output_and_control(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        materialization: TargetPostWriteMaterialization,
        budget_continuation=None,
    ) -> TargetFinalDocumentResult:
        document = materialization.document
        previous = self.artifact_service.resolve_exact(
            document.document_artifact_ref
        )
        visible_payload = ArtifactCanonicalPayloadMaterializer.business_payload(
            previous.canonical_payload_ref
        )
        manifest = self.binding_store.resolve_assembly_manifest(
            document.manifest_ref
        )
        projection = ArtifactValidationViewResolver.build_projection(
            artifact_scope=ArtifactScope.DOCUMENT,
            logical_target_ref=manifest.document_logical_id,
            visible_payload=visible_payload,
        )
        output_format = str(project_input.output_schema.output_format or "").strip()
        output_identity = (
            "target-validated-output://sha256/"
            + canonical_sha256(
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "document_id": document_id,
                    "visible_projection_sha256": projection.projection_sha256,
                    "output_format": output_format,
                }
            )
        )
        if output_format == "json":
            output_ref = self.output_store.put_json(
                visible_payload,
                artifact_type="validated_document_output",
                schema_version="validated_document_output_v1",
                created_by="TargetPostWriteComposition",
                task_id=task_id,
                run_id=run_id,
                input_snapshot_version=str(
                    document.document_artifact_ref.artifact_version + 1
                ),
                artifact_ref=output_identity,
            )
            renderer_contract_version = "target-json-renderer-v1"
        elif output_format == "markdown":
            output_ref = self.output_store.put_bytes(
                self._render_markdown_visible_payload(visible_payload),
                artifact_type="validated_document_output",
                schema_version="validated_document_output_v1",
                created_by="TargetPostWriteComposition",
                task_id=task_id,
                run_id=run_id,
                input_snapshot_version=str(
                    document.document_artifact_ref.artifact_version + 1
                ),
                artifact_ref=output_identity,
                content_encoding="markdown-utf8",
            )
            renderer_contract_version = "target-markdown-renderer-v1"
        else:
            raise TargetPostWriteCompositionError(
                "unsupported Target release output_format: " + output_format
            )
        output_binding = self.output_conformance_verifier.build_output_binding(
            output_format=output_format,
            renderer_contract_version=renderer_contract_version,
            output_artifact_ref=output_ref,
        )
        conformance = self.output_conformance_verifier.prove_precommit(
            artifact_id=document.document_artifact_ref.artifact_id,
            artifact_scope=ArtifactScope.DOCUMENT,
            canonical_payload_ref=previous.canonical_payload_ref,
            visible_projection=projection,
            output_binding=output_binding,
        )
        final_artifact_ref = self._append_or_reuse_exact_document_output_version(
            previous_ref=document.document_artifact_ref,
            projection=projection,
            output_binding=output_binding,
            conformance_proof=conformance,
        )

        active_projection = document.active_projection.model_copy(
            update={"artifact_state_ref": final_artifact_ref},
            deep=True,
        )
        final_entry_ref = self.document_entry_assembler.assemble_and_persist(
            run_id=run_id,
            document_id=document_id,
            assembly_input_manifest_ref=document.manifest_ref,
            artifact_state_ref=final_artifact_ref,
            evidence_binding=active_projection.evidence_binding,
            requirement_ref=materialization.requirement_ref,
            quality_delivery_contract_ref=(
                active_projection.quality_delivery_contract_ref
            ),
        )
        currentness = self.currentness_resolver.prove(
            historical_entry_ref=final_entry_ref,
            active_projection=active_projection,
            comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
            active_materialized_post_write_entry_ref=final_entry_ref,
        )
        completion_ref, gate_ref = self._complete_validation(
            run_id=run_id,
            entry_ref=final_entry_ref,
            artifact_ref=final_artifact_ref,
            scope=ValidationScope.DOCUMENT,
            target_ref=document_id,
            quality_contract_ref=active_projection.quality_delivery_contract_ref,
            currentness_proof=currentness,
            requirement_ref=materialization.requirement_ref,
            assembly_input_manifest_ref=document.manifest_ref,
            source_sections=materialization.source_sections,
            **({"budget_continuation":budget_continuation} if budget_continuation is not None else {}),
        )
        control_ref = self._control(
            task_id=task_id,
            run_id=run_id,
            artifact_ref=final_artifact_ref,
            gate_ref=gate_ref,
            currentness=currentness,
            generation_class=None,
            assembly_input_manifest_ref=document.manifest_ref,
        )
        return TargetFinalDocumentResult(
            artifact_state_ref=final_artifact_ref,
            post_write_entry_ref=final_entry_ref,
            active_projection=active_projection,
            currentness_proof=currentness,
            validation_completion_ref=completion_ref,
            delivery_gate_decision_ref=gate_ref,
            control_decision_ref=control_ref,
            output_binding=output_binding,
        )

    def _active_document_projection_from_assessment(
        self,
        record,
    ) -> ActiveDocumentBindingProjectionSchema:
        entry_ref = PostWriteEntryRefSchema.model_validate(
            record.document_post_write_entry_ref.exact_ref_payload
        )
        artifact_ref = ArtifactStateRefSchema.model_validate(
            record.document_artifact_ref.exact_ref_payload
        )
        control_ref = PostWriteControlDecisionRefSchema.model_validate(
            record.post_write_control_decision_ref.exact_ref_payload
        )
        entry = self.entry_store.resolve_exact(entry_ref)
        control = self.control_store.resolve_exact(control_ref)
        if (
            entry.identity.artifact_state_ref != artifact_ref
            or control.input_manifest.post_write_entry_ref != entry_ref
            or control.input_manifest.artifact_state_ref != artifact_ref
        ):
            raise TargetPostWriteCompositionError(
                "RunCompletion final Document exact refs no longer cohere"
            )
        manifest_ref = getattr(
            control.input_manifest,
            "assembly_input_manifest_ref",
            None,
        )
        if manifest_ref is None:
            raise TargetPostWriteCompositionError(
                "RunCompletion final Control is not a Document control"
            )
        manifest = self.binding_store.resolve_assembly_manifest(manifest_ref)
        section_controls: dict[str, PostWriteControlDecisionRefSchema] = {}
        for envelope in record.section_completion_refs:
            if envelope.source_kind != "SECTION_CONTROL_DECISION":
                raise TargetPostWriteCompositionError(
                    "RunCompletion Section completion ref is not exact Section Control"
                )
            scope = str(envelope.source_scope_ref)
            prefix = "section:"
            if not scope.startswith(prefix) or not scope[len(prefix):]:
                raise TargetPostWriteCompositionError(
                    "RunCompletion Section completion scope is malformed"
                )
            section_id = scope[len(prefix):]
            exact_ref = PostWriteControlDecisionRefSchema.model_validate(
                envelope.exact_ref_payload
            )
            if section_id in section_controls:
                raise TargetPostWriteCompositionError(
                    "RunCompletion contains duplicate Section completion scope"
                )
            section_controls[section_id] = exact_ref
        from .run_finalization_currentness import assert_assessment_sections
        assert_assessment_sections(record, manifest)
        evaluated = {item.section_key for item in manifest.ordered_section_entries
            if item.selected_section_control_decision_ref is not None}
        if set(section_controls) != evaluated:
            raise TargetPostWriteCompositionError(
                "RunCompletion Section completion set differs from Document manifest"
            )

        selections = []
        for original in manifest.ordered_section_entries:
            section_key = original.section_key
            if section_key not in evaluated:
                selections.append(active_selection_from_manifest_entry(original))
                continue
            eligibility = self.section_eligibility.resolve(
                section_key=section_key,
                control_decision_ref=section_controls[section_key],
            )
            entry_projection = self.manifest_builder.selection_entry(
                section_key=section_key,
                eligibility=eligibility,
            )
            if entry_projection != original:
                raise TargetPostWriteCompositionError("Assessment selected Section eligibility changed")
            selections.append(
                active_selection_from_manifest_entry(entry_projection)
            )
        identity = entry.identity
        return ActiveDocumentBindingProjectionSchema(
            run_id=identity.run_id,
            document_id=identity.document_id,
            assembly_input_manifest_ref=manifest_ref,
            artifact_state_ref=artifact_ref,
            evidence_binding=identity.evidence_binding,
            requirement_ref=identity.requirement_ref,
            quality_delivery_contract_ref=(
                identity.quality_delivery_contract_ref
            ),
            assembly_owner_section_selections=tuple(selections),
        )

    def _terminalization_snapshot(
        self,
        record,
    ) -> RunCompletionTerminalizationSnapshot:
        control_ref = PostWriteControlDecisionRefSchema.model_validate(
            record.post_write_control_decision_ref.exact_ref_payload
        )
        entry_ref = PostWriteEntryRefSchema.model_validate(
            record.document_post_write_entry_ref.exact_ref_payload
        )
        control = self.control_store.resolve_exact(control_ref)
        active_projection = self._active_document_projection_from_assessment(
            record
        )
        currentness = self.currentness_resolver.prove(
            historical_entry_ref=entry_ref,
            active_projection=active_projection,
            comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
        )
        return RunCompletionTerminalizationSnapshot(
            post_write_control_decision=control,
            fresh_document_currentness_proof=currentness,
            active_repair_attempt_refs=(),
        )

    @_observe_postwrite("S-ASSESS")
    def _commit_and_terminalize_run(
        self,
        *,
        run_id: str,
        materialization: TargetPostWriteMaterialization,
        final_document: TargetFinalDocumentResult,
        task_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
    ) -> tuple[RunCompletionAssessmentRefSchema, RunLifecycleRecordSchema]:
        if self.lifecycle_service is None:
            raise TargetPostWriteCompositionError(
                "RunCompletion requires canonical Run lifecycle owner"
            )
        final_control = self.control_store.resolve_exact(
            final_document.control_decision_ref
        )
        from .run_finalization_currentness import final_control_eligible
        if not final_control_eligible(final_control):
            raise TargetPostWriteCompositionError("nonterminal Control requires existing correction/recovery")
        barrier = build_run_finalization_barrier(run_id=run_id, final_control_ref=final_control.as_ref())
        current = self._enter_finalization(run_id, barrier)
        assessment_ref, terminal, _, _ = self._finish_finalization(current,
            task_id=task_id, document_id=document_id, project_input=project_input,
            source_sections=materialization.source_sections)
        return assessment_ref, terminal

    def _enter_finalization(self, run_id, barrier):
        from .lifecycle_runtime import LifecycleRevisionConflict
        current = self.lifecycle_service.load_run_lifecycle(run_id)
        if current.status is RunLifecycleStatus.RUNNING:
            try:
                return self.lifecycle_service.transition_run(run_id, current.lifecycle_revision,
                    RunLifecycleStatus.FINALIZING, run_finalization_barrier=barrier,
                    reason_ref="finalization:target-post-write")
            except LifecycleRevisionConflict:
                current = self.lifecycle_service.load_run_lifecycle(run_id)
        if (current.status not in {RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS}
                or current.run_finalization_barrier != barrier):
            raise TargetPostWriteCompositionError("concurrent finalizer selected another barrier or unavailable Run")
        return current

    def _assert_finalization_current(self, values, **kwargs):
        from .run_finalization_currentness import assert_finalization_current, FinalizationStaleError
        from .post_write_claim_projection import PostWriteClaimProjectionError
        from .paragraph_runtime import ParagraphCurrentnessError
        from .research_runtime import EvidencePackageCurrentnessError, ResearchProgressConflict
        from .fact_snapshot_runtime import FactSnapshotCurrentnessError
        from .claim_verifier_composition import TargetClaimVerifierError
        from .claim_composition import TargetClaimCompositionError
        try:
            return assert_finalization_current(self, values, **kwargs)
        except (PostWriteClaimProjectionError, ParagraphCurrentnessError,
                EvidencePackageCurrentnessError, ResearchProgressConflict, FactSnapshotCurrentnessError,
                TargetClaimVerifierError, TargetClaimCompositionError) as exc:
            # Never reopen writes for a technical read failure or corrupt durable chain.
            cause = exc
            while cause is not None:
                if (isinstance(cause, (OSError, TimeoutError, PostWritePersistenceIntegrityError))
                        or 'Integrity' in type(cause).__name__ or 'IdentityCollision' in type(cause).__name__):
                    raise
                cause = cause.__cause__
            raise FinalizationStaleError(str(exc)) from exc

    def _finish_finalization(self, current, *, task_id, document_id, project_input, source_sections):
        from .run_finalization_currentness import (
            read_postwrite_finalization, validate_request, evaluated_section_envelopes,
            assert_assessment_sections, FinalizationStaleError,
        )
        from .lifecycle_runtime import LifecycleRevisionConflict
        run_id = current.identity.run_id
        if current.status is RunLifecycleStatus.SUCCESS:
            # Identical CAS winner; no live read inside lifecycle owner.
            return current.run_completion_assessment_ref, current, None, None
        barrier = current.run_finalization_barrier
        if current.status is not RunLifecycleStatus.FINALIZING or barrier is None:
            raise TargetPostWriteCompositionError("finalization requires exact FINALIZING barrier")
        try:
            values = read_postwrite_finalization(self, barrier)
            sources = validate_request(self, values, task_id=task_id, run_id=run_id, document_id=document_id,
                project_input=project_input, source_sections=source_sections)
            fresh = self._assert_finalization_current(values, task_id=task_id, run_id=run_id,
                project_input=project_input, sources=sources)
        except FinalizationStaleError:
            # The original revision prevents a stale reader aborting another lineage.
            self.lifecycle_service.transition_run(run_id, current.lifecycle_revision,
                RunLifecycleStatus.RUNNING, reason_ref="abort-finalization:stale-currentness")
            raise
        materialization, final_document = self._finalization_results(values, tuple(source_sections), project_input)
        assessment = self.assessment_builder.build_success(run_id=run_id,
            section_completion_refs=evaluated_section_envelopes(values.manifest),
            document_artifact_ref=values.document.artifact.artifact_ref,
            document_post_write_entry_ref=values.document.control.input_manifest.post_write_entry_ref,
            post_write_control_decision=values.document.control, fresh_document_currentness_proof=fresh,
            active_repair_attempt_refs=(), post_write_mutation_closure_refs=(exact_ref_envelope(
                source_authority="RUN_LIFECYCLE", source_kind="RUN_FINALIZATION_BARRIER",
                source_scope_ref=f"run:{run_id}", exact_ref=barrier.as_ref()),))
        assert_assessment_sections(assessment, values.manifest)
        assessment_ref = self._observe_validation_producer("assessment", self.assessment_store.put_if_absent_or_same, assessment)
        try:
            terminal = self._observe_validation_producer("terminal", self.lifecycle_service.transition_run, run_id, current.lifecycle_revision,
                RunLifecycleStatus.SUCCESS, reason_ref="completion:target-post-write",
                run_completion_assessment_ref=assessment_ref)
        except LifecycleRevisionConflict:
            terminal = self.lifecycle_service.load_run_lifecycle(run_id)
            if (terminal.status is not RunLifecycleStatus.SUCCESS or terminal.run_finalization_barrier != barrier
                    or terminal.run_completion_assessment_ref != assessment_ref):
                raise
        return assessment_ref, terminal, materialization, final_document

    def _finalization_results(self, values, source_sections, project_input):
        doc = values.document
        identity = doc.entry.identity
        bindings = tuple(b for b in doc.artifact.validated_output_bindings
            if b.output_format == project_input.output_schema.output_format)
        if len(bindings) != 1:
            raise PostWritePersistenceIntegrityError("finalization output binding missing or ambiguous")
        binding = bindings[0]
        task_id = self.lifecycle_service.load_run_identity(identity.run_id).task_id
        renderer = {'json':'target-json-renderer-v1','markdown':'target-markdown-renderer-v1'}.get(binding.output_format)
        expected_output = 'target-validated-output://sha256/' + canonical_sha256({'task_id':task_id,
            'run_id':identity.run_id,'document_id':identity.document_id,
            'visible_projection_sha256':doc.artifact.visible_projection.projection_sha256,
            'output_format':binding.output_format})
        if (binding.renderer_contract_version != renderer or binding.output_artifact_ref.artifact_ref != expected_output
                or binding.output_artifact_ref.task_id != task_id or binding.output_artifact_ref.run_id != identity.run_id):
            raise PostWritePersistenceIntegrityError('finalization output request/renderer exact binding differs')
        self.output_conformance_verifier.resolve_bound_conforming(record=doc.artifact, output_binding=binding)
        active = doc.currentness.active_projection
        parent = doc.artifact.parent_artifact_ref
        base_active = active.model_copy(update={"artifact_state_ref": parent})
        base_ref = values.base_entry_ref
        sections = tuple(TargetSectionPostWriteResult(section_id=key,
            artifact_state_ref=chain.artifact.artifact_ref,
            post_write_entry_ref=chain.control.input_manifest.post_write_entry_ref,
            currentness_proof=chain.currentness,
            validation_completion_ref=chain.gate.gate_input_manifest.validation_completion_ref,
            delivery_gate_decision_ref=chain.gate.as_ref(), control_decision_ref=chain.control.as_ref())
            for key, chain in values.sections)
        materialization = TargetPostWriteMaterialization(requirement_ref=identity.requirement_ref,
            section_results=sections, source_sections=source_sections, document=DocumentLifecycleResult(
                manifest_ref=identity.assembly_input_manifest_ref,
                document_evidence_ref=identity.evidence_binding.document_projection_ref,
                document_artifact_ref=parent, document_entry_ref=base_ref, active_projection=base_active))
        final = TargetFinalDocumentResult(artifact_state_ref=doc.artifact.artifact_ref,
            post_write_entry_ref=doc.control.input_manifest.post_write_entry_ref, active_projection=active,
            currentness_proof=doc.currentness,
            validation_completion_ref=doc.gate.gate_input_manifest.validation_completion_ref,
            delivery_gate_decision_ref=doc.gate.as_ref(), control_decision_ref=doc.control.as_ref(), output_binding=bindings[0])
        return materialization, final

    def _resume_finalization(self, current, *, task_id, document_id, project_input, section_execution_results):
        assessment_ref, terminal, materialization, final = self._finish_finalization(current,
            task_id=task_id, document_id=document_id, project_input=ProjectInputSchema.model_validate(project_input),
            source_sections=tuple(SectionExecutionResultSchema.model_validate(s) for s in section_execution_results))
        if materialization is None:
            return self._restore_terminal_target(task_id=task_id, run_id=current.identity.run_id,
                document_id=document_id, project_input=project_input, section_execution_results=section_execution_results,
                terminal=terminal)
        published, publication_failure = self._attempt_review_safe_publication(
            task_id=task_id, run_id=current.identity.run_id,
            document_id=document_id, final_document=final,
            assessment_ref=assessment_ref, terminal_run=terminal,
        )
        return TargetPostWriteCompletion(
            materialization, final, assessment_ref, terminal, published,
            publication_failure,
        )

    def _attempt_review_safe_publication(
        self, *, task_id, run_id, document_id, final_document, assessment_ref,
        terminal_run,
    ) -> tuple[PostWritePublishedReleaseSchema | None, dict[str, str] | None]:
        """Never let a *post-terminal* publisher failure swallow a trusted draft.

        Publication remains owned by the existing immutable publisher. This
        method neither grants release authority nor reclassifies Quality.
        """
        control = self.control_store.resolve_exact(final_document.control_decision_ref)
        if control.enforcement_action.value not in {"OUTPUT", "OUTPUT_BEST_EFFORT"}:
            return None, None
        try:
            published = self._publish_target_release(
                task_id=task_id, run_id=run_id, document_id=document_id,
                final_document=final_document, assessment_ref=assessment_ref,
                terminal_run=terminal_run,
            )
        except Exception as exc:
            return None, {
                "error_code": "TARGET_RELEASE_PUBLICATION_FAILED",
                "error_type": type(exc).__name__,
                # Private diagnostic only. Public Outcome uses an opaque ref.
                "message": str(exc)[:512],
            }
        return published, None

    @_observe_postwrite("S-PUBLISH")
    def _publish_target_release(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        final_document: TargetFinalDocumentResult,
        assessment_ref: RunCompletionAssessmentRefSchema,
        terminal_run: RunLifecycleRecordSchema,
    ) -> PostWritePublishedReleaseSchema:
        request = self.release_service.build_request(
            release_contract_version=self.release_service.release_contract_version,
            task_id=task_id,
            run_id=run_id,
            document_logical_id=f"document-logical:{run_id}:{document_id}",
            document_artifact_ref=final_document.artifact_state_ref,
            post_write_control_decision_ref=final_document.control_decision_ref,
            output_format=final_document.output_binding.output_format,
            validated_output_binding_sha256=(
                final_document.output_binding.binding_sha256
            ),
            publication_target_id=(
                f"target://{task_id}/{document_id}/primary"
            ),
        )
        return self._observe_validation_producer("publication", self.release_service.publish,
            request=request,
            run_completion_assessment_ref=assessment_ref,
            run_lifecycle_revision=terminal_run.lifecycle_revision,
            active_document_binding_supplier=(
                lambda: final_document.active_projection
            ),
        )

    def _restore_terminal_control(self, control_ref, *, source_sections, scope,
                                  target_ref, expected_projection):
        """Validate an assessment-selected Control and its completed validators, read-only."""
        control = self.control_store.resolve_exact(control_ref)
        manifest = control.input_manifest
        entry = self.entry_store.resolve_exact(manifest.post_write_entry_ref)
        identity = entry.identity
        if (identity.artifact_state_ref != manifest.artifact_state_ref
                or identity.run_id != expected_projection.run_id
                or identity.document_id != expected_projection.document_id):
            raise TargetPostWriteCompositionError("terminal Control/Entry/request identity differs")
        if control.control_contract_version != self.control_service.control_contract_version:
            raise TargetPostWriteCompositionError("terminal Control execution contract version differs")
        policy = self.correction_policy_store.resolve_exact(manifest.correction_policy_ref)
        if (policy.policy_id != "correction-policy://multi-agent-v3/w13-no-inference"
                or policy.policy_version != "v1" or policy.automatic_correction_enabled
                or policy.rules or policy.technical_retry_limit != 1):
            raise TargetPostWriteCompositionError("terminal replay correction policy version differs")
        contract = self.contract_store.resolve_exact(identity.quality_delivery_contract_ref)
        expected_conditions = tuple(sorted(
            (RequiredDimensionConditionSchema(dimension_id=value) for value in self.REQUIRED_DIMENSIONS),
            key=lambda value: (value.dimension_id, value.effective_condition_ref())))
        expected_consistency_procedure = self.semantic_quality_config.procedure_input(
            requirement_ref=identity.requirement_ref
        )
        if (contract.contract_id != "quality-delivery://multi-agent-v3/target-v1"
                or contract.contract_version != "v1" or contract.gate_contract_version != "delivery_gate_v1"
                or contract.required_dimensions != expected_conditions or contract.fact_policy_rules
                or contract.unmatched_fact_effect.value != "INFORMATIONAL"
                or contract.policy_projection_contract_version != "quality_policy_projection_v1"
                or contract.consistency_procedure_input != expected_consistency_procedure
                or contract.contract_metadata != {
                    "authority":"target_post_write",
                    "missing_semantic_execution":"UNRESOLVED",
                    "semantic_quality_config_ref":self.semantic_quality_config.source_ref,
                }):
            raise TargetPostWriteCompositionError("terminal replay quality policy version differs")
        currentness = self.currentness_resolver.prove(
            historical_entry_ref=manifest.post_write_entry_ref, active_projection=expected_projection,
            comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
            active_materialized_post_write_entry_ref=manifest.post_write_entry_ref)
        if currentness != control.fresh_currentness_proof or currentness.currentness_state.value != "CURRENT":
            raise TargetPostWriteCompositionError("terminal Control is stale against exact current sources")
        usability = self.usability_resolver.resolve(artifact_state_ref=identity.artifact_state_ref,
            currentness_proof=currentness)
        if usability != control.artifact_usability_integrity_proof:
            raise TargetPostWriteCompositionError("terminal Control output usability changed")
        completion_ref, gate_ref = self._complete_validation(
            run_id=identity.run_id,
            entry_ref=manifest.post_write_entry_ref,artifact_ref=identity.artifact_state_ref,scope=scope,
            target_ref=target_ref,quality_contract_ref=identity.quality_delivery_contract_ref,
            currentness_proof=currentness,requirement_ref=identity.requirement_ref,
            assembly_input_manifest_ref=identity.assembly_input_manifest_ref,source_sections=source_sections,
            evidence_snapshot_ref=identity.evidence_binding.section_writer_snapshot_ref,
            committed_gate_ref=control.delivery_gate_decision_ref)
        if control.validation_run_id != completion_ref.validation_run_id:
            raise TargetPostWriteCompositionError("terminal Control binds a different ValidationRun")
        return entry, currentness, completion_ref, gate_ref

    def _restore_terminal_target(self, *, task_id, run_id, document_id, project_input,
                                 section_execution_results, terminal):
        """The lifecycle-bound assessment is the only terminal recovery root."""
        from application.task_factory import TaskFactory
        project = ProjectInputSchema.model_validate(project_input)
        identity = self.lifecycle_service.load_run_identity(run_id)
        request = TaskFactory().build(task_id=task_id,run_id=run_id,user_input=project.user_query,
            created_at="terminal-request-verification",project_input=project.model_dump(mode="json"))
        memory = getattr(self, "memory_composition", None)
        if memory is not None and memory.required:
            request = request.model_copy(update={"metadata": {
                **request.metadata, "durable_memory_required": True}})
        if (identity.task_id != task_id or identity.run_id != run_id
                or request.canonical_request_sha256() != identity.canonical_request_sha256):
            raise TargetPostWriteCompositionError("terminal replay request differs from immutable RunIdentity")
        if terminal.run_completion_assessment_ref is None:
            raise TargetPostWriteCompositionError("terminal SUCCESS has no exact completion assessment")
        assessment_ref = terminal.run_completion_assessment_ref
        assessment = self.terminalization_resolver.resolve_exact(assessment_ref)
        if assessment.run_id != run_id:
            raise TargetPostWriteCompositionError("terminal assessment belongs to another Run")
        active = self._active_document_projection_from_assessment(assessment)
        if active.run_id != run_id or active.document_id != document_id:
            raise TargetPostWriteCompositionError("terminal replay Document identity differs")
        manifest = self.binding_store.resolve_assembly_manifest(active.assembly_input_manifest_ref)
        sections = tuple(SectionExecutionResultSchema.model_validate(value) for value in section_execution_results)
        evaluated_entries = tuple(e for e in manifest.ordered_section_entries
            if e.selection_state is not AssemblySelectionState.MISSING)
        if (tuple(value.section.section_id for value in sections) != tuple(e.section_key for e in evaluated_entries)
                or manifest.document_logical_id != f"document-logical:{run_id}:{document_id}"
                or manifest.assembly_contract_version != self.manifest_builder.assembly_contract_version):
            raise TargetPostWriteCompositionError("terminal replay Section set/order or assembly version differs")
        controls = {ref.source_scope_ref.removeprefix("section:"):
            PostWriteControlDecisionRefSchema.model_validate(ref.exact_ref_payload)
            for ref in assessment.section_completion_refs}
        restored_sections = []
        for source, original_selection in zip(sections, evaluated_entries):
            control_ref = controls[source.section.section_id]
            control = self.control_store.resolve_exact(control_ref)
            entry = self.entry_store.resolve_exact(control.input_manifest.post_write_entry_ref)
            bound = entry.identity
            if (bound.writer_execution_ref != source.writer_execution_ref
                    or bound.writer_invocation_ref != source.writer_generation_invocation_ref
                    or bound.section_id != source.section.section_id
                    or bound.requirement_ref != active.requirement_ref
                    or bound.quality_delivery_contract_ref != active.quality_delivery_contract_ref):
                raise TargetPostWriteCompositionError("terminal replay Section Writer/Entry refs differ")
            candidate = self.artifact_service.materializer.materialize(
                artifact_id=f"section-artifact:{run_id}:{document_id}:{source.section.section_id}",
                artifact_version=1,artifact_scope=ArtifactScope.SECTION,
                canonical_payload_ref={"payload_kind":"section","section_id":source.section.section_id,
                    "section_title":source.section.section_title,"content":source.section.content},
                origin=InitialWriterArtifactOriginSchema(writer_execution_ref=source.writer_execution_ref,
                    writer_invocation_ref=source.writer_generation_invocation_ref))
            if self.artifact_store.resolve_exact(bound.artifact_state_ref) != candidate:
                raise TargetPostWriteCompositionError("terminal replay Section Artifact content differs")
            if source.active_evidence_package_binding is None:
                raise TargetPostWriteCompositionError("terminal replay lacks exact Evidence binding")
            package = self.research_runtime.validate_evidence_package_binding_current(source.active_evidence_package_binding)
            bundle = self.research_runtime.resolve_evidence_bundle(package.evidence_bundle_binding)
            if (source.evidence.evidence_bundle is None
                    or evidence_bundle_sha256(source.evidence.evidence_bundle) != evidence_bundle_sha256(bundle)
                    or bound.evidence_binding.section_writer_snapshot_ref.evidence_bundle_sha256 != evidence_bundle_sha256(bundle)):
                raise TargetPostWriteCompositionError("terminal replay Evidence differs from exact Writer snapshot")
            projection = ActiveSectionBindingProjectionSchema(run_id=run_id,document_id=document_id,
                section_id=bound.section_id,artifact_state_ref=bound.artifact_state_ref,
                evidence_binding=bound.evidence_binding,requirement_ref=bound.requirement_ref,
                quality_delivery_contract_ref=bound.quality_delivery_contract_ref,
                writer_execution_ref=bound.writer_execution_ref,writer_invocation_ref=bound.writer_invocation_ref,
                route_decision_ref=bound.route_decision_ref,generation_class=bound.generation_class)
            entry,currentness,completion_ref,gate_ref = self._restore_terminal_control(control_ref,
                source_sections=(source,),scope=ValidationScope.SECTION,target_ref=bound.section_id,
                expected_projection=projection)
            selection = self.manifest_builder.selection_entry(section_key=bound.section_id,
                eligibility=self.section_eligibility.resolve(section_key=bound.section_id,control_decision_ref=control_ref))
            if selection != original_selection:
                raise TargetPostWriteCompositionError("terminal replay Section Control differs from original assembly")
            restored_sections.append(TargetSectionPostWriteResult(section_id=bound.section_id,
                artifact_state_ref=bound.artifact_state_ref,post_write_entry_ref=control.input_manifest.post_write_entry_ref,
                currentness_proof=currentness,validation_completion_ref=completion_ref,
                delivery_gate_decision_ref=gate_ref,control_decision_ref=control_ref))
        final_control_ref = PostWriteControlDecisionRefSchema.model_validate(assessment.post_write_control_decision_ref.exact_ref_payload)
        final_entry,currentness,completion_ref,gate_ref = self._restore_terminal_control(final_control_ref,
            source_sections=sections,scope=ValidationScope.DOCUMENT,target_ref=document_id,expected_projection=active)
        final_record = self.artifact_store.resolve_exact(active.artifact_state_ref)
        parent_ref = final_record.parent_artifact_ref
        if parent_ref is None or final_record.artifact_ref.artifact_version != parent_ref.artifact_version + 1:
            raise TargetPostWriteCompositionError("terminal final output has no exact assembly parent")
        parent = self.artifact_store.resolve_exact(parent_ref)
        if (final_record.origin != parent.origin
                or ArtifactCanonicalPayloadMaterializer.business_payload(final_record.canonical_payload_ref)
                != ArtifactCanonicalPayloadMaterializer.business_payload(parent.canonical_payload_ref)
                or self.citation_integrity.committed_assembly(artifact_id=parent_ref.artifact_id,
                    artifact_version=parent_ref.artifact_version,manifest=manifest,expected_parent_ref=parent.parent_artifact_ref) != parent_ref):
            raise TargetPostWriteCompositionError("terminal final output differs from committed assembly")
        base_projection = active.model_copy(update={"artifact_state_ref":parent_ref},deep=True)
        base_identity = self.identity_builder.from_active_document_projection(base_projection)
        base_entry_ref = entry_ref_from_identity(base_identity)
        if self.entry_store.resolve_exact(base_entry_ref).identity != base_identity:
            raise TargetPostWriteCompositionError("terminal assembly Entry differs")
        evidence_ref = active.evidence_binding.document_projection_ref
        self.binding_store.resolve_document_evidence_snapshot(evidence_ref)
        output_format = str(project.output_schema.output_format or "").strip()
        bindings = [value for value in final_record.validated_output_bindings if value.output_format == output_format]
        if len(bindings) != 1:
            raise TargetPostWriteCompositionError("terminal final output binding is missing or ambiguous")
        binding = bindings[0]
        renderer = {"json":"target-json-renderer-v1","markdown":"target-markdown-renderer-v1"}.get(output_format)
        expected_output = "target-validated-output://sha256/" + canonical_sha256({"task_id":task_id,"run_id":run_id,
            "document_id":document_id,"visible_projection_sha256":final_record.visible_projection.projection_sha256,
            "output_format":output_format})
        if (binding.renderer_contract_version != renderer or binding.output_artifact_ref.artifact_ref != expected_output
                or binding.output_artifact_ref.task_id != task_id or binding.output_artifact_ref.run_id != run_id):
            raise TargetPostWriteCompositionError("terminal output request/renderer binding differs")
        self.output_conformance_verifier.resolve_bound_conforming(record=final_record,output_binding=binding)
        materialization = TargetPostWriteMaterialization(requirement_ref=active.requirement_ref,
            section_results=tuple(restored_sections),source_sections=sections,
            document=DocumentLifecycleResult(manifest_ref=active.assembly_input_manifest_ref,
                document_evidence_ref=evidence_ref,document_artifact_ref=parent_ref,
                document_entry_ref=base_entry_ref,active_projection=base_projection))
        final_document = TargetFinalDocumentResult(artifact_state_ref=active.artifact_state_ref,
            post_write_entry_ref=PostWriteEntryRefSchema.model_validate(assessment.document_post_write_entry_ref.exact_ref_payload),
            active_projection=active,currentness_proof=currentness,validation_completion_ref=completion_ref,
            delivery_gate_decision_ref=gate_ref,control_decision_ref=final_control_ref,output_binding=binding)
        # The existing publisher distinguishes an already published exact request
        # from SUCCESS-before-publication recovery. It owns any publication writes.
        published, publication_failure = self._attempt_review_safe_publication(
            task_id=task_id, run_id=run_id, document_id=document_id,
            final_document=final_document, assessment_ref=assessment_ref,
            terminal_run=terminal,
        )
        return TargetPostWriteCompletion(materialization=materialization,
            final_document=final_document, run_completion_assessment_ref=assessment_ref,
            terminal_run=terminal, published_release=published,
            publication_failure=publication_failure)

    @_run_write_context
    def capture_budget_materialization(self, materialization, *, task_id, run_id, document_id, project_input):
        """Bind an actual completed materialization without replaying Sections."""
        from dataclasses import fields
        document = {field.name:getattr(materialization.document,field.name).model_dump(mode="json")
                    for field in fields(materialization.document)}
        value = {"schema_version":"budget_document_materialization_v1","task_id":task_id,"run_id":run_id,
            "document_id":document_id,"project_sha256":canonical_sha256(project_input.model_dump(mode="json")),
            "requirement_ref":materialization.requirement_ref.model_dump(mode="json"),
            "section_results":[{field.name:(getattr(row,field.name).model_dump(mode="json")
                if hasattr(getattr(row,field.name),"model_dump") else getattr(row,field.name))
                for field in fields(row)} for row in materialization.section_results],
            "source_sections":[row.model_dump(mode="json") for row in materialization.source_sections],"document":document}
        identity={"task_id":task_id,"run_id":run_id}
        ref=self.citation_grounding.store.put_json(value,
            artifact_ref="artifact://budget-document-materialization/"+canonical_sha256(value),
            artifact_type="budget_document_materialization",schema_version=value["schema_version"],
            created_by="TargetPostWriteComposition",**identity)
        return ref

    def restore_budget_materialization(self, reference, *, task_id, run_id, document_id, project_input, section_execution_results, budget_continuation=None):
        """Re-resolve exact native refs; no Section or completed cognition entry."""
        return self._resolve_budget_materialization(reference, task_id=task_id, run_id=run_id,
            document_id=document_id, project_input=project_input, section_execution_results=section_execution_results,
            budget_continuation=budget_continuation)

    def _resolve_budget_materialization(self, reference, *, task_id, run_id, document_id, project_input,
                                       section_execution_results, budget_continuation=None, passive_completed=False):
        from application.task_factory import TaskFactory
        from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
        from apps.enterprise_document.schemas.post_write.document import (
            AssemblyInputManifestRefSchema, DocumentEvidenceSnapshotRefSchema,
            DocumentEvidenceSourceBindingSchema, DocumentEvidenceMemberRefSchema,
        )
        from apps.enterprise_document.services.post_write.document_lifecycle import active_selection_from_manifest_entry
        project = ProjectInputSchema.model_validate(project_input)
        sources = tuple(SectionExecutionResultSchema.model_validate(row) for row in section_execution_results)
        ref=ArtifactReferenceSchema.model_validate(reference)
        native,value=self.citation_grounding.store.get_json_with_reference(ref.artifact_ref,task_id=task_id,run_id=run_id)
        if (native!=ref or value.get("schema_version")!="budget_document_materialization_v1"
                or (value.get("task_id"),value.get("run_id"),value.get("document_id"))!=(task_id,run_id,document_id)
                or value.get("project_sha256")!=canonical_sha256(project.model_dump(mode="json"))
                or value.get("source_sections")!=[row.model_dump(mode="json") for row in sources]):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor materialization/request differs")
        source_keys = tuple(row.section.section_id for row in sources)
        frozen_sections = value.get("section_results")
        if (not source_keys or len(set(source_keys)) != len(source_keys)
                or not isinstance(frozen_sections, list) or len(frozen_sections) != len(sources)
                or tuple(row.get("section_id") for row in frozen_sections) != source_keys):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor Section set/order differs")
        run_identity = self.lifecycle_service.load_run_identity(run_id)
        request = TaskFactory().build(task_id=task_id,run_id=run_id,user_input=project.user_query,
            created_at="budget-request-verification",project_input=project.model_dump(mode="json"))
        memory = getattr(self, "memory_composition", None)
        if memory is not None and memory.required:
            request = request.model_copy(update={"metadata": {**request.metadata, "durable_memory_required":True}})
        if (run_identity.task_id != task_id or request.canonical_request_sha256() != run_identity.canonical_request_sha256):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor immutable RunIdentity/request differs")
        raw=value["document"]
        document=DocumentLifecycleResult(manifest_ref=AssemblyInputManifestRefSchema.model_validate(raw["manifest_ref"]),
            document_evidence_ref=DocumentEvidenceSnapshotRefSchema.model_validate(raw["document_evidence_ref"]),
            document_artifact_ref=ArtifactStateRefSchema.model_validate(raw["document_artifact_ref"]),
            document_entry_ref=PostWriteEntryRefSchema.model_validate(raw["document_entry_ref"]),
            active_projection=ActiveDocumentBindingProjectionSchema.model_validate(raw["active_projection"]))
        manifest = self.binding_store.resolve_assembly_manifest(document.manifest_ref)
        requirement_ref=RequirementRefSchema.model_validate(value["requirement_ref"])
        self.requirement_store.resolve_exact(requirement_ref)
        active = document.active_projection
        if (active.run_id != run_id or active.document_id != document_id
                or active.artifact_state_ref != document.document_artifact_ref
                or active.assembly_input_manifest_ref != document.manifest_ref
                or active.evidence_binding.document_projection_ref != document.document_evidence_ref
                or active.requirement_ref != requirement_ref
                or manifest.document_logical_id != f"document-logical:{run_id}:{document_id}"
                or manifest.assembly_contract_version != self.manifest_builder.assembly_contract_version
                or manifest.required_section_keys != source_keys
                or tuple(entry.section_key for entry in manifest.ordered_section_entries) != source_keys
                or active.assembly_owner_section_selections != tuple(active_selection_from_manifest_entry(entry) for entry in manifest.ordered_section_entries)):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor assembly/projection binding differs")
        base_identity = self.identity_builder.from_active_document_projection(active)
        base_entry_ref = entry_ref_from_identity(base_identity)
        if (document.document_entry_ref != base_entry_ref
                or self.entry_store.resolve_exact(document.document_entry_ref).identity != base_identity):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor native assembly Entry differs")
        sections=[]; bundles={}
        for raw,source,selection in zip(frozen_sections,sources,manifest.ordered_section_entries):
            row=TargetSectionPostWriteResult(section_id=raw["section_id"],artifact_state_ref=ArtifactStateRefSchema.model_validate(raw["artifact_state_ref"]),
                post_write_entry_ref=PostWriteEntryRefSchema.model_validate(raw["post_write_entry_ref"]),currentness_proof=CurrentnessProofSchema.model_validate(raw["currentness_proof"]),
                validation_completion_ref=ValidationCompletionRefSchema.model_validate(raw["validation_completion_ref"]),delivery_gate_decision_ref=DeliveryGateDecisionRefSchema.model_validate(raw["delivery_gate_decision_ref"]),
                control_decision_ref=PostWriteControlDecisionRefSchema.model_validate(raw["control_decision_ref"]))
            writer=self.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
            invocation=self.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
            entry=self.entry_store.resolve_exact(row.post_write_entry_ref)
            control=self.control_store.resolve_exact(row.control_decision_ref)
            self.validation_store.resolve_completion(row.validation_completion_ref)
            self.gate_store.resolve_exact(row.delivery_gate_decision_ref)
            record=self.artifact_store.resolve_exact(row.artifact_state_ref)
            package=self.research_runtime.validate_evidence_package_binding_current(source.active_evidence_package_binding)
            bundle=self.research_runtime.resolve_evidence_bundle(package.evidence_bundle_binding)
            candidate=self.artifact_service.materializer.materialize(
                artifact_id=f"section-artifact:{run_id}:{document_id}:{source.section.section_id}",
                artifact_version=1,artifact_scope=ArtifactScope.SECTION,
                canonical_payload_ref={"payload_kind":"section","section_id":source.section.section_id,
                    "section_title":source.section.section_title,"content":source.section.content},
                origin=InitialWriterArtifactOriginSchema(writer_execution_ref=source.writer_execution_ref,
                    writer_invocation_ref=source.writer_generation_invocation_ref))
            if (row.section_id!=source.section.section_id or writer.artifact is None
                    or any(getattr(writer.artifact, field)!=getattr(source.section, field)
                           for field in ("section_id", "section_title", "content"))
                    or record!=candidate or source.evidence.evidence_bundle is None
                    or evidence_bundle_sha256(source.evidence.evidence_bundle)!=evidence_bundle_sha256(bundle)
                    or entry.identity.writer_execution_ref!=source.writer_execution_ref
                    or entry.identity.writer_invocation_ref!=source.writer_generation_invocation_ref
                    or entry.identity.artifact_state_ref!=row.artifact_state_ref
                    or entry.identity.run_id!=run_id or entry.identity.document_id!=document_id
                    or entry.identity.section_id!=source.section.section_id
                    or entry.identity.requirement_ref!=requirement_ref
                    or entry.identity.quality_delivery_contract_ref!=active.quality_delivery_contract_ref
                    or entry.identity.generation_class!=invocation.generation_class
                    or entry.identity.evidence_binding.section_writer_snapshot_ref.evidence_bundle_sha256!=evidence_bundle_sha256(bundle)
                    or control.input_manifest.post_write_entry_ref!=row.post_write_entry_ref
                    or control.delivery_gate_decision_ref!=row.delivery_gate_decision_ref
                    or control.fresh_currentness_proof!=row.currentness_proof):
                raise TargetPostWriteCompositionError("budget DOCUMENT cursor Section native refs differ")
            if self.artifact_store.resolve_current(row.artifact_state_ref.artifact_id).artifact_ref!=record.artifact_ref:
                raise TargetPostWriteCompositionError("budget DOCUMENT cursor Section artifact stale")
            bound=entry.identity
            projection=ActiveSectionBindingProjectionSchema(run_id=run_id,document_id=document_id,
                section_id=bound.section_id,artifact_state_ref=bound.artifact_state_ref,evidence_binding=bound.evidence_binding,
                requirement_ref=bound.requirement_ref,quality_delivery_contract_ref=bound.quality_delivery_contract_ref,
                writer_execution_ref=bound.writer_execution_ref,writer_invocation_ref=bound.writer_invocation_ref,
                route_decision_ref=bound.route_decision_ref,generation_class=bound.generation_class)
            if passive_completed:
                restored_entry,currentness,completion_ref,gate_ref=self._read_budget_completed_control(row, projection)
            else:
                restored_entry,currentness,completion_ref,gate_ref=self._restore_terminal_control(row.control_decision_ref,
                    source_sections=(source,),scope=ValidationScope.SECTION,target_ref=row.section_id,expected_projection=projection)
            if (entry_ref_from_identity(restored_entry.identity)!=row.post_write_entry_ref or currentness!=row.currentness_proof
                    or completion_ref!=row.validation_completion_ref or gate_ref!=row.delivery_gate_decision_ref
                    or self.manifest_builder.selection_entry(section_key=row.section_id,
                        eligibility=self.section_eligibility.resolve(section_key=row.section_id,control_decision_ref=row.control_decision_ref))!=selection):
                raise TargetPostWriteCompositionError("budget DOCUMENT cursor native Section validation/selection differs")
            bundles[row.section_id]=bundle
            sections.append(row)
        evidence=self.binding_store.resolve_document_evidence_snapshot(document.document_evidence_ref)
        expected_sources=[]; expected_members=[]
        for selection in manifest.ordered_section_entries:
            candidate=selection.section_assembly_candidate
            if candidate is None:continue
            expected_sources.append(DocumentEvidenceSourceBindingSchema(logical_section_id=selection.section_key,
                section_artifact_ref=candidate.section_artifact_ref,section_post_write_entry_ref=candidate.section_post_write_entry_ref,
                section_evidence_snapshot_ref=candidate.section_evidence_snapshot_ref))
            bundle=bundles[selection.section_key]; by_id={item.evidence_id:item for item in bundle.items}
            for ordinal,evidence_id in enumerate(bundle.selected_evidence_ids):
                item=by_id[evidence_id]
                expected_members.append(DocumentEvidenceMemberRefSchema(logical_section_id=selection.section_key,
                    source_evidence_snapshot_ref=candidate.section_evidence_snapshot_ref,evidence_id=evidence_id,
                    evidence_item_sha256=canonical_sha256(item.model_dump(mode="json",round_trip=True)),source_member_ordinal=ordinal))
        record=self.artifact_store.resolve_exact(document.document_artifact_ref)
        if (evidence.document_logical_id!=manifest.document_logical_id or evidence.source_bindings!=tuple(expected_sources)
                or evidence.selected_members!=tuple(expected_members)
                or evidence.projection_contract_version!=self.document_lifecycle.evidence_projector.projection_contract_version
                or self.citation_integrity.committed_assembly(artifact_id=record.artifact_ref.artifact_id,
                    artifact_version=record.artifact_ref.artifact_version,manifest=manifest,expected_parent_ref=record.parent_artifact_ref)!=document.document_artifact_ref):
            raise TargetPostWriteCompositionError("budget DOCUMENT cursor assembly Artifact/Evidence sources differ")
        if budget_continuation is not None:
            identity={"task_id":task_id,"run_id":run_id,"section_id":sources[0].section.section_id}
            raw_binding=budget_continuation.model_dump(mode="json") if hasattr(budget_continuation,"model_dump") else budget_continuation
            if raw_binding["predecessor_result_ref"].startswith("artifact://claim-support/") and raw_binding["predecessor_result_ref"].endswith("/candidate"):
                native_candidate,_ = self.citation_grounding.store.get_json_with_reference(raw_binding["predecessor_result_ref"],task_id=task_id,run_id=run_id)
                identity={"task_id":task_id,"run_id":run_id,"section_id":native_candidate.section_id}
                refusal=self.citation_grounding.resolve_budget_refusal(receipt_ref=raw_binding["predecessor_result_ref"],expected_sha256=raw_binding["predecessor_result_sha256"],identity=identity)
                _,input_receipt=self.citation_grounding.store.get_json_with_reference(refusal["document_input_ref"],**refusal["document_identity"])
            else:
                _,input_receipt=self.citation_grounding.store.get_json_with_reference(
                    raw_binding["predecessor_result_ref"].removesuffix("/result")+"/input",**identity)
            self.citation_grounding.resolve_budget_continuation(budget_continuation,input_receipt["input_material"],identity,live=True)
            failed_ref=ArtifactStateRefSchema.model_validate(input_receipt["input_material"]["artifact_state_ref"])
            failed_record=self.artifact_store.resolve_exact(failed_ref)
            if (failed_ref.artifact_id!=document.document_artifact_ref.artifact_id
                    or self.artifact_store.resolve_current(failed_ref.artifact_id).artifact_ref!=failed_ref
                    or failed_record.parent_artifact_ref!=document.document_artifact_ref
                    or failed_record.origin!=record.origin
                    or ArtifactCanonicalPayloadMaterializer.business_payload(failed_record.canonical_payload_ref)
                       !=ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)):
                raise TargetPostWriteCompositionError("budget DOCUMENT cursor refused Artifact/current head differs")
        return TargetPostWriteMaterialization(requirement_ref,tuple(sections),document,sources)

    def _read_budget_completed_control(self, section, projection):
        """Resolve frozen completed Gate input without entering validation producers."""
        control = self.control_store.resolve_exact(section.control_decision_ref)
        manifest = control.input_manifest
        entry = self.entry_store.resolve_exact(manifest.post_write_entry_ref)
        identity = entry.identity
        policy = self.correction_policy_store.resolve_exact(manifest.correction_policy_ref)
        contract = self.contract_store.resolve_exact(identity.quality_delivery_contract_ref)
        conditions = tuple(sorted((RequiredDimensionConditionSchema(dimension_id=value)
            for value in self.REQUIRED_DIMENSIONS), key=lambda value:(value.dimension_id,value.effective_condition_ref())))
        if (control.control_contract_version != self.control_service.control_contract_version
                or policy.policy_id != "correction-policy://multi-agent-v3/w13-no-inference"
                or policy.policy_version != "v1" or policy.automatic_correction_enabled
                or policy.rules or policy.technical_retry_limit != 1
                or contract.contract_id != "quality-delivery://multi-agent-v3/target-v1"
                or contract.contract_version != "v1" or contract.gate_contract_version != "delivery_gate_v1"
                or contract.required_dimensions != conditions or contract.fact_policy_rules
                or contract.unmatched_fact_effect.value != "INFORMATIONAL"
                or contract.policy_projection_contract_version != "quality_policy_projection_v1"
                or contract.consistency_procedure_input != self.semantic_quality_config.procedure_input(requirement_ref=identity.requirement_ref)
                or contract.contract_metadata != {"authority":"target_post_write","missing_semantic_execution":"UNRESOLVED",
                    "semantic_quality_config_ref":self.semantic_quality_config.source_ref}):
            raise TargetPostWriteCompositionError("budget completed Section policy/contract differs")
        proof = self.currentness_resolver.prove(historical_entry_ref=manifest.post_write_entry_ref,
            active_projection=projection, comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
            active_materialized_post_write_entry_ref=manifest.post_write_entry_ref)
        if (identity.artifact_state_ref != manifest.artifact_state_ref
                or proof != section.currentness_proof or proof != control.fresh_currentness_proof
                or proof.currentness_state.value != "CURRENT"
                or self.usability_resolver.resolve(artifact_state_ref=identity.artifact_state_ref,
                    currentness_proof=proof) != control.artifact_usability_integrity_proof):
            raise TargetPostWriteCompositionError("budget completed Section currentness/usability differs")
        gate = self.gate_store.resolve_exact(section.delivery_gate_decision_ref)
        resolved = self.gate_service.input_guard.resolve(validation_run_id=section.validation_completion_ref.validation_run_id,
            validation_completion_ref=section.validation_completion_ref,
            quality_delivery_contract_ref=identity.quality_delivery_contract_ref, currentness_proof=proof)
        if (control.validation_run_id != resolved.validation_run.validation_run_id
                or resolved.validation_run.subject_key.validation_scope is not ValidationScope.SECTION
                or resolved.validation_run.subject_key.target_selector.target_ref != section.section_id
                or resolved.post_write_entry != entry or gate.currentness_proof != proof
                or resolved.manifest != gate.gate_input_manifest
                or resolved.quality_policy_projection != gate.quality_policy_projection):
            raise TargetPostWriteCompositionError("budget completed Section native Gate input differs")
        return entry, proof, section.validation_completion_ref, section.delivery_gate_decision_ref

    def _read_budget_citation_sources(self, material, artifact_ref, manifest):
        """Passive exact committed Citation snapshot; no ordinary lifecycle read grant."""
        from .post_write_citation_projection import CitationIntegritySnapshot, PARSER_ID, PARSER_VERSION
        from apps.enterprise_document.schemas.post_write.foundation import ValidationIntermediateKind
        validation_run_id, subject_hash = material["validation_run_id"], material["subject_hash"]
        item = "citation:integrity:" + artifact_ref.artifact_hash
        plan = self.citation_integrity.stage_plan()
        record = self.validation_store.find_validator_execution_for_item(validation_run_id=validation_run_id,
            validator_id=plan.validator_id, expected_item_ref=item)
        if (record is None or record.subject_hash != subject_hash or record.validation_run_id != validation_run_id
                or record.validator_id != plan.validator_id or record.validator_version != plan.validator_version
                or record.stage_plan_ref != plan.stage_plan_ref or record.stage_plan_version != plan.stage_plan_version
                or record.terminal_execution_state is not ValidatorTerminalExecutionState.COMPLETED
                or len(record.intermediate_refs) != 1 or len(record.expected_item_accounting) != 1):
            raise TargetPostWriteCompositionError("budget DOCUMENT committed Citation execution differs")
        intermediate = record.intermediate_refs[0]
        snapshot = self.validation_intermediate_runtime.resolve_typed(intermediate, CitationIntegritySnapshot)
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(self.artifact_store.resolve_exact(artifact_ref).canonical_payload_ref)
        catalog = self.citation_integrity._bound_document_projection(payload, manifest).catalog
        digest = canonical_sha256(snapshot.model_dump(mode="json"))
        provenance = ("citation-integrity-snapshot://sha256/" + digest,)
        envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=self.validation_view_resolver).build(
            validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
            stage_plan=plan, applicable_expected_validation_item_refs=(item,), exact_authority_refs=provenance)
        accounting = record.expected_item_accounting[0]
        if (intermediate.validation_run_id != validation_run_id
                or intermediate.intermediate_kind is not ValidationIntermediateKind.TARGET_ACCOUNTING
                or snapshot.artifact_state_ref != artifact_ref.model_dump(mode="json")
                or snapshot.validation_subject_hash != subject_hash or snapshot.catalog != catalog
                or snapshot.catalog.scope != "DOCUMENT" or snapshot.parser_id != PARSER_ID or snapshot.parser_version != PARSER_VERSION
                or digest != material["citation_source_hash"]
                or [ref.model_dump(mode="json") for ref in record.produced_fact_refs] != material["prerequisite_refs"]
                or list(snapshot.occurrences) != material["occurrences"]
                or record.input_hash != envelope.input_hash
                or record.validator_execution_ref != "validator-execution:citation-integrity:"+canonical_sha256({"input_hash":envelope.input_hash,"item_ref":item})
                or accounting.expected_validation_item_ref != item or accounting.execution_state is not ExpectedItemExecutionState.COMPLETED
                or accounting.fact_refs != record.produced_fact_refs or accounting.provenance_refs != provenance):
            raise TargetPostWriteCompositionError("budget DOCUMENT frozen Citation input/accounting differs")
        facts = tuple(self.validation_store.resolve_fact(ref) for ref in record.produced_fact_refs)
        targets = {row["target"]["occurrence_ref"] for row in snapshot.occurrences}
        if (facts != self.citation_integrity.facts(snapshot,validation_run_id=validation_run_id,subject_hash=subject_hash)
                or bool(targets) != bool(facts) or {fact.target_ref for fact in facts} != targets
                or any(fact.validation_run_id != validation_run_id or fact.subject_hash != subject_hash
                    or fact.family.value != "CITATION_INTEGRITY"
                    or fact.payload.get("alias_catalog_ref") != "citation-alias-catalog://sha256/"+catalog.catalog_hash for fact in facts)):
            raise TargetPostWriteCompositionError("budget DOCUMENT Citation Fact sources differ")
        return snapshot

    def validate_budget_document_current(self, refusal, *, materialization_ref, task_id, run_id,
                                         document_id, project_input, section_execution_results):
        """Verify an immutable DOCUMENT hold against native heads, including PAUSED.

        This read grants no lifecycle, Budget, commit or dispatch authority.
        """
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus
        from apps.enterprise_document.schemas.post_write.quality import UpstreamSIF07ClaimAuthorityResolutionSchema
        from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer
        identity = {key:refusal[key] for key in ("task_id","run_id","section_id")}
        if identity["task_id"] != task_id or identity["run_id"] != run_id:
            raise TargetPostWriteCompositionError("budget DOCUMENT refusal belongs to another Run")
        native = self.citation_grounding.resolve_budget_refusal(receipt_ref=refusal["native_denial_ref"],
            expected_sha256=refusal["native_denial_sha256"], identity=identity)
        if self.citation_grounding.normalize_budget_refusal(native) != self.citation_grounding.normalize_budget_refusal(refusal):
            raise TargetPostWriteCompositionError("budget DOCUMENT refusal packet differs from native proof")
        preimage = self.lifecycle_service.budget_preimage(run_id)
        run = preimage.run
        if run.status not in {RunLifecycleStatus.RUNNING,RunLifecycleStatus.PAUSED}:
            raise TargetPostWriteCompositionError("budget DOCUMENT source Run is unavailable")
        materialization = self._resolve_budget_materialization(materialization_ref, task_id=task_id, run_id=run_id,
            document_id=document_id, project_input=project_input, section_execution_results=section_execution_results,
            passive_completed=True)
        input_ref = refusal["document_input_ref"] if refusal.get("operation_kind") == "DOCUMENT_SUPPORT" else refusal["input_ref"]
        input_identity = refusal["document_identity"] if refusal.get("operation_kind") == "DOCUMENT_SUPPORT" else identity
        _, input_receipt = self.citation_grounding.store.get_json_with_reference(input_ref, **input_identity)
        material = input_receipt["input_material"]
        failed_ref = ArtifactStateRefSchema.model_validate(material["artifact_state_ref"])
        failed = self.artifact_store.resolve_exact(failed_ref)
        base = self.artifact_store.resolve_exact(materialization.document.document_artifact_ref)
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(failed.canonical_payload_ref)
        validation = self.validation_store.resolve_run_exact(material["validation_run_id"])
        entry = self.entry_store.resolve_exact(validation.subject_key.post_write_entry_ref)
        if (failed_ref.artifact_scope is not ArtifactScope.DOCUMENT or failed_ref != ArtifactStateRefSchema.model_validate(refusal["artifact_state_ref"])
                or failed_ref.artifact_id != base.artifact_ref.artifact_id
                or self.artifact_store.resolve_current(failed_ref.artifact_id).artifact_ref != failed_ref
                or failed.parent_artifact_ref != base.artifact_ref or failed.origin != base.origin
                or payload != ArtifactCanonicalPayloadMaterializer.business_payload(base.canonical_payload_ref)
                or payload != material["visible_artifact"] or payload.get("payload_kind") != "document"
                or validation.subject_key.subject_hash != material["subject_hash"]
                or validation.subject_key.validation_scope is not ValidationScope.DOCUMENT
                or validation.subject_key.target_selector.target_ref != document_id
                or validation.execution_state is not ValidationRunState.RUNNING
                or entry.identity.artifact_state_ref != failed_ref or entry.identity.run_id != run_id
                or entry.identity.document_id != document_id or entry.identity.requirement_ref != materialization.requirement_ref
                or entry.identity.assembly_input_manifest_ref != materialization.document.manifest_ref
                or entry.identity.evidence_binding != materialization.document.active_projection.evidence_binding
                or entry.identity.quality_delivery_contract_ref != materialization.document.active_projection.quality_delivery_contract_ref):
            raise TargetPostWriteCompositionError("budget DOCUMENT refused Artifact/source head differs")
        claim_projection = self.citation_grounding.claim_projection
        snapshot = claim_projection.resolve_committed_source_snapshot(post_write=self,
            validation_run_id=material["validation_run_id"], subject_hash=material["subject_hash"],
            artifact_ref=failed_ref, scope=ValidationScope.DOCUMENT, validate_canonical_results=True)
        if (canonical_sha256(snapshot.model_dump(mode="json")) != material["claim_source_hash"]
                or snapshot.assembly_input_manifest_ref != materialization.document.manifest_ref.model_dump(mode="json")
                or tuple(section.section_id for section in snapshot.sections)
                   != tuple(source.section.section_id for source in materialization.source_sections)):
            raise TargetPostWriteCompositionError("budget DOCUMENT immutable Claim source snapshot differs")
        generations = []
        for section, source in zip(snapshot.sections, materialization.source_sections):
            lineage = source.active_execution_lineage
            current = self.lifecycle_service.resolve_active_lineage(run_id,lineage.attempt_scope_ref)
            if (current.attempt_id != lineage.attempt_id or current.input_snapshot_version != lineage.input_snapshot_version
                    or current.attempt_lifecycle_revision != lineage.attempt_lifecycle_revision
                    or current.attempt_status is not AttemptLifecycleStatus.RUNNING
                    or current.run_status not in {RunLifecycleStatus.RUNNING,RunLifecycleStatus.PAUSED}
                    or section.writer_execution_ref != source.writer_execution_ref
                    or section.writer_invocation_ref != source.writer_generation_invocation_ref
                    or section.section_title != source.section.section_title or section.body_sha256 != canonical_sha256(source.section.content)
                    or section.paragraph_manifest != source.section_paragraph_manifest
                    or section.coverage_proof_refs != tuple(source.canonical_claim_coverage_proof_refs)
                    or tuple(row.verification_result_ref for row in section.verification_records) != tuple(source.canonical_claim_verification_refs)
                    or claim_projection.paragraph_runtime.load_manifest(section.paragraph_manifest.manifest_ref) != section.paragraph_manifest
                    or tuple(claim_projection.claim_runtime.resolve_claim_coverage(ref) for ref in section.coverage_proof_refs) != section.coverage_records):
                raise TargetPostWriteCompositionError("budget DOCUMENT Section/Child source refs are stale")
            body = source.section.content
            cursor = 0
            for member, span in zip(section.paragraph_manifest.members, section.paragraph_body_spans):
                binding = claim_projection.paragraph_runtime.resolve_paragraph_binding(
                    logical_ref=member.logical_ref,manifest_ref=section.paragraph_manifest.manifest_ref)
                if binding is None or binding.version_ref != member:
                    raise TargetPostWriteCompositionError("budget DOCUMENT Paragraph head differs")
                content = claim_projection.paragraph_runtime.validate_paragraph_binding_current(binding)
                start,end=span["start_offset"],span["end_offset"]
                invocation=self.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
                if (span["paragraph_version_ref"] != member.model_dump(mode="json") or start < cursor
                        or body[cursor:start] != span["preceding_whitespace"] or body[cursor:start].strip()
                        or body[start:end] != content.text or content.source_binding.writer_subject_ref != invocation.writer_subject_ref
                        or content.source_binding.write_instance_id != invocation.write_instance_id
                        or content.source_binding.route_decision_ref != invocation.route_decision_ref):
                    raise TargetPostWriteCompositionError("budget DOCUMENT Paragraph/Writer bytes differ")
                # The normal source-validator entry also grants a commit, which
                # PAUSED cannot receive. Reuse only its pure native source reads.
                from .paragraph_composition import TargetParagraphRuntimeValidators
                validators = getattr(claim_projection.paragraph_runtime.source_validator,"__self__",None)
                if not isinstance(validators,TargetParagraphRuntimeValidators):
                    raise TargetPostWriteCompositionError("budget DOCUMENT native Paragraph source reader unavailable")
                writer_source = content.source_binding
                route = validators.route_decision_store.resolve_exact(writer_source.route_decision_ref)
                if (route.writer_subject_ref != writer_source.writer_subject_ref
                        or route.write_instance_id != writer_source.write_instance_id
                        or route.binding_ref != writer_source.writer_gate_binding_ref):
                    raise TargetPostWriteCompositionError("budget DOCUMENT Paragraph Route/Gate source differs")
                _, paragraph_invocation = validators._resolve_writer_invocation(writer_source)
                validators._validate_plan_fact(writer_source)
                validators._validate_evidence_package(writer_source)
                validators._validate_context(writer_source,paragraph_invocation)
                cursor=end
            if (len(section.paragraph_manifest.members) != len(section.paragraph_body_spans)
                    or body[cursor:].strip() or (section.paragraph_body_spans
                        and section.paragraph_body_spans[-1].get("trailing_whitespace","") != body[cursor:])):
                raise TargetPostWriteCompositionError("budget DOCUMENT Paragraph coverage differs")
            generation = claim_projection.claim_runtime.resolve_current_generation_sources(run_id=run_id,
                section_id=section.section_id,paragraph_version_refs=section.paragraph_manifest.members)
            generations.append((section, generation))
            if (tuple(row[0] for row in generation) != section.claim_records
                    or tuple(row[3] for row in generation) != section.verification_records
                    or tuple(claim_projection.claim_runtime.resolve_binding_facts(row.binding_facts_ref)
                        for row in section.verification_records) != section.binding_records
                    or tuple(claim_projection.support.runtime.resolve_support(row.claim_evidence_support_ref)
                        for row in section.verification_records) != section.support_records):
                raise TargetPostWriteCompositionError("budget DOCUMENT active Claim/Verifier/Support sources differ")
            for claim,binding,support in zip(section.claim_records,section.binding_records,section.support_records):
                sources = claim_projection.verifier.sources._resolve_source_material(
                    claim_projection.claim_runtime._binding_record_candidate(binding),current,read_only=True)
                claim_projection.verifier._validate_resolved_sources(
                    claim_projection.claim_runtime._binding_record_candidate(binding),binding,support,sources)
        records = {row.claim_ref.claim_id:row for section in snapshot.sections for row in section.claim_records}
        targets = ArtifactClaimTargetMaterializer.materialize(
            authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(state="UPSTREAM_SIF07_CLAIM_AUTHORITY_PRESENT",
                claim_refs=tuple(records[key].claim_ref for key in sorted(records)),resolution_reason_ref="claim-authority://committed-343"),
            artifact_state_ref=failed_ref,validation_subject_hash=material["subject_hash"],
            semantic_version=self.citation_grounding.CLAIM_PROJECTION_CONTRACT_VERSION,resolved_claim_records=records) if records else ()
        if [row.model_dump(mode="json") for row in targets] != material["candidate_claim_targets"]:
            raise TargetPostWriteCompositionError("budget DOCUMENT exact canonical Claim candidate domain differs")
        manifest = self.binding_store.resolve_assembly_manifest(materialization.document.manifest_ref)
        self._read_budget_citation_sources(material,failed_ref,manifest)
        if self.lifecycle_service.budget_preimage(run_id) != preimage:
            raise TargetPostWriteCompositionError("budget DOCUMENT Run/current Children changed during read")
        for section, generation in generations:
            if claim_projection.claim_runtime.resolve_current_generation_sources(run_id=run_id,
                    section_id=section.section_id,paragraph_version_refs=section.paragraph_manifest.members) != generation:
                raise TargetPostWriteCompositionError("budget DOCUMENT Claim source heads changed during read")
        return materialization

    @_run_write_context
    def complete_target_post_write(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        section_execution_results: tuple[SectionExecutionResultSchema, ...],
        budget_continuation=None,
        budget_materialization_ref=None,
    ) -> TargetPostWriteCompletion:
        if self.lifecycle_service is not None:
            terminal = self.lifecycle_service.load_run_lifecycle(run_id)
            if terminal.status is RunLifecycleStatus.SUCCESS:
                memory = getattr(self, "memory_composition", None)
                if memory is not None:
                    memory.reconcile_run(run_id)
                return self._restore_terminal_target(task_id=task_id,run_id=run_id,document_id=document_id,
                    project_input=project_input,section_execution_results=section_execution_results,terminal=terminal)
            if terminal.status is RunLifecycleStatus.FINALIZING:
                return self._resume_finalization(terminal, task_id=task_id, document_id=document_id,
                    project_input=project_input, section_execution_results=section_execution_results)
            if terminal.status is not RunLifecycleStatus.RUNNING:
                raise TargetPostWriteCompositionError("Target Post-write requires live RUNNING or exact terminal SUCCESS")
        if (budget_continuation is None) != (budget_materialization_ref is None):
            raise TargetPostWriteCompositionError("budget DOCUMENT continuation requires exact materialization")
        materialization = self.restore_budget_materialization(budget_materialization_ref,
            task_id=task_id,run_id=run_id,document_id=document_id,project_input=project_input,
            section_execution_results=section_execution_results,budget_continuation=budget_continuation) if budget_continuation is not None else self.materialize_sections_and_document(
            task_id=task_id,
            run_id=run_id,
            document_id=document_id,
            project_input=project_input,
            section_execution_results=section_execution_results,
        )
        final_document = assessment_ref = terminal = None
        try:
            final_document = self._finalize_document_output_and_control(
                task_id=task_id,
                run_id=run_id,
                document_id=document_id,
                project_input=project_input,
                materialization=materialization,
                **({"budget_continuation":budget_continuation} if budget_continuation is not None else {}),
            )
            memory = getattr(self, "memory_composition", None)
            if memory is not None:
                memory.reconcile_run(run_id)
            assessment_ref, terminal = self._commit_and_terminalize_run(
                run_id=run_id,
                task_id=task_id, document_id=document_id, project_input=project_input,
                materialization=materialization,
                final_document=final_document,
            )
            published, publication_failure = self._attempt_review_safe_publication(
                task_id=task_id, run_id=run_id, document_id=document_id,
                final_document=final_document, assessment_ref=assessment_ref,
                terminal_run=terminal,
            )
            return TargetPostWriteCompletion(
                materialization=materialization,
                final_document=final_document,
                run_completion_assessment_ref=assessment_ref,
                terminal_run=terminal,
                published_release=published,
                publication_failure=publication_failure,
            )
        except Exception as exc:
            if getattr(exc,"budget_failure_receipt",None) is not None:
                try:
                    refusal=exc.budget_failure_receipt
                    if not refusal.get("phase_proof_sha256"):
                        raise TargetPostWriteCompositionError("Budget hold requires positive native first-dispatch phase proof")
                    identity={key:refusal[key] for key in ("task_id","run_id","section_id")}
                    self.citation_grounding.resolve_budget_refusal(receipt_ref=refusal["native_denial_ref"],
                        expected_sha256=refusal["native_denial_sha256"],identity=identity)
                    exc.budget_materialization_ref=self.capture_budget_materialization(materialization,
                        task_id=task_id,run_id=run_id,document_id=document_id,project_input=project_input)
                except Exception as proof_error:
                    exc.budget_hold_validation_error={"error_type":type(proof_error).__name__,"message":str(proof_error)}
            completed = (TargetPostWriteCompletion(materialization=materialization,
                final_document=final_document, run_completion_assessment_ref=assessment_ref,
                terminal_run=terminal, published_release=None)
                if final_document is not None and assessment_ref is not None and terminal is not None else None)
            raise TargetPostWriteInterrupted(exc, materialization.section_results, completed) from exc

    @_run_write_context
    def _section(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        result: SectionExecutionResultSchema,
        requirement_ref: RequirementRefSchema,
        quality_contract_ref,
    ) -> TargetSectionPostWriteResult:
        exact = SectionExecutionResultSchema.model_validate(result)
        if (
            exact.writer_execution_ref is None
            or exact.writer_generation_invocation_ref is None
            or exact.active_evidence_package_binding is None
            or exact.section_paragraph_manifest is None
            or not exact.canonical_claim_coverage_proof_refs
        ):
            raise TargetPostWriteCompositionError(
                "Target Section result lacks canonical Writer/Evidence/Paragraph/Claim refs"
            )
        writer_result = self.writer_execution_store.resolve_writer_execution(
            exact.writer_execution_ref
        )
        invocation = (
            self.writer_execution_store.resolve_writer_generation_invocation(
                exact.writer_generation_invocation_ref
            )
        )
        if writer_result.artifact is None:
            raise TargetPostWriteCompositionError(
                "Writer execution has no canonical Section artifact"
            )
        if writer_result.artifact.section_id != exact.section.section_id:
            raise TargetPostWriteCompositionError(
                "Section result differs from durable Writer artifact identity"
            )
        package = self.research_runtime.validate_evidence_package_binding_current(
            exact.active_evidence_package_binding
        )
        bundle = self.research_runtime.resolve_evidence_bundle(
            package.evidence_bundle_binding
        )
        bundle_hash = evidence_bundle_sha256(bundle)
        if (
            exact.evidence.evidence_bundle is None
            or evidence_bundle_sha256(exact.evidence.evidence_bundle) != bundle_hash
        ):
            raise TargetPostWriteCompositionError(
                "Section result Evidence differs from current canonical EvidencePackage"
            )

        artifact_ref = self.artifact_service.create_initial(
            artifact_id=(
                f"section-artifact:{run_id}:{document_id}:"
                f"{exact.section.section_id}"
            ),
            artifact_scope=ArtifactScope.SECTION,
            canonical_payload_ref={
                "payload_kind": "section",
                "section_id": exact.section.section_id,
                "section_title": exact.section.section_title,
                "content": exact.section.content,
            },
            origin=InitialWriterArtifactOriginSchema(
                writer_execution_ref=exact.writer_execution_ref,
                writer_invocation_ref=exact.writer_generation_invocation_ref,
            ),
        )
        evidence_ref = EvidenceSnapshotRefSchema(
            evidence_bundle_schema_version=bundle.schema_version,
            evidence_bundle_sha256=bundle_hash,
            writer_invocation_ref=exact.writer_generation_invocation_ref,
        )
        evidence_binding = PostWriteEvidenceBindingRef(
            binding_kind=PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT,
            section_writer_snapshot_ref=evidence_ref,
        )
        route_ref = exact.writer_execution_ref.logical_execution_key.route_decision_ref
        entry_ref = self.section_entry_assembler.assemble_and_persist(
            run_id=run_id,
            document_id=document_id,
            section_id=exact.section.section_id,
            artifact_state_ref=artifact_ref,
            writer_execution_ref=exact.writer_execution_ref,
            writer_invocation_ref=exact.writer_generation_invocation_ref,
            route_decision_ref=route_ref,
            evidence_binding=evidence_binding,
            requirement_ref=requirement_ref,
            quality_delivery_contract_ref=quality_contract_ref,
            generation_class=invocation.generation_class,
        )
        projection = ActiveSectionBindingProjectionSchema(
            run_id=run_id,
            document_id=document_id,
            section_id=exact.section.section_id,
            artifact_state_ref=artifact_ref,
            evidence_binding=evidence_binding,
            requirement_ref=requirement_ref,
            quality_delivery_contract_ref=quality_contract_ref,
            writer_execution_ref=exact.writer_execution_ref,
            writer_invocation_ref=exact.writer_generation_invocation_ref,
            route_decision_ref=route_ref,
            generation_class=invocation.generation_class,
        )
        currentness = self.currentness_resolver.prove(
            historical_entry_ref=entry_ref,
            active_projection=projection,
            comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
            active_materialized_post_write_entry_ref=entry_ref,
        )
        completion_ref, gate_ref = self._complete_validation(
            run_id=run_id,
            entry_ref=entry_ref,
            artifact_ref=artifact_ref,
            scope=ValidationScope.SECTION,
            target_ref=exact.section.section_id,
            quality_contract_ref=quality_contract_ref,
            currentness_proof=currentness,
            requirement_ref=requirement_ref,
            source_sections=(exact,),
            evidence_snapshot_ref=evidence_ref,
        )
        control_ref = self._control(
            task_id=task_id,
            run_id=run_id,
            artifact_ref=artifact_ref,
            gate_ref=gate_ref,
            currentness=currentness,
            generation_class=invocation.generation_class,
        )
        return TargetSectionPostWriteResult(
            section_id=exact.section.section_id,
            artifact_state_ref=artifact_ref,
            post_write_entry_ref=entry_ref,
            currentness_proof=currentness,
            validation_completion_ref=completion_ref,
            delivery_gate_decision_ref=gate_ref,
            control_decision_ref=control_ref,
        )

    @_run_write_context
    @_observe_postwrite("S-MATERIALIZE")
    def materialize_sections_and_document(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        section_execution_results: tuple[SectionExecutionResultSchema, ...],
    ) -> TargetPostWriteMaterialization:
        if not section_execution_results:
            raise TargetPostWriteCompositionError(
                "Target Post-write requires completed Section results"
            )
        exact_project = ProjectInputSchema.model_validate(project_input)
        requirement_ref = self._materialize_requirement(
            project_input=exact_project,
            run_id=run_id,
        )
        quality_contract_ref = self._quality_contract_ref(requirement_ref)
        completed_sections = []
        try:
            for result in section_execution_results:
                completed_sections.append(self._section(
                    task_id=task_id, run_id=run_id, document_id=document_id,
                    result=result, requirement_ref=requirement_ref,
                    quality_contract_ref=quality_contract_ref,
                ))
        except Exception as exc:
            raise TargetPostWriteInterrupted(exc, completed_sections) from exc
        sections = tuple(completed_sections)

        try:
            entries: list[AssemblySectionEntrySchema] = []
            for section in sections:
                eligibility = self.section_eligibility.resolve(
                    section_key=section.section_id,
                    control_decision_ref=section.control_decision_ref,
                )
                entries.append(
                    self.manifest_builder.selection_entry(
                        section_key=section.section_id,
                        eligibility=eligibility,
                    )
                )
            document = self.document_lifecycle.materialize(
                run_id=run_id,
                document_id=document_id,
                document_logical_id=f"document-logical:{run_id}:{document_id}",
                required_section_keys=tuple(
                    item.section.section_id for item in section_execution_results
                ),
                ordered_section_entries=tuple(entries),
                requirement_ref=requirement_ref,
                quality_delivery_contract_ref=quality_contract_ref,
            )
            return TargetPostWriteMaterialization(
                requirement_ref=requirement_ref,
                section_results=sections,
                document=document,
                source_sections=tuple(section_execution_results),
            )
        except Exception as exc:
            raise TargetPostWriteInterrupted(exc, sections) from exc


__all__ = [
    "TargetFinalDocumentResult",
    "TargetPostWriteCompletion",
    "TargetPostWriteComposition",
    "TargetPostWriteCompositionError",
    "TargetPostWriteMaterialization",
    "TargetSectionPostWriteResult",
]
