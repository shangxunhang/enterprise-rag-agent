"""Agent-RAG V1 协议模型的严格公共基类。"""

from __future__ import annotations

from pydantic import ConfigDict

from schemas.common import SchemaBase


class ContractSchema(SchemaBase):
    """拒绝未声明字段，防止持久化边界静默丢失协议数据。"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
    )
