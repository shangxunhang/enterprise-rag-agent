from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.enterprise_document.runtime.agent_rag_v1 import (
    MultiAgentApplicationService,
    MultiAgentProjectInputSchema,
    RuntimeFactory,
)
from apps.enterprise_document.runtime.agent_rag_v1.standalone_adapters import (
    FixtureFactSnapshotProvider,
    FixtureTemplateProvider,
    MarkdownDocumentExporter,
    ScenarioAgentReasoner,
    ScenarioRetrievalService,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    DocumentAssemblyValidator,
    DocumentVerifierService,
    StateGateway,
    WorkflowRouter,
)


@pytest.mark.unit
def test_project_input_rejects_zero_parallelism() -> None:
    with pytest.raises(ValidationError):
        MultiAgentProjectInputSchema(max_parallel_sections=0)


@pytest.mark.contract
def test_standalone_adapters_expose_the_stable_port_methods() -> None:
    assert callable(getattr(ScenarioAgentReasoner, "invoke"))
    assert callable(getattr(ScenarioRetrievalService, "retrieve"))
    assert callable(getattr(FixtureFactSnapshotProvider, "load"))
    assert callable(getattr(FixtureTemplateProvider, "load"))
    assert callable(getattr(MarkdownDocumentExporter, "export"))


@pytest.mark.component_integration
def test_runtime_factory_is_the_single_composition_root(tmp_path: Path) -> None:
    project_input = MultiAgentProjectInputSchema(
        task_id="factory-contract",
        run_id="run-001",
        output_dir=str(tmp_path),
    )

    components = RuntimeFactory().create(project_input)

    assert isinstance(components.state_gateway, StateGateway)
    assert isinstance(components.workflow_router, WorkflowRouter)
    assert isinstance(components.document_verifier, DocumentVerifierService)
    assert isinstance(
        components.assembly_validator,
        DocumentAssemblyValidator,
    )
    assert {
        components.planner_agent.role.value,
        components.research_agent.role.value,
        components.writer_agent.role.value,
        components.verifier_agent.role.value,
    } == {"planner", "research", "writer", "verifier"}


@pytest.mark.component_integration
def test_application_service_routes_retrieval_through_injected_port(
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, int]] = []

    class RecordingRetrieval:
        def retrieve(self, request):
            observed.append((request.section_id, request.research_round))
            return ScenarioRetrievalService(project_input).retrieve(request)

    project_input = MultiAgentProjectInputSchema(
        task_id="retrieval-port-contract",
        run_id="run-001",
        output_dir=str(tmp_path),
    )
    factory = RuntimeFactory(
        retrieval_factory=lambda _: RecordingRetrieval(),
    )

    result = MultiAgentApplicationService(factory).run(project_input)

    assert result.task_status.value == "success"
    assert observed == [("compute", 1), ("storage", 1)]
    assert result.trace.retrieval_tool_calls == 2
