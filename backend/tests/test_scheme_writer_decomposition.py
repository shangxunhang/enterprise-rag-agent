from pathlib import Path

from agent.base_agent import BaseAgent
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.services.scheme_writer import SchemeGenerationUseCase
from apps.enterprise_document.services.scheme_writer.advisory_service import SectionAdvisoryService
from apps.enterprise_document.services.scheme_writer.capture_service import SchemeCaptureService
from apps.enterprise_document.services.scheme_writer.citation_service import CitationService
from apps.enterprise_document.services.scheme_writer.document_citation_registry import DocumentCitationRegistry
from apps.enterprise_document.services.scheme_writer.document_planning_service import DocumentPlanningService
from apps.enterprise_document.services.scheme_writer.evidence_service import SchemeEvidenceService
from apps.enterprise_document.services.scheme_writer.grounding_repair_service import GroundingRepairService
from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import LegacyEvidenceAdapter
from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
from apps.enterprise_document.services.scheme_writer.model_service import SectionModelService
from apps.enterprise_document.services.scheme_writer.prompt_service import SectionPromptService
from apps.enterprise_document.services.scheme_writer.runtime_support import SchemeWriterRuntimeSupport
from apps.enterprise_document.services.scheme_writer.section_generation_service import SectionGenerationService
from apps.enterprise_document.services.scheme_writer.section_retrieval_query_builder import SectionRetrievalQueryBuilder


def test_scheme_writer_agent_is_protocol_shell_without_forwarding_facade() -> None:
    assert issubclass(SchemeWriterAgent, BaseAgent)
    assert SchemeWriterAgent.__bases__ == (BaseAgent,)
    assert "SchemeWriterServiceFacade" not in SchemeWriterAgent.__mro__


def test_scheme_writer_services_have_explicit_dependencies() -> None:
    agent = SchemeWriterAgent()

    assert isinstance(agent.use_case, SchemeGenerationUseCase)
    assert agent.use_case.input_service is agent.input_service
    assert agent.use_case.evidence_service is agent.evidence_service
    assert not hasattr(agent.use_case, "section_generation_service")
    assert (
        agent.use_case.section_execution_coordinator
        is agent.section_execution_coordinator
    )
    assert agent.use_case.document_assembler is agent.document_assembler
    assert (
        agent.section_execution_coordinator.evidence_service
        is agent.evidence_service
    )
    assert (
        agent.section_execution_coordinator.section_generation_service
        is agent.section_generation_service
    )
    assert (
        agent.section_execution_coordinator.query_builder
        is agent.section_retrieval_query_builder
    )
    assert agent.section_execution_coordinator.runtime_support is agent.runtime_support
    assert (
        agent.section_execution_coordinator.generation_quality_metadata
        == agent.section_generation_service.generation_quality_metadata
    )
    assert agent.section_generation_service.model_service is agent.model_service
    assert (
        agent.section_generation_service.citation_service
        is agent.citation_service
    )
    assert (
        agent.section_generation_service.grounding_repair_service
        is agent.grounding_repair_service
    )
    assert agent.grounding_repair_service.citation_service is agent.citation_service
    assert agent.grounding_repair_service.model_service is agent.model_service
    assert agent.grounding_repair_service.prompt_service is agent.prompt_service


def test_section_coordinator_owns_budget_policy_without_reaching_into_generation_service() -> None:
    coordinator_source = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
        / "section_execution_coordinator.py"
    ).read_text(encoding="utf-8")

    assert "self.section_generation_service.generation_quality_metadata" not in coordinator_source
    assert "self.section_generation_service.runtime_support" not in coordinator_source


def test_scheme_writer_business_boundaries_expose_public_entrypoints() -> None:
    assert hasattr(SchemeInputService, "read_inputs")
    assert not hasattr(SchemeInputService, "_read_inputs")

    assert hasattr(DocumentPlanningService, "build_document_plan")
    assert not hasattr(DocumentPlanningService, "_build_document_plan")

    for name in ("retrieve", "extract_rag_output"):
        assert hasattr(SchemeEvidenceService, name)
    assert not hasattr(SchemeEvidenceService, "build_section_query")
    assert not hasattr(SchemeEvidenceService, "remap_bundle_citations")

    assert hasattr(SectionRetrievalQueryBuilder, "build")
    assert hasattr(DocumentCitationRegistry, "register")
    assert hasattr(DocumentCitationRegistry, "remap_bundle")
    assert hasattr(LegacyEvidenceAdapter, "extract")

    for legacy_name in (
        "_call_rag_tool",
        "_build_section_query",
        "_remap_bundle_citations",
        "_extract_rag_output",
    ):
        assert not hasattr(SchemeEvidenceService, legacy_name)

    assert hasattr(SectionGenerationService, "generate_section")
    assert hasattr(SectionGenerationService, "build_insufficient_evidence_section")
    assert not hasattr(SectionGenerationService, "_generate_section")
    assert not hasattr(
        SectionGenerationService,
        "_build_insufficient_evidence_section",
    )

    public_surfaces = {
        SectionModelService: (
            "call_model",
            "retry_truncated_section",
            "recover_complete_prefix",
            "compress_overlong_section",
        ),
        SectionPromptService: (
            "citation_catalog",
            "target_section_chars",
            "section_generation_contract",
            "render_section_prompt",
        ),
        CitationService: (
            "bind_citations",
            "citation_match_tokens",
            "supported_bindings",
            "insert_deterministic_citations",
            "strip_known_citation_markers",
        ),
        GroundingRepairService: (
            "repair_section_citations",
            "regenerate_section_from_evidence",
        ),
        SectionAdvisoryService: ("project_fact_violations",),
        SchemeWriterRuntimeSupport: ("now_iso", "error"),
    }
    for service_type, names in public_surfaces.items():
        for name in names:
            assert hasattr(service_type, name)
            assert not hasattr(service_type, f"_{name}")

    assert not hasattr(CitationService, "model_service")
    assert not hasattr(CitationService, "prompt_service")
    assert not hasattr(CitationService, "repair_section_citations")
    assert not hasattr(CitationService, "regenerate_section_from_evidence")

    assert hasattr(SchemeCaptureService, "capture")
    assert not hasattr(SchemeCaptureService, "_capture")


def test_top_level_orchestrators_do_not_call_legacy_private_service_entrypoints() -> None:
    service_dir = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
    )
    source = "\n".join(
        (service_dir / filename).read_text(encoding="utf-8")
        for filename in ("use_case.py", "section_execution_coordinator.py")
    )
    forbidden = (
        ". _read_inputs(",
        "._read_inputs(",
        "._build_document_plan(",
        "._call_rag_tool(",
        "._build_section_query(",
        "._remap_bundle_citations(",
        "._extract_rag_output(",
        "._build_insufficient_evidence_section(",
        "._generate_section(",
        "._capture(",
    )
    for marker in forbidden:
        assert marker not in source


def test_scheme_writer_collaborators_do_not_cross_private_boundaries() -> None:
    service_dir = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in service_dir.glob("*.py")
    )
    collaborator_names = (
        "input_service",
        "document_planning_service",
        "evidence_service",
        "section_retrieval_query_builder",
        "section_generation_service",
        "grounding_repair_service",
        "capture_service",
        "runtime_support",
        "prompt_service",
        "model_service",
        "citation_service",
        "advisory_service",
    )
    for collaborator in collaborator_names:
        assert f"self.{collaborator}._" not in source


def test_hidden_runtime_delegation_layer_is_deleted() -> None:
    service_dir = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
    )
    assert not (service_dir / "facade.py").exists()
    assert not (service_dir / "base.py").exists()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in service_dir.glob("*.py")
    )
    assert "__getattr__" not in source
    assert "RuntimeBoundService" not in source
