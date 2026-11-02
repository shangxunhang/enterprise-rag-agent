"""定义框架无关的工作流步骤与工作流定义 Schema。

业务层通过这些模型描述节点顺序、目标组件、允许写入的状态路径、分支规则和失败
提交策略。执行引擎再把定义编译为具体状态图，因此 Schema 中不能混入 LangGraph
节点对象或供应商运行时配置。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from schemas.common import SchemaBase


class WorkflowStepSchema(SchemaBase):
    """描述一个可被工作流引擎编译和执行的标准步骤。

    字段同时约束逻辑数据流和物理状态写入边界。节点即使在代码中能够访问完整状态，
    也只能提交 ``write_paths`` 明确允许的路径，从而降低并发节点互相覆盖的风险。
    """

    # 当前步骤定义契约版本。
    schema_version: str = "workflow_step_v1"

    # 工作流内部稳定且唯一的步骤标识，用于路由和状态记录。
    step_id: str
    # 便于日志和人工阅读的步骤名称。
    step_name: str
    # 步骤执行类别，例如 agent、tool、model、rule_check 或 export。
    step_type: str

    # 由执行器解析的目标组件名称，通常是 Agent 名称或工具名称。
    target_name: str

    # 默认线性执行顺序；显式分支仍由 on_success 和 on_failure 控制。
    order: int

    # 节点声明会从共享状态读取的逻辑输入键，主要用于契约检查和可视化。
    input_keys: List[str] = Field(default_factory=list)
    # 节点声明会产生的逻辑输出键，不等同于实际状态写入路径。
    output_keys: List[str] = Field(default_factory=list)

    # 节点获准修改的物理 GraphState 路径；提交器会依据该白名单校验增量。
    write_paths: List[str] = Field(default_factory=list)

    # 节点成功后的路由，可取 next、end 等特殊值或具体 step_id。
    on_success: str = "next"
    # 节点失败后的路由，默认直接使任务失败。
    on_failure: str = "fail_task"

    # 首次执行失败后允许的最大重试次数，不包含第一次尝试。
    max_retries: int = Field(default=0, ge=0)
    # 单次节点尝试允许占用的最长秒数。
    timeout_seconds: float = Field(default=1000.0, gt=0)

    # 决定节点失败时是否允许提交部分业务状态：
    # success_only 只提交成功/部分成功节点；allow_partial_on_failure 允许指定失败
    # 保留白名单输出；always 则始终提交经过校验的增量。
    commit_policy: Literal[
        "success_only", "allow_partial_on_failure", "always"
    ] = "success_only"

    # 失败提交场景下额外允许保留的状态路径。
    failure_write_paths: List[str] = Field(default_factory=list)
    # 只有错误码命中该集合时，失败节点才可按策略保留部分输出。
    failure_commit_error_codes: List[str] = Field(default_factory=list)

    # 尚未进入稳定契约的步骤扩展配置。
    extra: Dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinitionSchema(SchemaBase):
    """描述一个完整、可验证且可版本化的业务工作流。"""

    # 当前工作流定义 Schema 版本。
    schema_version: str = "workflow_definition_v1"

    # 工作流定义的稳定唯一标识。
    workflow_id: str
    # 面向日志、管理界面和人工阅读的名称。
    workflow_name: str
    # 该工作流能够处理的任务类型。
    task_type: str
    # 业务工作流版本，用于发布、回滚和检查点兼容判断。
    workflow_version: str

    # 对工作流业务目标和边界的可选文字说明。
    description: Optional[str] = None

    # 构成工作流的标准步骤集合；顺序和路由在模型校验阶段检查。
    steps: List[WorkflowStepSchema] = Field(default_factory=list)

    # 是否允许装配层把该定义注册为当前可执行工作流。
    is_active: bool = True

    # 定义首次创建时间。
    created_at: str
    # 最近修改时间；从未修改时为 None。
    updated_at: Optional[str] = None

    # 尚未进入稳定契约的工作流级扩展元数据。
    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workflow_graph(self) -> "WorkflowDefinitionSchema":
        """验证步骤身份、顺序和显式路由是否构成可解析工作流。

        Returns:
            校验通过后的当前工作流定义对象。

        Raises:
            ValueError: 步骤标识或顺序重复、路由为空、引用未知步骤，或步骤直接
                路由到自身时抛出。
        """

        # 第一步：收集步骤标识并禁止重复，确保路由目标能够唯一解析。
        step_ids = [item.step_id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step_id values must be unique")

        # 第二步：禁止默认顺序重复，避免线性 next 路由出现不确定性。
        orders = [item.order for item in self.steps]
        if len(orders) != len(set(orders)):
            raise ValueError("workflow step order values must be unique")

        # 第三步：定义执行器能够直接解释、无需对应具体节点的特殊路由值。
        special_routes = {"next", "fail_task", "end", "complete", "stop"}
        known_steps = set(step_ids)

        # 第四步：逐项验证成功和失败路由，防止编译后才暴露定义错误。
        for step in self.steps:
            for field_name, route in (
                ("on_success", step.on_success),
                ("on_failure", step.on_failure),
            ):
                # 统一转换并去除空白，兼容来自配置文件的字符串输入。
                route = str(route or "").strip()
                if not route:
                    raise ValueError(f"{step.step_id}.{field_name} cannot be empty")

                # 路由必须是特殊控制值或当前定义中真实存在的步骤标识。
                if route not in special_routes and route not in known_steps:
                    raise ValueError(
                        f"{step.step_id}.{field_name} references unknown step: {route}"
                    )

                # 禁止直接自环；需要循环时必须由显式重试或受控反馈机制表达。
                if route == step.step_id:
                    raise ValueError(
                        f"{step.step_id}.{field_name} cannot route to itself"
                    )

        # 第五步：返回自身，让 Pydantic 完成 after 模式校验链。
        return self
