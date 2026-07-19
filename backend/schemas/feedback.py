# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：FeedbackRatingSchema、HumanEditSchema、FeedbackSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Human feedback scheme for evaluation and post-training data."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from .common import SchemaBase


# 阅读注释（类）：封装 反馈 rating Schema，定义跨模块传递的数据结构与字段约束。
class FeedbackRatingSchema(SchemaBase):
    """封装 反馈 rating Schema，定义跨模块传递的数据结构与字段约束。"""
    overall: Optional[int] = None
    completeness: Optional[int] = None
    accuracy: Optional[int] = None
    relevance: Optional[int] = None
    format_quality: Optional[int] = None
    usability: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 human edit Schema，定义跨模块传递的数据结构与字段约束。
class HumanEditSchema(SchemaBase):
    """封装 human edit Schema，定义跨模块传递的数据结构与字段约束。"""
    has_edit: bool = False
    edited_result: Optional[Dict[str, Any]] = None
    edit_summary: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 反馈 Schema，定义跨模块传递的数据结构与字段约束。
class FeedbackSchema(SchemaBase):
    """封装 反馈 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "feedback_v1"

    feedback_id: str

    task_id: str
    run_id: str

    user_id: Optional[str] = None

    rating: FeedbackRatingSchema = Field(default_factory=FeedbackRatingSchema)
    comment: Optional[str] = None

    human_edit: HumanEditSchema = Field(default_factory=HumanEditSchema)
    accepted: Optional[bool] = None

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)
