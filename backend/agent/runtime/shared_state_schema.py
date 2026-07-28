"""定义 Agent 与工作流步骤共享的基础运行状态。

该模型集中承载任务身份、规范上下文、节点结果、错误告警和交付物生命周期。
``GraphStateSchema`` 会在此基础上增加图执行信息，因此业务 Agent 应优先依赖本
模型中稳定的业务字段，而不要直接耦合 LangGraph 的内部状态表示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.context import ContextBundleSchema, WorkflowStepStateSchema
from schemas.status import ExecutionStatus


class SharedStateSchema(SchemaBase):
    """表示一次任务执行中由多个 Agent、工具和工作流节点共享的业务状态。

    字段按职责分为任务身份、输入快照、规范上下文、节点结果、运行状态和最终产物。
    写入应通过 ``SharedStateWriter`` 等受控入口完成，避免不同节点直接覆盖彼此数据。
    """

    # 当前共享状态契约版本，用于持久化兼容和迁移判断。
    schema_version: str = "shared_state_v2"

    # 业务任务唯一标识；同一任务的恢复或重试通常保持该值不变。
    task_id: str
    # 单次执行唯一标识；同一任务的不同运行通过该字段隔离。
    run_id: str
    # 任务类型，用于选择工作流、Agent 和业务 Schema。
    task_type: str
    # 经过入口层接收的原始用户需求文本。
    user_input: str
    # 租户隔离标识；未显式提供时使用默认租户。
    tenant_id: str = "default"

    # 任务对象的序列化快照，供节点读取任务级元数据和原始结构。
    task: Dict[str, Any] = Field(default_factory=dict)
    # 当前工作流定义或运行信息的序列化快照。
    workflow: Dict[str, Any] = Field(default_factory=dict)
    # 从 ProjectInput 等入口对象提取的生成与业务要求。
    requirements: Dict[str, Any] = Field(default_factory=dict)

    # 新生产代码唯一可信的上下文事实源，内部区分业务、证据、生成和运行时上下文。
    context_bundle: ContextBundleSchema

    # 迁移期兼容视图：由 SharedStateWriter 从 context_bundle 单向投影。
    # 新代码不得直接写入，也不得用它反向覆盖规范上下文。
    contexts: Dict[str, Any] = Field(default_factory=dict)

    # 迁移期结构化事实视图，同样只用于兼容旧调用方，不是独立事实源。
    structured_facts: List[Dict[str, Any]] = Field(default_factory=list)

    # 按 Agent 名称保存最近或规范化后的 Agent 执行结果。
    agent_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # 按工具调用标识或工具名称保存标准工具结果。
    tool_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # 按步骤标识保存工作流步骤生命周期状态。
    workflow_step_states: Dict[str, WorkflowStepStateSchema] = Field(
        default_factory=dict
    )

    # 保存节点产生的可复用结构化输出，键名由工作流契约约定。
    generated_outputs: Dict[str, Any] = Field(default_factory=dict)
    # 不阻止执行但需要向后续节点或调用方暴露的告警集合。
    warnings: List[WarningSchema] = Field(default_factory=list)
    # 执行期间收集的规范错误集合；终态由策略和关键错误共同决定。
    errors: List[ErrorSchema] = Field(default_factory=list)

    # 当前共享执行状态，初始为待执行。
    status: ExecutionStatus = ExecutionStatus.PENDING
    # 当前工作流步骤名称，用于可观测性、错误定位和恢复判断。
    current_step: Optional[str] = None

    # 尚未经过最终质量门禁的生成草稿。
    generated_draft: Optional[Dict[str, Any]] = None
    # 被质量门禁拒绝但需要保留用于审计和改进的产物。
    rejected_artifact: Optional[Dict[str, Any]] = None
    # 通过交付门禁、允许返回给业务调用方的最终结果。
    deliverable_final_result: Optional[Dict[str, Any]] = None

    # 通用工作流兼容字段；企业文档生成必须使用上面三个显式产物生命周期字段，
    # 不能把 final_result 当作已通过业务门禁的默认保证。
    final_result: Optional[Dict[str, Any]] = None

    # 共享状态结构版本；与 graph_revision 的受控业务提交修订含义不同。
    state_version: int = 2
    # 状态对象创建时间，使用统一时钟生成的 ISO 时间字符串。
    created_at: str
    # 最近一次受控写入时间；尚未更新时为 None。
    updated_at: Optional[str] = None

    # 为实验性或尚未进入稳定契约的数据保留的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
