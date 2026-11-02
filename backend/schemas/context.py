"""定义工作流共享上下文和单次模型调用上下文包。

``ContextBundleSchema`` 按用户、任务、会话、业务、证据、生成和运行时职责保存长期共享
状态；``ContextBuildRequestSchema`` 与 ``LLMContextPackageSchema`` 则描述一次模型调用
如何在字符和 token 预算内选择、裁剪并渲染候选上下文。后续 Context Manager 可以
替换排序与压缩策略，而无需改变调用方协议。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from core.hashing import canonical_sha256

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


# ---------------------------------------------------------------------------
# SIF-10 Target Context Runtime contracts.
# Existing schemas above remain compatibility surfaces; the contracts below
# describe one exact authorized model invocation package.
# ---------------------------------------------------------------------------

TargetNonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
TargetSha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenTargetContextSchema(SchemaBase):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )


class ContextSourceFamily(str, Enum):
    FACT_SNAPSHOT = "FACT_SNAPSHOT"
    EVIDENCE_PACKAGE = "EVIDENCE_PACKAGE"
    SECTION_PLAN = "SECTION_PLAN"
    PARAGRAPH_ARTIFACT = "PARAGRAPH_ARTIFACT"
    ACTIVE_VERIFIED_CLAIM = "ACTIVE_VERIFIED_CLAIM"
    VERIFIER_ISSUE = "VERIFIER_ISSUE"
    DURABLE_RUN_MEMORY = "DURABLE_RUN_MEMORY"
    WORKFLOW_EXECUTION_METADATA = "WORKFLOW_EXECUTION_METADATA"
    CLAIM_RECORD = "CLAIM_RECORD"
    CLAIM_BINDING_FACTS = "CLAIM_BINDING_FACTS"


class ContextRoleVisibility(str, Enum):
    MUST = "MUST"
    MAY = "MAY"
    CONDITIONAL = "CONDITIONAL"
    FORBIDDEN = "FORBIDDEN"


class ContextHardEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"


class ContextPriorityTier(str, Enum):
    P0_MANDATORY_AUTHORITY = "P0_MANDATORY_AUTHORITY"
    P1_OPERATION_CRITICAL = "P1_OPERATION_CRITICAL"
    P2_RELEVANT_CURRENT = "P2_RELEVANT_CURRENT"
    P3_SUPPORTING = "P3_SUPPORTING"
    P4_HISTORICAL_OPTIONAL = "P4_HISTORICAL_OPTIONAL"


class ContextRepresentationLevel(str, Enum):
    EXACT = "EXACT"
    STRUCTURED_COMPACT = "STRUCTURED_COMPACT"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class ContextFinalInputKind(str, Enum):
    PROMPT = "PROMPT"
    MESSAGES = "MESSAGES"


class ConsumerAuthorizationDecisionState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class ContextPackageReplayState(str, Enum):
    CURRENTLY_REUSABLE = "CURRENTLY_REUSABLE"
    HISTORICAL_REPLAY_ONLY = "HISTORICAL_REPLAY_ONLY"
    REBUILD_REQUIRED = "REBUILD_REQUIRED"
    INTEGRITY_INVALID = "INTEGRITY_INVALID"


class ContextPackageRefSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_package_ref_v1"] = "context_package_ref_v1"
    run_id: TargetNonEmptyStr
    attempt_id: TargetNonEmptyStr
    invocation_id: TargetNonEmptyStr
    context_package_id: TargetNonEmptyStr


class ContextInvocationSubjectSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_invocation_subject_v1"] = (
        "context_invocation_subject_v1"
    )
    run_id: TargetNonEmptyStr
    attempt_id: TargetNonEmptyStr
    invocation_id: TargetNonEmptyStr
    logical_operation_id: TargetNonEmptyStr
    agent_role: TargetNonEmptyStr
    execution_mode: TargetNonEmptyStr
    context_purpose: TargetNonEmptyStr
    consumer_scope_ref: TargetNonEmptyStr


class ContextPolicyBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_policy_binding_v1"] = "context_policy_binding_v1"
    source_catalog_id: TargetNonEmptyStr
    source_catalog_version: TargetNonEmptyStr
    role_policy_id: TargetNonEmptyStr
    role_policy_version: TargetNonEmptyStr
    cross_section_policy_id: TargetNonEmptyStr
    cross_section_policy_version: TargetNonEmptyStr
    consumer_authorization_policy_id: TargetNonEmptyStr
    consumer_authorization_policy_version: TargetNonEmptyStr
    selection_policy_id: TargetNonEmptyStr
    selection_policy_version: TargetNonEmptyStr
    budget_policy_id: TargetNonEmptyStr
    budget_policy_version: TargetNonEmptyStr
    representation_policy_id: TargetNonEmptyStr
    representation_policy_version: TargetNonEmptyStr


class ContextSourceStatusBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_source_status_binding_v1"] = (
        "context_source_status_binding_v1"
    )
    status_schema_id: TargetNonEmptyStr
    status_schema_version: TargetNonEmptyStr
    resolver_id: TargetNonEmptyStr
    resolver_policy_version: TargetNonEmptyStr
    status_payload: dict[str, Any]
    status_payload_sha256: TargetSha256Hex
    status_semantic_fingerprint: TargetSha256Hex

    @model_validator(mode="after")
    def _payload_hash(self) -> "ContextSourceStatusBindingSchema":
        if canonical_sha256(self.status_payload) != self.status_payload_sha256:
            raise ValueError("Context source status payload hash mismatch")
        return self


class ContextSourceBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_source_binding_v1"] = (
        "context_source_binding_v1"
    )
    source_family: ContextSourceFamily
    exact_source_ref: dict[str, Any]
    source_schema_id: TargetNonEmptyStr
    source_schema_version: TargetNonEmptyStr
    source_semantic_fingerprint: TargetSha256Hex
    authority_owner: TargetNonEmptyStr
    run_id: TargetNonEmptyStr
    scope_ref: TargetNonEmptyStr
    currentness_binding: ContextSourceStatusBindingSchema
    lifecycle_binding: ContextSourceStatusBindingSchema | None = None

    @field_validator("exact_source_ref")
    @classmethod
    def _exact_ref_non_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("Context exact_source_ref must be non-empty")
        return value

    def semantic_identity(self) -> dict[str, Any]:
        """Return only source/status semantics that may affect package identity.

        Full status payload/hash remain immutable audit evidence on the binding,
        but audit-only observation fields (for example assessed_at) must not
        churn ContextPackage semantic identity.
        """
        return {
            "source_family": self.source_family.value,
            "exact_source_ref": self.exact_source_ref,
            "source_schema_id": self.source_schema_id,
            "source_schema_version": self.source_schema_version,
            "source_semantic_fingerprint": self.source_semantic_fingerprint,
            "authority_owner": self.authority_owner,
            "run_id": self.run_id,
            "scope_ref": self.scope_ref,
            "currentness_semantic_fingerprint": (
                self.currentness_binding.status_semantic_fingerprint
            ),
            "currentness_resolver_id": self.currentness_binding.resolver_id,
            "currentness_resolver_policy_version": (
                self.currentness_binding.resolver_policy_version
            ),
            "lifecycle_semantic_fingerprint": (
                self.lifecycle_binding.status_semantic_fingerprint
                if self.lifecycle_binding is not None
                else "NOT_APPLICABLE"
            ),
            "lifecycle_resolver_id": (
                self.lifecycle_binding.resolver_id
                if self.lifecycle_binding is not None
                else "NOT_APPLICABLE"
            ),
            "lifecycle_resolver_policy_version": (
                self.lifecycle_binding.resolver_policy_version
                if self.lifecycle_binding is not None
                else "NOT_APPLICABLE"
            ),
        }

    def binding_id(self) -> str:
        return "ctxsrc_" + canonical_sha256(self.semantic_identity())


class ContextRepresentationOptionSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_representation_option_v1"] = (
        "context_representation_option_v1"
    )
    representation_level: ContextRepresentationLevel
    projection_schema_id: TargetNonEmptyStr
    projection_schema_version: TargetNonEmptyStr
    representation_policy_id: TargetNonEmptyStr
    representation_policy_version: TargetNonEmptyStr
    materialized_payload: Any
    materialized_payload_sha256: TargetSha256Hex
    protected_field_evidence: tuple[TargetNonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _payload_and_lists(self) -> "ContextRepresentationOptionSchema":
        if canonical_sha256(self.materialized_payload) != self.materialized_payload_sha256:
            raise ValueError("Context representation payload hash mismatch")
        if tuple(self.protected_field_evidence) != tuple(
            sorted(set(self.protected_field_evidence))
        ):
            raise ValueError("protected_field_evidence must be canonical ordered-unique")
        return self


class ContextSourceCandidateSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_source_candidate_v1"] = (
        "context_source_candidate_v1"
    )
    candidate_id: TargetNonEmptyStr
    source_binding: ContextSourceBindingSchema
    title: TargetNonEmptyStr
    representations: tuple[ContextRepresentationOptionSchema, ...] = Field(
        min_length=1
    )
    relevance_evidence: dict[str, Any] | None = None
    conflict_group_ref: TargetNonEmptyStr | None = None
    dedup_group_ref: TargetNonEmptyStr | None = None

    @model_validator(mode="after")
    def _representations_unique(self) -> "ContextSourceCandidateSchema":
        levels = [item.representation_level for item in self.representations]
        if len(levels) != len(set(levels)):
            raise ValueError("Context candidate representation levels must be unique")
        return self


class ContextRoleSourceRuleSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_role_source_rule_v1"] = (
        "context_role_source_rule_v1"
    )
    source_family: ContextSourceFamily
    visibility: ContextRoleVisibility
    mandatory: bool = False
    priority_tier: ContextPriorityTier
    allowed_representation_levels: tuple[ContextRepresentationLevel, ...] = Field(
        min_length=1
    )
    minimum_representation_floor: ContextRepresentationLevel
    conditional_authorization_ref: TargetNonEmptyStr | None = None

    @model_validator(mode="after")
    def _rule_consistency(self) -> "ContextRoleSourceRuleSchema":
        if self.visibility is ContextRoleVisibility.FORBIDDEN and self.mandatory:
            raise ValueError("FORBIDDEN Context source cannot be mandatory")
        if self.visibility is ContextRoleVisibility.CONDITIONAL:
            if self.conditional_authorization_ref is None:
                raise ValueError(
                    "CONDITIONAL Context source requires exact authorization ref"
                )
        elif self.conditional_authorization_ref is not None:
            raise ValueError(
                "conditional_authorization_ref is only legal for CONDITIONAL visibility"
            )
        if self.minimum_representation_floor not in self.allowed_representation_levels:
            raise ValueError("Context representation floor must be allowed")
        if len(self.allowed_representation_levels) != len(
            set(self.allowed_representation_levels)
        ):
            raise ValueError("allowed representation levels must be unique")
        return self


class ContextRolePolicySchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_role_policy_v1"] = "context_role_policy_v1"
    role_policy_id: TargetNonEmptyStr
    role_policy_version: TargetNonEmptyStr
    agent_role: TargetNonEmptyStr
    execution_mode: TargetNonEmptyStr
    context_purpose: TargetNonEmptyStr
    source_rules: tuple[ContextRoleSourceRuleSchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_families(self) -> "ContextRolePolicySchema":
        families = [item.source_family for item in self.source_rules]
        if len(families) != len(set(families)):
            raise ValueError("Role Policy source families must be unique")
        return self


class ContextCandidateDecisionSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_candidate_decision_v1"] = (
        "context_candidate_decision_v1"
    )
    candidate_id: TargetNonEmptyStr
    source_binding: ContextSourceBindingSchema
    role_visibility: ContextRoleVisibility
    hard_eligibility: ContextHardEligibility
    hard_reason_codes: tuple[TargetNonEmptyStr, ...] = ()
    mandatory: bool
    priority_tier: ContextPriorityTier
    relevance_strategy_id: TargetNonEmptyStr | None = None
    relevance_strategy_version: TargetNonEmptyStr | None = None
    relevance_evidence: dict[str, Any] | None = None
    conflict_group_ref: TargetNonEmptyStr | None = None
    dedup_group_ref: TargetNonEmptyStr | None = None

    @model_validator(mode="after")
    def _decision_consistency(self) -> "ContextCandidateDecisionSchema":
        if tuple(self.hard_reason_codes) != tuple(sorted(set(self.hard_reason_codes))):
            raise ValueError("hard_reason_codes must be canonical ordered-unique")
        if self.hard_eligibility is ContextHardEligibility.REJECTED and self.mandatory:
            raise ValueError("rejected Context candidate cannot be mandatory")
        if bool(self.relevance_strategy_id) != bool(self.relevance_strategy_version):
            raise ValueError("relevance strategy id/version must be supplied together")
        return self


    def semantic_identity(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_binding": self.source_binding.semantic_identity(),
            "role_visibility": self.role_visibility.value,
            "hard_eligibility": self.hard_eligibility.value,
            "hard_reason_codes": self.hard_reason_codes,
            "mandatory": self.mandatory,
            "priority_tier": self.priority_tier.value,
            "relevance_strategy_id": self.relevance_strategy_id,
            "relevance_strategy_version": self.relevance_strategy_version,
            "relevance_evidence": self.relevance_evidence,
            "conflict_group_ref": self.conflict_group_ref,
            "dedup_group_ref": self.dedup_group_ref,
        }


class ContextMaterializationSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_materialization_v1"] = (
        "context_materialization_v1"
    )
    candidate_id: TargetNonEmptyStr
    source_binding_ref: TargetNonEmptyStr
    representation_level: ContextRepresentationLevel
    projection_schema_id: TargetNonEmptyStr
    projection_schema_version: TargetNonEmptyStr
    representation_policy_id: TargetNonEmptyStr
    representation_policy_version: TargetNonEmptyStr
    materialized_payload: Any
    materialized_payload_sha256: TargetSha256Hex
    estimated_tokens: int = Field(ge=0)
    protected_field_evidence: tuple[TargetNonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _materialization_integrity(self) -> "ContextMaterializationSchema":
        if canonical_sha256(self.materialized_payload) != self.materialized_payload_sha256:
            raise ValueError("Context materialized payload hash mismatch")
        if tuple(self.protected_field_evidence) != tuple(
            sorted(set(self.protected_field_evidence))
        ):
            raise ValueError("protected_field_evidence must be canonical ordered-unique")
        return self


class ConsumerInvocationRefSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_consumer_invocation_ref_v1"] = (
        "context_consumer_invocation_ref_v1"
    )
    run_id: TargetNonEmptyStr
    attempt_id: TargetNonEmptyStr
    invocation_id: TargetNonEmptyStr


class ConsumerAuthorizationDecisionRefSchema(_FrozenTargetContextSchema):
    schema_version: Literal["consumer_authorization_decision_ref_v1"] = (
        "consumer_authorization_decision_ref_v1"
    )
    consumer_invocation_ref: ConsumerInvocationRefSchema
    claim_ref: dict[str, Any]
    decision_id: TargetNonEmptyStr


class IntrinsicActiveVerifiedClaimAssessmentBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal[
        "intrinsic_active_verified_claim_assessment_binding_v1"
    ] = "intrinsic_active_verified_claim_assessment_binding_v1"
    claim_ref: dict[str, Any]
    assessed_verification_revision_ref: dict[str, Any] | None = None
    claim_head_revision: int | None = Field(default=None, ge=1)
    intrinsic_dimension_results: dict[str, Any]
    intrinsic_result: Literal[
        "ACTIVE_VERIFIED",
        "NOT_ACTIVE_VERIFIED",
        "SOURCE_UNRESOLVABLE",
    ]
    intrinsic_source_set_sha256: TargetSha256Hex | None = None
    intrinsic_resolver_id: TargetNonEmptyStr
    intrinsic_resolver_policy_version: TargetNonEmptyStr
    assessment_semantic_fingerprint: TargetSha256Hex

    @model_validator(mode="after")
    def _assessment_integrity(
        self,
    ) -> "IntrinsicActiveVerifiedClaimAssessmentBindingSchema":
        preimage = {
            "claim_ref": self.claim_ref,
            "assessed_verification_revision_ref": self.assessed_verification_revision_ref,
            "claim_head_revision": self.claim_head_revision,
            "intrinsic_dimension_results": self.intrinsic_dimension_results,
            "intrinsic_result": self.intrinsic_result,
            "intrinsic_source_set_sha256": self.intrinsic_source_set_sha256,
            "intrinsic_resolver_id": self.intrinsic_resolver_id,
            "intrinsic_resolver_policy_version": self.intrinsic_resolver_policy_version,
        }
        if canonical_sha256(preimage) != self.assessment_semantic_fingerprint:
            raise ValueError("intrinsic Claim assessment fingerprint mismatch")
        return self


class AuthorizationEvidenceBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["authorization_evidence_binding_v1"] = (
        "authorization_evidence_binding_v1"
    )
    authorization_path: Literal[
        "EXPLICIT_DEPENDENCY",
        "GOVERNED_CONSISTENCY_SCOPE",
    ]
    owning_authority_ref: dict[str, Any]
    owning_authority_schema_id: TargetNonEmptyStr
    owning_authority_schema_version: TargetNonEmptyStr
    owning_authority_semantic_fingerprint: TargetSha256Hex
    matched_dependency_ref: dict[str, Any] | None = None
    matched_consistency_subject: TargetNonEmptyStr | None = None
    matched_consistency_domain: TargetNonEmptyStr | None = None
    evidence_binding_sha256: TargetSha256Hex

    @model_validator(mode="after")
    def _evidence_integrity(self) -> "AuthorizationEvidenceBindingSchema":
        preimage = {
            "authorization_path": self.authorization_path,
            "owning_authority_ref": self.owning_authority_ref,
            "owning_authority_schema_id": self.owning_authority_schema_id,
            "owning_authority_schema_version": self.owning_authority_schema_version,
            "owning_authority_semantic_fingerprint": (
                self.owning_authority_semantic_fingerprint
            ),
            "matched_dependency_ref": self.matched_dependency_ref,
            "matched_consistency_subject": self.matched_consistency_subject,
            "matched_consistency_domain": self.matched_consistency_domain,
        }
        if canonical_sha256(preimage) != self.evidence_binding_sha256:
            raise ValueError("consumer authorization evidence hash mismatch")
        if self.authorization_path == "EXPLICIT_DEPENDENCY":
            if self.matched_dependency_ref is None:
                raise ValueError(
                    "EXPLICIT_DEPENDENCY authorization requires dependency ref"
                )
        elif (
            self.matched_consistency_subject is None
            and self.matched_consistency_domain is None
        ):
            raise ValueError(
                "GOVERNED_CONSISTENCY_SCOPE requires subject or domain binding"
            )
        return self


class ConsumerAuthorizationDecisionSchema(_FrozenTargetContextSchema):
    schema_version: Literal["consumer_authorization_decision_v1"] = (
        "consumer_authorization_decision_v1"
    )
    decision_ref: ConsumerAuthorizationDecisionRefSchema
    consumer_invocation_ref: ConsumerInvocationRefSchema
    consumer_role: TargetNonEmptyStr
    consumer_scope_ref: TargetNonEmptyStr
    context_purpose: TargetNonEmptyStr
    intrinsic_claim_assessment: IntrinsicActiveVerifiedClaimAssessmentBindingSchema
    authorization_evidence_binding: AuthorizationEvidenceBindingSchema | None = None
    role_authorized: bool
    scope_authorized: bool
    purpose_authorized: bool
    cross_section_authorized: bool
    policy_id: TargetNonEmptyStr
    policy_version: TargetNonEmptyStr
    decision: ConsumerAuthorizationDecisionState
    reason_codes: tuple[TargetNonEmptyStr, ...]
    decision_semantic_fingerprint: TargetSha256Hex

    @model_validator(mode="after")
    def _authorization_integrity(self) -> "ConsumerAuthorizationDecisionSchema":
        if self.consumer_invocation_ref != self.decision_ref.consumer_invocation_ref:
            raise ValueError("Consumer authorization invocation ref mismatch")
        if self.decision_ref.claim_ref != self.intrinsic_claim_assessment.claim_ref:
            raise ValueError("consumer authorization Claim ref mismatch")
        if tuple(self.reason_codes) != tuple(sorted(set(self.reason_codes))):
            raise ValueError("consumer authorization reason_codes must be ordered-unique")
        accepted = (
            self.intrinsic_claim_assessment.intrinsic_result == "ACTIVE_VERIFIED"
            and self.role_authorized
            and self.scope_authorized
            and self.purpose_authorized
            and self.cross_section_authorized
        )
        if self.decision is ConsumerAuthorizationDecisionState.AUTHORIZED and not accepted:
            raise ValueError("AUTHORIZED consumer decision lacks required authority proof")

        evidence_hash = (
            self.authorization_evidence_binding.evidence_binding_sha256
            if self.authorization_evidence_binding is not None
            else "NOT_APPLICABLE"
        )
        decision_id_preimage = {
            "contract": "consumer_authorization_decision_v1",
            "consumer_invocation_ref": self.consumer_invocation_ref.model_dump(
                mode="json"
            ),
            "claim_ref": self.decision_ref.claim_ref,
            "consumer_role": self.consumer_role,
            "consumer_scope_ref": self.consumer_scope_ref,
            "context_purpose": self.context_purpose,
            "intrinsic_claim_assessment_fingerprint": (
                self.intrinsic_claim_assessment.assessment_semantic_fingerprint
            ),
            "authorization_evidence_sha256": evidence_hash,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
        }
        expected_decision_id = "cauth_" + canonical_sha256(
            decision_id_preimage
        )
        if self.decision_ref.decision_id != expected_decision_id:
            raise ValueError("consumer authorization decision id mismatch")

        semantic_preimage = {
            **decision_id_preimage,
            "role_authorized": self.role_authorized,
            "scope_authorized": self.scope_authorized,
            "purpose_authorized": self.purpose_authorized,
            "cross_section_authorized": self.cross_section_authorized,
            "decision": self.decision.value,
            "reason_codes": self.reason_codes,
        }
        if canonical_sha256(semantic_preimage) != self.decision_semantic_fingerprint:
            raise ValueError("consumer authorization decision fingerprint mismatch")
        return self


class ModelContextCapabilityBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["model_context_capability_binding_v1"] = (
        "model_context_capability_binding_v1"
    )
    model_capability_ref: TargetNonEmptyStr
    capability_schema_id: TargetNonEmptyStr
    capability_schema_version: TargetNonEmptyStr
    capability_semantic_fingerprint: TargetSha256Hex
    model_context_window: int = Field(gt=0)
    safe_max_output_tokens: int = Field(ge=0)
    token_estimator_id: TargetNonEmptyStr
    token_estimator_version: TargetNonEmptyStr
    token_estimator_semantic_fingerprint: TargetSha256Hex


class ContextBudgetBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_budget_binding_v1"] = "context_budget_binding_v1"
    model_capability_binding: ModelContextCapabilityBindingSchema
    model_context_window: int = Field(gt=0)
    reserved_instruction_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    safety_margin_tokens: int = Field(ge=0)
    usable_context_tokens: int = Field(ge=0)
    estimated_selected_tokens: int = Field(ge=0)
    estimated_final_input_tokens: int = Field(ge=0)
    overflow_policy_id: TargetNonEmptyStr
    overflow_policy_version: TargetNonEmptyStr
    degradation_actions: tuple[TargetNonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _budget_consistency(self) -> "ContextBudgetBindingSchema":
        if self.model_context_window != self.model_capability_binding.model_context_window:
            raise ValueError("Context budget model window/capability mismatch")
        expected_usable = (
            self.model_context_window
            - self.reserved_instruction_tokens
            - self.reserved_output_tokens
            - self.safety_margin_tokens
        )
        if expected_usable < 0 or self.usable_context_tokens != expected_usable:
            raise ValueError("Context usable token budget derivation mismatch")
        if self.estimated_selected_tokens > self.usable_context_tokens:
            raise ValueError("selected Context exceeds usable context budget")
        if (
            self.estimated_final_input_tokens
            + self.reserved_output_tokens
            + self.safety_margin_tokens
            > self.model_context_window
        ):
            raise ValueError("final model input exceeds model context window")
        return self


class DynamicControlBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["dynamic_control_binding_v1"] = "dynamic_control_binding_v1"
    control_ref: TargetNonEmptyStr
    control_version: TargetNonEmptyStr
    control_sha256: TargetSha256Hex


class InstructionBindingSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_instruction_binding_v1"] = (
        "context_instruction_binding_v1"
    )
    system_instruction_ref: TargetNonEmptyStr
    system_instruction_version: TargetNonEmptyStr
    system_instruction_sha256: TargetSha256Hex
    role_instruction_ref: TargetNonEmptyStr
    role_instruction_version: TargetNonEmptyStr
    role_instruction_sha256: TargetSha256Hex
    prompt_template_ref: TargetNonEmptyStr
    prompt_template_version: TargetNonEmptyStr
    prompt_template_sha256: TargetSha256Hex
    task_output_contract_ref: TargetNonEmptyStr | None = None
    task_output_contract_sha256: TargetSha256Hex | None = None
    dynamic_control_bindings: tuple[DynamicControlBindingSchema, ...] = ()

    @model_validator(mode="after")
    def _instruction_consistency(self) -> "InstructionBindingSchema":
        if bool(self.task_output_contract_ref) != bool(self.task_output_contract_sha256):
            raise ValueError("task output contract ref/hash must be supplied together")
        refs = [item.control_ref for item in self.dynamic_control_bindings]
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ValueError("dynamic control bindings must be canonical ordered-unique")
        return self


class ContextInstructionMaterialSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_instruction_material_v1"] = (
        "context_instruction_material_v1"
    )
    system_instruction: str
    role_instruction: str
    prompt_template: str
    task_output_contract: str | None = None
    dynamic_control_payloads: tuple[tuple[TargetNonEmptyStr, str], ...] = ()

    @model_validator(mode="after")
    def _controls_unique(self) -> "ContextInstructionMaterialSchema":
        refs = [item[0] for item in self.dynamic_control_payloads]
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ValueError("dynamic control payloads must be canonical ordered-unique")
        return self


class FinalModelInputMaterialSchema(_FrozenTargetContextSchema):
    schema_version: Literal["final_model_input_material_v1"] = (
        "final_model_input_material_v1"
    )
    input_kind: ContextFinalInputKind
    input_schema_id: TargetNonEmptyStr
    input_schema_version: TargetNonEmptyStr
    canonicalization_policy_id: TargetNonEmptyStr
    canonicalization_policy_version: TargetNonEmptyStr
    inline_final_input_payload: Any | None = None
    immutable_final_input_content_ref: TargetNonEmptyStr | None = None
    final_model_input_sha256: TargetSha256Hex

    @model_validator(mode="after")
    def _replay_material(self) -> "FinalModelInputMaterialSchema":
        if (self.inline_final_input_payload is not None) == (
            self.immutable_final_input_content_ref is not None
        ):
            raise ValueError("exactly one final-input replay representation is required")
        if self.inline_final_input_payload is not None:
            if canonical_sha256(self.inline_final_input_payload) != self.final_model_input_sha256:
                raise ValueError("final model input inline payload hash mismatch")
        return self


class ModelInvocationInputEnvelopeSchema(_FrozenTargetContextSchema):
    schema_version: Literal["model_invocation_input_envelope_v1"] = (
        "model_invocation_input_envelope_v1"
    )
    invocation_subject: ContextInvocationSubjectSchema
    instruction_binding: InstructionBindingSchema
    ordered_context_materializations: tuple[ContextMaterializationSchema, ...]
    rendered_context_sha256: TargetSha256Hex
    final_messages_or_prompt_sha256: TargetSha256Hex
    final_input_material: FinalModelInputMaterialSchema
    final_model_input_sha256: TargetSha256Hex
    budget_binding: ContextBudgetBindingSchema

    @model_validator(mode="after")
    def _final_input_integrity(self) -> "ModelInvocationInputEnvelopeSchema":
        if self.final_input_material.final_model_input_sha256 != self.final_model_input_sha256:
            raise ValueError("final model input material/envelope hash mismatch")
        if self.final_messages_or_prompt_sha256 != self.final_model_input_sha256:
            raise ValueError("final prompt/messages hash mismatch")
        return self


class ContextPackageSchema(_FrozenTargetContextSchema):
    schema_version: Literal["context_package_v1"] = "context_package_v1"
    package_ref: ContextPackageRefSchema
    invocation_subject: ContextInvocationSubjectSchema
    policy_binding: ContextPolicyBindingSchema
    candidate_decisions: tuple[ContextCandidateDecisionSchema, ...]
    selected_materializations: tuple[ContextMaterializationSchema, ...]
    consumer_authorization_decisions: tuple[
        ConsumerAuthorizationDecisionSchema, ...
    ] = ()
    instruction_binding: InstructionBindingSchema
    budget_binding: ContextBudgetBindingSchema
    model_input: ModelInvocationInputEnvelopeSchema
    context_semantic_fingerprint: TargetSha256Hex
    created_at: datetime

    def semantic_preimage(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_subject": self.invocation_subject.model_dump(mode="json"),
            "policy_binding": self.policy_binding.model_dump(mode="json"),
            "candidate_decisions": [
                item.semantic_identity() for item in self.candidate_decisions
            ],
            "selected_materializations": [
                item.model_dump(mode="json") for item in self.selected_materializations
            ],
            "consumer_authorization_decisions": [
                item.model_dump(mode="json")
                for item in self.consumer_authorization_decisions
            ],
            "instruction_binding": self.instruction_binding.model_dump(mode="json"),
            "budget_binding": self.budget_binding.model_dump(mode="json"),
            "model_input": self.model_input.model_dump(mode="json"),
        }

    @model_validator(mode="after")
    def _package_integrity(self) -> "ContextPackageSchema":
        subject = self.invocation_subject
        ref = self.package_ref
        if (
            ref.run_id != subject.run_id
            or ref.attempt_id != subject.attempt_id
            or ref.invocation_id != subject.invocation_id
        ):
            raise ValueError("ContextPackageRef invocation identity mismatch")
        if self.instruction_binding != self.model_input.instruction_binding:
            raise ValueError("Context package/model-input instruction binding mismatch")
        if self.budget_binding != self.model_input.budget_binding:
            raise ValueError("Context package/model-input budget binding mismatch")
        if subject != self.model_input.invocation_subject:
            raise ValueError("Context package/model-input invocation mismatch")
        if self.selected_materializations != self.model_input.ordered_context_materializations:
            raise ValueError("Context package/model-input materialization mismatch")

        decisions = {item.candidate_id: item for item in self.candidate_decisions}
        selected_ids = [item.candidate_id for item in self.selected_materializations]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("Context selected materializations must be unique")
        for candidate_id in selected_ids:
            decision = decisions.get(candidate_id)
            if decision is None or decision.hard_eligibility is not ContextHardEligibility.ELIGIBLE:
                raise ValueError("selected Context material lacks eligible decision")
        if any(
            item.decision is not ConsumerAuthorizationDecisionState.AUTHORIZED
            for item in self.consumer_authorization_decisions
        ):
            raise ValueError("ContextPackage cannot retain rejected consumer authorization")

        expected_fingerprint = canonical_sha256(self.semantic_preimage())
        if expected_fingerprint != self.context_semantic_fingerprint:
            raise ValueError("ContextPackage semantic fingerprint mismatch")
        expected_id = "ctxpkg_" + canonical_sha256(
            {
                "contract": "context_package_v1",
                "invocation_subject": subject.model_dump(mode="json"),
                "context_semantic_fingerprint": expected_fingerprint,
            }
        )
        if ref.context_package_id != expected_id:
            raise ValueError("ContextPackage id derivation mismatch")
        return self


__all__ = [
    "AuthorizationEvidenceBindingSchema",
    "BusinessContextSchema",
    "ConsumerAuthorizationDecisionRefSchema",
    "ConsumerAuthorizationDecisionSchema",
    "ConsumerAuthorizationDecisionState",
    "ConsumerInvocationRefSchema",
    "ContextBudgetBindingSchema",
    "ContextBudgetSchema",
    "ContextBuildRequestSchema",
    "ContextBundleSchema",
    "ContextCandidateDecisionSchema",
    "ContextDecisionSchema",
    "ContextFinalInputKind",
    "ContextHardEligibility",
    "ContextInstructionMaterialSchema",
    "ContextInvocationSubjectSchema",
    "ContextItemSchema",
    "ContextMaterializationSchema",
    "ContextPackageRefSchema",
    "ContextPackageReplayState",
    "ContextPackageSchema",
    "ContextPolicyBindingSchema",
    "ContextPriorityTier",
    "ContextRepresentationLevel",
    "ContextRepresentationOptionSchema",
    "ContextRolePolicySchema",
    "ContextRoleSourceRuleSchema",
    "ContextRoleVisibility",
    "ContextSourceBindingSchema",
    "ContextSourceCandidateSchema",
    "ContextSourceFamily",
    "ContextSourceStatusBindingSchema",
    "ConversationContextSchema",
    "DynamicControlBindingSchema",
    "FinalModelInputMaterialSchema",
    "GenerationContextSchema",
    "InstructionBindingSchema",
    "IntrinsicActiveVerifiedClaimAssessmentBindingSchema",
    "LLMContextPackageSchema",
    "ModelContextCapabilityBindingSchema",
    "ModelInvocationInputEnvelopeSchema",
    "RuntimeContextSchema",
    "TaskContextSchema",
    "UserContextSchema",
    "WorkflowStepStateSchema",
]
