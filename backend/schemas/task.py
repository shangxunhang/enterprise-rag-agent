"""定义进入主链的运行时任务外壳。

``ProjectInput`` 必须在工作流开始前由调用方或显式 API/CLI 适配器完成严格校验，
``TaskSchema`` 再补充单次运行身份、展平资源范围、执行选项和生命周期字段。任务请求
哈希只覆盖决定执行语义的不可变字段，不包含运行中会变化的状态和更新时间。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from .common import SchemaBase
from .status import ExecutionStatus


# 构成任务请求身份的不可变字段集合。
# ``status``、``created_at`` 和 ``updated_at`` 不参与：它们描述运行生命周期，而不是
# 调用方要求系统执行什么。字段集合用于持久化恢复时核对请求是否发生语义漂移。
_TASK_REQUEST_IDENTITY_FIELDS = (
    "task_id",
    "run_id",
    "tenant_id",
    "task_type",
    "task_name",
    "project_name",
    "user_id",
    "session_id",
    "user_input",
    "project_input",
    "source_materials",
    "generation_requirements",
    "output_schema",
    "metadata",
    "file_ids",
    "doc_ids",
    "kb_ids",
    "table_refs",
    "template_id",
    "priority",
    "options",
    "extra",
)


class TaskOptionsSchema(SchemaBase):
    """保存单个任务对可选能力和运行预算的明确要求。"""

    # 严格类型并禁止未知顶层字段；实验选项必须进入 extra。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 是否执行表格或结构化输入分析。
    need_table_analysis: bool = True
    # 是否启用证据检索链。
    need_rag: bool = True
    # 是否要求生成结果携带可追溯引用。
    need_citation: bool = True
    # 是否生成 Word 等办公文档导出物。
    need_word_export: bool = False
    # 是否在业务流程中保留人工审核节点或建议。
    need_human_review: bool = True
    # 期望使用的检索模式，例如 vector、keyword 或 hybrid。
    retrieval_mode: str = "hybrid"
    # 允许打包进模型上下文的最大字符数；为空表示由下层策略决定。
    max_context_chars: Optional[int] = 6000
    # 尚未进入稳定任务选项契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskSchema(SchemaBase):
    """表示一次可执行任务的完整运行时输入和控制外壳。

    ``project_input`` 是完整规范业务输入，其他材料、要求和索引字段是供运行时高频读取
    的投影。投影必须由入口工厂统一构造，不能由不同节点各自从原始输入重新解释。
    """

    # 严格类型并禁止未知顶层字段，防止入口层静默吞掉调用方拼写错误。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前任务外壳契约版本。
    schema_version: str = "task_v2"

    # 业务任务唯一标识；同一业务任务的恢复和重试通常保持不变。
    task_id: str
    # 单次执行唯一标识；不同执行通过该字段隔离轨迹、检查点和产物。
    run_id: str
    # 租户隔离标识；当前默认值也为后续权限和资源隔离预留。
    tenant_id: str = "default"

    # 业务任务类型，例如 scheme_generation、bid_generation 或 design_review。
    task_type: str
    # 面向人工和管理界面的任务名称。
    task_name: Optional[str] = None
    # 任务所属项目名称。
    project_name: Optional[str] = None

    # 发起任务的用户标识，用于权限、审计和历史关联。
    user_id: Optional[str] = None
    # 发起任务的会话标识。
    session_id: Optional[str] = None

    # 入口接收并与 ProjectInput.user_query 保持一致的原始用户请求。
    user_input: str
    # 已通过业务 Schema 严格校验的完整项目输入快照。
    project_input: Dict[str, Any]
    # 从规范项目输入投影出的来源材料集合。
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    # 文体、章节、引用、禁止臆测等生成要求。
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    # 最终输出标题、格式和结构契约。
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    # 行业、地区、年份等可用于过滤、审计和统计的业务元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 与任务关联的原始文件标识，按入口材料顺序有序去重。
    file_ids: List[str] = Field(default_factory=list)
    # 与任务关联的规范文档标识。
    doc_ids: List[str] = Field(default_factory=list)
    # 任务允许访问或明确指定的知识库标识。
    kb_ids: List[str] = Field(default_factory=list)
    # 任务引用的结构化表格标识。
    table_refs: List[str] = Field(default_factory=list)
    # 明确指定的输出或业务模板标识。
    template_id: Optional[str] = None

    # 调度使用的任务优先级字符串。
    priority: str = "normal"
    # 当前任务生命周期状态；初始为待执行。
    status: ExecutionStatus = ExecutionStatus.PENDING
    # 当前任务启用的可选能力和运行预算。
    options: TaskOptionsSchema = Field(default_factory=TaskOptionsSchema)

    # 任务对象创建时间。
    created_at: str
    # 最近一次任务生命周期更新时间。
    updated_at: Optional[str] = None

    # 实验标签、调试标记和尚未进入稳定任务契约的兼容字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    def canonical_request_projection(self) -> Dict[str, Any]:
        """返回只包含不可变执行语义字段的规范请求投影。

        Returns:
            可直接稳定 JSON 序列化的请求身份字典。
        """

        # 使用显式白名单排除运行中状态和时间字段，防止恢复校验因生命周期变化失败。
        return self.model_dump(
            mode="json",
            include=set(_TASK_REQUEST_IDENTITY_FIELDS),
        )

    def canonical_request_sha256(self) -> str:
        """计算与字典键顺序无关的任务请求身份哈希。

        Returns:
            规范请求投影的 SHA-256 十六进制摘要。
        """

        # 第一步：按键排序并使用紧凑分隔符生成确定性 UTF-8 JSON 文本。
        serialized = json.dumps(
            self.canonical_request_projection(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        # 第二步：对规范字节计算哈希，用于恢复、幂等和请求身份核对。
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
