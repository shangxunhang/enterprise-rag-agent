"""Reusable business stages for scheme generation.

The explicit LangGraph production path and the legacy use-case facade both
delegate to this service. There is one implementation of planning, retrieval,
section execution, assembly, hard-gate evaluation, artifact publication and
terminal result construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn, TYPE_CHECKING

from agent.runtime.graph_state_ops import (
    GraphStateDiffer,
    StateWriteContractViolation,
)
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.workflow_stream_context import emit_workflow_custom_event
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    activate_workflow_budget,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    ChangeSetSchema,
    DocumentVerificationResultSchema,
    RepairTaskStatus,
)
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionGraphExecutionRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DeliverableFinalResultSchema,
    DocumentAssemblyRequestSchema,
    ParallelSectionExecutionEnvelopeSchema,
    ParallelSectionStateDeltaSchema,
    PlanProjectionStatus,
    RejectedDraftArtifactSchema,
    SchemeGenerationOptionsSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.workflows.section_parallel_execution_graph import (
    ParallelSectionBudgetAllocator,
    SectionParallelExecutionGraph,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.agent_rag_v1.assembly_validator import (
    DocumentAssemblyValidator,
)
from apps.enterprise_document.services.agent_rag_v1.conflict_repair import (
    ConflictRepairPlanService,
)
from apps.enterprise_document.services.agent_rag_v1.document_verifier import (
    DocumentVerifierService,
)
from apps.enterprise_document.services.agent_rag_v1.final_artifact_review import (
    FinalArtifactReviewService,
)
from apps.enterprise_document.services.agent_rag_v1.generation_report import (
    GenerationReportService,
)
from apps.enterprise_document.services.agent_rag_v1.version_policy import (
    VersionPolicyService,
)
from core.retry_policy import RetryDecisionPolicy, classify_failure
from core.runtime.timing import MonotonicTimer, elapsed_ms
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema, ErrorSourceSchema, WarningSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus

from .agent_integration import MainlinePlannerAdapter, MainlinePlannerFailure
from .capture_service import SchemeCaptureService
from .citation_service import CitationService
from .document_assembler import DocumentAssembler
from .document_citation_registry import DocumentCitationRegistry
from .document_planning_service import DocumentPlanningService
from .evidence_service import SchemeEvidenceService
from .input_service import SchemeInputService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_execution_coordinator import SectionExecutionCoordinator

if TYPE_CHECKING:
    from apps.enterprise_document.workflows.section_execution_graph import (
        SectionExecutionGraph,
    )


@dataclass(frozen=True)
class SchemeGenerationStageFailure(Exception):
    """Structured technical failure routed to the graph failure node."""

    error: ErrorSchema

    def __str__(self) -> str:
        return self.error.message


class SchemeGenerationStageService:
    """Own the canonical implementation of every top-level business stage."""

    def __init__(
        self,
        *,
        input_service: SchemeInputService,
        evidence_service: SchemeEvidenceService,
        section_execution_coordinator: SectionExecutionCoordinator,
        document_assembler: DocumentAssembler,
        document_planning_service: DocumentPlanningService,
        capture_service: SchemeCaptureService,
        runtime_support: SchemeWriterRuntimeSupport,
        agent_name: str,
        agent_type: str,
        rag_service_name: str,
        enable_semantic_gate: bool,
        section_max_concurrency: int = 1,
    ) -> None:
        self.input_service = input_service
        self.evidence_service = evidence_service
        self.section_execution_coordinator = section_execution_coordinator
        self.document_assembler = document_assembler
        self.document_planning_service = document_planning_service
        self.capture_service = capture_service
        self.runtime_support = runtime_support
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.rag_service_name = rag_service_name
        self.enable_semantic_gate = enable_semantic_gate
        if not 1 <= int(section_max_concurrency) <= 32:
            raise ValueError("section_max_concurrency must be between 1 and 32")
        self.section_max_concurrency = int(section_max_concurrency)
        self.state_writer = SharedStateWriter()
        self.section_execution_graph: SectionExecutionGraph | None = None
        self.section_parallel_execution_graph = SectionParallelExecutionGraph()
        self.planner_adapter: MainlinePlannerAdapter | None = None
        self.document_verifier = DocumentVerifierService()
        self.conflict_repair_service = ConflictRepairPlanService()
        self.document_assembly_validator = DocumentAssemblyValidator()
        self.final_artifact_review_service = FinalArtifactReviewService()
        self.generation_report_service = GenerationReportService()
        self.version_policy_service = VersionPolicyService()

    def attach_planner_adapter(
        self,
        adapter: MainlinePlannerAdapter,
    ) -> None:
        """Install the production PlannerAgent adapter at the composition root."""

        self.planner_adapter = adapter

    def attach_section_execution_graph(
        self,
        graph: "SectionExecutionGraph",
    ) -> None:
        """Install the production section subgraph without changing legacy wiring."""

        self.section_execution_graph = graph

    @staticmethod
    def _hard_gate_failure_policy(
        hard_gate,
        *,
        evidence_requirements_satisfied: bool,
    ) -> tuple[FailureCategory, bool]:
        citation_contract_failed = any(
            not bool(hard_gate.checks.get(check_name, True))
            for check_name in (
                "citation_marker_binding_consistent",
                "citation_marker_binding_bijection",
                "citation_marker_occurrences_verified",
                "citation_orphan_markers_absent",
                "citation_orphan_bindings_absent",
                "citation_targets_valid",
                "citation_grounding_verified",
            )
        )
        if not evidence_requirements_satisfied:
            return FailureCategory.EVIDENCE_INSUFFICIENT, False
        if citation_contract_failed:
            return FailureCategory.VALIDATION, False
        return FailureCategory.BUSINESS_QUALITY, True

    @staticmethod
    def _evidence_assessment(normalized_output: dict) -> dict:
        contract = normalized_output.get("evidence")
        contract = contract if isinstance(contract, dict) else {}
        assessment = contract.get("assessment")
        assessment = dict(assessment) if isinstance(assessment, dict) else {}
        raw_status = assessment.get("status") or "not_assessed"
        if hasattr(raw_status, "value"):
            raw_status = raw_status.value
        assessment["status"] = str(raw_status).strip().lower() or "not_assessed"
        details = assessment.get("details")
        assessment["details"] = dict(details) if isinstance(details, dict) else {}
        return assessment

    def _require(
        self,
        value,
        *,
        field_name: str,
        node_name: str,
    ):
        if value is None:
            raise ValueError(
                f"{node_name} requires initialized payload field {field_name}"
            )
        return value

    def prepare_document_plan(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Read normalized input and obtain a canonical plan through PlannerAgent."""

        project_input, table_analysis, structured_facts = (
            self.input_service.read_inputs(shared_state)
        )
        required_sections = list(
            project_input.generation_requirements.required_sections
            or project_input.output_schema.required_sections
        )
        if not required_sections:
            raise ValueError("required_sections is empty in ProjectInput")
        options = SchemeGenerationOptionsSchema(
            need_citation=project_input.generation_requirements.need_citation,
            citation_required_sections=list(
                project_input.generation_requirements.citation_required_sections
            ),
            need_human_review=project_input.generation_requirements.need_human_review,
            max_context_chars=project_input.generation_requirements.max_context_chars,
            max_section_retries=(
                project_input.generation_requirements.max_section_retries
            ),
            max_tokens_per_section=(
                project_input.generation_requirements.max_tokens_per_section
            ),
            min_section_chars=project_input.generation_requirements.min_section_chars,
        )
        required_evidence_sections = list(
            options.citation_required_sections
            or (required_sections if options.need_citation else [])
        )
        document_id = f"document_{shared_state.run_id}"
        started_at = payload.started_at or self.runtime_support.now_iso()
        plan_state = payload.plan_state
        if self.planner_adapter is not None:
            try:
                planner_outcome = self.planner_adapter.plan(
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                    project_input=project_input,
                    required_sections=required_sections,
                    document_id=document_id,
                    created_at=started_at,
                    plan_revision=max(1, plan_state.plan_revision or 1),
                    current_plan_ref=plan_state.active_plan_revision_id,
                )
            except MainlinePlannerFailure as exc:
                self.state_writer.add_error(shared_state, exc.error)
                raise SchemeGenerationStageFailure(exc.error) from exc
            document_plan = planner_outcome.document_plan
            plan_state = plan_state.model_copy(
                update={
                    "status": PlanProjectionStatus.READY,
                    "canonical_plan_id": document_plan.plan_id,
                    "plan_revision": planner_outcome.plan_revision,
                    "active_plan_revision_id": document_plan.plan_id,
                    "input_snapshot_version": (
                        planner_outcome.input_snapshot_version
                    ),
                    "section_order": [
                        section.section_id for section in document_plan.sections
                    ],
                    "metadata": {
                        **dict(plan_state.metadata),
                        **dict(planner_outcome.metadata),
                        "planner_agent_attached": True,
                    },
                },
                deep=True,
            )
        else:
            # Legacy/unit-test compatibility only. SupervisorFactory always
            # installs the PlannerAgent adapter on the production mainline.
            document_plan = self.document_planning_service.build_document_plan(
                run_id=shared_state.run_id,
                document_id=document_id,
                project_input=project_input,
                required_sections=required_sections,
                created_at=started_at,
            )
            plan_state = plan_state.model_copy(
                update={
                    "metadata": {
                        **dict(plan_state.metadata),
                        "planner_agent_attached": False,
                        "planning_fallback": "legacy_compatibility",
                    }
                },
                deep=True,
            )
        print(
            f"[DocumentPlan] sections={len(document_plan.sections)} "
            f"source={document_plan.planning_source}",
            flush=True,
        )
        scheme_input = SchemeWriterInputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            user_input=project_input.user_query,
            requirements=project_input.generation_requirements.model_dump(),
            project_input=project_input,
            table_analysis=table_analysis,
            structured_facts=structured_facts,
            kb_ids=(shared_state.task or {}).get("kb_ids") or [],
            template_id=(shared_state.task or {}).get("template_id"),
            required_sections=required_sections,
            generation_options=options,
        )
        return payload.model_copy(
            update={
                "started_at": started_at,
                "project_input": project_input,
                "table_analysis": table_analysis,
                "structured_facts": list(structured_facts),
                "required_sections": required_sections,
                "required_evidence_sections": required_evidence_sections,
                "generation_options": options,
                "document_id": document_id,
                "document_plan": document_plan,
                "plan_state": plan_state,
                "scheme_input": scheme_input,
            },
            deep=True,
        )

    def retrieve_document_evidence(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run only document-level retrieval and publish its canonical evidence."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="retrieve_document_evidence",
        )
        print(f"[RAG] START query={project_input.user_query[:80]!r}", flush=True)
        timer = MonotonicTimer()
        rag_started = timer.now()
        document_budget_config = {
            "max_retrieval_rounds": 4,
            "max_rewrite_rounds": 2,
            "max_total_llm_calls": 16,
            "max_total_tokens": 32000,
            "human_review_on_exhaustion": True,
            **dict(
                project_input.generation_requirements.extra.get(
                    "document_rag_budget",
                    {},
                )
                or {}
            ),
        }
        document_rag_budget = WorkflowBudget.from_policy_metadata(
            document_budget_config
        )
        with activate_workflow_budget(document_rag_budget):
            rag_result = self.evidence_service.retrieve(
                shared_state,
                project_input,
            )
        print(
            f"[RAG] END   success={bool(rag_result and rag_result.success)} "
            f"latency_ms={elapsed_ms(timer, rag_started)}",
            flush=True,
        )
        rag_context, chunks, citations, rag_output = (
            self.evidence_service.extract_rag_output(shared_state, rag_result)
        )
        rag_output.setdefault("extra", {})["document_rag_model_budget"] = (
            document_rag_budget.snapshot()
        )
        citation_registry = DocumentCitationRegistry()
        rag_context, chunks, citations, rag_output = citation_registry.remap_bundle(
            context=rag_context,
            chunks=chunks,
            citations=citations,
            normalized=rag_output,
            scope="document",
            query=project_input.user_query,
        )
        print(
            f"[RAG] NORMALIZED chunks={len(chunks)} citations={len(citations)} "
            f"citation_ids={[item.citation_id for item in citations]}",
            flush=True,
        )
        evidence_contract = rag_output.get("evidence") or {}
        evidence_assessment = self._evidence_assessment(rag_output)
        assessment_status = evidence_assessment["status"]
        raw_evidence_available = evidence_assessment.get("evidence_available")
        evidence_available = bool(
            raw_evidence_available
            if raw_evidence_available is not None
            else (rag_context.context_item_count and citations)
        )
        semantic_sufficiency = (
            True
            if assessment_status == "sufficient"
            else False
            if assessment_status == "insufficient"
            else None
        )
        self.state_writer.set_evidence_context(
            shared_state,
            query=project_input.user_query,
            evidence_contract=evidence_contract,
            context_text=rag_context.context_text,
            retrieved_chunks=[item.model_dump() for item in chunks],
            citations=[item.model_dump() for item in citations],
            used_doc_ids=rag_context.used_doc_ids,
            evidence_available=evidence_available,
            assessment_status=assessment_status,
            evidence_sufficient=semantic_sufficiency,
        )
        if rag_result is None or not rag_result.success:
            node_name = "retrieve_document_evidence"
            underlying = rag_result.error if rag_result else None
            if underlying is not None:
                source = (
                    underlying.source.model_copy(
                        update={"step_name": node_name},
                        deep=True,
                    )
                    if underlying.source is not None
                    else ErrorSourceSchema(
                        component=self.rag_service_name,
                        agent_name=self.agent_name,
                        step_name=node_name,
                    )
                )
                error = underlying.model_copy(
                    update={
                        "failed_node": node_name,
                        "source": source,
                    },
                    deep=True,
                )
            else:
                error = self.runtime_support.error(
                    "RAG_TOOL_FAILED",
                    rag_result.error_message if rag_result else "RAG tool is not configured",
                    node=node_name,
                    retryable=True,
                    failure_category=FailureCategory.AVAILABILITY,
                    requires_human=True,
                    user_message="知识检索失败，文档生成任务已停止。",
                )
            self.state_writer.add_error(shared_state, error)
            raise SchemeGenerationStageFailure(error)

        section_retrieval_enabled = bool(
            project_input.generation_requirements.extra.get(
                "enable_section_aware_retrieval",
                self.rag_service_name == "RAGService",
            )
        )
        corrective_retrieval_enabled = bool(
            project_input.generation_requirements.extra.get(
                "enable_corrective_section_retrieval",
                section_retrieval_enabled,
            )
        )
        return payload.model_copy(
            update={
                "document_rag_context": rag_context,
                "document_retrieved_chunks": list(chunks),
                "document_citations": list(citations),
                "document_rag_output": dict(rag_output),
                "document_evidence_assessment": dict(evidence_assessment),
                "document_evidence_available": evidence_available,
                "document_assessment_status": assessment_status,
                "document_tool_call_ids": [rag_result.retrieval_trace_id],
                "section_retrieval_enabled": section_retrieval_enabled,
                "corrective_retrieval_enabled": corrective_retrieval_enabled,
                "all_retrieved_chunks": list(chunks),
                "all_citations": list(citations),
                "all_rag_outputs": [dict(rag_output)],
            },
            deep=True,
        )

    def initialize_generation(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Initialize deterministic generation context without external calls."""
        # 获取项目输入
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="initialize_generation",
        )
        # 获取文档ID
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="initialize_generation",
        )
        # 初始化生成状态
        self.state_writer.initialize_generation(
            shared_state,
            document_id=document_id,
            document_title=project_input.output_schema.document_title,
            required_sections=list(payload.required_sections),
        )
        return payload.model_copy(
            update={
                "sections": [],
                "section_evidence": [],
            },
            deep=True,
        )

    @classmethod
    def _remap_citation_payload(
        cls,
        value: Any,
        *,
        id_map: dict[str, str],
        registry: DocumentCitationRegistry,
    ) -> Any:
        """Remap diagnostic RAG payloads during deterministic ordered fan-in."""

        if isinstance(value, list):
            return [
                cls._remap_citation_payload(
                    item,
                    id_map=id_map,
                    registry=registry,
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        remapped: dict[str, Any] = {}
        for key, item in value.items():
            if key == "citation_id" and isinstance(item, str):
                remapped[key] = id_map.get(item, item)
            elif key == "citation_ids" and isinstance(item, list):
                remapped[key] = [
                    id_map.get(str(citation_id), str(citation_id))
                    for citation_id in item
                ]
            elif key in {
                "content",
                "context_text",
                "model_output",
                "prompt",
            } and isinstance(item, str):
                remapped[key] = registry.replace_markers(item, id_map)
            else:
                remapped[key] = cls._remap_citation_payload(
                    item,
                    id_map=id_map,
                    registry=registry,
                )
        return remapped

    def _merge_parallel_section_result(
        self,
        result: SectionExecutionResultSchema,
        *,
        document_id: str,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Merge an isolated section result in deterministic document order."""

        local_citations = list(
            result.citation_registry or result.evidence.citations
        )
        canonical_citations, id_map = citation_registry.register(
            local_citations,
            scope=f"section:{result.section.section_id}",
            query=result.evidence.query,
        )
        # ``register`` preserves input order, so this also maps citations that
        # are diagnostic-only and not present in the active evidence bundle.
        id_map.update(
            {
                source.citation_id: target.citation_id
                for source, target in zip(
                    local_citations,
                    canonical_citations,
                )
            }
        )
        context, chunks, evidence_citations, _normalized = (
            citation_registry.remap_bundle(
                context=result.evidence.rag_context,
                chunks=list(result.evidence.retrieved_chunks),
                citations=list(result.evidence.citations),
                normalized={},
                scope=result.evidence.retrieval_scope,
                query=result.evidence.query,
            )
        )
        content = citation_registry.replace_markers(
            result.section.content,
            id_map,
        )
        model_output = citation_registry.replace_markers(
            result.section.model_output,
            id_map,
        )
        prompt = citation_registry.replace_markers(
            result.section.prompt,
            id_map,
        )
        rebuilt_bindings = CitationService.bind_citations(
            document_id,
            result.section.section_id,
            content,
            citation_registry.all(),
        )
        original_by_index = {
            binding.target_marker_index: binding
            for binding in result.section.citation_bindings
            if binding.target_marker_index is not None
        }
        rebuilt_bindings = [
            binding.model_copy(
                update={
                    "metadata": dict(
                        original_by_index.get(
                            binding.target_marker_index,
                            binding,
                        ).metadata
                    )
                }
            )
            for binding in rebuilt_bindings
        ]
        section = result.section.model_copy(
            update={
                "content": content,
                "model_output": model_output,
                "prompt": prompt,
                "citation_ids": list(
                    dict.fromkeys(
                        binding.citation_id for binding in rebuilt_bindings
                    )
                ),
                "citation_bindings": rebuilt_bindings,
                "extra": {
                    **dict(result.section.extra),
                    "parallel_execution": True,
                    "citation_fan_in_order": result.section.section_order,
                },
            },
            deep=True,
        )
        evidence = result.evidence.model_copy(
            update={
                "rag_context": context,
                "retrieved_chunks": chunks,
                "citations": evidence_citations,
                "evidence_contract_sha256": (
                    context.extra.get("evidence_contract_sha256")
                    or result.evidence.evidence_contract_sha256
                ),
                "metadata": {
                    **dict(result.evidence.metadata),
                    "parallel_execution": True,
                    "citation_id_map": id_map,
                },
            },
            deep=True,
        )
        rag_outputs = [
            self._remap_citation_payload(
                item,
                id_map=id_map,
                registry=citation_registry,
            )
            for item in result.rag_outputs
        ]
        return result.model_copy(
            update={
                "section": section,
                "evidence": evidence,
                "rag_outputs": rag_outputs,
                "citation_registry": citation_registry.all(),
            },
            deep=True,
        )

    @staticmethod
    def _parallel_state_delta(
        before: SharedStateSchema,
        after: SharedStateSchema,
        *,
        section_id: str,
    ) -> ParallelSectionStateDeltaSchema:
        """Extract only additive, section-scoped writes from one branch."""

        def appended(before_items: list, after_items: list, path: str) -> list:
            if after_items[: len(before_items)] != before_items:
                raise StateWriteContractViolation(
                    changed_paths=[path],
                    allowed_paths=[f"{path} (append-only)"],
                )
            return list(after_items[len(before_items) :])

        generated_ids = appended(
            before.context_bundle.generation.generated_section_ids,
            after.context_bundle.generation.generated_section_ids,
            "context_bundle.generation.generated_section_ids",
        )
        warnings = appended(before.warnings, after.warnings, "warnings")
        errors = appended(before.errors, after.errors, "errors")
        runtime_errors = appended(
            before.context_bundle.runtime.errors,
            after.context_bundle.runtime.errors,
            "context_bundle.runtime.errors",
        )

        generated_outputs: dict[str, Any] = {}
        for key, value in before.generated_outputs.items():
            if (
                key not in after.generated_outputs
                or after.generated_outputs[key] != value
            ):
                raise StateWriteContractViolation(
                    changed_paths=[f"generated_outputs.{key}"],
                    allowed_paths=["generated_outputs.<new-section-call-id>"],
                )
        for key, value in after.generated_outputs.items():
            if key not in before.generated_outputs:
                generated_outputs[key] = value

        metadata_updates: dict[str, dict[str, Any]] = {}
        for scope, before_metadata, after_metadata in (
            (
                "generation",
                before.context_bundle.generation.metadata,
                after.context_bundle.generation.metadata,
            ),
            (
                "runtime",
                before.context_bundle.runtime.metadata,
                after.context_bundle.runtime.metadata,
            ),
        ):
            for key, value in before_metadata.items():
                if key not in after_metadata or after_metadata[key] != value:
                    raise StateWriteContractViolation(
                        changed_paths=[
                            f"context_bundle.{scope}.metadata.{key}"
                        ],
                        allowed_paths=[
                            f"context_bundle.{scope}.metadata.{section_id}"
                        ],
                    )
            new_values = {
                key: value
                for key, value in after_metadata.items()
                if key not in before_metadata
            }
            invalid = [
                key
                for key in new_values
                if key != section_id and not key.startswith(f"{section_id}:")
            ]
            if invalid:
                raise StateWriteContractViolation(
                    changed_paths=[
                        f"context_bundle.{scope}.metadata.{key}"
                        for key in invalid
                    ],
                    allowed_paths=[
                        f"context_bundle.{scope}.metadata.{section_id}"
                    ],
                )
            if new_values:
                metadata_updates[scope] = new_values

        retry_delta = (
            after.context_bundle.runtime.retry_count
            - before.context_bundle.runtime.retry_count
        )
        if retry_delta < 0:
            raise StateWriteContractViolation(
                changed_paths=["context_bundle.runtime.retry_count"],
                allowed_paths=["context_bundle.runtime.retry_count (+delta)"],
            )

        delta = ParallelSectionStateDeltaSchema(
            generated_section_ids=generated_ids,
            generated_outputs=generated_outputs,
            warnings=warnings,
            errors=errors,
            runtime_errors=runtime_errors,
            metadata_updates=metadata_updates,
            retry_count_delta=retry_delta,
        )
        expected = before.model_copy(deep=True)
        expected.context_bundle.generation.generated_section_ids.extend(
            delta.generated_section_ids
        )
        expected.generated_outputs.update(delta.generated_outputs)
        expected.warnings.extend(delta.warnings)
        expected.errors.extend(delta.errors)
        expected.context_bundle.runtime.errors.extend(delta.runtime_errors)
        expected.context_bundle.runtime.retry_count += delta.retry_count_delta
        expected.context_bundle.generation.metadata.update(
            delta.metadata_updates.get("generation", {})
        )
        expected.context_bundle.runtime.metadata.update(
            delta.metadata_updates.get("runtime", {})
        )
        if expected.model_dump(mode="python") != after.model_dump(mode="python"):
            changed_paths = GraphStateDiffer().changed_paths(
                expected.model_dump(mode="python"),
                after.model_dump(mode="python"),
            )
            raise StateWriteContractViolation(
                changed_paths=changed_paths,
                allowed_paths=[
                    "context_bundle.generation.generated_section_ids",
                    "generated_outputs.<new-section-call-id>",
                    "warnings",
                    "errors",
                    "context_bundle.runtime.errors",
                    "context_bundle.generation.metadata.<section-id>",
                    "context_bundle.runtime.metadata.<section-id>",
                    "context_bundle.runtime.retry_count",
                ],
            )
        return delta

    @staticmethod
    def _apply_parallel_state_delta(
        shared_state: SharedStateSchema,
        delta: ParallelSectionStateDeltaSchema,
    ) -> None:
        """Commit one validated delta in deterministic fan-in order."""

        generated_ids = (
            shared_state.context_bundle.generation.generated_section_ids
        )
        for section_id in delta.generated_section_ids:
            if section_id not in generated_ids:
                generated_ids.append(section_id)
        for key, value in delta.generated_outputs.items():
            if (
                key in shared_state.generated_outputs
                and shared_state.generated_outputs[key] != value
            ):
                raise StateWriteContractViolation(
                    changed_paths=[f"generated_outputs.{key}"],
                    allowed_paths=["generated_outputs.<unique-section-call-id>"],
                )
            shared_state.generated_outputs[key] = value
        shared_state.warnings.extend(delta.warnings)
        shared_state.errors.extend(delta.errors)
        shared_state.context_bundle.runtime.errors.extend(delta.runtime_errors)
        shared_state.context_bundle.runtime.retry_count += delta.retry_count_delta
        for scope, updates in delta.metadata_updates.items():
            target = (
                shared_state.context_bundle.generation.metadata
                if scope == "generation"
                else shared_state.context_bundle.runtime.metadata
            )
            for key, value in updates.items():
                if key in target and target[key] != value:
                    raise StateWriteContractViolation(
                        changed_paths=[
                            f"context_bundle.{scope}.metadata.{key}"
                        ],
                        allowed_paths=["conflict-free section metadata"],
                    )
                target[key] = value

    def execute_sections(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Execute sections serially or through declared safe parallel batches."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="execute_sections",
        )
        document_plan = self._require(
            payload.document_plan,
            field_name="document_plan",
            node_name="execute_sections",
        )
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="execute_sections",
        )
        rag_context = self._require(
            payload.document_rag_context,
            field_name="document_rag_context",
            node_name="execute_sections",
        )
        citation_registry = DocumentCitationRegistry()
        citation_registry.register(
            payload.document_citations,
            scope="document",
            query=project_input.user_query,
        )
        sections = []
        section_evidence_bundles = []
        all_chunks = list(payload.document_retrieved_chunks)
        all_rag_outputs = list(payload.all_rag_outputs)
        print(
            f"[SchemeWriter] 开始逐章节生成，共 {len(document_plan.sections)} 章",
            flush=True,
        )
        ordered_plans = sorted(
            document_plan.sections,
            key=lambda item: (item.section_order, item.section_id),
        )
        section_count = len(ordered_plans)
        base_section_budget = WorkflowBudget.from_policy_metadata(
            self.section_execution_coordinator.generation_quality_metadata
        )
        parallel_budget_config = dict(
            project_input.generation_requirements.extra.get(
                "parallel_section_budget",
                {},
            )
            or {}
        )
        parallel_llm_slice = max(
            1,
            int(
                parallel_budget_config.get(
                    "llm_calls_per_section",
                    base_section_budget.max_total_llm_calls,
                )
            ),
        )
        parallel_token_slice = max(
            256,
            int(
                parallel_budget_config.get(
                    "tokens_per_section",
                    base_section_budget.max_total_tokens,
                )
            ),
        )
        parallel_budget = ParallelSectionBudgetAllocator(
            max_total_llm_calls=max(
                1,
                int(
                    parallel_budget_config.get(
                        "max_total_llm_calls",
                        parallel_llm_slice * max(section_count, 1),
                    )
                ),
            ),
            max_total_tokens=max(
                256,
                int(
                    parallel_budget_config.get(
                        "max_total_tokens",
                        parallel_token_slice * max(section_count, 1),
                    )
                ),
            ),
        )
        section_position = {
            item.section_id: index
            for index, item in enumerate(ordered_plans, start=1)
        }
        completed_ids: set[str] = set()
        results_by_id: dict[str, SectionExecutionResultSchema] = {}
        remaining = {item.section_id: item for item in ordered_plans}

        def completed_sections() -> list:
            return [
                results_by_id[item.section_id].section
                for item in ordered_plans
                if item.section_id in results_by_id
            ]

        def run_one(
            section_plan: SectionPlanSchema,
            *,
            state: SharedStateSchema,
            registry: DocumentCitationRegistry,
            previous_sections: list,
            emit_events: bool,
            parallel_isolated: bool,
            budget_policy_override: dict[str, Any],
        ) -> SectionExecutionResultSchema:
            section_index = section_position[section_plan.section_id]
            if emit_events:
                emit_workflow_custom_event(
                    "section_started",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    progress=(section_index - 1) / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "section_index": section_index,
                        "section_count": section_count,
                    },
                )
            request_data = {
                "document_id": document_id,
                "project_input": project_input,
                "section_plan": section_plan,
                "structured_facts": list(payload.structured_facts),
                "previous_sections": list(previous_sections),
                "document_rag_context": rag_context,
                "document_retrieved_chunks": list(
                    payload.document_retrieved_chunks
                ),
                "document_citations": list(payload.document_citations),
                "document_evidence_assessment": dict(
                    payload.document_evidence_assessment
                ),
                "document_tool_call_ids": list(payload.document_tool_call_ids),
                "section_retrieval_enabled": payload.section_retrieval_enabled,
                "corrective_retrieval_enabled": (
                    payload.corrective_retrieval_enabled
                ),
                "parallel_isolated": parallel_isolated,
                "budget_policy_override": dict(budget_policy_override),
            }
            if self.section_execution_graph is not None:
                section_result = self.section_execution_graph.execute(
                    SectionGraphExecutionRequestSchema(**request_data),
                    shared_state=state,
                    citation_registry=registry,
                    workflow_namespace=payload.workflow_namespace,
                )
            else:
                section_result = self.section_execution_coordinator.execute(
                    SectionExecutionRequestSchema(
                        shared_state=state,
                        **request_data,
                    ),
                    citation_registry=registry,
                )
            if emit_events:
                emit_workflow_custom_event(
                    "section_generated",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    status=section_result.section.status.value,
                    progress=section_index / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "content_chars": len(section_result.section.content),
                    },
                )
                emit_workflow_custom_event(
                    "section_completed",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    status=section_result.section.status.value,
                    progress=section_index / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "section_index": section_index,
                        "section_count": section_count,
                    },
                )
            return section_result

        while remaining:
            ready = [
                item
                for item in ordered_plans
                if item.section_id in remaining
                and set(item.depends_on) <= completed_ids
            ]
            if not ready:
                raise ValueError(
                    "no section is ready; dependency graph is inconsistent"
                )
            first = ready[0]
            parallel_safe = (
                self.section_max_concurrency > 1
                and first.can_run_in_parallel
                and not first.requires_global_context
                and not first.depends_on
            )
            batch = [first]
            if parallel_safe:
                batch = [
                    item
                    for item in ready
                    if item.can_run_in_parallel
                    and not item.requires_global_context
                    and not item.depends_on
                    and item.execution_group == first.execution_group
                ][: self.section_max_concurrency]

            if len(batch) > 1:
                previous = completed_sections()
                base_citations = citation_registry.all()
                budget_reservations = parallel_budget.reserve_batch(
                    [item.section_id for item in batch],
                    llm_calls_per_section=parallel_llm_slice,
                    tokens_per_section=parallel_token_slice,
                )
                for section_plan in batch:
                    emit_workflow_custom_event(
                        "section_started",
                        node_name="execute_sections",
                        section_id=section_plan.section_id,
                        progress=(
                            (section_position[section_plan.section_id] - 1)
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": section_plan.section_title,
                            "section_index": section_position[
                                section_plan.section_id
                            ],
                            "section_count": section_count,
                            "parallel_batch": True,
                        },
                    )

                def execute_isolated(
                    section_plan: SectionPlanSchema,
                ) -> ParallelSectionExecutionEnvelopeSchema:
                    local_state = shared_state.model_copy(deep=True)
                    before_state = local_state.model_copy(deep=True)
                    local_registry = DocumentCitationRegistry.from_citations(
                        base_citations
                    )
                    result = run_one(
                        section_plan,
                        state=local_state,
                        registry=local_registry,
                        previous_sections=previous,
                        emit_events=False,
                        parallel_isolated=True,
                        budget_policy_override=budget_reservations[
                            section_plan.section_id
                        ],
                    )
                    return ParallelSectionExecutionEnvelopeSchema(
                        section_id=section_plan.section_id,
                        result=result,
                        state_delta=self._parallel_state_delta(
                            before_state,
                            local_state,
                            section_id=section_plan.section_id,
                        ),
                    )

                try:
                    batch_results = (
                        self.section_parallel_execution_graph.execute_batch(
                            batch,
                            max_concurrency=self.section_max_concurrency,
                            execute=execute_isolated,
                        )
                    )
                except BaseException as exc:
                    if classify_failure(exc) == FailureCategory.CANCELLED:
                        for section_plan in batch:
                            emit_workflow_custom_event(
                                "section_cancelled",
                                node_name="execute_sections",
                                section_id=section_plan.section_id,
                                status=ExecutionStatus.CANCELLED.value,
                                payload={
                                    "section_title": section_plan.section_title,
                                    "parallel_batch": True,
                                },
                            )
                    raise
                for envelope in batch_results:
                    merged = self._merge_parallel_section_result(
                        envelope.result,
                        document_id=document_id,
                        citation_registry=citation_registry,
                    )
                    self._apply_parallel_state_delta(
                        shared_state,
                        envelope.state_delta,
                    )
                    results_by_id[merged.section.section_id] = merged
                    completed_ids.add(merged.section.section_id)
                    remaining.pop(merged.section.section_id)
                    self.state_writer.set_current_section(
                        shared_state,
                        section_id=merged.section.section_id,
                        section_title=merged.section.section_title,
                    )
                    emit_workflow_custom_event(
                        "section_generated",
                        node_name="execute_sections",
                        section_id=merged.section.section_id,
                        status=merged.section.status.value,
                        progress=(
                            section_position[merged.section.section_id]
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": merged.section.section_title,
                            "content_chars": len(merged.section.content),
                            "parallel_batch": True,
                        },
                    )
                    emit_workflow_custom_event(
                        "section_completed",
                        node_name="execute_sections",
                        section_id=merged.section.section_id,
                        status=merged.section.status.value,
                        progress=(
                            section_position[merged.section.section_id]
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": merged.section.section_title,
                            "section_index": section_position[
                                merged.section.section_id
                            ],
                            "section_count": section_count,
                            "parallel_batch": True,
                        },
                    )
            else:
                result = run_one(
                    first,
                    state=shared_state,
                    registry=citation_registry,
                    previous_sections=completed_sections(),
                    emit_events=True,
                    parallel_isolated=False,
                    budget_policy_override={},
                )
                results_by_id[first.section_id] = result
                completed_ids.add(first.section_id)
                remaining.pop(first.section_id)

        for section_plan in ordered_plans:
            section_result = results_by_id[section_plan.section_id]
            sections.append(section_result.section)
            section_evidence_bundles.append(section_result.evidence)
            all_chunks.extend(section_result.retrieved_chunks)
            all_rag_outputs.extend(section_result.rag_outputs)
        shared_state.context_bundle.generation.metadata[
            "parallel_section_budget"
        ] = parallel_budget.snapshot()
        section_states = dict(payload.section_states)
        for section_id, section_result in results_by_id.items():
            if section_result.section_state is not None:
                section_states[section_id] = section_result.section_state
        return payload.model_copy(
            update={
                "sections": sections,
                "section_states": section_states,
                "section_evidence": section_evidence_bundles,
                "all_retrieved_chunks": all_chunks,
                "all_citations": citation_registry.all(),
                "all_rag_outputs": all_rag_outputs,
            },
            deep=True,
        )

    def document_verify(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run full cross-section verification over canonical production state."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="document_verify",
        )
        verification = self.document_verifier.verify_mainline(
            sections=list(payload.sections),
            section_states=dict(payload.section_states),
            project_input=project_input,
            mode="full_logic",
        )
        return payload.model_copy(
            update={
                "document_verification": verification,
                "conflict_repair_plan": verification.repair_plan,
            },
            deep=True,
        )

    def repair_document_conflicts(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Execute the repair plan strictly serially and fail closed when unresolved."""

        del shared_state
        verification = self._require(
            payload.document_verification,
            field_name="document_verification",
            node_name="repair_document_conflicts",
        )
        plan = payload.conflict_repair_plan or verification.repair_plan
        valid_sections = set(payload.section_states)

        def resolve(task):
            if not task.target_section_ids:
                return RepairTaskStatus.OBSOLETE
            if set(task.target_section_ids) - valid_sections:
                return RepairTaskStatus.FAILED
            # Round 7 owns the serial routing and audit trail, but does not
            # fabricate a successful rewrite. A future successful Section resume
            # may return RESOLVED; until then unresolved business conflicts block.
            return RepairTaskStatus.BLOCKED

        resolved_plan, history = self.conflict_repair_service.execute_serially(
            plan,
            resolver=resolve,
        )
        return payload.model_copy(
            update={
                "conflict_repair_plan": resolved_plan,
                "conflict_repair_history": history,
            },
            deep=True,
        )

    def incremental_document_reverify(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Re-check only Sections affected by the serial repair plan."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="incremental_document_reverify",
        )
        changed_section_ids = {
            section_id
            for item in payload.conflict_repair_history
            for section_id in list(item.get("target_section_ids") or [])
        }
        if changed_section_ids:
            verification = self.document_verifier.verify_mainline(
                sections=list(payload.sections),
                section_states=dict(payload.section_states),
                project_input=project_input,
                mode="incremental_logic",
                changed_section_ids=changed_section_ids,
            )
        else:
            verification = DocumentVerificationResultSchema(
                mode="incremental_logic",
                checked_section_ids=[],
                conflicts=[],
                repair_plan=self.document_verifier._repair_plan([]),
                passed=True,
            )
        return payload.model_copy(
            update={"incremental_document_verification": verification},
            deep=True,
        )

    def assemble_document(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Deterministically aggregate sections and evidence into one draft."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="assemble_document",
        )
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="assemble_document",
        )
        started_at = self._require(
            payload.started_at,
            field_name="started_at",
            node_name="assemble_document",
        )
        assembly = self.document_assembler.assemble(
            DocumentAssemblyRequestSchema(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                document_id=document_id,
                document_title=(
                    project_input.output_schema.document_title
                    or "项目建设方案"
                ),
                required_sections=list(payload.required_sections),
                sections=list(payload.sections),
                retrieved_chunks=list(payload.all_retrieved_chunks),
                citations=list(payload.all_citations),
                section_evidence=list(payload.section_evidence),
                document_evidence_available=payload.document_evidence_available,
                document_assessment_status=payload.document_assessment_status,
                document_evidence_assessment=dict(
                    payload.document_evidence_assessment
                ),
                citation_required_sections=list(
                    payload.required_evidence_sections
                ),
                created_at=started_at,
                updated_at=self.runtime_support.now_iso(),
            )
        )
        return payload.model_copy(
            update={
                "assembly": assembly,
                "generated_draft": assembly.draft,
                "all_retrieved_chunks": list(assembly.retrieved_chunks),
                "all_citations": list(assembly.citations),
            },
            deep=True,
        )

    def validate_document_assembly(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run deterministic validation over the canonical assembled draft."""

        del shared_state
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="validate_document_assembly",
        )
        validation = self.document_assembly_validator.validate_mainline(
            assembly=assembly,
            required_sections=list(payload.required_sections),
        )
        return payload.model_copy(
            update={"assembly_validation": validation},
            deep=True,
        )

    def final_artifact_review(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Review the user-visible assembled draft before the final Hard Gate."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="final_artifact_review",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="final_artifact_review",
        )
        validation = self._require(
            payload.assembly_validation,
            field_name="assembly_validation",
            node_name="final_artifact_review",
        )
        old_names = list(
            project_input.metadata.get("old_project_names") or []
        )
        review = self.final_artifact_review_service.review_mainline(
            draft=assembly.draft,
            assembly_validation=validation,
            old_project_names=[str(item) for item in old_names],
        )
        return payload.model_copy(
            update={"final_artifact_review": review},
            deep=True,
        )

    def decide_document_version(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Apply deterministic immutable version policy without publishing early."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="decide_document_version",
        )
        previous_version = str(
            project_input.metadata.get("previous_document_version") or ""
        ).strip() or None
        raw_change_set = project_input.metadata.get("change_set") or {}
        change_set = ChangeSetSchema.model_validate(raw_change_set)
        decision = self.version_policy_service.decide(
            previous_version=previous_version,
            changes=change_set,
        )
        return payload.model_copy(
            update={
                "version_decision": decision,
                "document_version": decision.next_version,
            },
            deep=True,
        )

    def evaluate_document_gate(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run the document hard gate exactly once and choose an artifact route."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="evaluate_document_gate",
        )
        options = self._require(
            payload.generation_options,
            field_name="generation_options",
            node_name="evaluate_document_gate",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="evaluate_document_gate",
        )
        hard_gate = evaluate_scheme_draft(
            assembly.draft,
            citation_required=options.need_citation,
            citation_required_sections=list(payload.required_evidence_sections),
            retrieved_chunk_ids=set(assembly.known_chunk_ids),
            project_input=project_input,
            canonical_citations=list(assembly.citations),
            tool_failed=False,
            key_fields_valid=bool(
                project_input.task_id
                and project_input.task_type
                and project_input.user_query
                and payload.required_sections
            ),
            output_schema_valid=bool(
                assembly.draft.full_text and assembly.draft.sections
            ),
            evidence_sufficient=assembly.evidence_requirements_satisfied,
            workflow_complete=True,
        )
        verification = (
            payload.incremental_document_verification
            or payload.document_verification
        )
        repair_tasks = list(
            (payload.conflict_repair_plan.repair_tasks)
            if payload.conflict_repair_plan is not None
            else []
        )
        repairs_resolved = all(
            task.status
            in {
                RepairTaskStatus.RESOLVED,
                RepairTaskStatus.OBSOLETE,
            }
            for task in repair_tasks
        )
        closure_checks = {
            "document_verification_passed": bool(
                verification is not None and verification.passed
            ),
            "conflict_repairs_resolved": repairs_resolved,
            "assembly_validation_passed": bool(
                payload.assembly_validation
                and payload.assembly_validation.passed
            ),
            "final_artifact_review_passed": bool(
                payload.final_artifact_review
                and payload.final_artifact_review.passed
            ),
            "document_version_decided": bool(payload.document_version),
        }
        closure_failures: list[str] = []
        if verification is None or not verification.passed:
            closure_failures.append("document_verification_failed")
        if not repairs_resolved:
            closure_failures.append("document_conflict_repairs_unresolved")
        if payload.assembly_validation is None or not payload.assembly_validation.passed:
            closure_failures.extend(
                list(payload.assembly_validation.failures)
                if payload.assembly_validation is not None
                else ["assembly_validation_missing"]
            )
        if payload.final_artifact_review is None or not payload.final_artifact_review.passed:
            closure_failures.extend(
                list(payload.final_artifact_review.issue_codes)
                if payload.final_artifact_review is not None
                else ["final_artifact_review_missing"]
            )
        if not payload.document_version:
            closure_failures.append("document_version_missing")
        if closure_failures:
            hard_gate = hard_gate.model_copy(
                update={
                    "passed": False,
                    "failures": list(
                        dict.fromkeys(
                            [*hard_gate.failures, *closure_failures]
                        )
                    ),
                    "checks": {
                        **dict(hard_gate.checks),
                        **closure_checks,
                    },
                    "metadata": {
                        **dict(hard_gate.metadata),
                        "document_closure_round": "round7",
                        "conflict_repair_history": list(
                            payload.conflict_repair_history
                        ),
                    },
                },
                deep=True,
            )
        else:
            hard_gate = hard_gate.model_copy(
                update={
                    "checks": {
                        **dict(hard_gate.checks),
                        **closure_checks,
                    },
                    "metadata": {
                        **dict(hard_gate.metadata),
                        "document_closure_round": "round7",
                    },
                },
                deep=True,
            )
        print(
            f"[HardGate] passed={hard_gate.passed} "
            f"failures={hard_gate.failures} warnings={hard_gate.warnings}",
            flush=True,
        )
        has_partial_sections = any(
            section.status == ExecutionStatus.PARTIAL_SUCCESS
            for section in payload.sections
        )
        if not hard_gate.passed:
            status = ExecutionStatus.FAILED
        elif has_partial_sections or hard_gate.warnings:
            status = ExecutionStatus.PARTIAL_SUCCESS
        else:
            status = ExecutionStatus.SUCCESS
        output_error = None
        if not hard_gate.passed:
            failure_category, retryable = self._hard_gate_failure_policy(
                hard_gate,
                evidence_requirements_satisfied=(
                    assembly.evidence_requirements_satisfied
                ),
            )
            output_error = self.runtime_support.error(
                "DOCUMENT_HARD_GATE_FAILED",
                "; ".join(hard_gate.failures),
                node="evaluate_document_gate",
                retryable=retryable,
                failure_category=failure_category,
                requires_human=True,
                user_message="文档未通过强制质量校验，不能标记为成功。",
            )
            self.state_writer.add_error(shared_state, output_error)
        output_warnings = [
            WarningSchema(
                warning_code="DOCUMENT_SOFT_GATE_WARNING",
                message=message,
                source=ErrorSourceSchema(
                    component=self.agent_name,
                    agent_name=self.agent_name,
                    step_name="evaluate_document_gate",
                ),
                created_at=self.runtime_support.now_iso(),
            )
            for message in hard_gate.warnings
        ]
        return payload.model_copy(
            update={
                "hard_gate": hard_gate,
                "output_status": status,
                "output_error": output_error,
                "output_warnings": output_warnings,
                "artifact_route": (
                    "deliverable" if hard_gate.passed else "rejected"
                ),
            },
            deep=True,
        )

    def build_generation_report(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Persist a typed business report in the existing top-level checkpoint."""

        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="build_generation_report",
        )
        document_version = self._require(
            payload.document_version,
            field_name="document_version",
            node_name="build_generation_report",
        )
        source_versions: dict[str, str] = {}
        if payload.document_rag_context is not None:
            contract = dict(
                (payload.document_rag_context.extra or {}).get(
                    "evidence_contract"
                )
                or {}
            )
            lineage = dict(contract.get("lineage") or {})
            for key in (
                "index_version",
                "dataset_version",
                "embedding_version",
                "static_retrieval_spec_version",
            ):
                value = str(lineage.get(key) or "").strip()
                if value:
                    source_versions[key] = value
        budget_usage = {
            section.section_id: dict(
                (section.extra or {}).get("workflow_budget") or {}
            )
            for section in payload.sections
        }
        report = self.generation_report_service.build_mainline(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            sections=list(payload.sections),
            section_states=dict(payload.section_states),
            document_version=document_version,
            hard_gate=hard_gate,
            source_versions=source_versions,
            budget_usage={"sections": budget_usage},
        )
        return payload.model_copy(
            update={"generation_report": report},
            deep=True,
        )

    def _persist_output(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        rejected_artifact: RejectedDraftArtifactSchema | None,
        deliverable_final_result: DeliverableFinalResultSchema | None,
    ) -> SchemeGenerationGraphPayloadSchema:
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="persist_artifact",
        )
        scheme_input = self._require(
            payload.scheme_input,
            field_name="scheme_input",
            node_name="persist_artifact",
        )
        document_plan = self._require(
            payload.document_plan,
            field_name="document_plan",
            node_name="persist_artifact",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_artifact",
        )
        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="persist_artifact",
        )
        status = self._require(
            payload.output_status,
            field_name="output_status",
            node_name="persist_artifact",
        )
        output = SchemeWriterOutputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            status=status,
            document_plan=document_plan,
            generated_draft=assembly.draft,
            rejected_artifact=rejected_artifact,
            deliverable_final_result=deliverable_final_result,
            rag_context=payload.document_rag_context,
            retrieved_chunks=list(assembly.retrieved_chunks),
            citations=list(assembly.citations),
            section_evidence=list(payload.section_evidence),
            hard_gate=hard_gate,
            warnings=list(payload.output_warnings),
            error=payload.output_error,
            need_human_review=True,
            extra={
                "scheme_writer_input": scheme_input.model_dump(),
                "document_plan": document_plan.model_dump(),
                "rag_tool_output": dict(payload.document_rag_output),
                "rag_tool_outputs": list(payload.all_rag_outputs),
                "section_evidence": [
                    item.model_dump() for item in payload.section_evidence
                ],
                "section_aware_retrieval_enabled": (
                    payload.section_retrieval_enabled
                ),
                "corrective_section_retrieval_enabled": (
                    payload.corrective_retrieval_enabled
                ),
                "section_count": len(payload.sections),
                "citation_binding_count": len(assembly.citation_bindings),
                "partial_section_count": sum(
                    1
                    for section in payload.sections
                    if section.status == ExecutionStatus.PARTIAL_SUCCESS
                ),
                "semantic_gate_enabled": self.enable_semantic_gate,
                "semantic_evidence_sufficient": (
                    assembly.semantic_evidence_sufficient
                ),
                "evidence_requirements_satisfied": (
                    assembly.evidence_requirements_satisfied
                ),
                "section_evidence_requirements": [
                    item.model_dump()
                    for item in assembly.section_evidence_requirements
                ],
                "document_closure": {
                    "document_verification": (
                        payload.document_verification.model_dump(mode="json")
                        if payload.document_verification is not None
                        else None
                    ),
                    "incremental_document_verification": (
                        payload.incremental_document_verification.model_dump(
                            mode="json"
                        )
                        if payload.incremental_document_verification is not None
                        else None
                    ),
                    "conflict_repair_history": list(
                        payload.conflict_repair_history
                    ),
                    "assembly_validation": (
                        payload.assembly_validation.model_dump(mode="json")
                        if payload.assembly_validation is not None
                        else None
                    ),
                    "final_artifact_review": (
                        payload.final_artifact_review.model_dump(mode="json")
                        if payload.final_artifact_review is not None
                        else None
                    ),
                    "document_version": payload.document_version,
                    "version_decision": (
                        payload.version_decision.model_dump(mode="json")
                        if payload.version_decision is not None
                        else None
                    ),
                    "generation_report": (
                        payload.generation_report.model_dump(mode="json")
                        if payload.generation_report is not None
                        else None
                    ),
                },
            },
        )
        enriched_rag_output = {
            **dict(payload.document_rag_output),
            "section_evidence": [
                item.model_dump() for item in payload.section_evidence
            ],
            "rag_tool_outputs": list(payload.all_rag_outputs),
        }
        self.state_writer.set_scheme_outputs(
            shared_state,
            scheme_writer_input=scheme_input.model_dump(),
            scheme_writer_output=output.model_dump(),
            rag_tool_output=enriched_rag_output,
        )
        self.state_writer.set_scheme_artifacts(
            shared_state,
            generated_draft=assembly.draft.model_dump(),
            rejected_artifact=(
                rejected_artifact.model_dump()
                if rejected_artifact is not None
                else None
            ),
            deliverable_final_result=(
                deliverable_final_result.model_dump()
                if deliverable_final_result is not None
                else None
            ),
        )
        if not payload.capture_completed:
            self.capture_service.capture(
                shared_state,
                scheme_input,
                output,
                enriched_rag_output,
            )
        return payload.model_copy(
            update={
                "scheme_writer_output": output,
                "generated_draft": assembly.draft,
                "rejected_artifact": rejected_artifact,
                "deliverable_final_result": deliverable_final_result,
                "capture_completed": True,
            },
            deep=True,
        )

    def persist_deliverable(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Publish the only deliverable lifecycle after a passed hard gate."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="persist_deliverable",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_deliverable",
        )
        deliverable_final_result = DeliverableFinalResultSchema(
            draft=assembly.draft,
            output_format=project_input.output_schema.output_format,
        )
        return self._persist_output(
            shared_state,
            payload,
            rejected_artifact=None,
            deliverable_final_result=deliverable_final_result,
        )

    def persist_rejected(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Publish a rejected draft without exposing a business deliverable."""

        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_rejected",
        )
        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="persist_rejected",
        )
        rejected_artifact = RejectedDraftArtifactSchema(
            draft=assembly.draft,
            rejection_code="DOCUMENT_HARD_GATE_FAILED",
            rejection_reasons=list(hard_gate.failures),
            hard_gate=hard_gate,
        )
        return self._persist_output(
            shared_state,
            payload,
            rejected_artifact=rejected_artifact,
            deliverable_final_result=None,
        )

    def finalize_workflow_result(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Build the one terminal AgentResult for a business-complete workflow."""

        output = self._require(
            payload.scheme_writer_output,
            field_name="scheme_writer_output",
            node_name="finalize_workflow_result",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="finalize_workflow_result",
        )
        result = AgentResultSchema(
            result_id=f"result_{shared_state.run_id}_scheme",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=output.status,
            result_type="scheme_writer_output",
            result={
                "scheme_writer_output": output.model_dump(),
                "generated_draft": (
                    payload.generated_draft.model_dump()
                    if payload.generated_draft is not None
                    else None
                ),
                "rejected_artifact": (
                    payload.rejected_artifact.model_dump()
                    if payload.rejected_artifact is not None
                    else None
                ),
                "deliverable_final_result": (
                    payload.deliverable_final_result.model_dump()
                    if payload.deliverable_final_result is not None
                    else None
                ),
            },
            citations=list(assembly.citations),
            error=payload.output_error,
            error_message=(
                payload.output_error.message
                if payload.output_error is not None
                else None
            ),
            started_at=payload.started_at,
            finished_at=self.runtime_support.now_iso(),
            need_human_review=True,
            metadata={
                "output_schema": output.schema_version,
                "hard_gate_passed": bool(
                    payload.hard_gate and payload.hard_gate.passed
                ),
                "section_count": len(payload.sections),
                "citation_binding_count": len(assembly.citation_bindings),
                "semantic_evidence_sufficient": (
                    assembly.semantic_evidence_sufficient
                ),
                "evidence_requirements_satisfied": (
                    assembly.evidence_requirements_satisfied
                ),
                "section_evidence_requirements": [
                    item.model_dump()
                    for item in assembly.section_evidence_requirements
                ],
            },
        )
        shared_state.status = result.status
        self.state_writer.set_final_result(shared_state, result.result)
        return payload.model_copy(
            update={
                "terminal_agent_result": result,
                "terminal_error": result.error,
            },
            deep=True,
        )

    def finalize_workflow_failure(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Build a technical-failure result without fabricating an artifact route."""

        error = payload.terminal_error or self.runtime_support.error(
            "SCHEME_WRITER_FAILED",
            "scheme generation graph terminated without a structured error",
            node=payload.failed_business_node or "finalize_workflow_failure",
            failure_category=FailureCategory.UNKNOWN,
            requires_human=True,
            user_message="方案生成主链路发生错误。",
        )
        if error not in shared_state.errors:
            self.state_writer.add_error(shared_state, error)
        self.state_writer.set_scheme_artifacts(
            shared_state,
            generated_draft=(
                payload.generated_draft.model_dump()
                if payload.generated_draft is not None
                else {}
            ),
            rejected_artifact=None,
            deliverable_final_result=None,
        )
        status = (
            ExecutionStatus.RETRYABLE_FAILED
            if error.retryable
            else ExecutionStatus.FAILED
        )
        result = AgentResultSchema(
            result_id=f"result_{shared_state.run_id}_scheme_failed",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=status,
            result_type="scheme_writer_output",
            result={},
            error=error,
            error_message=error.message,
            started_at=payload.started_at,
            finished_at=self.runtime_support.now_iso(),
            need_human_review=True,
            metadata={
                "failed_business_node": payload.failed_business_node,
                "technical_failure": True,
            },
        )
        shared_state.status = status
        return payload.model_copy(
            update={
                "output_status": status,
                "terminal_agent_result": result,
                "terminal_error": error,
                "deliverable_final_result": None,
                "rejected_artifact": None,
                "artifact_route": "technical_failure",
            },
            deep=True,
        )

    def mark_technical_failure(
        self,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        node_name: str,
        exc: Exception,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Normalize one node exception into the structured failure payload."""

        if isinstance(exc, SchemeGenerationStageFailure):
            error = exc.error
        else:
            failure_category = classify_failure(exc)
            retryable = RetryDecisionPolicy().should_retry(failure_category)
            error = self.runtime_support.error(
                "SCHEME_GENERATION_STAGE_FAILED",
                exc if isinstance(exc, Exception) else str(exc),
                node=node_name,
                retryable=retryable,
                failure_category=failure_category,
                requires_human=True,
                user_message="方案生成顶层业务节点执行失败。",
            )
        return payload.model_copy(
            update={
                "terminal_error": error,
                "failed_business_node": node_name,
                "artifact_route": "technical_failure",
            },
            deep=True,
        )

    def _raise_failure(self, error: ErrorSchema) -> NoReturn:
        raise SchemeGenerationStageFailure(error)

    def run_compat(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """Thin legacy facade that calls the same stages in production order."""

        payload = SchemeGenerationGraphPayloadSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            started_at=self.runtime_support.now_iso(),
        )
        try:
            payload = self.prepare_document_plan(shared_state, payload)
            payload = self.retrieve_document_evidence(shared_state, payload)
            payload = self.initialize_generation(shared_state, payload)
            payload = self.execute_sections(shared_state, payload)
            payload = self.assemble_document(shared_state, payload)
            payload = self.evaluate_document_gate(shared_state, payload)
            if payload.artifact_route == "deliverable":
                payload = self.persist_deliverable(shared_state, payload)
            else:
                payload = self.persist_rejected(shared_state, payload)
            payload = self.finalize_workflow_result(shared_state, payload)
        except Exception as exc:
            payload = self.mark_technical_failure(
                payload,
                node_name=shared_state.current_step or self.agent_name,
                exc=exc,
            )
            payload = self.finalize_workflow_failure(shared_state, payload)
        if payload.terminal_agent_result is None:
            error = self.runtime_support.error(
                "SCHEME_WRITER_FAILED",
                "scheme generation compatibility facade produced no result",
                node=self.agent_name,
                failure_category=FailureCategory.UNKNOWN,
            )
            self._raise_failure(error)
        return payload.terminal_agent_result
