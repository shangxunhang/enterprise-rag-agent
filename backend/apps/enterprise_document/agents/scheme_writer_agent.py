"""Agent facade for enterprise scheme generation.

All generation behavior is implemented by focused services under
``apps.enterprise_document.services.scheme_writer``.  This class is kept as
the workflow/agent protocol boundary and as a temporary compatibility surface
for existing tests and subclasses.
"""

from __future__ import annotations

from typing import Optional

from agent.base_agent import BaseAgent
from contracts.observability import DataCaptureSink
from agent.runtime.tool_executor import ToolExecutor
from apps.enterprise_document.services.semantic_section_judge import SemanticSectionJudge
from apps.enterprise_document.services.scheme_writer import SchemeWriterServiceFacade
from model_gateway.model_gateway import ModelGateway
from prompt_manager.prompt_manager import PromptManager


class SchemeWriterAgent(SchemeWriterServiceFacade, BaseAgent):
    agent_name = "SchemeWriterAgent"
    agent_type = "sub_agent"

    def __init__(
        self,
        tool_executor: Optional[ToolExecutor] = None,
        model_gateway: Optional[ModelGateway] = None,
        data_capture_recorder: Optional[DataCaptureSink] = None,
        prompt_manager: Optional[PromptManager] = None,
        prompt_id: str = "scheme_section_generation_v1",
        model_name: str = "fake_llm",
        rag_tool_name: str = "RealRAGTool",
        rag_retrieval_mode: str = "hybrid",
        enable_agent_self_rag: bool = True,
        enable_semantic_gate: bool = False,
        semantic_gate_model_name: Optional[str] = None,
        generation_checker: object | None = None,
        repair_strategy: object | None = None,
        generation_quality_metadata: Optional[dict] = None,
    ) -> None:
        self.tool_executor = tool_executor
        self.model_gateway = model_gateway
        self.data_capture_recorder = data_capture_recorder
        self.prompt_manager = prompt_manager
        self.prompt_id = prompt_id
        self.model_name = model_name
        self.rag_tool_name = rag_tool_name
        self.rag_retrieval_mode = rag_retrieval_mode
        self.enable_agent_self_rag = enable_agent_self_rag
        self.enable_semantic_gate = enable_semantic_gate
        self.semantic_gate_model_name = semantic_gate_model_name or model_name
        self.generation_checker = generation_checker
        self.repair_strategy = repair_strategy
        self.generation_quality_metadata = dict(generation_quality_metadata or {})
        self.semantic_judge = SemanticSectionJudge(
            model_gateway=model_gateway,
            model_name=self.semantic_gate_model_name,
            enabled=enable_semantic_gate,
        )
        self._init_scheme_writer_services()
