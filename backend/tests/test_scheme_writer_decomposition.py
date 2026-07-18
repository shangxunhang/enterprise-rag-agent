from pathlib import Path

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.services.scheme_writer import SchemeWriterServiceFacade


def test_scheme_writer_agent_is_thin_protocol_facade() -> None:
    source_path = Path(__file__).parents[1] / "apps" / "enterprise_document" / "agents" / "scheme_writer_agent.py"
    assert len(source_path.read_text(encoding="utf-8").splitlines()) <= 100
    assert issubclass(SchemeWriterAgent, SchemeWriterServiceFacade)


def test_scheme_writer_services_are_composed_on_construction() -> None:
    agent = SchemeWriterAgent()
    assert agent._input_service is not None
    assert agent._evidence_service is not None
    assert agent._prompt_service is not None
    assert agent._model_service is not None
    assert agent._citation_service is not None
    assert agent._section_generation_service is not None
    assert agent._scheme_generation_use_case is not None
