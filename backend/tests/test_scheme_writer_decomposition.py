from pathlib import Path

from agent.base_agent import BaseAgent
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.services.scheme_writer import SchemeGenerationUseCase


def test_scheme_writer_agent_is_protocol_shell_without_forwarding_facade() -> None:
    assert issubclass(SchemeWriterAgent, BaseAgent)
    assert SchemeWriterAgent.__bases__ == (BaseAgent,)
    assert "SchemeWriterServiceFacade" not in SchemeWriterAgent.__mro__


def test_scheme_writer_services_have_explicit_dependencies() -> None:
    agent = SchemeWriterAgent()

    assert isinstance(agent.use_case, SchemeGenerationUseCase)
    assert agent.use_case.input_service is agent.input_service
    assert agent.use_case.evidence_service is agent.evidence_service
    assert (
        agent.use_case.section_generation_service
        is agent.section_generation_service
    )
    assert agent.section_generation_service.model_service is agent.model_service
    assert (
        agent.section_generation_service.citation_service
        is agent.citation_service
    )


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
