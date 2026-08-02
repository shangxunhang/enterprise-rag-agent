"""Contracts retained after retirement of the duplicate Standalone control plane."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from apps.enterprise_document.runtime.agent_rag_v1.contracts import (
    MultiAgentProjectInputSchema,
)
from apps.enterprise_document.runtime.agent_rag_v1.standalone_adapters import (
    FixtureFactSnapshotProvider,
    FixtureTemplateProvider,
    MarkdownDocumentExporter,
    ScenarioAgentReasoner,
    ScenarioRetrievalService,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory


@pytest.mark.unit
def test_fixture_project_input_still_rejects_zero_parallelism() -> None:
    with pytest.raises(ValidationError):
        MultiAgentProjectInputSchema(max_parallel_sections=0)


@pytest.mark.contract
def test_fake_adapters_remain_stable_fixture_ports() -> None:
    assert callable(getattr(ScenarioAgentReasoner, "invoke"))
    assert callable(getattr(ScenarioRetrievalService, "retrieve"))
    assert callable(getattr(FixtureFactSnapshotProvider, "load"))
    assert callable(getattr(FixtureTemplateProvider, "load"))
    assert callable(getattr(MarkdownDocumentExporter, "export"))


@pytest.mark.component_integration
def test_supervisor_factory_is_the_only_production_composition_root() -> None:
    source = inspect.getsource(SupervisorFactory.build)
    assert "EnterpriseDocumentWorkflowEngine(" in source
    assert "create_sqlite_checkpoint_resource(" in source
    assert "RuntimeFactory" not in source
    assert "WorkflowOrchestrator" not in source
    assert "MultiAgentApplicationService" not in source


@pytest.mark.component_integration
def test_production_graphs_do_not_import_fixture_control_plane() -> None:
    source = "\n".join(
        (
            inspect.getsource(EnterpriseDocumentWorkflowEngine),
            inspect.getsource(SectionExecutionGraph),
        )
    )
    for symbol in (
        "WorkflowOrchestrator",
        "RuntimeFactory",
        "MultiAgentApplicationService",
        "JsonCheckpointStore",
        "TaskStatusCalculator",
        "WorkflowRouter",
        "ResumeRouter",
    ):
        assert symbol not in source
