"""Scheme generation agent protocol shell and explicit service composition."""

from __future__ import annotations

from typing import Optional

from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.services.semantic_section_judge import SemanticSectionJudge
from apps.enterprise_document.services.scheme_writer.advisory_service import (
    SectionAdvisoryService,
)
from apps.enterprise_document.services.scheme_writer.capture_service import (
    SchemeCaptureService,
)
from apps.enterprise_document.services.scheme_writer.citation_service import (
    CitationService,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import (
    DocumentAssembler,
)
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.grounding_repair_service import (
    GroundingRepairService,
)
from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
from apps.enterprise_document.services.scheme_writer.model_service import SectionModelService
from apps.enterprise_document.services.scheme_writer.prompt_service import SectionPromptService
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
)
from apps.enterprise_document.services.scheme_writer.section_retrieval_query_builder import (
    SectionRetrievalQueryBuilder,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from contracts.observability import DataCaptureSink
from contracts.rag import RAGServicePort
from model_gateway.model_gateway import ModelGateway
from prompt_manager.prompt_manager import PromptManager
from schemas.agent import AgentResultSchema


class SchemeWriterAgent(BaseAgent):
    """Expose the Agent protocol while delegating business flow to one use case."""

    agent_name = "SchemeWriterAgent"
    agent_type = "sub_agent"

    def __init__(
        self,
        rag_service: RAGServicePort | None = None,
        model_gateway: Optional[ModelGateway] = None,
        data_capture_recorder: Optional[DataCaptureSink] = None,
        prompt_manager: Optional[PromptManager] = None,
        prompt_id: str = "scheme_section_generation_v1",
        model_name: str = "fake_llm",
        rag_service_name: str = "RAGService",
        enable_agent_self_rag: bool = True,
        enable_semantic_gate: bool = False,
        semantic_gate_model_name: Optional[str] = None,
        generation_checker: object | None = None,
        repair_strategy: object | None = None,
        generation_quality_metadata: Optional[dict] = None,
    ) -> None:
        # Long-lived adapters and configuration are owned by the composition
        # root.  Focused services receive only the collaborators they use.
        context_manager = LLMContextManager()
        context_policy = SectionGenerationContextPolicy()
        semantic_model_name = semantic_gate_model_name or model_name

        self.runtime_support = SchemeWriterRuntimeSupport()
        self.input_service = SchemeInputService()
        self.section_retrieval_query_builder = SectionRetrievalQueryBuilder()
        self.evidence_service = SchemeEvidenceService(
            rag_service=rag_service,
            agent_name=self.agent_name,
            model_gateway=model_gateway,
        )
        self.prompt_service = SectionPromptService(
            context_manager=context_manager,
            context_policy=context_policy,
            prompt_manager=prompt_manager,
            prompt_id=prompt_id,
            model_gateway=model_gateway,
        )
        self.model_service = SectionModelService(
            model_gateway=model_gateway,
            model_name=model_name,
            agent_name=self.agent_name,
            context_manager=context_manager,
            prompt_service=self.prompt_service,
            runtime_support=self.runtime_support,
        )

        # Public test seam: specialized test agents may override call_model
        # without reaching into SectionModelService private implementation details.
        model_override = self.__class__.__dict__.get("call_model")
        if model_override is not None:
            self.model_service.call_model = self.call_model  # type: ignore[attr-defined,method-assign]

        self.citation_service = CitationService()
        self.grounding_repair_service = GroundingRepairService(
            model_service=self.model_service,
            prompt_service=self.prompt_service,
            citation_service=self.citation_service,
        )
        self.advisory_service = SectionAdvisoryService()
        self.section_generation_service = SectionGenerationService(
            runtime_support=self.runtime_support,
            prompt_service=self.prompt_service,
            model_service=self.model_service,
            citation_service=self.citation_service,
            grounding_repair_service=self.grounding_repair_service,
            advisory_service=self.advisory_service,
            semantic_judge=SemanticSectionJudge(
                model_gateway=model_gateway,
                model_name=semantic_model_name,
                enabled=enable_semantic_gate,
            ),
            enable_semantic_gate=enable_semantic_gate,
            semantic_gate_model_name=semantic_model_name,
            generation_checker=generation_checker,
            repair_strategy=repair_strategy,
            generation_quality_metadata=generation_quality_metadata,
        )
        self.section_execution_coordinator = SectionExecutionCoordinator(
            evidence_service=self.evidence_service,
            query_builder=self.section_retrieval_query_builder,
            section_generation_service=self.section_generation_service,
            runtime_support=self.runtime_support,
            generation_quality_metadata=generation_quality_metadata,
        )
        self.document_assembler = DocumentAssembler()
        self.document_planning_service = DocumentPlanningService()
        self.capture_service = SchemeCaptureService(
            data_capture_recorder=data_capture_recorder,
            agent_name=self.agent_name,
        )
        self.use_case = SchemeGenerationUseCase(
            input_service=self.input_service,
            evidence_service=self.evidence_service,
            section_execution_coordinator=self.section_execution_coordinator,
            document_assembler=self.document_assembler,
            document_planning_service=self.document_planning_service,
            capture_service=self.capture_service,
            runtime_support=self.runtime_support,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            rag_service_name=rag_service_name,
            enable_semantic_gate=enable_semantic_gate,
        )

    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        return self.use_case.run(shared_state)
