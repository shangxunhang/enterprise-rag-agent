"""Wave-13 production validators for canonical SIF-06 ParagraphRuntime.

The validators in this module compose existing durable owners.  They do not
create Paragraph truth and do not recover currentness from checkpoints.
"""

from __future__ import annotations

from typing import Any

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    ParagraphOperationRefSchema,
    ParagraphWriterSourceBindingSchema,
    SectionParagraphManifestOperationRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    EvidencePackageBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionStatus,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
)
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import (
    SectionPlanRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.research_runtime import (
    ResearchRuntime,
)
from core.runtime.pre_write_execution_store import WriterExecutionState
from schemas.context import ContextPackageSchema


class TargetParagraphCompositionError(RuntimeError):
    """A canonical Paragraph source/authorization cannot be proven."""


class TargetParagraphRuntimeValidators:
    """Exact owner resolvers used by production ParagraphRuntime."""

    _CONTEXT_AUDIT_EPOCH = "1970-01-01T00:00:00+00:00"

    def __init__(
        self,
        *,
        lifecycle_service: RunAttemptLifecycleService,
        fact_snapshot_runtime: FactSnapshotRuntime,
        section_plan_runtime: SectionPlanRuntime,
        research_runtime: ResearchRuntime,
        route_decision_store: Any,
        writer_execution_store: Any,
    ) -> None:
        self.lifecycle_service = lifecycle_service
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.section_plan_runtime = section_plan_runtime
        self.research_runtime = research_runtime
        self.route_decision_store = route_decision_store
        self.writer_execution_store = writer_execution_store
        self.repair_provider = None

    def _assert_lineage(
        self,
        lineage: ActiveExecutionLineageSchema,
    ) -> ActiveExecutionLineageSchema:
        lineage = ActiveExecutionLineageSchema.model_validate(lineage)
        current = self.lifecycle_service.assert_attempt_committable(
            run_id=lineage.run_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
            input_snapshot_version=lineage.input_snapshot_version,
        )
        if current != lineage:
            raise TargetParagraphCompositionError(
                "Paragraph active Attempt lineage changed before commit"
            )
        return current

    def validate_authorization(
        self,
        operation: ParagraphOperationRefSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> None:
        operation = ParagraphOperationRefSchema.model_validate(operation)
        lineage = self._assert_lineage(lineage)
        if (
            operation.run_id != lineage.run_id
            or operation.write_instance_id != lineage.attempt_id
            or lineage.attempt_scope_ref != f"section:{operation.section_id}"
        ):
            raise TargetParagraphCompositionError(
                "Paragraph operation is outside active Attempt scope"
            )
        if operation.authorization_kind != "INITIAL_WRITER":
            if operation.authorization_kind == "REPAIR" and self.repair_provider is not None:
                identity = self.lifecycle_service.load_run_identity(lineage.run_id)
                self.repair_provider(identity.task_id, identity.run_id).paragraph_authorization_validator(
                    operation, lineage
                )
                return
            # Repair/rewrite authorization is wired later by the Control/Repair
            # owner.  Until then production cannot silently treat it as Writer auth.
            raise TargetParagraphCompositionError(
                "non-initial Paragraph authorization owner is not composed"
            )
        if not operation.authorization_ref.startswith(
            "writer-logical-execution://sha256/"
        ):
            raise TargetParagraphCompositionError(
                "Paragraph initial authorization is not Writer-key derived"
            )

    def _resolve_writer_invocation(
        self,
        binding: ParagraphWriterSourceBindingSchema,
    ):
        key = WriterLogicalExecutionKey(
            writer_subject_ref=binding.writer_subject_ref,
            write_instance_id=binding.write_instance_id,
            route_decision_ref=binding.route_decision_ref,
        )
        record = self.writer_execution_store.get_writer_execution(key)
        if (
            record is None
            or record.state is not WriterExecutionState.SUCCEEDED
            or record.result is None
            or record.result.status is not WriterExecutionStatus.SUCCESS
            or record.result.logical_execution_key != key
        ):
            raise TargetParagraphCompositionError(
                "Paragraph source requires exact successful durable Writer execution"
            )
        invocation = self.writer_execution_store.resolve_writer_generation_invocation(
            WriterGenerationInvocationRefSchema(
                logical_execution_key=key,
                invocation_sha256=record.semantic_fingerprint,
            )
        )
        if (
            invocation.writer_subject_ref != binding.writer_subject_ref
            or invocation.write_instance_id != binding.write_instance_id
            or invocation.route_decision_ref != binding.route_decision_ref
        ):
            raise TargetParagraphCompositionError(
                "Paragraph Writer invocation lineage is stale"
            )
        return key, invocation

    def _validate_plan_fact(
        self,
        binding: ParagraphWriterSourceBindingSchema,
    ) -> None:
        plan_ref = binding.section_plan_source.plan_version_ref
        head = self.section_plan_runtime.artifact_runtime.resolve_active(
            plan_ref.logical_ref
        )
        if (
            head is None
            or head.active_version_ref != plan_ref
            or head.head_revision < 1
        ):
            raise TargetParagraphCompositionError(
                "Paragraph source SectionPlan is not current"
            )
        plan_binding = SectionPlanBindingSchema(
            plan_version_ref=plan_ref,
            resolved_head_revision=head.head_revision,
            section_id=binding.section_plan_source.section_id,
        )
        self.section_plan_runtime.validate_plan_binding_current(plan_binding)
        raw_plan, canonical_plan_ref = (
            self.section_plan_runtime.artifact_runtime.read_version(plan_ref)
        )
        if canonical_plan_ref != plan_ref:
            raise TargetParagraphCompositionError(
                "Paragraph Plan resolver returned another immutable version"
            )
        plan = SectionPlanContentSchema.model_validate(raw_plan)
        if plan.fact_snapshot_version_ref != binding.fact_snapshot_version_ref:
            raise TargetParagraphCompositionError(
                "Paragraph FactSnapshot does not match Plan-bound source"
            )
        fact_binding = self.fact_snapshot_runtime.resolve_current_binding(
            binding.fact_snapshot_version_ref.logical_ref
        )
        if (
            fact_binding is None
            or fact_binding.version_ref != binding.fact_snapshot_version_ref
        ):
            raise TargetParagraphCompositionError(
                "Paragraph FactSnapshot source is stale"
            )
        self.fact_snapshot_runtime.validate_binding_current(fact_binding)

    def _validate_evidence_package(
        self,
        binding: ParagraphWriterSourceBindingSchema,
    ) -> None:
        version_ref = binding.evidence_package_version_ref
        head = self.research_runtime.artifact_runtime.resolve_active(
            version_ref.logical_ref
        )
        if (
            head is None
            or head.active_version_ref != version_ref
            or head.head_revision < 1
        ):
            raise TargetParagraphCompositionError(
                "Paragraph EvidencePackage source is not current"
            )
        package_binding = EvidencePackageBindingSchema(
            version_ref=version_ref,
            resolved_head_revision=head.head_revision,
        )
        package = self.research_runtime.validate_evidence_package_binding_current(
            package_binding,
            require_source_fresh=True,
        )
        if (
            package.section_plan_source.plan_version_ref
            != binding.section_plan_source.plan_version_ref
            or package.section_plan_source.section_id
            != binding.section_plan_source.section_id
        ):
            raise TargetParagraphCompositionError(
                "Paragraph EvidencePackage does not bind current SectionPlan"
            )

    def _validate_context(
        self,
        binding: ParagraphWriterSourceBindingSchema,
        invocation,
    ) -> None:
        snapshot = invocation.model_dump(mode="json")[
            "unified_writer_input_snapshot"
        ]
        raw_context = snapshot.get("target_context_package")
        if not isinstance(raw_context, dict):
            raise TargetParagraphCompositionError(
                "Target Paragraph source requires embedded SIF-10 ContextPackage"
            )
        package = ContextPackageSchema.model_validate(
            {
                **raw_context,
                "created_at": self._CONTEXT_AUDIT_EPOCH,
            }
        )
        if (
            package.package_ref.context_package_id != binding.context_package_id
            or package.model_input.final_model_input_sha256
            != binding.context_sha256
        ):
            raise TargetParagraphCompositionError(
                "Paragraph ContextPackage identity/final-input hash mismatch"
            )
        subject = package.invocation_subject
        if (
            subject.run_id != binding.writer_subject_ref.run_id
            or subject.attempt_id != binding.write_instance_id
            or subject.consumer_scope_ref
            != f"section:{binding.writer_subject_ref.section_id}"
        ):
            raise TargetParagraphCompositionError(
                "Paragraph ContextPackage invocation lineage mismatch"
            )

    def validate_source(
        self,
        binding: ParagraphWriterSourceBindingSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> None:
        binding = ParagraphWriterSourceBindingSchema.model_validate(binding)
        lineage = self._assert_lineage(lineage)
        if (
            binding.writer_subject_ref.run_id != lineage.run_id
            or binding.write_instance_id != lineage.attempt_id
            or lineage.attempt_scope_ref
            != f"section:{binding.writer_subject_ref.section_id}"
        ):
            raise TargetParagraphCompositionError(
                "Paragraph Writer source is outside active Attempt"
            )

        route = self.route_decision_store.resolve_exact(
            binding.route_decision_ref
        )
        if (
            route.writer_subject_ref != binding.writer_subject_ref
            or route.write_instance_id != binding.write_instance_id
            or route.binding_ref != binding.writer_gate_binding_ref
        ):
            raise TargetParagraphCompositionError(
                "Paragraph RouteDecision/Gate binding is stale"
            )

        _key, invocation = self._resolve_writer_invocation(binding)
        self._validate_plan_fact(binding)
        self._validate_evidence_package(binding)
        self._validate_context(binding, invocation)

    def validate_manifest_authorization(
        self,
        operation: SectionParagraphManifestOperationRefSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> None:
        operation = SectionParagraphManifestOperationRefSchema.model_validate(
            operation
        )
        lineage = self._assert_lineage(lineage)
        manifest = operation.manifest_ref
        if (
            manifest.run_id != lineage.run_id
            or manifest.attempt_id != lineage.attempt_id
            or manifest.attempt_scope_ref != lineage.attempt_scope_ref
            or manifest.write_instance_id != lineage.attempt_id
        ):
            raise TargetParagraphCompositionError(
                "Paragraph Manifest operation is outside active Attempt"
            )
        if not operation.authorization_ref.startswith(
            "writer-logical-execution://sha256/"
        ):
            raise TargetParagraphCompositionError(
                "Paragraph Manifest is not authorized by initial Writer occurrence"
            )


__all__ = [
    "TargetParagraphCompositionError",
    "TargetParagraphRuntimeValidators",
]
