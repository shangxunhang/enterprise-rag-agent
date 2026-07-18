"""Human feedback scheme for evaluation and post-training data."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from .common import SchemaBase


class FeedbackRatingSchema(SchemaBase):
    overall: Optional[int] = None
    completeness: Optional[int] = None
    accuracy: Optional[int] = None
    relevance: Optional[int] = None
    format_quality: Optional[int] = None
    usability: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class HumanEditSchema(SchemaBase):
    has_edit: bool = False
    edited_result: Optional[Dict[str, Any]] = None
    edit_summary: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class FeedbackSchema(SchemaBase):
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
