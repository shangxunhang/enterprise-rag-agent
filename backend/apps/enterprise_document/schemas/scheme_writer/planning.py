"""文档章节计划及其依赖图约束。"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from schemas.common import SchemaBase


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class _FrozenPlanContract(ContractSchema):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)


class PlannedFactRequirementSchema(_FrozenPlanContract):
    schema_version: Literal["planned_fact_requirement_v1"] = "planned_fact_requirement_v1"
    requirement_id: NonEmptyStr
    fact_key: NonEmptyStr
    fact_type: NonEmptyStr
    importance: Literal["required", "optional"]
    purpose: NonEmptyStr | None = None


# This dependency-neutral requirement is defined before the compatibility views;
# both carriers reuse the same type, without importing Artifact during re-entry.
# Compatibility payloads re-enter this module while agent_rag_v1 initializes.
# Define the legacy planning views first so that re-entry can resolve them without
# weakening the canonical ArtifactVersionRef type used by the SIF-05 contracts.
class SectionPlanSchema(SchemaBase):
    """一个章节的顺序、引用要求、依赖关系和并行资格。"""
    schema_version: str = "section_plan_v1"
    section_id: str
    section_title: str
    section_order: int
    citation_required: bool = False
    fact_requirements: tuple[PlannedFactRequirementSchema, ...] = ()
    depends_on: List[str] = Field(default_factory=list)
    execution_group: str | None = None
    can_run_in_parallel: bool = False
    requires_global_context: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("fact_requirements")
    @classmethod
    def _validate_fact_requirements(
        cls, value: tuple[PlannedFactRequirementSchema, ...],
    ) -> tuple[PlannedFactRequirementSchema, ...]:
        ids = [item.requirement_id for item in value]
        keys = [item.fact_key for item in value]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise ValueError("fact requirement IDs and keys must be unique")
        return tuple(sorted(value, key=lambda item: item.requirement_id))

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


from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactVersionRefSchema


class PlanningConsistencyRefSchema(_FrozenPlanContract):
    schema_version: Literal["planning_consistency_ref_v1"] = "planning_consistency_ref_v1"
    ref_kind: Literal["fact", "generated_section"]
    ref_id: NonEmptyStr


class PlanningRiskHintSchema(_FrozenPlanContract):
    schema_version: Literal["planning_risk_hint_v1"] = "planning_risk_hint_v1"
    risk_code: NonEmptyStr
    summary: NonEmptyStr | None = None


class SectionStrategyCode(str, Enum):
    SOURCE_ADAPT = "SOURCE_ADAPT"
    RAG_GENERATE = "RAG_GENERATE"
    TECHNICAL_GENERATE = "TECHNICAL_GENERATE"
    SKIP = "SKIP"


class EvidenceRequirementsSourceRefSchema(_FrozenPlanContract):
    schema_version: Literal["evidence_requirements_source_ref_v1"] = (
        "evidence_requirements_source_ref_v1"
    )
    requirements_schema_version: Literal["evidence_requirements_snapshot_v1"] = (
        "evidence_requirements_snapshot_v1"
    )
    requirements_contract_version: Literal["evidence_requirement_semantics_v1"] = (
        "evidence_requirement_semantics_v1"
    )
    requirements_sha256: Sha256Hex
    source_profile_ref: NonEmptyStr
    source_task_ref: NonEmptyStr


class SectionPlanEntrySchema(_FrozenPlanContract):
    schema_version: Literal["section_plan_entry_v1"] = "section_plan_entry_v1"
    section_id: NonEmptyStr
    section_title: NonEmptyStr
    section_order: int = Field(ge=0)
    applicability: Literal["applicable", "not_applicable"]
    strategy: SectionStrategyCode
    citation_required: bool = False
    fact_requirements: tuple[PlannedFactRequirementSchema, ...] = ()
    evidence_requirements_source: EvidenceRequirementsSourceRefSchema | None = None
    evidence_requirement_ids: tuple[NonEmptyStr, ...] = ()
    hard_dependencies: tuple[NonEmptyStr, ...] = ()
    consistency_refs: tuple[PlanningConsistencyRefSchema, ...] = ()
    execution_group: NonEmptyStr | None = None
    parallel_eligible: bool = False
    risk_hints: tuple[PlanningRiskHintSchema, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_unordered_fields(cls, value: Any) -> Any:
        if isinstance(value, cls) or not isinstance(value, dict):
            return value
        payload = dict(value)

        raw_fact_requirements = payload.get("fact_requirements")
        if isinstance(raw_fact_requirements, (list, tuple)):
            parsed = [
                PlannedFactRequirementSchema.model_validate(item)
                for item in raw_fact_requirements
            ]
            payload["fact_requirements"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(
                    parsed,
                    key=lambda item: (
                        item.requirement_id,
                        item.fact_key,
                        item.fact_type,
                        item.importance,
                        item.purpose or "",
                    ),
                )
            ]

        raw_dependencies = payload.get("hard_dependencies")
        if isinstance(raw_dependencies, (list, tuple)) and all(
            isinstance(item, str) for item in raw_dependencies
        ):
            payload["hard_dependencies"] = sorted(
                item.strip() for item in raw_dependencies
            )

        raw_consistency_refs = payload.get("consistency_refs")
        if isinstance(raw_consistency_refs, (list, tuple)):
            parsed = [
                PlanningConsistencyRefSchema.model_validate(item)
                for item in raw_consistency_refs
            ]
            payload["consistency_refs"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(
                    parsed,
                    key=lambda item: (item.ref_kind, item.ref_id),
                )
            ]

        raw_risk_hints = payload.get("risk_hints")
        if isinstance(raw_risk_hints, (list, tuple)):
            parsed = [
                PlanningRiskHintSchema.model_validate(item)
                for item in raw_risk_hints
            ]
            payload["risk_hints"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(parsed, key=lambda item: item.risk_code)
            ]

        return payload

    @model_validator(mode="after")
    def _validate_entry(self) -> "SectionPlanEntrySchema":
        if len(self.hard_dependencies) != len(set(self.hard_dependencies)):
            raise ValueError("hard_dependencies must be unique")
        if self.section_id in self.hard_dependencies:
            raise ValueError("section cannot depend on itself")
        if len(self.evidence_requirement_ids) != len(set(self.evidence_requirement_ids)):
            raise ValueError("evidence_requirement_ids must be unique")
        if self.evidence_requirements_source is None and self.evidence_requirement_ids:
            raise ValueError(
                "evidence_requirement_ids require evidence_requirements_source"
            )
        if self.citation_required and (
            self.evidence_requirements_source is None
            or not self.evidence_requirement_ids
        ):
            raise ValueError(
                "citation_required requires exact evidence requirements source and ids"
            )
        if self.strategy is SectionStrategyCode.RAG_GENERATE and (
            self.evidence_requirements_source is None
            or not self.evidence_requirement_ids
        ):
            raise ValueError(
                "RAG_GENERATE requires exact evidence requirements source and ids"
            )
        if self.applicability == "not_applicable":
            if self.strategy is not SectionStrategyCode.SKIP:
                raise ValueError("not_applicable section must use SKIP strategy")
            if self.evidence_requirement_ids:
                raise ValueError(
                    "not_applicable section cannot select evidence requirements"
                )
        elif self.strategy is SectionStrategyCode.SKIP:
            raise ValueError("applicable section cannot use SKIP strategy")
        if self.strategy is SectionStrategyCode.SKIP and self.citation_required:
            raise ValueError("SKIP section cannot require citations")
        if self.parallel_eligible and self.hard_dependencies:
            raise ValueError("parallel section cannot have hard dependencies")
        generated_refs = [
            item.ref_id
            for item in self.consistency_refs
            if item.ref_kind == "generated_section"
        ]
        if any(ref not in self.hard_dependencies for ref in generated_refs):
            raise ValueError(
                "generated_section consistency refs must target hard dependencies"
            )
        if self.parallel_eligible and generated_refs:
            raise ValueError(
                "parallel section cannot carry generated_section consistency refs"
            )
        fact_requirement_ids = [item.requirement_id for item in self.fact_requirements]
        fact_keys = [item.fact_key for item in self.fact_requirements]
        risk_codes = [item.risk_code for item in self.risk_hints]
        if len(fact_requirement_ids) != len(set(fact_requirement_ids)):
            raise ValueError("fact requirement_id values must be unique")
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("fact requirement fact_key values must be unique")
        if len(risk_codes) != len(set(risk_codes)):
            raise ValueError("risk_code values must be unique")
        return self


class SectionPlanContentSchema(_FrozenPlanContract):
    schema_version: Literal["section_plan_content_v1"] = "section_plan_content_v1"
    planning_contract_version: NonEmptyStr
    fact_snapshot_version_ref: ArtifactVersionRefSchema
    sections: tuple[SectionPlanEntrySchema, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_sections(cls, value: Any) -> Any:
        if isinstance(value, cls) or not isinstance(value, dict):
            return value
        payload = dict(value)
        raw_sections = payload.get("sections")
        if isinstance(raw_sections, (list, tuple)):
            parsed = [SectionPlanEntrySchema.model_validate(item) for item in raw_sections]
            payload["sections"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(parsed, key=lambda item: (item.section_order, item.section_id))
            ]
        return payload

    @model_validator(mode="after")
    def _validate_aggregate(self) -> "SectionPlanContentSchema":
        ref = self.fact_snapshot_version_ref
        if ref.logical_ref.artifact_type != "fact_snapshot":
            raise ValueError("SectionPlan requires fact_snapshot Artifact source")
        if ref.content_schema_version != "fact_snapshot_content_v1":
            raise ValueError("SectionPlan requires fact_snapshot_content_v1 source")

        section_ids = [item.section_id for item in self.sections]
        orders = [item.section_order for item in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section_id values must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("section_order values must be unique")
        known = set(section_ids)
        applicability = {item.section_id: item.applicability for item in self.sections}
        for section in self.sections:
            unknown = set(section.hard_dependencies) - known
            if unknown:
                raise ValueError(
                    f"section {section.section_id} depends on unknown sections: "
                    + ", ".join(sorted(unknown))
                )
            for dependency in section.hard_dependencies:
                if (
                    section.applicability == "applicable"
                    and applicability[dependency] == "not_applicable"
                ):
                    raise ValueError(
                        "applicable section cannot hard-depend on not_applicable section"
                    )

        completed: set[str] = set()
        remaining = {item.section_id: item for item in self.sections}
        while remaining:
            ready = [
                section_id
                for section_id, section in remaining.items()
                if set(section.hard_dependencies) <= completed
            ]
            if not ready:
                raise ValueError("section plan contains a dependency cycle")
            completed.update(ready)
            for section_id in ready:
                remaining.pop(section_id)
        return self


class SectionPlanBindingSchema(_FrozenPlanContract):
    schema_version: Literal["section_plan_binding_v1"] = "section_plan_binding_v1"
    plan_version_ref: ArtifactVersionRefSchema
    resolved_head_revision: int = Field(ge=1)
    section_id: NonEmptyStr

    @model_validator(mode="after")
    def _validate_plan_ref(self) -> "SectionPlanBindingSchema":
        if self.plan_version_ref.logical_ref.artifact_type != "section_plan":
            raise ValueError("SectionPlan binding requires artifact_type=section_plan")
        if self.plan_version_ref.content_schema_version != "section_plan_content_v1":
            raise ValueError(
                "SectionPlan binding requires section_plan_content_v1 Artifact content"
            )
        return self
