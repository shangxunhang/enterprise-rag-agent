"""Legacy scheme-generation facade backed by the Phase 2 stage service."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.agent import AgentResultSchema

from .capture_service import SchemeCaptureService
from .document_assembler import DocumentAssembler
from .document_planning_service import DocumentPlanningService
from .evidence_service import SchemeEvidenceService
from .input_service import SchemeInputService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_execution_coordinator import SectionExecutionCoordinator
from .stage_service import SchemeGenerationStageService


class SchemeGenerationUseCase:
    """Compatibility entry point; production orchestration is owned by LangGraph."""

    # Preserve the legacy policy seam without duplicating its implementation.
    _hard_gate_failure_policy = staticmethod(
        SchemeGenerationStageService._hard_gate_failure_policy
    )

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
        stage_service: SchemeGenerationStageService | None = None,
    ) -> None:
        # Keep the stable public seams used by compatibility tests and callers.
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
        self.stage_service = stage_service or SchemeGenerationStageService(
            input_service=input_service,
            evidence_service=evidence_service,
            section_execution_coordinator=section_execution_coordinator,
            document_assembler=document_assembler,
            document_planning_service=document_planning_service,
            capture_service=capture_service,
            runtime_support=runtime_support,
            agent_name=agent_name,
            agent_type=agent_type,
            rag_service_name=rag_service_name,
            enable_semantic_gate=enable_semantic_gate,
            section_max_concurrency=section_max_concurrency,
        )

    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """Execute the compatibility path through the canonical stage methods."""

        return self.stage_service.run_compat(shared_state)
