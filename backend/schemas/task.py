# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：TaskOptionsSchema、TaskSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Task entry schema.

A Task is the runtime envelope. ProjectInput is carried as a validated payload
inside the envelope and must be provided by the caller or explicitly built by
an API/CLI adapter before the workflow starts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import SchemaBase
from .status import ExecutionStatus


# 阅读注释（类）：封装 任务 options Schema，定义跨模块传递的数据结构与字段约束。
class TaskOptionsSchema(SchemaBase):
    """封装 任务 options Schema，定义跨模块传递的数据结构与字段约束。"""
    need_table_analysis: bool = True
    need_rag: bool = True
    need_citation: bool = True
    need_word_export: bool = False
    need_human_review: bool = True
    retrieval_mode: str = "hybrid"
    max_context_chars: Optional[int] = 6000
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 任务 Schema，定义跨模块传递的数据结构与字段约束。
class TaskSchema(SchemaBase):
    """封装 任务 Schema，定义跨模块传递的数据结构与字段约束。"""
    # schema自身的版本
    schema_version: str = "task_v2"
    #########################任务身份字段##################################
    # 当前任务实例ID
    task_id: str
    # 本次具体执行的ID
    run_id: str
    # 租户ID 多租户预留
    tenant_id: str = "default"
    ########################业务类型和名称##################################
    # 任务类型：scheme_generation、bid_generation、design_review、table_parse
    task_type: str
    # 任务的可读名称 例如：生成一个政务云的建设方案
    task_name: Optional[str] = None
    # 这个任务属于哪个项目
    project_name: Optional[str] = None
    ###################################用户和会话信息###########################
    # 谁发起的任务 以后用于：权限、审计、历史记录、数据隔离
    user_id: Optional[str] = None
    #这次对话或会话ID
    session_id: Optional[str] = None
    #######################核心业务输入#########################################
    # 原始用户Query：生成一个政务云建设方案
    user_input: str
    # 已经标准化后的完整项目输入
    project_input: Dict[str, Any]
    #  从 project_input 里单独提出来的材料列表
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    #  生成约束 例如：必须引用、禁止无依据生成、正式语气
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    # 规定最终输出长什么样子
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    # 业务通用元数据 例如：行业、地区、项目年份，后续检索过滤，审计，统计可能会用
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ####################资源索引字段############################################
    # 关联的原始文件ID
    file_ids: List[str] = Field(default_factory=list)
    # 文档ID
    doc_ids: List[str] = Field(default_factory=list)
    # 允许访问或指定使用的知识库 ID
    kb_ids: List[str] = Field(default_factory=list)
    # 结构化表格引用
    table_refs: List[str] = Field(default_factory=list)
    # 指定使用哪个模板
    template_id: Optional[str] = None
    ####################执行控制字段############################################
    # 任务优先级
    priority: str = "normal"
    # 当前任务状态（RUNNING  SUCCESS PARTIAL_SUCCESS FAILED）
    status: ExecutionStatus = ExecutionStatus.PENDING
    # 任务级执行选项  是否允许重试 超时 是否启用某些能力 最大循环次数
    options: TaskOptionsSchema = Field(default_factory=TaskOptionsSchema)
    ####################时间字段############################################
    # 任务创建时间
    created_at: str
    # 最后更新时间
    updated_at: Optional[str] = None
    #####################扩展字段############################################
    # 用来放暂时不值得正式进入 Schema 的信息。  实验标签、调试标记、临时兼容字段
    extra: Dict[str, Any] = Field(default_factory=dict)
