"""提供 ``SharedStateSchema`` 的规范读取和受控写入入口。

新代码应先写 ``context_bundle`` 等规范字段。本适配器是迁移期 ``contexts``、
``structured_facts`` 等兼容投影的唯一所有者，兼容字段只能由规范状态单向生成，
不能反向覆盖规范事实源。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

from core.hashing import canonical_sha256

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import WorkflowStepStateSchema


class SharedStateReader:
    """集中封装仍需兼容多来源读取的共享状态查询。"""

    def project_input_candidates(self, state: SharedStateSchema) -> list[Any]:
        """按可信度顺序返回迁移期可能存在的项目输入候选。

        Args:
            state: 当前工作流共享状态。

        Returns:
            固定顺序候选列表：规范业务上下文、requirements 兼容载荷、task 快照、
            legacy contexts 视图。调用方应选择首个满足自身 Schema 的值。

        该方法只为兼容旧状态存在；新生产路径应直接读取第一个规范字段。
        """

        # 规范 context_bundle 始终排在第一位，旧字段不得获得更高优先级。
        return [
            state.context_bundle.business.project_input,
            (state.requirements or {}).get("project_input"),
            (state.task or {}).get("project_input"),
            (state.contexts or {}).get("project_input"),
        ]

    def get_agent_result(
        self,
        state: SharedStateSchema,
        agent_name: str,
    ) -> Optional[Dict[str, Any]]:
        """读取指定 Agent 最近登记的序列化结果。

        Args:
            state: 当前工作流共享状态。
            agent_name: 要查询的稳定 Agent 名称。

        Returns:
            已保存结果字典；该 Agent 尚未执行时返回 ``None``。
        """

        return state.agent_results.get(agent_name)


class SharedStateWriter:
    """集中修改规范共享状态并维护必要的单向兼容投影。

    所有方法原地修改传入状态。写入顺序始终以规范字段为先，随后才同步顶层或
    ``contexts`` 兼容视图，防止旧结构成为独立事实源。
    """

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
        """发布项目输入规范化结果及迁移期兼容视图。

        Args:
            state: 要更新的共享状态。
            project_input: 规范化后的完整项目输入。
            table_agent_output: 表格/输入解析 Agent 的结构化输出。
            structured_facts: 从输入中确认的结构化事实集合。
            source_materials: 规范化后的来源材料声明。
            missing_information: 当前输入缺失的必要信息。
            conflicting_information: 输入中检测到的冲突事实。
            manual_boundaries: 明确要求人工处理、不由模型生成的边界。
        """

        # 第一步：ProjectInput 在一个 run 内只能首次发布或精确幂等重放。
        business = state.context_bundle.business
        existing_project_input = dict(business.project_input or {})
        if existing_project_input:
            if canonical_sha256(existing_project_input) != canonical_sha256(project_input):
                raise ValueError(
                    "canonical ProjectInput cannot change within the same active run"
                )
        else:
            business.project_input = deepcopy(project_input)
        business.source_materials = source_materials
        business.missing_information = missing_information
        business.conflicting_information = conflicting_information
        business.manual_boundaries = manual_boundaries

        # 第二步：更新顶层结构化事实兼容字段；其生命周期仍由本写入器拥有。
        state.structured_facts = structured_facts

        # 第三步：从规范结果单向投影到旧 contexts，供尚未迁移的调用方读取。
        state.contexts["project_input"] = project_input
        state.contexts["table_agent_output"] = table_agent_output

    def set_step_state(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
    ) -> None:
        """同步保存工作流步骤生命周期状态。

        Args:
            state: 要更新的共享状态。
            step_state: 当前步骤的规范状态记录。
        """

        # 第一步：更新顶层兼容映射，维持现有执行器和报告读取路径。
        state.workflow_step_states[step_state.step_id] = step_state

        # 第二步：同步规范 runtime 上下文，使 context_bundle 保持事实权威。
        state.context_bundle.runtime.workflow_step_states[step_state.step_id] = step_state

    def set_current_step(self, state: SharedStateSchema, step_name: str) -> None:
        """同步更新当前工作流步骤名称。

        Args:
            state: 要更新的共享状态。
            step_name: 当前步骤的可读名称。
        """

        # 顶层字段保留给旧调用方和快速序列化视图。
        state.current_step = step_name
        # 规范运行时上下文保存同一值，避免两个字段漂移。
        state.context_bundle.runtime.current_step = step_name

    def add_agent_result(
        self,
        state: SharedStateSchema,
        result: AgentResultSchema,
    ) -> None:
        """按 Agent 名称登记标准执行结果。

        Args:
            state: 要更新的共享状态。
            result: 已完成的标准 Agent 结果。
        """

        # 使用 model_dump 保存稳定数据快照，不在共享状态中保留可变模型对象引用。
        state.agent_results[result.agent_name] = result.model_dump()

    def set_tool_result(
        self,
        state: SharedStateSchema,
        tool_call_id: str,
        result: Dict[str, Any],
    ) -> None:
        """按工具调用标识保存标准工具结果。

        Args:
            state: 要更新的共享状态。
            tool_call_id: 单次工具调用唯一标识。
            result: 工具结果的序列化字典。
        """

        state.tool_results[tool_call_id] = result

    def add_error(self, state: SharedStateSchema, error: ErrorSchema) -> None:
        """把结构化错误同步加入顶层和规范运行时错误集合。

        Args:
            state: 要更新的共享状态。
            error: 最接近失败源创建的规范错误。
        """

        # 第一步：顶层错误列表保留完整发生顺序，兼容现有结果汇总逻辑。
        state.errors.append(error)

        # 第二步：规范 runtime 集合避免重复同一 Pydantic 错误对象。
        if error not in state.context_bundle.runtime.errors:
            state.context_bundle.runtime.errors.append(error)

    def add_errors(
        self,
        state: SharedStateSchema,
        errors: Iterable[ErrorSchema],
    ) -> None:
        """按输入顺序批量登记结构化错误。

        Args:
            state: 要更新的共享状态。
            errors: 可迭代错误集合。
        """

        # 复用单错误入口，确保去重和双视图同步规则只有一份实现。
        for error in errors:
            self.add_error(state, error)

    def initialize_generation(
        self,
        state: SharedStateSchema,
        *,
        document_id: str,
        document_title: str,
        required_sections: list[str],
    ) -> None:
        """初始化文档生成上下文的身份和章节契约。

        Args:
            state: 要更新的共享状态。
            document_id: 本次生成文档的稳定标识。
            document_title: 规范文档标题。
            required_sections: 必须生成或显式说明缺失的章节顺序。
        """

        generation = state.context_bundle.generation
        generation.document_id = document_id
        generation.document_title = document_title
        generation.required_sections = required_sections

    def set_current_section(
        self,
        state: SharedStateSchema,
        *,
        section_id: str,
        section_title: str,
    ) -> None:
        """更新当前正在处理的章节身份。

        Args:
            state: 要更新的共享状态。
            section_id: 当前章节稳定标识。
            section_title: 当前章节标题。
        """

        generation = state.context_bundle.generation
        generation.current_section_id = section_id
        generation.current_section_title = section_title

    def add_generated_section(self, state: SharedStateSchema, section_id: str) -> None:
        """把已生成章节标识追加到生成上下文。

        Args:
            state: 要更新的共享状态。
            section_id: 已完成生成的章节标识。
        """

        # 保留完成顺序；是否允许重复由上层章节协调器控制。
        state.context_bundle.generation.generated_section_ids.append(section_id)

    def set_scheme_outputs(
        self,
        state: SharedStateSchema,
        *,
        scheme_writer_input: Dict[str, Any],
        scheme_writer_output: Dict[str, Any],
        rag_tool_output: Dict[str, Any],
    ) -> None:
        """维护方案生成 v1 调用方仍读取的 contexts 兼容输出。

        Args:
            state: 要更新的共享状态。
            scheme_writer_input: 方案编写 Agent 的结构化输入。
            scheme_writer_output: 方案编写 Agent 的结构化输出。
            rag_tool_output: RAG 工具的兼容输出视图。

        新功能不得在本适配器之外新增直接 ``state.contexts[...]`` 写入。
        """

        state.contexts["scheme_writer_input"] = scheme_writer_input
        state.contexts["scheme_writer_output"] = scheme_writer_output
        state.contexts["rag_tool_output"] = rag_tool_output

    def set_final_result(
        self,
        state: SharedStateSchema,
        result: Dict[str, Any],
    ) -> None:
        """设置通用工作流兼容最终结果。

        Args:
            state: 要更新的共享状态。
            result: 通用工作流最终结果字典。

        企业文档生成不能用该字段替代显式草稿、拒绝产物和可交付结果生命周期。
        """

        state.final_result = result

    def set_scheme_artifacts(
        self,
        state: SharedStateSchema,
        *,
        generated_draft: Dict[str, Any],
        rejected_artifact: Dict[str, Any] | None,
        deliverable_final_result: Dict[str, Any] | None,
    ) -> None:
        """发布方案草稿、拒绝产物和最终可交付物的独立生命周期状态。

        Args:
            state: 要更新的共享状态。
            generated_draft: 已生成但不自动代表通过门禁的完整草稿。
            rejected_artifact: 门禁拒绝后保留的产物和原因；未拒绝时为 ``None``。
            deliverable_final_result: 通过最终交付门禁的结果；未通过时为 ``None``。
        """

        # 三个字段必须同时更新，防止旧运行残留的 deliverable 与新草稿混用。
        state.generated_draft = generated_draft
        state.rejected_artifact = rejected_artifact
        state.deliverable_final_result = deliverable_final_result
