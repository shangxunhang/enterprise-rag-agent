"""文档章节计划及其依赖图约束。"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import Field, field_validator, model_validator

from schemas.common import SchemaBase


class SectionPlanSchema(SchemaBase):
    """一个章节的顺序、引用要求、依赖关系和并行资格。"""
    schema_version: str = "section_plan_v1"
    section_id: str
    section_title: str
    section_order: int
    citation_required: bool = False
    depends_on: List[str] = Field(default_factory=list)
    execution_group: str | None = None
    can_run_in_parallel: bool = False
    requires_global_context: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: List[str]) -> List[str]:
        """清理依赖标识并拒绝空值或重复边。"""
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("section dependencies cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("section dependencies must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_parallel_contract(self) -> "SectionPlanSchema":
        """并行章节必须无依赖且明确不读取全局上下文。"""
        if self.section_id in self.depends_on:
            raise ValueError("section cannot depend on itself")
        if self.can_run_in_parallel and self.requires_global_context:
            raise ValueError(
                "parallel section must explicitly waive global context"
            )
        if self.can_run_in_parallel and self.depends_on:
            raise ValueError(
                "only dependency-free sections may run in parallel"
            )
        return self


class DocumentPlanSchema(SchemaBase):
    """完整文档的有向无环章节计划。"""
    schema_version: str = "document_plan_v1"
    plan_id: str
    document_id: str
    document_title: str
    sections: List[SectionPlanSchema] = Field(default_factory=list)
    planning_source: str = "project_input"
    created_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_section_dependencies(self) -> "DocumentPlanSchema":
        """校验章节 ID 唯一、依赖存在，并用拓扑消除法拒绝依赖环。"""
        section_ids = [item.section_id for item in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("document plan section_id values must be unique")
        known = set(section_ids)
        for section in self.sections:
            unknown = set(section.depends_on) - known
            if unknown:
                raise ValueError(
                    f"section {section.section_id} depends on unknown sections: "
                    + ", ".join(sorted(unknown))
                )
        completed: set[str] = set()
        remaining = {item.section_id: item for item in self.sections}
        while remaining:
            ready = [
                section_id
                for section_id, section in remaining.items()
                if set(section.depends_on) <= completed
            ]
            if not ready:
                raise ValueError("document plan contains a dependency cycle")
            completed.update(ready)
            for section_id in ready:
                remaining.pop(section_id)
        return self
