"""定义用于评测、人工验收和后训练数据构造的反馈结构。

反馈将多维评分、自由文本、人工编辑和最终接受决定分开保存。这样可以区分“用户给低
分但未修改”“人工改写后接受”以及“结果未评分但明确拒绝”等不同监督信号。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from .common import SchemaBase


class FeedbackRatingSchema(SchemaBase):
    """保存一次人工反馈中的可选多维评分。

    当前 Schema 不强制评分范围，具体量表应由采集界面或评测协议定义并记录在外层
   元数据中；不同量表的数据不能仅凭整数直接合并比较。
    """

    # 对结果的总体评分。
    overall: Optional[int] = None
    # 对要求和章节覆盖完整性的评分。
    completeness: Optional[int] = None
    # 对事实、数字和引用正确性的评分。
    accuracy: Optional[int] = None
    # 对输出与用户任务相关程度的评分。
    relevance: Optional[int] = None
    # 对结构、格式和可读性的评分。
    format_quality: Optional[int] = None
    # 对结果能否直接进入业务流程的评分。
    usability: Optional[int] = None
    # 保存尚未纳入稳定量表的评分维度或量表说明。
    extra: Dict[str, Any] = Field(default_factory=dict)


class HumanEditSchema(SchemaBase):
    """描述人工是否修改模型结果以及修改后的监督信息。"""

    # 是否发生了实质人工编辑。
    has_edit: bool = False
    # 人工修改后的完整结构化结果；未编辑或只提供说明时可为空。
    edited_result: Optional[Dict[str, Any]] = None
    # 对主要修改点、错误类型或编辑原因的文字摘要。
    edit_summary: Optional[str] = None
    # 尚未进入稳定人工编辑契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class FeedbackSchema(SchemaBase):
    """把一次反馈关联到具体任务运行和人工评价结果。"""

    # 当前反馈契约版本。
    schema_version: str = "feedback_v1"

    # 反馈记录唯一标识。
    feedback_id: str

    # 被评价业务任务标识。
    task_id: str
    # 被评价单次运行标识，避免同一任务多次执行的反馈混淆。
    run_id: str

    # 提交反馈的用户或评审者标识；匿名反馈可为空。
    user_id: Optional[str] = None

    # 多维评分；未评分的维度保持 ``None``，不能自动解释为零分。
    rating: FeedbackRatingSchema = Field(default_factory=FeedbackRatingSchema)
    # 自由文本评价、问题描述或验收意见。
    comment: Optional[str] = None

    # 人工编辑后的结果及编辑摘要。
    human_edit: HumanEditSchema = Field(default_factory=HumanEditSchema)
    # 最终是否接受本次模型结果；未做决定时为 ``None``。
    accepted: Optional[bool] = None

    # 反馈创建时间。
    created_at: str

    # 评审场景、量表版本或其他尚未进入稳定契约的字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
