"""定义工作流共享上下文和单次模型调用上下文包。

``ContextBundleSchema`` 按用户、任务、会话、业务、证据、生成和运行时职责保存长期共享
状态；``ContextBuildRequestSchema`` 与 ``LLMContextPackageSchema`` 则描述一次模型调用
如何在字符和 token 预算内选择、裁剪并渲染候选上下文。后续 Context Manager 可以
替换排序与压缩策略，而无需改变调用方协议。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .common import ErrorSchema, SchemaBase
from .status import ExecutionStatus


class UserContextSchema(SchemaBase):
    """保存任务发起者、租户、会话和原始请求信息。"""

    # 发起任务的用户标识。
    user_id: Optional[str] = None
    # 租户隔离标识。
    tenant_id: str = "default"
    # 当前对话或业务会话标识。
    session_id: Optional[str] = None
    # 入口接收的原始用户请求。
    user_query: str
    # 权限、偏好或入口来源等用户侧元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskContextSchema(SchemaBase):
    """保存本次任务运行的身份和稳定生成契约。"""

    # 业务任务标识。
    task_id: str
    # 单次执行标识。
    run_id: str
    # 业务任务类型。
    task_type: str
    # 任务所属项目名称。
    project_name: Optional[str] = None
    # 章节、文体、引用和禁止臆测等生成要求。
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    # 最终文档标题、格式和结构契约。
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    # 任务级业务和运行元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationContextSchema(SchemaBase):
    """保存当前会话可供后续节点使用的消息窗口和压缩摘要。"""

    # 按时间顺序保存的结构化会话消息。
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    # 超出窗口或经过压缩后的会话摘要。
    summary: Optional[str] = None
    # 上下文管理器默认保留的最大消息数量。
    max_messages: int = 20
    # 会话压缩策略、版本和其他元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BusinessContextSchema(SchemaBase):
    """保存经过入口和解析节点确认的业务输入及其完整性状态。"""

    # 完整规范项目输入，是业务上下文中的主要事实源。
    project_input: Dict[str, Any] = Field(default_factory=dict)
    # 用户提供或任务声明的来源材料集合。
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    # 当前输入缺失、可能阻止可靠生成的信息。
    missing_information: List[str] = Field(default_factory=list)
    # 当前输入中互相矛盾的事实或约束。
    conflicting_information: List[str] = Field(default_factory=list)
    # 必须由人工计算、绘图、确认或决策的边界。
    manual_boundaries: List[Dict[str, Any]] = Field(default_factory=list)
    # 行业、地区、年份和规范化来源等业务元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GenerationContextSchema(SchemaBase):
    """保存当前文档生成的身份、章节进度和预算信息。"""

    # 本次生成文档的稳定标识。
    document_id: Optional[str] = None
    # 规范文档标题。
    document_title: Optional[str] = None
    # 必须生成或显式说明缺失的章节顺序。
    required_sections: List[str] = Field(default_factory=list)
    # 当前正在处理的章节标识。
    current_section_id: Optional[str] = None
    # 当前正在处理的章节标题。
    current_section_title: Optional[str] = None
    # 已完成生成的章节标识，保留完成顺序。
    generated_section_ids: List[str] = Field(default_factory=list)
    # 当前生成阶段可使用的 token 预算。
    token_budget: Optional[int] = None
    # 生成策略、模板和进度相关元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowStepStateSchema(SchemaBase):
    """描述一个工作流步骤从开始到终止的生命周期状态。"""

    # 步骤稳定标识。
    step_id: str
    # 步骤可读名称。
    step_name: str
    # 步骤调用的 Agent、工具或组件名称。
    target_name: str
    # 当前规范执行状态。
    status: ExecutionStatus = ExecutionStatus.PENDING
    # 当前尝试开始时间。
    started_at: Optional[str] = None
    # 当前尝试结束时间。
    finished_at: Optional[str] = None
    # 当前执行尝试序号。
    attempt: int = 0
    # 步骤失败时最接近来源创建的结构化错误。
    error: Optional[ErrorSchema] = None
    # 提交决定、重试和节点运行元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RuntimeContextSchema(SchemaBase):
    """保存整个工作流当前运行状态、步骤记录和累计错误。"""

    # 当前工作流规范状态。
    status: ExecutionStatus = ExecutionStatus.PENDING
    # 当前正在执行或最近进入的步骤名称。
    current_step: Optional[str] = None
    # 按步骤标识保存的生命周期状态。
    workflow_step_states: Dict[str, WorkflowStepStateSchema] = Field(default_factory=dict)
    # 工作流级累计重试次数。
    retry_count: int = 0
    # 运行期间收集的结构化错误集合。
    errors: List[ErrorSchema] = Field(default_factory=list)
    # 检查点、线程、引擎和其他运行元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextBundleSchema(SchemaBase):
    """把一次任务长期共享的七类上下文聚合为单一规范对象。"""

    # 当前上下文包契约版本。
    schema_version: str = "context_bundle_v1"

    # 用户和租户信息。
    user: UserContextSchema
    # 任务身份和生成契约。
    task: TaskContextSchema
    # 会话历史和摘要。
    conversation: ConversationContextSchema = Field(
        default_factory=ConversationContextSchema
    )
    # 规范业务输入和完整性状态。
    business: BusinessContextSchema = Field(default_factory=BusinessContextSchema)
    # 文档和章节生成进度。
    generation: GenerationContextSchema = Field(default_factory=GenerationContextSchema)
    # 工作流运行状态和错误。
    runtime: RuntimeContextSchema = Field(default_factory=RuntimeContextSchema)


class ContextItemSchema(SchemaBase):
    """表示一次模型调用候选上下文中的一个独立内容块。"""

    # 当前上下文候选项契约版本。
    schema_version: str = "context_item_v1"

    # 候选项唯一标识。
    item_id: str
    # 内容来源类别，例如 business、evidence、conversation 或 instruction。
    source_type: str
    # 渲染时用于分隔和解释内容的标题。
    title: str
    # 候选项完整文本内容。
    content: str

    # 选择优先级；具体数值方向由 Context Manager 策略定义。
    priority: int = 50
    # 是否属于必须保留项；预算不足时可能使构建失败而不是丢弃。
    required: bool = False
    # 是否允许对内容执行截断。
    truncate_allowed: bool = True
    # 该上下文项携带或支持的引用标识。
    citation_ids: List[str] = Field(default_factory=list)
    # 来源、原始长度和其他候选项元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextBudgetSchema(SchemaBase):
    """保存一次模型调用的确定性上下文规划预算和实际使用估计。

    ``estimated_*_tokens`` 是与具体 tokenizer 无关的规划估计；真实 token 用量仍以模型
    网关或供应商响应为事实源。
    """

    # 当前上下文预算契约版本。
    schema_version: str = "context_budget_v1"

    # 渲染上下文允许占用的最大字符数。
    max_context_chars: int
    # 模型调用允许的最大输入 token 数。
    max_input_tokens: int
    # 为模型输出预留的 token 数。
    reserved_output_tokens: int = 0
    # 为模板、系统消息和估计误差保留的安全余量。
    safety_margin_tokens: int = 0

    # 最终渲染上下文实际字符数。
    used_context_chars: int = 0
    # 最终上下文估计输入 token 数。
    estimated_input_tokens: int = 0
    # 字符预算剩余量。
    remaining_context_chars: int = 0
    # 输入 token 预算剩余量。
    remaining_input_tokens: int = 0


class ContextDecisionSchema(SchemaBase):
    """记录一个候选上下文被保留、截断或丢弃的原因。"""

    # 当前上下文决策契约版本。
    schema_version: str = "context_decision_v1"

    # 被决策候选项标识。
    item_id: str
    # 候选项来源类别。
    source_type: str
    # 最终动作，例如 selected、truncated 或 dropped。
    action: str
    # 策略给出的可审计决定原因。
    reason: str
    # 决策时使用的候选项优先级。
    priority: int
    # 候选项是否被声明为必须保留。
    required: bool

    # 决策前字符数。
    chars_before: int
    # 决策后实际保留字符数。
    chars_after: int
    # 决策前估计 token 数。
    estimated_tokens_before: int
    # 决策后估计 token 数。
    estimated_tokens_after: int

    # 排序、压缩和预算策略等决策元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextBuildRequestSchema(SchemaBase):
    """描述构建一次有界模型上下文包所需的完整输入。"""

    # 当前上下文构建请求契约版本。
    schema_version: str = "context_build_request_v1"

    # 请求所属业务任务标识。
    task_id: str
    # 请求所属单次运行标识。
    run_id: str
    # 本次上下文用于何种模型调用，例如 section_generation 或 quality_judge。
    call_purpose: str

    # 章节级调用对应的章节标识。
    section_id: Optional[str] = None
    # 章节级调用对应的章节标题。
    section_title: Optional[str] = None

    # 等待排序、裁剪和选择的候选上下文项。
    items: List[ContextItemSchema] = Field(default_factory=list)

    # 最大上下文字符预算。
    max_context_chars: int = 6000
    # 最大模型输入 token 预算。
    max_input_tokens: int = 8192
    # 为输出预留的 token 数。
    reserved_output_tokens: int = 1024
    # 为估计误差和非上下文输入预留的 token 数。
    safety_margin_tokens: int = 256

    # 上游输入、证据和候选项来源血缘。
    lineage: Dict[str, Any] = Field(default_factory=dict)
    # 构建策略、估算器版本和其他元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> "ContextBuildRequestSchema":
        """验证候选项身份唯一且预算参数可执行。

        Returns:
            校验通过后的当前构建请求。

        Raises:
            ValueError: 候选项标识重复、主预算非正、预留预算为负，或输入预算不足以
                覆盖输出预留与安全余量时抛出。
        """

        # 第一步：候选项标识必须唯一，保证决策和血缘能够一一对应。
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("context item_id values must be unique")

        # 第二步：字符和输入 token 主预算必须为正。
        if self.max_context_chars <= 0 or self.max_input_tokens <= 0:
            raise ValueError("context budgets must be positive")

        # 第三步：预留输出和安全余量不能为负。
        if self.reserved_output_tokens < 0 or self.safety_margin_tokens < 0:
            raise ValueError("reserved token budgets must be non-negative")

        # 第四步：主输入预算必须在扣除预留后仍留有至少一个可用 token。
        if self.max_input_tokens <= (
            self.reserved_output_tokens + self.safety_margin_tokens
        ):
            raise ValueError("max_input_tokens must exceed reserved budgets")

        return self


class LLMContextPackageSchema(SchemaBase):
    """表示一次提示词/模型调用实际消费的规范上下文包。

    ``selected_items`` 和 ``decisions`` 是结构化事实源；``rendered_context`` 是当前文本
    提示词运行时的确定性投影。渲染文本必须与哈希和预算使用量完全一致。
    """

    # 当前模型上下文包契约版本。
    schema_version: str = "llm_context_package_v1"

    # 上下文包唯一标识。
    package_id: str
    # 上下文包所属业务任务标识。
    task_id: str
    # 上下文包所属单次运行标识。
    run_id: str
    # 使用该上下文的模型调用用途。
    call_purpose: str

    # 章节级上下文对应的章节标识。
    section_id: Optional[str] = None
    # 章节级上下文对应的章节标题。
    section_title: Optional[str] = None

    # 最终保留或截断后进入上下文的结构化项。
    selected_items: List[ContextItemSchema] = Field(default_factory=list)
    # 对所有候选项作出的选择、截断或丢弃决定。
    decisions: List[ContextDecisionSchema] = Field(default_factory=list)

    # 当前文本模型调用实际使用的确定性上下文字符串。
    rendered_context: str = ""
    # ``rendered_context`` UTF-8 字节的 SHA-256 摘要。
    context_sha256: str
    # 本次构建的预算上限、使用量和剩余量。
    budget: ContextBudgetSchema

    # 上游事实、证据和当前上下文项之间的血缘。
    lineage: Dict[str, Any] = Field(default_factory=dict)
    # 截断、必要项不足或估计风险等上下文告警。
    warnings: List[str] = Field(default_factory=list)
    # 构建策略、预算是否强制和估算器版本等元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_package(self) -> "LLMContextPackageSchema":
        """验证上下文项、决策、文本哈希和预算使用量一致。

        Returns:
            校验通过后的当前上下文包。

        Raises:
            ValueError: 选中项或决策标识重复、文本哈希/字符数不匹配，或在启用预算
                强制时超过字符或估计 token 上限时抛出。
        """

        # 第一步：每个选中上下文项只能出现一次。
        selected_ids = [item.item_id for item in self.selected_items]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected context item_id values must be unique")

        # 第二步：每个候选项只能有一个最终决策。
        decision_ids = [item.item_id for item in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("context decisions must be unique by item_id")

        # 第三步：重新计算实际渲染文本哈希，防止结构化项与文本投影被分离修改。
        actual_hash = hashlib.sha256(
            self.rendered_context.encode("utf-8")
        ).hexdigest()
        if actual_hash != self.context_sha256:
            raise ValueError("context_sha256 does not match rendered_context")

        # 第四步：预算记录的已用字符必须等于真实 Python 字符串长度。
        if self.budget.used_context_chars != len(self.rendered_context):
            raise ValueError("used_context_chars must match rendered_context length")

        # 第五步：默认强制执行预算；显式关闭只适用于诊断或兼容场景。
        if self.metadata.get("budget_enforced", True):
            usable_tokens = (
                self.budget.max_input_tokens
                - self.budget.reserved_output_tokens
                - self.budget.safety_margin_tokens
            )

            # 第六步：真实字符数不能超过字符预算。
            if self.budget.used_context_chars > self.budget.max_context_chars:
                raise ValueError("rendered context exceeds max_context_chars")

            # 第七步：估计输入 token 不能超过扣除预留后的可用预算。
            if self.budget.estimated_input_tokens > usable_tokens:
                raise ValueError("estimated context tokens exceed input budget")

        return self
