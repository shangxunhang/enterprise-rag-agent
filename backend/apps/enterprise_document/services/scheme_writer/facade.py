"""Compatibility facade for the decomposed SchemeWriter services.

The legacy private-method surface is intentionally retained while callers and
tests migrate.  Each method delegates to one focused service; the agent itself
contains no document-generation implementation.
"""


from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentPlanSchema,
    SchemeSectionSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema, TableAnalysisSchema
from schemas.agent import AgentResultSchema
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.common import ErrorSchema
from schemas.model import ModelResponseSchema
from schemas.prompt import PromptRenderResultSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.tool import ToolResultSchema

from .advisory_service import SectionAdvisoryService
from .capture_service import SchemeCaptureService
from .citation_service import CitationService
from .document_planning_service import DocumentPlanningService
from .evidence_service import DocumentCitationRegistry, SchemeEvidenceService
from .input_service import SchemeInputService
from .model_service import SectionModelService
from .prompt_service import SectionPromptService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_generation_service import SectionGenerationService
from .use_case import SchemeGenerationUseCase


class SchemeWriterServiceFacade:
    """Thin delegation layer shared by ``SchemeWriterAgent`` subclasses."""

    def _init_scheme_writer_services(self) -> None:
        self.context_manager = getattr(self, "context_manager", None) or LLMContextManager()
        self.context_policy = getattr(self, "context_policy", None) or SectionGenerationContextPolicy()
        self._runtime_support = SchemeWriterRuntimeSupport(self)
        self._input_service = SchemeInputService(self)
        self._evidence_service = SchemeEvidenceService(self)
        self._prompt_service = SectionPromptService(self)
        self._model_service = SectionModelService(self)
        self._citation_service = CitationService(self)
        self._advisory_service = SectionAdvisoryService(self)
        self._section_generation_service = SectionGenerationService(self)
        self._document_planning_service = DocumentPlanningService(self)
        self._capture_service = SchemeCaptureService(self)
        self._scheme_generation_use_case = SchemeGenerationUseCase(self)

    @staticmethod
    def _now_iso() -> str:
        return SchemeWriterRuntimeSupport._now_iso()

    @staticmethod
    def _error(
        code: str,
        exc_or_message: Exception | str,
        *,
        node: str,
        retryable: bool = False,
        user_message: Optional[str] = None,
    ) -> ErrorSchema:
        return SchemeWriterRuntimeSupport._error(
            code,
            exc_or_message,
            node=node,
            retryable=retryable,
            user_message=user_message,
        )

    def _read_inputs(
        self, shared_state: SharedStateSchema
    ) -> Tuple[ProjectInputSchema, TableAnalysisSchema, List[StructuredFactSchema]]:
        return self._input_service._read_inputs(shared_state)

    def _call_rag_tool(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        **kwargs: Any,
    ) -> Optional[ToolResultSchema]:
        return self._evidence_service._call_rag_tool(
            shared_state, project_input, **kwargs
        )

    @staticmethod
    def _build_section_query(
        project_input: ProjectInputSchema,
        section_title: str,
        *,
        recovery: bool = False,
    ) -> str:
        return SchemeEvidenceService._build_section_query(
            project_input, section_title, recovery=recovery
        )

    @staticmethod
    def _remap_bundle_citations(**kwargs: Any):
        return SchemeEvidenceService._remap_bundle_citations(**kwargs)

    @staticmethod
    def _extract_rag_output(
        shared_state: SharedStateSchema,
        result: Optional[ToolResultSchema],
    ) -> Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema], Dict[str, Any]]:
        return SchemeEvidenceService._extract_rag_output(shared_state, result)

    @staticmethod
    def _citation_catalog(citations: Iterable[CitationSchema]) -> str:
        return SectionPromptService._citation_catalog(citations)

    @staticmethod
    def _target_section_chars(project_input: ProjectInputSchema) -> int:
        return SectionPromptService._target_section_chars(project_input)

    @staticmethod
    def _has_concrete_resource_input(project_input: ProjectInputSchema) -> bool:
        return SectionPromptService._has_concrete_resource_input(project_input)

    @classmethod
    def _section_generation_contract(
        cls,
        section_title: str,
        project_input: ProjectInputSchema,
    ) -> str:
        return SectionPromptService._section_generation_contract(section_title, project_input)

    @staticmethod
    def _section_scope_violations(
        content: str,
        section_title: str,
    ) -> List[Dict[str, Any]]:
        return SectionPromptService._section_scope_violations(content, section_title)

    def _render_section_prompt(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        section_id: str,
        section_title: str,
        section_order: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        previous_sections: List[SchemeSectionSchema],
    ) -> PromptRenderResultSchema:
        return self._prompt_service._render_section_prompt(
            shared_state,
            project_input,
            section_id,
            section_title,
            section_order,
            rag_context,
            citations,
            previous_sections,
        )

    def _call_model(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._model_service._call_model(shared_state, **kwargs)

    def _continue_truncated_section(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._model_service._continue_truncated_section(shared_state, **kwargs)

    def _retry_truncated_section(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._model_service._retry_truncated_section(shared_state, **kwargs)

    @staticmethod
    def _recover_complete_prefix(content: str, *, min_chars: int, max_chars: int) -> Optional[str]:
        return SectionModelService._recover_complete_prefix(
            content,
            min_chars=min_chars,
            max_chars=max_chars,
        )

    def _compress_overlong_section(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._model_service._compress_overlong_section(shared_state, **kwargs)

    def _rewrite_invalid_section(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._model_service._rewrite_invalid_section(shared_state, **kwargs)

    @staticmethod
    def _claim_for_marker(paragraph: str, marker: str, marker_position: Optional[int] = None) -> str:
        return CitationService._claim_for_marker(paragraph, marker, marker_position)

    @staticmethod
    def _bind_citations(
        document_id: str,
        section_id: str,
        content: str,
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        return CitationService._bind_citations(
            document_id=document_id,
            section_id=section_id,
            content=content,
            citations=citations,
        )

    @staticmethod
    def _citation_match_tokens(text: str) -> set[str]:
        return CitationService._citation_match_tokens(text)

    @classmethod
    def _citation_support_score(cls, claim: str, evidence: CitationSchema) -> float:
        return CitationService._citation_support_score(claim, evidence)

    @classmethod
    def _binding_is_supported(
        cls,
        binding: CitationBindingSchema,
        citation_map: Dict[str, CitationSchema],
    ) -> bool:
        return CitationService._binding_is_supported(binding, citation_map)

    @classmethod
    def _supported_bindings(
        cls,
        bindings: List[CitationBindingSchema],
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        return CitationService._supported_bindings(bindings, citations)

    @staticmethod
    def _project_fact_corpus(
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> str:
        return SectionAdvisoryService._project_fact_corpus(project_input, citations)

    @classmethod
    def _project_fact_violations(
        cls,
        content: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> List[Dict[str, Any]]:
        return SectionAdvisoryService._project_fact_violations(
            content,
            project_input,
            citations,
        )

    @classmethod
    def _insert_deterministic_citations(
        cls,
        content: str,
        citations: List[CitationSchema],
        *,
        max_bindings: int = 3,
    ) -> Tuple[str, List[Tuple[str, str, float]]]:
        return CitationService._insert_deterministic_citations(
            content,
            citations,
            max_bindings=max_bindings,
        )

    @staticmethod
    def _strip_known_citation_markers(content: str, citation_ids: Iterable[str]) -> str:
        return CitationService._strip_known_citation_markers(content, citation_ids)

    def _repair_section_citations(
        self,
        shared_state: SharedStateSchema,
        **kwargs: Any,
    ) -> Tuple[str, Optional[ModelResponseSchema]]:
        return self._citation_service._repair_section_citations(shared_state, **kwargs)

    def _regenerate_section_from_evidence(self, shared_state: SharedStateSchema, **kwargs: Any) -> ModelResponseSchema:
        return self._citation_service._regenerate_section_from_evidence(shared_state, **kwargs)

    def _generate_section(self, shared_state: SharedStateSchema, **kwargs: Any) -> SchemeSectionSchema:
        return self._section_generation_service._generate_section(shared_state, **kwargs)

    @staticmethod
    def _build_document_plan(
        *,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        required_sections: List[str],
        created_at: str,
    ) -> DocumentPlanSchema:
        return DocumentPlanningService._build_document_plan(
            run_id=run_id,
            document_id=document_id,
            project_input=project_input,
            required_sections=required_sections,
            created_at=created_at,
        )

    def _capture(self, shared_state: SharedStateSchema, scheme_input: Any, output: Any, rag_output: Dict[str, Any]) -> None:
        self._capture_service._capture(shared_state, scheme_input, output, rag_output)

    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        return self._scheme_generation_use_case.run(shared_state)
