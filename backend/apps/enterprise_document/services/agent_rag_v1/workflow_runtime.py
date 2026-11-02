"""Wave-13 composition helpers for canonical pre-write Workflow sources.

This module owns no new business truth.  It lowers already-authoritative Task /
ProjectInput normalization output into the existing SIF-04 FactSnapshot and
SectionPlan runtimes, resolves canonical sources before replay, and returns exact
refs for later Workflow composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    CanonicalFactRecordSchema,
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
    FactSourceBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    DocumentPlanSchema,
    EvidenceRequirementsSourceRefSchema,
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import (
    SectionPlanCommitOperations,
    SectionPlanRuntime,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
    AttemptLifecycleStatus,
    RunIdentitySchema,
    RunLifecycleRecordSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundRefSchema,
)
from apps.enterprise_document.services.project_input_normalization.fact_contract import (
    PROJECT_INPUT_FACT_NORMALIZATION_VERSION,
    project_input_fact_normalization_version,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef
from task.task_manager import JsonlTaskManager


PLANNING_CONTRACT_VERSION = "workflow-cutover-section-plan-v2"
# Compatibility export for callers that historically imported the V1 constant.
# Runtime snapshots select the exact version from their bound ProjectInput.
FACT_NORMALIZATION_CONTRACT_VERSION = PROJECT_INPUT_FACT_NORMALIZATION_VERSION
TASK_PROJECT_INPUT_SOURCE_PREFIX = "project-input://task/"
TASK_REQUEST_VERSION_PREFIX = "task-request://sha256/"


class WorkflowCutoverIntegrityError(RuntimeError):
    """Canonical source/currentness conflict during Workflow cutover."""


@dataclass(frozen=True)
class CanonicalPlanningProjection:
    """Exact canonical refs returned by existing owner runtimes."""

    fact_snapshot_binding: FactSnapshotBindingSchema
    plan_version_ref: ArtifactVersionRefSchema
    section_plan_bindings: tuple[SectionPlanBindingSchema, ...]

    def binding_for_section(self, section_id: str) -> SectionPlanBindingSchema:
        normalized = str(section_id or "").strip()
        matches = tuple(
            item for item in self.section_plan_bindings if item.section_id == normalized
        )
        if len(matches) != 1:
            raise WorkflowCutoverIntegrityError(
                "canonical SectionPlan binding is missing or ambiguous"
            )
        return matches[0]


class TaskRequestFactSourceResolver:
    """Resolve Fact material sources only through immutable Task authorization identity."""

    def __init__(self, task_manager: JsonlTaskManager) -> None:
        self.task_manager = task_manager

    def __call__(self, binding: FactSourceBindingSchema) -> bool:
        exact = FactSourceBindingSchema.model_validate(binding)
        if exact.source_type != "project_input":
            return False
        if not exact.source_ref.startswith(TASK_PROJECT_INPUT_SOURCE_PREFIX):
            return False
        task_id = exact.source_ref[len(TASK_PROJECT_INPUT_SOURCE_PREFIX) :]
        if not task_id:
            return False
        try:
            identity = self.task_manager.get_authorization_metadata(task_id)
        except Exception:
            return False
        expected_version = (
            TASK_REQUEST_VERSION_PREFIX + identity.canonical_task_request_sha256
        )
        return (
            exact.source_ref == TASK_PROJECT_INPUT_SOURCE_PREFIX + identity.task_id
            and exact.source_version_ref == expected_version
        )


class TargetWorkflowRuntime:
    """Composition-only coordinator over canonical Lifecycle/Fact/Plan owners."""

    def __init__(
        self,
        *,
        task_manager: JsonlTaskManager,
        lifecycle_service: RunAttemptLifecycleService,
        fact_snapshot_runtime: FactSnapshotRuntime,
        section_plan_runtime: SectionPlanRuntime,
        requirements: EvidenceRequirementsSnapshotSchema,
        memory_composition=None,
    ) -> None:
        self.task_manager = task_manager
        self.memory_composition = memory_composition
        self.lifecycle_service = lifecycle_service
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.section_plan_runtime = section_plan_runtime
        self.requirements = EvidenceRequirementsSnapshotSchema.model_validate(
            requirements
        )
        self.section_dependencies = None

    @staticmethod
    def _operation(
        *,
        operation_kind: str,
        run_id: str,
        operation_scope_ref: str,
        material: dict,
    ) -> LogicalOperationRef:
        return LogicalOperationRef(
            logical_operation_id=(
                "workflow-cutover-op:"
                + canonical_sha256(
                    {
                        "operation_kind": operation_kind,
                        "run_id": run_id,
                        "operation_scope_ref": operation_scope_ref,
                        "material": material,
                    }
                )
            ),
            operation_kind=operation_kind,
            run_id=run_id,
            operation_scope_ref=operation_scope_ref,
        )

    def ensure_running_run(self, *, task_id: str, run_id: str) -> RunLifecycleRecordSchema:
        identity = self.task_manager.get_authorization_metadata(task_id)
        if identity.run_id != run_id:
            raise WorkflowCutoverIntegrityError(
                "Task immutable identity does not match Workflow run_id"
            )
        expected = RunIdentitySchema(
            run_id=identity.run_id,
            task_id=identity.task_id,
            tenant_id=identity.tenant_id,
            canonical_request_sha256=identity.canonical_task_request_sha256,
        )
        try:
            current = self.lifecycle_service.load_run(run_id)
        except KeyError:
            current = self.lifecycle_service.claim_new_run(expected)
        if current.identity != expected:
            raise WorkflowCutoverIntegrityError(
                "canonical Run identity conflicts with immutable Task request"
            )
        if current.status is RunLifecycleStatus.PENDING:
            current = self.lifecycle_service.transition_run(
                run_id,
                current.lifecycle_revision,
                RunLifecycleStatus.RUNNING,
            )
        if current.status is not RunLifecycleStatus.RUNNING:
            raise WorkflowCutoverIntegrityError(
                "Workflow execution requires canonical RUNNING Run"
            )
        return current

    def _task_project_input_source(
        self,
        *,
        task_id: str,
        run_id: str,
    ) -> FactSourceBindingSchema:
        identity = self.task_manager.get_authorization_metadata(task_id)
        if identity.run_id != run_id:
            raise WorkflowCutoverIntegrityError(
                "ProjectInput source task/run identity mismatch"
            )
        return FactSourceBindingSchema(
            source_type="project_input",
            source_ref=TASK_PROJECT_INPUT_SOURCE_PREFIX + task_id,
            source_version_ref=(
                TASK_REQUEST_VERSION_PREFIX
                + identity.canonical_task_request_sha256
            ),
        )

    def _fact_content(
        self,
        *,
        task_id: str,
        run_id: str,
        structured_facts: Iterable[StructuredFactSchema],
        project_input_version: str,
    ) -> FactSnapshotContentSchema:
        source = self._task_project_input_source(task_id=task_id, run_id=run_id)
        task = self.task_manager.get_task(task_id)
        identity = self.task_manager.get_authorization_metadata(task_id)
        if (
            task.run_id != run_id
            or task.canonical_task_request_sha256
            != identity.canonical_task_request_sha256
        ):
            raise WorkflowCutoverIntegrityError(
                "ProjectInput normalization Task identity is not verified"
            )
        try:
            expected_profile = EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION[
                project_input_version
            ]
        except KeyError as exc:
            raise WorkflowCutoverIntegrityError(
                "unsupported Task-bound ProjectInput version"
            ) from exc
        if (
            task.metadata.get("execution_profile") != expected_profile
            and not (
                project_input_version == "project_input_v1"
                and "execution_profile" not in task.metadata
            )
        ):
            raise WorkflowCutoverIntegrityError(
                "Task execution profile does not match ProjectInput version"
            )
        try:
            normalization_version = project_input_fact_normalization_version(
                project_input_version
            )
        except ValueError as exc:
            raise WorkflowCutoverIntegrityError(
                "unsupported or invalid Task-bound ProjectInput normalization"
            ) from exc
        canonical_facts: list[CanonicalFactRecordSchema] = []
        for raw in structured_facts:
            fact = StructuredFactSchema.model_validate(raw)
            if fact.task_id != task_id or fact.run_id != run_id:
                raise WorkflowCutoverIntegrityError(
                    "StructuredFact task/run identity mismatch"
                )
            # The live StructuredFactExtractor is a deterministic ProjectInput
            # projection.  Do not invent standalone durable authority for source_ids.
            if (
                fact.source_type != "project_input"
                or tuple(fact.source_ids) != (task_id,)
            ):
                raise WorkflowCutoverIntegrityError(
                    "StructuredFact source is not exact ProjectInput Task authority"
                )
            canonical_facts.append(
                CanonicalFactRecordSchema(
                    fact_id=fact.fact_id,
                    fact_type=fact.fact_type,
                    normalized_value=fact.content,
                    source_bindings=[source],
                    confidence=fact.confidence,
                )
            )
        return self.fact_snapshot_runtime.build_content_from_authoritative_sources(
            normalization_contract_version=normalization_version,
            source_bindings=[source],
            facts=canonical_facts,
        )

    def ensure_fact_snapshot(
        self,
        *,
        task_id: str,
        run_id: str,
        structured_facts: Iterable[StructuredFactSchema],
        project_input_version: str = "project_input_v1",
    ) -> FactSnapshotBindingSchema:
        self.ensure_running_run(task_id=task_id, run_id=run_id)
        logical = self.fact_snapshot_runtime.logical_ref_for_scope(
            run_id=run_id,
            domain_scope_ref=f"project:{task_id}",
        )
        candidate = self._fact_content(
            task_id=task_id,
            run_id=run_id,
            structured_facts=structured_facts,
            project_input_version=project_input_version,
        )
        current = self.fact_snapshot_runtime.resolve_current_binding(logical)
        if current is not None:
            existing = self.fact_snapshot_runtime.read_binding(
                current,
                require_current=True,
            )
            if existing != candidate:
                raise WorkflowCutoverIntegrityError(
                    "canonical FactSnapshot differs from deterministic ProjectInput projection"
                )
            if self.memory_composition is not None:
                self.memory_composition.project_artifact(current.version_ref)
            return current

        version_operation = self._operation(
            operation_kind="ARTIFACT_VERSION_COMMIT",
            run_id=run_id,
            operation_scope_ref=logical.logical_artifact_id,
            material={
                "purpose": "fact_snapshot_initial_version",
                "logical_ref": logical.model_dump(mode="json"),
            },
        )
        version_ref = self.fact_snapshot_runtime.commit_snapshot(
            logical_ref=logical,
            content=candidate,
            operation_ref=version_operation,
        )
        promotion_operation = self._operation(
            operation_kind="ARTIFACT_ACTIVE_PROMOTION",
            run_id=run_id,
            operation_scope_ref=logical.logical_artifact_id,
            material={
                "purpose": "fact_snapshot_initial_promotion",
                "target_version_ref": version_ref.model_dump(mode="json"),
            },
        )
        binding = self.fact_snapshot_runtime.promote_snapshot(
            logical_ref=logical,
            target_version_ref=version_ref,
            expected_head_revision=0,
            expected_active_version_id=None,
            operation_ref=promotion_operation,
        )
        if self.memory_composition is not None:
            self.memory_composition.project_artifact(binding.version_ref)
        return binding

    @staticmethod
    def initial_plan_input_snapshot_version(
        *, document_id: str, project_input: ProjectInputSchema,
        required_sections: list[str], fact_binding: FactSnapshotBindingSchema,
    ) -> str:
        return "sha256:" + canonical_sha256({
            "contract": "initial-planning-input-v1", "document_id": document_id,
            "project_input": project_input.model_dump(mode="json"),
            "required_sections": required_sections,
            "fact_snapshot_binding": fact_binding.model_dump(mode="json"),
        })

    def ensure_initial_plan_prerequisites(
        self, *, task_id: str, run_id: str, document_id: str,
        project_input: ProjectInputSchema, required_sections: list[str],
        structured_facts: Iterable[StructuredFactSchema],
    ) -> tuple[FactSnapshotBindingSchema, ActiveExecutionLineageSchema]:
        """Bind the initial Planner to real Facts and a canonical planning Attempt."""
        if project_input.task_id != task_id or not document_id:
            raise WorkflowCutoverIntegrityError("initial Planner task/document identity mismatch")
        if required_sections != list(project_input.generation_requirements.required_sections):
            raise WorkflowCutoverIntegrityError("initial Planner sections differ from Task contract")
        fact_binding = self.ensure_fact_snapshot(
            task_id=task_id, run_id=run_id, structured_facts=structured_facts,
            project_input_version=project_input.schema_version,
        )
        snapshot = self.initial_plan_input_snapshot_version(
            document_id=document_id, project_input=project_input,
            required_sections=required_sections, fact_binding=fact_binding,
        )
        scope = f"planning:{document_id}"
        attempt_id = self.initial_plan_attempt_id(run_id, document_id)
        try:
            attempt = self.lifecycle_service.load_attempt(attempt_id)
        except KeyError:
            attempt = self.lifecycle_service.open_attempt(run_id, scope, snapshot, attempt_id=attempt_id)
        if attempt.identity.input_snapshot_version != snapshot:
            raise WorkflowCutoverIntegrityError("initial Planner input changed under the same Attempt scope")
        if attempt.status is AttemptLifecycleStatus.PENDING:
            attempt = self.lifecycle_service.activate_attempt(
                attempt.identity.attempt_id, attempt.lifecycle_revision,
            )
        if attempt.status not in {AttemptLifecycleStatus.RUNNING, AttemptLifecycleStatus.COMPLETED}:
            raise WorkflowCutoverIntegrityError("initial Planner Attempt is not runnable or replayable")
        return fact_binding, self.initial_planning_lineage(run_id, document_id)

    @staticmethod
    def initial_plan_attempt_id(run_id: str, document_id: str) -> str:
        return "initial-plan-attempt:" + canonical_sha256({"run_id": run_id, "document_id": document_id})

    def initial_planning_lineage(self, run_id: str, document_id: str) -> ActiveExecutionLineageSchema:
        """Read the exact initial Attempt; terminal view permits receipt replay only."""
        scope = f"planning:{document_id}"
        attempt = self.lifecycle_service.load_attempt(self.initial_plan_attempt_id(run_id, document_id))
        if attempt.identity.run_id != run_id or attempt.identity.attempt_scope_ref != scope:
            raise WorkflowCutoverIntegrityError("initial Planner identity is inconsistent")
        if attempt.status is not AttemptLifecycleStatus.COMPLETED:
            return self.lifecycle_service.resolve_active_lineage(run_id, scope)
        try:
            active = self.lifecycle_service.load_active_attempt(run_id, scope)
        except LifecycleCurrentnessError:
            active = None
        if active is not None and active.identity.attempt_id != attempt.identity.attempt_id:
            raise WorkflowCutoverIntegrityError("a successor invalidates initial Planner replay")
        run = self.lifecycle_service.load_run_lifecycle(run_id)
        # This preserves COMPLETED, never fabricates a running/committable Attempt.
        return ActiveExecutionLineageSchema(
            run_id=run_id, run_lifecycle_revision=run.lifecycle_revision, run_status=run.status,
            attempt_scope_ref=scope, attempt_id=attempt.identity.attempt_id,
            attempt_lifecycle_revision=attempt.lifecycle_revision, attempt_status=attempt.status,
            input_snapshot_version=attempt.identity.input_snapshot_version,
        )

    def complete_initial_plan(
        self, *, lineage: ActiveExecutionLineageSchema,
        projection: CanonicalPlanningProjection,
    ) -> None:
        """A planning Attempt succeeds only after an exact current Plan exists."""
        if not lineage.attempt_scope_ref.startswith("planning:"):
            raise WorkflowCutoverIntegrityError("initial Planner has an invalid scope")
        document_id = lineage.attempt_scope_ref[len("planning:"):]
        current = self.initial_planning_lineage(lineage.run_id, document_id)
        if (projection.plan_version_ref.logical_ref.run_id != lineage.run_id
                or projection.plan_version_ref.logical_ref.domain_scope_ref != f"document:{document_id}"):
            raise WorkflowCutoverIntegrityError("initial Planner cannot complete from another Plan scope")
        if (current.attempt_id != lineage.attempt_id
                or current.input_snapshot_version != lineage.input_snapshot_version):
            raise WorkflowCutoverIntegrityError("initial Planner changed before Plan completion")
        if not projection.section_plan_bindings:
            raise WorkflowCutoverIntegrityError("initial Planner has no committed SectionPlan")
        for binding in projection.section_plan_bindings:
            self.section_plan_runtime.validate_plan_binding_current(binding)
            if binding.plan_version_ref != projection.plan_version_ref:
                raise WorkflowCutoverIntegrityError("initial Planner committed mixed Plan versions")
        if current.attempt_status is AttemptLifecycleStatus.COMPLETED:
            return
        self.lifecycle_service.assert_attempt_committable(
            run_id=current.run_id, attempt_scope_ref=current.attempt_scope_ref,
            attempt_id=current.attempt_id, input_snapshot_version=current.input_snapshot_version,
        )
        self.lifecycle_service.transition_attempt(
            current.attempt_id, current.attempt_lifecycle_revision, AttemptLifecycleStatus.COMPLETED,
        )

    @staticmethod
    def section_input_snapshot_version(
        binding: SectionPlanBindingSchema,
    ) -> str:
        exact = SectionPlanBindingSchema.model_validate(binding)
        return (
            "section-input://sha256/"
            + canonical_sha256(exact.model_dump(mode="json"))
        )

    @staticmethod
    def workflow_research_authorization_ref(
        *,
        lineage: ActiveExecutionLineageSchema,
        section_id: str,
    ) -> str:
        exact = ActiveExecutionLineageSchema.model_validate(lineage)
        section = str(section_id or "").strip()
        if not section or exact.attempt_scope_ref != f"section:{section}":
            raise WorkflowCutoverIntegrityError(
                "Research authorization requires exact Section Attempt scope"
            )
        return (
            "workflow-start-research://sha256/"
            + canonical_sha256(
                {
                    "contract": "workflow_start_research_v1",
                    "run_id": exact.run_id,
                    "section_id": section,
                    "attempt_scope_ref": exact.attempt_scope_ref,
                    "attempt_id": exact.attempt_id,
                    "input_snapshot_version": exact.input_snapshot_version,
                }
            )
        )

    def ensure_section_execution_prerequisites(
        self,
        binding: SectionPlanBindingSchema,
    ) -> tuple[
        ActiveExecutionLineageSchema,
        ResearchRoundAuthorizationBindingSchema,
    ]:
        exact_binding = self.section_plan_runtime.validate_plan_binding_current(
            SectionPlanBindingSchema.model_validate(binding)
        )
        dependency_assessment = (self.section_dependencies.require_dependencies(exact_binding)
                                 if self.section_dependencies is not None else None)
        run_id = exact_binding.plan_version_ref.logical_ref.run_id
        section_id = exact_binding.section_id
        scope = f"section:{section_id}"
        snapshot_version = self.section_input_snapshot_version(exact_binding)
        try:
            attempt = self.lifecycle_service.load_active_attempt(run_id, scope)
        except LifecycleCurrentnessError:
            opened = self.lifecycle_service.open_attempt(
                run_id,
                scope,
                snapshot_version,
            )
            attempt = self.lifecycle_service.activate_attempt(
                opened.identity.attempt_id,
                opened.lifecycle_revision,
            )
        else:
            if attempt.identity.input_snapshot_version != snapshot_version:
                raise WorkflowCutoverIntegrityError(
                    "active Section Attempt binds a different canonical input snapshot"
                )
            if attempt.status is AttemptLifecycleStatus.PENDING:
                attempt = self.lifecycle_service.activate_attempt(
                    attempt.identity.attempt_id,
                    attempt.lifecycle_revision,
                )
            elif attempt.status is not AttemptLifecycleStatus.RUNNING:
                raise WorkflowCutoverIntegrityError(
                    "existing Section Attempt is not execution-current"
                )
        lineage = self.lifecycle_service.resolve_active_lineage(run_id, scope)
        if (
            lineage.attempt_id != attempt.identity.attempt_id
            or lineage.input_snapshot_version != snapshot_version
        ):
            raise WorkflowCutoverIntegrityError(
                "Section Attempt currentness changed during Workflow preparation"
            )
        authorization = ResearchRoundAuthorizationBindingSchema(
            authorization_kind="WORKFLOW_RESEARCH",
            authorization_ref=self.workflow_research_authorization_ref(
                lineage=lineage,
                section_id=section_id,
            ),
        )
        if dependency_assessment is not None:
            # Replay-sensitive Research occurrence evidence in the existing store,
            # not a new route/control truth. Validation recomputes the assessment.
            self.section_plan_runtime.artifact_runtime.artifact_store.put_json(
                {"plan_binding": exact_binding.model_dump(mode="json"),
                 "dependency_fingerprint": dependency_assessment.fingerprint},
                artifact_ref=authorization.authorization_ref + "/dependency-input",
                artifact_type="research_dependency_projection", schema_version="research_dependency_projection_v1",
                created_by="sif11-workflow", task_id=self.lifecycle_service.load_run_identity(run_id).task_id,
                run_id=run_id, section_id=section_id, attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version,
            )
        return lineage, authorization

    def validate_research_authorization(
        self,
        binding: ResearchRoundAuthorizationBindingSchema,
        round_ref: ResearchRoundRefSchema,
    ) -> None:
        exact_binding = ResearchRoundAuthorizationBindingSchema.model_validate(binding)
        exact_round = ResearchRoundRefSchema.model_validate(round_ref)
        if exact_binding.authorization_kind != "WORKFLOW_RESEARCH":
            raise WorkflowCutoverIntegrityError(
                "Workflow Research validator accepts WORKFLOW_RESEARCH only"
            )
        current = self.lifecycle_service.resolve_active_lineage(
            exact_round.run_id,
            exact_round.attempt_scope_ref,
        )
        if (
            current.attempt_id != exact_round.attempt_id
            or current.attempt_scope_ref != f"section:{exact_round.section_id}"
        ):
            raise WorkflowCutoverIntegrityError(
                "Research authorization does not bind the active Section Attempt"
            )
        expected = self.workflow_research_authorization_ref(
            lineage=current,
            section_id=exact_round.section_id,
        )
        if exact_binding.authorization_ref != expected:
            raise WorkflowCutoverIntegrityError(
                "Research authorization_ref is not the canonical START_RESEARCH occurrence"
            )
        if self.section_dependencies is not None:
            payload = self.section_plan_runtime.artifact_runtime.artifact_store.get_json(
                expected + "/dependency-input",
                task_id=self.lifecycle_service.load_run_identity(current.run_id).task_id,
                run_id=current.run_id, section_id=exact_round.section_id, attempt_id=current.attempt_id,
                input_snapshot_version=current.input_snapshot_version,
            )
            plan = SectionPlanBindingSchema.model_validate(payload["plan_binding"])
            if (plan.section_id != exact_round.section_id
                    or plan.plan_version_ref.logical_ref.run_id != current.run_id
                    or self.section_input_snapshot_version(plan) != current.input_snapshot_version):
                raise WorkflowCutoverIntegrityError("Research dependency input identity mismatch")
            assessment = self.section_dependencies.require_dependencies(plan)
            if assessment.fingerprint != payload["dependency_fingerprint"]:
                raise WorkflowCutoverIntegrityError("Research dependency observation is stale")

    def _requirements_source(self) -> EvidenceRequirementsSourceRefSchema:
        return EvidenceRequirementsSourceRefSchema(
            requirements_sha256=self.requirements.requirements_sha256,
            source_profile_ref=self.requirements.source_profile_ref,
            source_task_ref=self.requirements.source_task_ref,
        )

    def _plan_content(
        self,
        *,
        document_plan: DocumentPlanSchema,
        fact_snapshot_binding: FactSnapshotBindingSchema,
    ) -> SectionPlanContentSchema:
        legacy = DocumentPlanSchema.model_validate(document_plan)
        requirements_source = self._requirements_source()
        requirement_ids = tuple(
            item.requirement_id for item in self.requirements.requirements
        )
        sections: list[SectionPlanEntrySchema] = []
        for item in legacy.sections:
            citation_required = bool(item.citation_required)
            sections.append(
                SectionPlanEntrySchema(
                    section_id=item.section_id,
                    section_title=item.section_title,
                    section_order=item.section_order,
                    applicability="applicable",
                    strategy=(
                        SectionStrategyCode.RAG_GENERATE
                        if citation_required
                        else SectionStrategyCode.TECHNICAL_GENERATE
                    ),
                    citation_required=citation_required,
                    fact_requirements=item.fact_requirements,
                    # WriterAdmission applies to every generated Section.
                    # citation_required controls rendering/citation semantics; it
                    # does not remove the deployment-fixed Gate requirements owner.
                    evidence_requirements_source=requirements_source,
                    evidence_requirement_ids=requirement_ids,
                    hard_dependencies=tuple(item.depends_on),
                    execution_group=item.execution_group,
                    parallel_eligible=bool(item.can_run_in_parallel),
                )
            )
        return SectionPlanContentSchema(
            planning_contract_version=PLANNING_CONTRACT_VERSION,
            fact_snapshot_version_ref=fact_snapshot_binding.version_ref,
            sections=tuple(sections),
        )

    def ensure_section_plan(
        self,
        *,
        task_id: str,
        run_id: str,
        document_plan: DocumentPlanSchema,
        structured_facts: Iterable[StructuredFactSchema],
        project_input_version: str = "project_input_v1",
    ) -> CanonicalPlanningProjection:
        self.ensure_running_run(task_id=task_id, run_id=run_id)
        legacy = DocumentPlanSchema.model_validate(document_plan)
        fact_binding = self.ensure_fact_snapshot(
            task_id=task_id,
            run_id=run_id,
            structured_facts=structured_facts,
            project_input_version=project_input_version,
        )
        document_scope_ref = f"document:{legacy.document_id}"
        logical = self.section_plan_runtime.logical_ref_for_scope(
            run_id=run_id,
            document_scope_ref=document_scope_ref,
        )
        candidate = self._plan_content(
            document_plan=legacy,
            fact_snapshot_binding=fact_binding,
        )

        first_section_id = legacy.sections[0].section_id if legacy.sections else ""
        current_binding = (
            self.section_plan_runtime.resolve_current_plan_binding(
                run_id=run_id,
                document_scope_ref=document_scope_ref,
                section_id=first_section_id,
            )
            if first_section_id
            else None
        )
        if current_binding is not None:
            existing_raw, exact_ref = self.section_plan_runtime.artifact_runtime.read_version(
                current_binding.plan_version_ref
            )
            existing = SectionPlanContentSchema.model_validate(existing_raw)
            if exact_ref != current_binding.plan_version_ref or existing != candidate:
                raise WorkflowCutoverIntegrityError(
                    "canonical SectionPlan differs from deterministic DocumentPlan projection"
                )
            plan_version_ref = exact_ref
        else:
            version_operation = self._operation(
                operation_kind="ARTIFACT_VERSION_COMMIT",
                run_id=run_id,
                operation_scope_ref=logical.logical_artifact_id,
                material={
                    "purpose": "section_plan_initial_version",
                    "logical_ref": logical.model_dump(mode="json"),
                },
            )
            promotion_operation = self._operation(
                operation_kind="ARTIFACT_ACTIVE_PROMOTION",
                run_id=run_id,
                operation_scope_ref=logical.logical_artifact_id,
                material={
                    "purpose": "section_plan_initial_promotion",
                    "logical_ref": logical.model_dump(mode="json"),
                },
            )
            plan_version_ref = self.section_plan_runtime.commit_plan(
                candidate_content=candidate,
                logical_ref=logical,
                fact_snapshot_binding=fact_binding,
                expected_head_revision=0,
                expected_active_version_id=None,
                operations=SectionPlanCommitOperations(
                    version_commit=version_operation,
                    active_promotion=promotion_operation,
                ),
            )

        bindings: list[SectionPlanBindingSchema] = []
        for item in legacy.sections:
            binding = self.section_plan_runtime.resolve_current_plan_binding(
                run_id=run_id,
                document_scope_ref=document_scope_ref,
                section_id=item.section_id,
            )
            if binding is None or binding.plan_version_ref != plan_version_ref:
                raise WorkflowCutoverIntegrityError(
                    "canonical SectionPlan section binding is unresolved"
                )
            bindings.append(
                self.section_plan_runtime.validate_plan_binding_current(binding)
            )
        return CanonicalPlanningProjection(
            fact_snapshot_binding=fact_binding,
            plan_version_ref=plan_version_ref,
            section_plan_bindings=tuple(bindings),
        )


__all__ = [
    "CanonicalPlanningProjection",
    "FACT_NORMALIZATION_CONTRACT_VERSION",
    "PLANNING_CONTRACT_VERSION",
    "TaskRequestFactSourceResolver",
    "TargetWorkflowRuntime",
    "WorkflowCutoverIntegrityError",
]
