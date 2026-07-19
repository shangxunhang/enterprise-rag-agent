# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：GenerationParamsSchema、ModelMessageSchema、ModelRequestSchema、TokenUsageSchema、ModelResponseSchema、ModelRunSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""ModelGateway request/response/run schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import ErrorSchema, SchemaBase


# 阅读注释（类）：封装 生成 params Schema，定义跨模块传递的数据结构与字段约束。
class GenerationParamsSchema(SchemaBase):
    """封装 生成 params Schema，定义跨模块传递的数据结构与字段约束。"""
    max_new_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    do_sample: Optional[bool] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 模型 消息 Schema，定义跨模块传递的数据结构与字段约束。
class ModelMessageSchema(SchemaBase):
    """封装 模型 消息 Schema，定义跨模块传递的数据结构与字段约束。"""
    role: str  # system | user | assistant | tool
    content: str
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 模型 请求 Schema，定义跨模块传递的数据结构与字段约束。
class ModelRequestSchema(SchemaBase):
    """封装 模型 请求 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "model_request_v1"

    model_call_id: str
    task_id: str
    run_id: str

    model_name: str
    caller_agent: Optional[str] = None

    prompt: str
    system_prompt: Optional[str] = None
    messages: List[Dict[str, str]] = Field(default_factory=list)

    temperature: float = 0.2
    max_tokens: int = 2048

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 Token 用量 Schema，定义跨模块传递的数据结构与字段约束。
class TokenUsageSchema(SchemaBase):
    """封装 Token 用量 Schema，定义跨模块传递的数据结构与字段约束。"""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 模型 响应 Schema，定义跨模块传递的数据结构与字段约束。
class ModelResponseSchema(SchemaBase):
    """封装 模型 响应 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "model_response_v1"

    model_call_id: str
    task_id: str
    run_id: str

    model_name: str
    success: bool

    content: str = ""
    raw_output: Dict[str, Any] = Field(default_factory=dict)

    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None

    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None

    created_at: str
    token_usage: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    finish_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 模型 run Schema，定义跨模块传递的数据结构与字段约束。
class ModelRunSchema(SchemaBase):
    """封装 模型 run Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "model_run_v1"

    model_run_id: str

    task_id: str
    run_id: str

    agent_name: str

    model_role: str  # llm | embedding | reranker

    provider: str
    model_name: str
    model_version: Optional[str] = None

    prompt_id: Optional[str] = None
    prompt_version: Optional[str] = None

    input_preview: Optional[str] = None
    output_preview: Optional[str] = None

    generation_params: Dict[str, Any] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)

    latency_ms: Optional[int] = None

    status: str  # success | failed
    error: Optional[ErrorSchema] = None

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)
