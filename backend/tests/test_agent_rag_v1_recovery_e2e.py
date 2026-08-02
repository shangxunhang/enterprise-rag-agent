"""Recovery ownership moved from Standalone replay to production LangGraph."""

from __future__ import annotations

import inspect

from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import (
    MainlineAttemptGuard,
    MainlineReplanAttempt,
    MainlineRunningInvocationRecovery,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)


def test_mainline_owns_attempt_running_invocation_and_replan_recovery() -> None:
    assert callable(MainlineAttemptGuard.validate_result_metadata)
    assert callable(MainlineRunningInvocationRecovery.decide)
    assert callable(MainlineReplanAttempt.prepare)


def test_section_graph_owns_crash_resume_and_replan_entrypoints() -> None:
    assert callable(SectionExecutionGraph.resume)
    assert callable(SectionExecutionGraph.replan)
    assert callable(SectionExecutionGraph.recover_running_invocation)
    source = inspect.getsource(SectionExecutionGraph)
    assert "JsonCheckpointStore" not in source
    assert "EventReplayRecoveryService" not in source


def test_top_level_graph_owns_durable_human_review_resume() -> None:
    assert callable(EnterpriseDocumentWorkflowEngine.resume)
    source = inspect.getsource(EnterpriseDocumentWorkflowEngine)
    assert "human review decision snapshot is stale" in source
    assert "WorkflowOrchestrator" not in source
    assert "HumanReviewResumeService" not in source
