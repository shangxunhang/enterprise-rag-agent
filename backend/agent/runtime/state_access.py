# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：SharedStateReader、SharedStateWriter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Canonical read/write access to ``SharedStateSchema``.

The adapter keeps legacy compatibility fields synchronized while preventing
application services from scattering direct dictionary mutation throughout the
codebase.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import WorkflowStepStateSchema


# 阅读注释（类）：封装 shared 状态 reader，集中封装相关状态、依赖和行为。
class SharedStateReader:
    """封装 shared 状态 reader，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 项目 输入 candidates 相关逻辑。
    def project_input_candidates(self, state: SharedStateSchema) -> list[Any]:
        """处理 项目 输入 candidates 相关逻辑。

        参数:
            state: 工作流共享状态。

        返回:
            list[Any]

        阅读提示:
            主要直接调用：get。
        """
        return [
            state.context_bundle.business.project_input,
            (state.requirements or {}).get("project_input"),
            (state.task or {}).get("project_input"),
            (state.contexts or {}).get("project_input"),
        ]

    # 阅读注释（函数）：获取 Agent 结果。
    def get_agent_result(
        self,
        state: SharedStateSchema,
        agent_name: str,
    ) -> Optional[Dict[str, Any]]:
        """获取 Agent 结果。

        参数:
            state: 工作流共享状态。
            agent_name: Agent 名称，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[Dict[str, Any]]

        阅读提示:
            主要直接调用：state.agent_results.get。
        """
        return state.agent_results.get(agent_name)


# 阅读注释（类）：封装 shared 状态 writer，集中封装相关状态、依赖和行为。
class SharedStateWriter:
    """封装 shared 状态 writer，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：设置 项目 输入 normalization。
    def set_project_input_normalization(
        self,
        state: SharedStateSchema,
        *,
        project_input: Dict[str, Any],
        table_agent_output: Dict[str, Any],
        structured_facts: list[Dict[str, Any]],
        source_materials: list[Dict[str, Any]],
        missing_information: list[str],
        conflicting_information: list[str],
        manual_boundaries: list[Dict[str, Any]],
    ) -> None:
        """设置 项目 输入 normalization。

        参数:
            state: 工作流共享状态。
            project_input: 规范化后的项目输入。
            table_agent_output: table Agent 输出，具体约束请结合类型标注和调用方确认。
            structured_facts: structured facts，具体约束请结合类型标注和调用方确认。
            source_materials: source materials，具体约束请结合类型标注和调用方确认。
            missing_information: missing information，具体约束请结合类型标注和调用方确认。
            conflicting_information: conflicting information，具体约束请结合类型标注和调用方确认。
            manual_boundaries: manual boundaries，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        state.context_bundle.business.project_input = project_input
        state.context_bundle.business.source_materials = source_materials
        state.context_bundle.business.missing_information = missing_information
        state.context_bundle.business.conflicting_information = conflicting_information
        state.context_bundle.business.manual_boundaries = manual_boundaries
        state.structured_facts = structured_facts

        # Compatibility projection. New code should use context_bundle.
        state.contexts["project_input"] = project_input
        state.contexts["table_agent_output"] = table_agent_output

    # 阅读注释（函数）：设置 step 状态。
    def set_step_state(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
    ) -> None:
        """设置 step 状态。

        参数:
            state: 工作流共享状态。
            step_state: step 状态，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        state.workflow_step_states[step_state.step_id] = step_state
        state.context_bundle.runtime.workflow_step_states[step_state.step_id] = step_state

    # 阅读注释（函数）：设置 current step。
    def set_current_step(self, state: SharedStateSchema, step_name: str) -> None:
        """设置 current step。

        参数:
            state: 工作流共享状态。
            step_name: step 名称，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        state.current_step = step_name
        state.context_bundle.runtime.current_step = step_name

    # 阅读注释（函数）：处理 add Agent 结果 相关逻辑。
    def add_agent_result(
        self,
        state: SharedStateSchema,
        result: AgentResultSchema,
    ) -> None:
        """处理 add Agent 结果 相关逻辑。

        参数:
            state: 工作流共享状态。
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：result.model_dump。
        """
        state.agent_results[result.agent_name] = result.model_dump()

    # 阅读注释（函数）：设置 工具 结果。
    def set_tool_result(
        self,
        state: SharedStateSchema,
        tool_call_id: str,
        result: Dict[str, Any],
    ) -> None:
        """设置 工具 结果。

        参数:
            state: 工作流共享状态。
            tool_call_id: 工具 call 标识，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。

        返回:
            None
        """
        state.tool_results[tool_call_id] = result

    # 阅读注释（函数）：处理 add 错误 相关逻辑。
    def add_error(self, state: SharedStateSchema, error: ErrorSchema) -> None:
        """处理 add 错误 相关逻辑。

        参数:
            state: 工作流共享状态。
            error: 错误，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：state.errors.append, state.context_bundle.runtime.errors.append。
        """
        state.errors.append(error)
        if error not in state.context_bundle.runtime.errors:
            state.context_bundle.runtime.errors.append(error)

    # 阅读注释（函数）：处理 add errors 相关逻辑。
    def add_errors(
        self,
        state: SharedStateSchema,
        errors: Iterable[ErrorSchema],
    ) -> None:
        """处理 add errors 相关逻辑。

        参数:
            state: 工作流共享状态。
            errors: errors，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：self.add_error。
        """
        for error in errors:
            self.add_error(state, error)

    # 阅读注释（函数）：设置 证据 上下文。
    def set_evidence_context(
        self,
        state: SharedStateSchema,
        *,
        query: str,
        context_text: str,
        retrieved_chunks: list[Dict[str, Any]],
        citations: list[Dict[str, Any]],
        used_doc_ids: list[str],
        evidence_sufficient: Optional[bool] = None,
        evidence_contract: Optional[Dict[str, Any]] = None,
        evidence_available: Optional[bool] = None,
        assessment_status: str = "not_assessed",
    ) -> None:
        """设置 证据 上下文。

        参数:
            state: 工作流共享状态。
            query: 当前检索或生成查询。
            context_text: 上下文 文本，具体约束请结合类型标注和调用方确认。
            retrieved_chunks: retrieved chunks，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。
            used_doc_ids: used doc 标识集合，具体约束请结合类型标注和调用方确认。
            evidence_sufficient: 证据 sufficient，具体约束请结合类型标注和调用方确认。
            evidence_contract: 证据 contract，具体约束请结合类型标注和调用方确认。
            evidence_available: 证据 available，具体约束请结合类型标注和调用方确认。
            assessment_status: assessment 状态，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：dict, bool。
        """
        evidence = state.context_bundle.evidence
        evidence.query = query
        evidence.contract = dict(evidence_contract or {})
        evidence.context_text = context_text
        evidence.retrieved_chunks = retrieved_chunks
        evidence.citations = citations
        evidence.used_doc_ids = used_doc_ids
        evidence.evidence_available = (
            bool(retrieved_chunks) if evidence_available is None else evidence_available
        )
        evidence.assessment_status = assessment_status
        evidence.evidence_sufficient = evidence_sufficient
        evidence.metadata["context_is_projection"] = bool(evidence.contract)

    # 阅读注释（函数）：处理 initialize 生成 相关逻辑。
    def initialize_generation(
        self,
        state: SharedStateSchema,
        *,
        document_id: str,
        document_title: str,
        required_sections: list[str],
    ) -> None:
        """处理 initialize 生成 相关逻辑。

        参数:
            state: 工作流共享状态。
            document_id: 文档 标识，具体约束请结合类型标注和调用方确认。
            document_title: 文档 title，具体约束请结合类型标注和调用方确认。
            required_sections: required sections，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        generation = state.context_bundle.generation
        generation.document_id = document_id
        generation.document_title = document_title
        generation.required_sections = required_sections

    # 阅读注释（函数）：设置 current 章节。
    def set_current_section(
        self,
        state: SharedStateSchema,
        *,
        section_id: str,
        section_title: str,
    ) -> None:
        """设置 current 章节。

        参数:
            state: 工作流共享状态。
            section_id: 章节 标识，具体约束请结合类型标注和调用方确认。
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        generation = state.context_bundle.generation
        generation.current_section_id = section_id
        generation.current_section_title = section_title

    # 阅读注释（函数）：处理 add generated 章节 相关逻辑。
    def add_generated_section(self, state: SharedStateSchema, section_id: str) -> None:
        """处理 add generated 章节 相关逻辑。

        参数:
            state: 工作流共享状态。
            section_id: 章节 标识，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：state.context_bundle.generation.generated_section_ids.append。
        """
        state.context_bundle.generation.generated_section_ids.append(section_id)

    # 阅读注释（函数）：设置 scheme outputs。
    def set_scheme_outputs(
        self,
        state: SharedStateSchema,
        *,
        scheme_writer_input: Dict[str, Any],
        scheme_writer_output: Dict[str, Any],
        rag_tool_output: Dict[str, Any],
    ) -> None:
        # Compatibility projection retained for v1 callers.
        """设置 scheme outputs。

        参数:
            state: 工作流共享状态。
            scheme_writer_input: scheme writer 输入，具体约束请结合类型标注和调用方确认。
            scheme_writer_output: scheme writer 输出，具体约束请结合类型标注和调用方确认。
            rag_tool_output: RAG 工具 输出，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        state.contexts["scheme_writer_input"] = scheme_writer_input
        state.contexts["scheme_writer_output"] = scheme_writer_output
        state.contexts["rag_tool_output"] = rag_tool_output

    # 阅读注释（函数）：设置 final 结果。
    def set_final_result(
        self,
        state: SharedStateSchema,
        result: Dict[str, Any],
    ) -> None:
        """设置 final 结果。

        参数:
            state: 工作流共享状态。
            result: 待处理的结果对象。

        返回:
            None
        """
        state.final_result = result

