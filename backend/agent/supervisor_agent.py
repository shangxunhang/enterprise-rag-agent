"""Thin supervisor agent facade.

Routing, context construction, task lifecycle, tracing and workflow execution
are implemented by dedicated services under ``agent.services``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from agent.agent_registry import AgentRegistry
from contracts.observability import TraceSink
from contracts.task_state import TaskStateManager
from agent.runtime.workflow_executor import WorkflowExecutor
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.services.context_factory import ContextBundleFactory
from agent.services.supervisor_service import SupervisorService
from agent.services.task_lifecycle import TaskLifecycleService
from agent.services.workflow_router import (
    RoutingDecision,
    WorkflowCatalog,
    WorkflowRouter,
)
from agent.services.workflow_trace import WorkflowTraceService
from core.error_factory import ErrorFactory
from model_gateway.model_gateway import ModelGateway
from schemas.agent import AgentResultSchema
from schemas.context import ContextBundleSchema
from schemas.model import ModelResponseSchema
from schemas.task import TaskSchema


class SupervisorAgent:
    agent_name = "SupervisorAgent"
    agent_type = "supervisor"

    def __init__(
        self,
        agent_registry: AgentRegistry,
        workflows: Dict[str, WorkflowDefinitionSchema],
        run_trace_recorder: Optional[TraceSink] = None,
        task_manager: Optional[TaskStateManager] = None,
        model_gateway: Optional[ModelGateway] = None,
        supervisor_model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
    ) -> None:
        self.agent_registry = agent_registry
        self.workflows = workflows
        self.run_trace_recorder = run_trace_recorder
        self.task_manager = task_manager
        self.model_gateway = model_gateway
        self.supervisor_model_name = supervisor_model_name
        self.enable_llm_routing = enable_llm_routing

        self.catalog = WorkflowCatalog(workflows)
        self.router = WorkflowRouter(
            self.catalog,
            model_gateway=model_gateway,
            model_name=supervisor_model_name,
            enable_llm_routing=enable_llm_routing,
            caller_agent=self.agent_name,
        )
        self.workflow_executor = WorkflowExecutor(
            agent_registry=agent_registry,
            run_trace_recorder=run_trace_recorder,
        )
        self.context_factory = ContextBundleFactory()
        self.lifecycle = TaskLifecycleService(task_manager)
        self.trace_service = WorkflowTraceService(run_trace_recorder)
        self.service = SupervisorService(
            catalog=self.catalog,
            router=self.router,
            workflow_engine=self.workflow_executor,
            context_factory=self.context_factory,
            lifecycle=self.lifecycle,
            trace=self.trace_service,
            error_factory=ErrorFactory(),
            agent_name=self.agent_name,
            agent_type=self.agent_type,
        )

    # Compatibility surface retained while callers migrate to services.
    def select_workflow(self, task_type: str) -> WorkflowDefinitionSchema:
        return self.catalog.get(task_type)

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        return WorkflowRouter.extract_json_object(text)

    def _route_task_with_llm(
        self,
        task: TaskSchema,
    ) -> Tuple[str, Optional[ModelResponseSchema], Dict[str, Any]]:
        decision: RoutingDecision = self.router.route(task)
        return decision.task_type, decision.model_response, decision.metadata

    def _build_context_bundle(self, task: TaskSchema) -> ContextBundleSchema:
        return self.context_factory.build(task)

    def _trace_workflow(self, **kwargs: Any) -> None:
        self.trace_service.record(**kwargs)

    def _mark_task(self, task: TaskSchema, result: AgentResultSchema) -> None:
        self.lifecycle.mark_terminal(task, result)

    def run(self, task: TaskSchema) -> AgentResultSchema:
        return self.service.execute(task)
