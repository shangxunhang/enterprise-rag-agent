"""追加式 Domain Event 及 Writer 显式完成事件。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from ._base import ContractSchema
from .enums import StateActorRole
from .payloads import ParagraphArtifactSchema


class DomainEventSchema(ContractSchema):
    """一次成功状态提交对应的一条不可变、顺序递增事件。"""

    schema_version: str = "domain_event_v1"
    event_id: str
    event_type: str
    sequence: int = Field(ge=1)
    task_id: str
    run_id: str
    section_id: Optional[str] = None
    actor_role: StateActorRole
    execution_id: Optional[str] = None
    attempt_id: Optional[str] = None
    input_snapshot_version: str
    target_domain: str
    domain_version: int = Field(ge=1)
    patch_request_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    payload_ref: Optional[str] = None
    occurred_at: datetime


class ParagraphGeneratedEventSchema(ContractSchema):
    schema_version: str = "paragraph_generated_event_v1"
    event_id: str
    task_id: str
    run_id: str
    section_id: str
    attempt_id: str
    input_snapshot_version: str
    paragraph: ParagraphArtifactSchema
    generation_status: str = "paragraph_completed"
    occurred_at: datetime


class SectionGenerationCompletedEventSchema(ContractSchema):
    schema_version: str = "section_generation_completed_event_v1"
    event_id: str
    task_id: str
    run_id: str
    section_id: str
    attempt_id: str
    input_snapshot_version: str
    expected_paragraph_ids: List[str] = Field(default_factory=list)
    generation_complete: bool = True
    occurred_at: datetime

    @field_validator("generation_complete")
    @classmethod
    def _must_be_complete(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("section completion event must be explicit")
        return value
