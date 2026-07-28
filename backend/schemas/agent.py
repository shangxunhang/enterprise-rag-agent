"""定义 Agent 注册规格、Agent 间消息和标准执行结果。

工作流通过 ``AgentSpecSchema`` 了解能力和状态边界，通过 ``AgentMessageSchema`` 表达
显式通信，通过 ``AgentResultSchema`` 接收统一结果。人工审核使用 required、
recommended、status、request 和 decision 的显式状态机；``need_human_review`` 只保留
为旧调用方提供兼容布尔视图。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .citation import CitationSchema
from .common import ErrorSchema, SchemaBase, WarningSchema
from .human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from .status import ExecutionStatus


class AgentSpecSchema(SchemaBase):
    """描述一个可注册 Agent 的能力、协议和状态访问边界。"""

    # 当前 Agent 规格契约版本。
    schema_version: str = "agent_spec_v1"

    # Agent 注册表和工作流定义使用的稳定唯一名称。
    agent_name: str
    # Agent 类别，例如 supervisor 或 sub_agent。
    agent_type: str

    # 面向管理界面、开发者和路由器的职责说明。
    description: Optional[str] = None

    # Agent 可完成的稳定能力标签。
    capabilities: List[str] = Field(default_factory=list)
    # Agent 明确支持的任务类型集合。
    supported_task_types: List[str] = Field(default_factory=list)

    # Agent 正常运行必须装配的工具名称。
    required_tools: List[str] = Field(default_factory=list)
    # 装配后可增强能力、但缺失不阻止启动的工具名称。
    optional_tools: List[str] = Field(default_factory=list)

    # Agent 标准输入 Schema 名称或引用。
    input_schema: str
    # Agent 标准输出 Schema 名称或引用。
    output_schema: str

    # Agent 声明需要读取的共享状态逻辑键。
    state_read_keys: List[str] = Field(default_factory=list)
    # Agent 声明可能写入的共享状态逻辑键。
    state_write_keys: List[str] = Field(default_factory=list)

    # 首次执行失败后建议允许的最大重试次数。
    max_retries: int = 0
    # 单次 Agent 尝试建议超时秒数。
    timeout_seconds: int = 300

    # 是否允许装配层把该 Agent 注册为当前可用实例。
    is_active: bool = True

    # 尚未进入稳定 Agent 规格契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class AgentMessageSchema(SchemaBase):
    """表示两个 Agent 或控制组件之间的一条显式结构化消息。"""

    # 当前 Agent 消息契约版本。
    schema_version: str = "agent_message_v1"

    # 消息唯一标识。
    message_id: str
    # 消息所属业务任务标识。
    task_id: str
    # 消息所属单次运行标识。
    run_id: str

    # 消息发送方 Agent 或控制组件名称。
    from_agent: str
    # 消息接收方 Agent 或控制组件名称。
    to_agent: str

    # 消息类别，例如 task_dispatch、status_update、result_notice、error_notice 或 control。
    message_type: str

    # 消息对应的工作流步骤标识。
    step_id: Optional[str] = None
    # 消息对应的工作流步骤名称。
    step_name: Optional[str] = None

    # 消息的结构化业务载荷。
    payload: Dict[str, Any] = Field(default_factory=dict)

    # 消息创建时间。
    created_at: str

    # 尚未进入稳定消息契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class AgentResultSchema(SchemaBase):
    """表示一次 Agent 执行的规范状态、业务输出和审核信息。

    结果状态可以是成功、部分成功、失败、可重试失败、暂停或取消。处于人工审核等待时，
    执行状态必须是 ``PAUSED``，审核状态必须是 ``PENDING``，且审核被标记为 required。
    """

    # 当前 Agent 结果契约版本。
    schema_version: str = "agent_result_v1"

    # Agent 结果唯一标识。
    result_id: str
    # 结果所属业务任务标识。
    task_id: str
    # 结果所属单次运行标识。
    run_id: str

    # 实际产生结果的 Agent 名称。
    agent_name: str
    # Agent 类别，例如 supervisor 或 sub_agent。
    agent_type: str

    # Agent 本次执行的规范状态。
    status: ExecutionStatus
    # 业务结果类别，用于上层解释 result 字典结构。
    result_type: str

    # Agent 产生的结构化业务输出。
    result: Dict[str, Any] = Field(default_factory=dict)

    # 与结果声明关联的证据来源集合。
    citations: List[CitationSchema] = Field(default_factory=list)
    # 不阻止当前结果但需要暴露的告警集合。
    warnings: List[WarningSchema] = Field(default_factory=list)

    # 旧调用方读取的兼容布尔视图；after 校验器会根据 required/recommended 重算。
    need_human_review: bool = False
    # 当前结果是否必须等待人工决定才能继续。
    human_review_required: bool = False
    # 当前结果是否可以交付或继续，但建议人工复核。
    human_review_recommended: bool = False
    # 人工审核生命周期状态。
    human_review_status: HumanReviewStatus = HumanReviewStatus.NOT_REQUIRED
    # 等待人工审核时发出的结构化请求。
    human_review_request: Optional[HumanReviewRequestSchema] = None
    # 人工已经提交的批准、拒绝或取消决定。
    human_review_decision: Optional[HumanReviewDecisionSchema] = None

    # Agent 失败时最接近来源创建的结构化错误。
    error: Optional[ErrorSchema] = None

    # Agent 实际开始时间。
    started_at: Optional[str] = None
    # Agent 实际结束或暂停时间。
    finished_at: Optional[str] = None
    # Agent 本次执行毫秒耗时。
    latency_ms: Optional[int] = None

    # 尚未进入稳定结果契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    # 兼容和快速展示使用的错误摘要。
    error_message: Optional[str] = None
    # 路由、模型、提交决定和其他稳定结果元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_review_state(cls, value: Any) -> Any:
        """把只包含旧 ``need_human_review`` 的输入迁移到显式审核状态。

        Args:
            value: Pydantic 校验前收到的原始输入。

        Returns:
            非字典输入原样返回；字典输入在缺少全部显式审核字段时补齐兼容状态。
        """

        # 第一步：只处理字典载荷，已经构造的模型或其他输入交给 Pydantic 正常处理。
        if not isinstance(value, dict):
            return value

        # 第二步：复制原始字典，避免在校验过程中修改调用方对象。
        data = dict(value)

        # 第三步：任何显式审核字段存在都表示调用方采用新契约，不执行旧字段推导。
        explicit = any(
            key in data
            for key in (
                "human_review_required",
                "human_review_recommended",
                "human_review_status",
                "human_review_request",
                "human_review_decision",
            )
        )

        if not explicit:
            # 第四步：规范化执行状态，并读取旧兼容布尔值。
            status = ExecutionStatus(data.get("status", ExecutionStatus.PENDING))
            legacy = bool(data.get("need_human_review", False))

            if status == ExecutionStatus.PAUSED:
                # 第五步：旧结果处于暂停时，将其解释为必须等待的 pending 审核。
                data["human_review_required"] = True
                data["human_review_recommended"] = False
                data["human_review_status"] = HumanReviewStatus.PENDING
            else:
                # 第六步：非暂停旧结果不能是 required，只能投影为审核建议。
                data["human_review_required"] = False
                data["human_review_recommended"] = legacy
                data["human_review_status"] = HumanReviewStatus.NOT_REQUIRED

        return data

    @model_validator(mode="after")
    def _validate_review_state(self) -> "AgentResultSchema":
        """验证人工审核生命周期与执行状态、决定对象严格一致。

        Returns:
            审核状态合法且兼容布尔值已经重算的当前结果。

        Raises:
            ValueError: pending 审核不是 required/paused、非 pending 状态仍标记 required，
                或审核终态与决定对象缺失/不一致时抛出。
        """

        if self.human_review_status == HumanReviewStatus.PENDING:
            # 第一步：等待审核必须真正阻断流程，不能只是普通建议。
            if not self.human_review_required:
                raise ValueError("pending human review must be required")

            # 第二步：等待外部决定期间 Agent 执行状态必须明确为暂停。
            if self.status != ExecutionStatus.PAUSED:
                raise ValueError("pending human review requires paused execution")

            # 第三步：required 已经覆盖 recommended，避免同一状态表达两个不同语义。
            self.human_review_recommended = False
        elif self.human_review_required:
            # 第四步：required 只在 pending 生命周期中合法，终态或未要求状态不得保留。
            raise ValueError(
                "human_review_required is valid only while review status is pending"
            )

        if self.human_review_status == HumanReviewStatus.NOT_REQUIRED:
            # 第五步：没有审核流程时不能携带一个看似已执行的审核决定。
            if self.human_review_decision is not None:
                raise ValueError(
                    "not_required human review cannot carry a review decision"
                )
        elif self.human_review_status in {
            HumanReviewStatus.APPROVED,
            HumanReviewStatus.REJECTED,
            HumanReviewStatus.CANCELLED,
        }:
            # 第六步：审核终态必须存在结构化决定作为事实来源。
            if self.human_review_decision is None:
                raise ValueError(
                    "terminal human review status requires a review decision"
                )

            # 第七步：决定枚举必须与外层审核状态完全一致，禁止双事实源冲突。
            if self.human_review_decision.decision != self.human_review_status:
                raise ValueError("human review decision/status mismatch")

        # 第八步：旧布尔字段只作为 required 或 recommended 的兼容投影。
        self.need_human_review = bool(
            self.human_review_required or self.human_review_recommended
        )

        return self
