"""定义从运行原始数据到训练或评测就绪样本的统一采集记录。

同一记录可经历 raw、candidate、evaluated、accepted/rejected 和 readiness 等阶段。
运行成功只说明业务调用完成，不自动代表样本可训练或可评测；只有阶段与两个 readiness
布尔值同时满足严格双向约束时，记录才可进入对应数据集。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import SchemaBase


class DataCaptureRecordSchema(SchemaBase):
    """保存一条可追踪生命周期的后训练或评测候选数据。

    记录可以承载 SFT、偏好、评测、人工复核或拒绝样本所需信息。Schema 使用严格模式
    并禁止未知顶层字段，防止采集链因拼写错误静默产生不可解释数据。
    """

    # 禁止额外顶层字段并启用严格类型；扩展数据必须显式放入 metadata 或 extra。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前采集记录契约只接受唯一固定版本字符串。
    schema_version: Literal["data_capture_record_v2"] = "data_capture_record_v2"

    # 采集记录唯一标识。
    record_id: str
    # 同一记录经历状态更新时递增的生命周期修订号。
    lifecycle_revision: int = Field(default=0, ge=0)

    # 数据用途类别，例如原始交互、SFT 候选、偏好对或评测样本。
    capture_type: str
    # 当前数据治理阶段；readiness 阶段与可用性布尔值存在严格双向约束。
    capture_stage: Literal[
        "raw",
        "candidate",
        "evaluated",
        "accepted",
        "rejected",
        "runtime_rejected",
        "training_ready",
        "eval_ready",
    ] = "candidate"

    # 产生该记录的业务任务标识。
    task_id: str
    # 产生该记录的单次运行标识。
    run_id: str
    # 与运行轨迹关联的 trace 标识。
    trace_id: Optional[str] = None

    # 产生数据的组件类别，例如 agent、tool、model 或 workflow。
    source_component_type: Optional[str] = None
    # 产生数据的具体组件名称。
    source_component_name: Optional[str] = None
    # 触发采集的运行事件类别。
    source_event_type: Optional[str] = None

    # 用户原始任务输入。
    user_input: Optional[str] = None
    # 查询改写或扩展后的检索输入。
    rewritten_query: Optional[str] = None

    # RAG 上下文的兼容结构化快照。
    rag_context: Dict[str, Any] = Field(default_factory=dict)
    # 从输入和证据中确认的结构化事实。
    structured_facts: Dict[str, Any] = Field(default_factory=dict)

    # 实际发送给模型的提示词文本。
    prompt: Optional[str] = None
    # 模型原始或规范化输出文本。
    model_output: Optional[str] = None
    # 尚未通过最终交付门禁的生成草稿文本。
    generated_draft_output: Optional[str] = None
    # 已通过业务门禁、允许交付的输出文本。
    deliverable_output: Optional[str] = None

    # 人工评分、意见和编辑信息。
    human_feedback: Dict[str, Any] = Field(default_factory=dict)
    # 任务标签、偏好标签或监督目标。
    label: Dict[str, Any] = Field(default_factory=dict)

    # 当前质量评估的综合分数；量表定义应记录在元数据中。
    quality_score: Optional[float] = None
    # 质量规则产生的稳定标记集合。
    quality_flags: List[str] = Field(default_factory=list)
    # 是否仍需人工判断；默认保守为 True。
    need_human_review: bool = True
    # 是否已经通过训练数据治理并可进入训练集。
    is_usable_for_training: bool = False
    # 是否已经通过评测数据治理并可进入评测集。
    is_usable_for_eval: bool = False

    # 与该记录直接关联的轨迹事件标识。
    related_trace_event_ids: List[str] = Field(default_factory=list)
    # 与该记录相关的原始文件标识。
    related_file_ids: List[str] = Field(default_factory=list)
    # 原始输入、证据、模型调用、草稿和标签之间的血缘引用。
    lineage_refs: Dict[str, Any] = Field(default_factory=dict)

    # 采集记录首次创建时间。
    created_at: str

    # 稳定采集上下文元数据，例如数据集批次和质量规则版本。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定采集契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    # 明确保存召回和重排后的结构化片段，不只保留拼接文本。
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    # 与生成内容关联的引用来源或绑定信息。
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    # RAG 查询、召回、重排、压缩和质量判断轨迹。
    rag_trace: Dict[str, Any] = Field(default_factory=dict)

    # 模板 ID、版本、变量和渲染信息。
    prompt_info: Dict[str, Any] = Field(default_factory=dict)
    # 模型角色、供应商、实际模型、参数和用量信息。
    model_info: Dict[str, Any] = Field(default_factory=dict)

    # 面向离线评测器的标准样本结构。
    eval_sample: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_readiness_stage(self) -> "DataCaptureRecordSchema":
        """验证生命周期阶段与训练/评测可用性标记严格一致。

        Returns:
            校验通过后的当前记录。

        Raises:
            ValueError: readiness 阶段和对应布尔值不满足双向等价，或运行期拒绝样本
                被标记为可训练/可评测时抛出。
        """

        # 第一步：只有 training_ready 阶段可设置训练可用，同时该阶段必须设置为 True。
        if self.is_usable_for_training != (self.capture_stage == "training_ready"):
            raise ValueError(
                "capture_stage='training_ready' iff is_usable_for_training=True"
            )

        # 第二步：评测 readiness 使用相同双向约束，避免布尔值与生命周期漂移。
        if self.is_usable_for_eval != (self.capture_stage == "eval_ready"):
            raise ValueError("capture_stage='eval_ready' iff is_usable_for_eval=True")

        # 第三步：运行时已拒绝的数据不能绕过阶段约束进入任何正式数据集。
        if self.capture_stage == "runtime_rejected" and (
            self.is_usable_for_training or self.is_usable_for_eval
        ):
            raise ValueError("runtime_rejected capture cannot be readiness-eligible")

        return self
