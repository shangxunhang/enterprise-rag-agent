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
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
from apps.enterprise_document.services.scheme_writer.model_service import SectionModelService
from apps.enterprise_document.services.scheme_writer.prompt_service import SectionPromptService
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
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
        self.evidence_service = SchemeEvidenceService(
            rag_service=rag_service,
            agent_name=self.agent_name,
        )
        self.prompt_service = SectionPromptService(
            context_manager=context_manager,
            context_policy=context_policy,
            prompt_manager=prompt_manager,
            prompt_id=prompt_id,
        )
        self.model_service = SectionModelService(
            model_gateway=model_gateway,
            model_name=model_name,
            agent_name=self.agent_name,
            context_manager=context_manager,
            prompt_service=self.prompt_service,
            runtime_support=self.runtime_support,
        )

        # Temporary migration hook for tests that still subclass the old agent
        # to fake model calls.  Those callers are migrated in this refactor and
        # the hook is removed once no override remains.
        legacy_model_override = self.__class__.__dict__.get("_call_model")
        if legacy_model_override is not None:
            self.model_service._call_model = self._call_model  # type: ignore[attr-defined,method-assign]

        self.citation_service = CitationService(
            model_service=self.model_service,
            prompt_service=self.prompt_service,
        )
        self.advisory_service = SectionAdvisoryService()
        self.section_generation_service = SectionGenerationService(
            runtime_support=self.runtime_support,
            prompt_service=self.prompt_service,
            model_service=self.model_service,
            citation_service=self.citation_service,
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
        self.document_planning_service = DocumentPlanningService()
        self.capture_service = SchemeCaptureService(
            data_capture_recorder=data_capture_recorder,
            agent_name=self.agent_name,
        )
        self.use_case = SchemeGenerationUseCase(
            input_service=self.input_service,
            evidence_service=self.evidence_service,
            section_generation_service=self.section_generation_service,
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
