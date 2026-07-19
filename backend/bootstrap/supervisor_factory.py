"""Composition root for the Agent-RAG runtime."""

from __future__ import annotations

from pathlib import Path

from agent.agent_registry import AgentRegistry
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.supervisor_agent import SupervisorAgent
from apps.enterprise_document.agents.project_input_normalizer_agent import (
    ProjectInputNormalizerAgent,
)
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.model_factory import ModelGatewayFactory
from bootstrap.observability_factory import ObservabilityFactory
from bootstrap.rag_service_factory import RAGServiceFactory
from bootstrap.runtime_options import RuntimeOptions
from core.config import AppSettings
from prompt_manager.prompt_manager import PromptManager
from task.task_manager import JsonlTaskManager


class SupervisorFactory:
    """Build the supervisor and its application-facing dependencies."""

    def __init__(
        self,
        *,
        observability_factory: ObservabilityFactory | None = None,
        model_factory: ModelGatewayFactory | None = None,
        rag_factory: RAGServiceFactory | None = None,
        agent_quality_factory: AgentQualityFactory | None = None,
    ) -> None:
        self.observability_factory = observability_factory or ObservabilityFactory()
        self.model_factory = model_factory or ModelGatewayFactory()
        self.rag_factory = rag_factory or RAGServiceFactory()
        self.agent_quality_factory = agent_quality_factory or AgentQualityFactory()

    def build(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        runs_dir: Path,
        captures_dir: Path,
        task_manager: JsonlTaskManager,
        settings: AppSettings,
        options: RuntimeOptions,
    ) -> SupervisorAgent:
        """Assemble one isolated runtime for a workflow execution."""
        trace, capture = self.observability_factory.build(
            settings, runs_dir, captures_dir
        )
        prompt_manager = PromptManager(prompt_root=settings.prompt_root)
        rag_service = self.rag_factory.build(options, trace)
        rag_service_name = "RAGService" if options.use_real_rag else "FakeRAGService"
        model_gateway = self.model_factory.build(settings, trace)
        agent_quality = self.agent_quality_factory.build(
            options=options,
            model_gateway=model_gateway,
            model_name=settings.default_model_name,
        )
        prompt_id = settings.default_scheme_prompt_id
        if prompt_id == "scheme_generation_v1":
            prompt_id = "scheme_section_generation_v1"
            print(
                "[Prompt Runtime] deprecated prompt_id=scheme_generation_v1; "
                "redirected to scheme_section_generation_v1"
            )

        agents = AgentRegistry()
        agents.register(ProjectInputNormalizerAgent())
        agents.register(
            SchemeWriterAgent(
                rag_service=rag_service,
                model_gateway=model_gateway,
                data_capture_recorder=capture,
                prompt_manager=prompt_manager,
                prompt_id=prompt_id,
                model_name=settings.default_model_name,
                rag_service_name=rag_service_name,
                enable_agent_self_rag=options.enable_agent_self_rag,
                enable_semantic_gate=options.enable_semantic_gate,
                semantic_gate_model_name=options.semantic_gate_model_name,
                generation_checker=agent_quality.generation_checker,
                repair_strategy=agent_quality.repair_strategy,
                generation_quality_metadata=agent_quality.metadata,
            )
        )
        return SupervisorAgent(
            agent_registry=agents,
            workflows={"scheme_generation": workflow},
            run_trace_recorder=trace,
            task_manager=task_manager,
            model_gateway=model_gateway,
            supervisor_model_name=settings.supervisor_model_name,
            enable_llm_routing=settings.enable_llm_routing,
        )
