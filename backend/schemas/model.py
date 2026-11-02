"""定义模型网关的供应商无关请求、响应和运行审计结构。

业务代码通过稳定 ``model_role`` 表达调用意图，网关再解析实际模型；``model_name``
只作为显式覆盖或兼容字段。响应保存规范文本、错误、用量和原始输出，运行记录则用于
跨调用审计模型、提示词版本、参数、耗时和状态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import ErrorSchema, SchemaBase
from schemas.failure import FailureOrigin


class GenerationParamsSchema(SchemaBase):
    """保存常用文本生成参数及供应商扩展参数。"""

    # 单次生成允许返回的最大新 token 数。
    max_new_tokens: Optional[int] = None
    # 采样温度；为空时由模型配置或网关默认值决定。
    temperature: Optional[float] = None
    # nucleus sampling 的累计概率阈值。
    top_p: Optional[float] = None
    # 是否启用随机采样。
    do_sample: Optional[bool] = None
    # 供应商特有或尚未进入稳定参数契约的字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ModelMessageSchema(SchemaBase):
    """表示对话式模型上下文中的一条标准消息。"""

    # 消息角色，例如 system、user、assistant 或 tool。
    role: str
    # 消息文本内容。
    content: str
    # 工具调用标识、多模态信息等扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ModelRequestSchema(SchemaBase):
    """表示应用层提交给模型网关的一次标准生成请求。

    正常生产路由应提供 ``model_role``，让网关根据角色配置选择具体模型；测试、调试和
    迁移调用方才通过 ``model_name`` 显式覆盖。
    """

    # 当前模型请求契约版本。
    schema_version: str = "model_request_v1"

    # 单次模型调用唯一标识，用于响应、轨迹和用量关联。
    model_call_id: str
    # 调用所属业务任务标识。
    task_id: str
    # 调用所属单次运行标识。
    run_id: str

    # 稳定业务调用意图，例如 supervisor_routing 或 section_generation。
    model_role: Optional[str] = None
    # 实际模型注册名的显式覆盖；正常生产调用应优先使用 role。
    model_name: Optional[str] = None
    # 发起模型调用的 Agent 名称。
    caller_agent: Optional[str] = None

    # 兼容单文本模型客户端使用的完整提示词。
    prompt: str
    # 可选独立系统提示词。
    system_prompt: Optional[str] = None
    # OpenAI 风格对话消息列表；元素保持最小 role/content 字典契约。
    messages: List[Dict[str, str]] = Field(default_factory=list)

    # 当前调用采样温度。
    temperature: float = 0.2
    # 当前调用允许返回的最大 token 数。
    max_tokens: int = 2048

    # 请求对象创建时间。
    created_at: str

    # 调用用途、供应商参数和其他尚未进入稳定请求契约的信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


class TokenUsageSchema(SchemaBase):
    """保存一次模型调用的输入、输出和总 token 用量。"""

    # 输入提示词和上下文消耗的 token 数。
    prompt_tokens: Optional[int] = None
    # 模型生成内容消耗的 token 数。
    completion_tokens: Optional[int] = None
    # 供应商报告的总 token 数；不应在缺失时盲目假设等于前两项之和。
    total_tokens: Optional[int] = None
    # 缓存 token、推理 token 等供应商特有用量字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ModelResponseSchema(SchemaBase):
    """表示模型网关完成一次调用后返回的规范结果。"""

    # 当前模型响应契约版本。
    schema_version: str = "model_response_v1"

    # 对应请求的模型调用标识。
    model_call_id: str
    # 响应所属业务任务标识。
    task_id: str
    # 响应所属单次运行标识。
    run_id: str

    # 网关实际选择并调用的模型注册名。
    model_name: str
    # 调用是否成功完成并产生可消费响应。
    success: bool

    # 规范化后的主要文本内容。
    content: str = ""
    # 经过边界审查后保留的供应商原始响应结构。
    raw_output: Dict[str, Any] = Field(default_factory=dict)

    # 调用失败时最接近来源构造的结构化错误。
    error: Optional[ErrorSchema] = None
    # 兼容和快速展示使用的错误摘要。
    error_message: Optional[str] = None
    # 失败最接近的来源 family；只用于事实/路由边界判定，不授予业务控制权限。
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN

    # 供应商调用开始时间。
    started_at: Optional[str] = None
    # 供应商调用结束时间。
    finished_at: Optional[str] = None
    # 包含网关边界处理的毫秒耗时。
    latency_ms: Optional[int] = None

    # 响应对象创建时间。
    created_at: str
    # 规范 token 用量。
    token_usage: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    # 模型结束原因，例如 stop、length 或 content_filter。
    finish_reason: Optional[str] = None
    # 路由、供应商、重试和缓存等稳定响应元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定响应契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ModelRunSchema(SchemaBase):
    """保存一次模型运行的审计与数据血缘摘要。

    该记录可以覆盖 LLM、embedding 和 reranker 调用，不替代完整请求/响应。输入输出只
    保存受限预览，敏感或完整内容应进入受控数据采集链。
    """

    # 当前模型运行记录契约版本。
    schema_version: str = "model_run_v1"

    # 模型运行审计记录唯一标识。
    model_run_id: str

    # 运行所属业务任务标识。
    task_id: str
    # 运行所属单次工作流标识。
    run_id: str

    # 发起调用的 Agent 名称。
    agent_name: str

    # 模型能力类别，例如 llm、embedding 或 reranker。
    model_role: str

    # 实际服务供应商名称。
    provider: str
    # 实际模型名称。
    model_name: str
    # 可选模型版本、快照或部署版本。
    model_version: Optional[str] = None

    # 使用的提示词模板标识。
    prompt_id: Optional[str] = None
    # 使用的提示词模板版本。
    prompt_version: Optional[str] = None

    # 脱敏且截断后的输入预览。
    input_preview: Optional[str] = None
    # 脱敏且截断后的输出预览。
    output_preview: Optional[str] = None

    # 本次调用实际生效的生成或模型参数。
    generation_params: Dict[str, Any] = Field(default_factory=dict)
    # token、批量大小或其他模型用量。
    usage: Dict[str, Any] = Field(default_factory=dict)

    # 本次模型运行毫秒耗时。
    latency_ms: Optional[int] = None

    # 兼容运行状态字符串，例如 success 或 failed。
    status: str
    # 失败时的结构化错误。
    error: Optional[ErrorSchema] = None

    # 模型运行记录创建时间。
    created_at: str

    # 成本、硬件、路由和其他尚未进入稳定记录契约的信息。
    extra: Dict[str, Any] = Field(default_factory=dict)
