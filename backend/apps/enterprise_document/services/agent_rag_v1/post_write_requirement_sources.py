"""Exact *candidate* inputs for the sole Post-write Requirement materializer.

This boundary does not normalize an obligation, decide its applicability, or
publish Requirement/quality truth. Runtime knobs and fact payloads are excluded;
style/format selectors retain their advisory status even after a full ProjectInput
serialization makes explicit-field provenance unavailable.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    PROJECT_INPUT_V1,
    PROJECT_INPUT_V2,
    PROJECT_INPUT_V3,
)
from apps.enterprise_document.schemas.post_write.requirement import PostWriteRequirementEntrySchema
from core.hashing import canonical_sha256


# An exhaustive schema inventory is a drift guard, not a semantic classifier.
_PROJECT_FIELDS = frozenset({
    "schema_version", "task_id", "tenant_id", "project_name", "current_project_id",
    "task_type", "user_query", "source_materials", "generation_requirements",
    "output_schema", "metadata", "project_type", "customer_type", "business_goal",
    "target_documents", "total_staff", "functional_department_count",
    "business_department_count", "department_groups", "hardware_resources",
    "target_templates", "policy_requirements", "manual_boundaries",
    "missing_information", "conflicting_information", "extra",
})
_LEGACY_GENERATION_FIELDS = frozenset({
    "required_sections", "need_citation", "citation_required_sections",
    "need_human_review", "min_section_chars", "max_section_retries",
    "max_context_chars", "max_tokens_per_section", "citation_style", "language",
    "tone", "extra",
})
_GENERATION_FIELDS_BY_PROJECT_INPUT_VERSION = {
    PROJECT_INPUT_V1: _LEGACY_GENERATION_FIELDS,
    PROJECT_INPUT_V2: _LEGACY_GENERATION_FIELDS,
    PROJECT_INPUT_V3: _LEGACY_GENERATION_FIELDS | {"target_section_chars"},
}
_OUTPUT_FIELDS = frozenset({
    "document_title", "output_format", "required_sections", "required_fields",
    "heading_style", "extra",
})
_MANUAL_FIELDS = frozenset({"item", "handled_by", "description"})

_CANDIDATE_PATHS = {
    "/user_query": "TASK_TEXT",
    "/business_goal": "TASK_TEXT",
    "/generation_requirements/required_sections": "STRUCTURE_DECLARATION",
    "/generation_requirements/need_citation": "CITATION_DECLARATION",
    "/generation_requirements/citation_required_sections": "CITATION_DECLARATION",
    "/output_schema/required_sections": "STRUCTURE_DECLARATION",
    "/output_schema/required_fields": "STRUCTURE_DECLARATION",
}
_ADVISORY_PATHS = frozenset({
    "/task_type", "/project_name", "/current_project_id", "/project_type",
    "/customer_type", "/target_documents", "/target_templates",
    "/generation_requirements/language", "/generation_requirements/tone",
    "/generation_requirements/citation_style", "/output_schema/document_title",
    "/output_schema/output_format", "/output_schema/heading_style",
})
_TARGET_ADVISORY_PATH = "/generation_requirements/target_section_chars"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _path_role(path: str) -> tuple[str, str]:
    if path in _CANDIDATE_PATHS:
        return "CANDIDATE", _CANDIDATE_PATHS[path]
    if re.fullmatch(r"/policy_requirements/(0|[1-9][0-9]*)", path):
        return "CANDIDATE", "POLICY_TEXT"
    if re.fullmatch(r"/manual_boundaries/(0|[1-9][0-9]*)", path):
        return "CANDIDATE", "MANUAL_BOUNDARY_CANDIDATE"
    if path in _ADVISORY_PATHS:
        return "ADVISORY", "OUTPUT_OR_SCOPE_HINT"
    if path == _TARGET_ADVISORY_PATH:
        return "ADVISORY", "WRITING_SOFT_TARGET"
    raise ValueError("Requirement candidate path has no explicit field classification: " + path)


class RequirementSourceCandidate(BaseModel):
    """Immutable source bytes plus their origin, not a normalized requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["requirement_source_candidate_v1"] = "requirement_source_candidate_v1"
    task_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    role: Literal["CANDIDATE", "ADVISORY"]
    source_kind: str
    value_json: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _exact_source(self):
        role, kind = _path_role(self.field_path)
        if (self.role, self.source_kind) != (role, kind):
            raise ValueError("Requirement source role differs from its exact origin")
        value = json.loads(self.value_json)
        if _json(value) != self.value_json:
            raise ValueError("Requirement source value is not canonical JSON")
        payload = self.model_dump(mode="json", exclude={"source_id", "source_sha256"})
        digest = canonical_sha256(payload)
        if self.source_sha256 != digest or self.source_id != "requirement-source://sha256/" + digest:
            raise ValueError("Requirement source hash/identity mismatch")
        return self

    @property
    def value(self) -> Any:
        # Every access returns a fresh object. A decoded list/dict is not a mutable
        # backing store for this frozen value or for the original ProjectInput.
        return json.loads(self.value_json)

    @classmethod
    def from_value(cls, *, task_id: str, field_path: str, value: Any):
        role, kind = _path_role(field_path)
        payload = {
            "schema_version": "requirement_source_candidate_v1", "task_id": task_id,
            "field_path": field_path, "role": role, "source_kind": kind,
            "value_json": _json(value),
        }
        digest = canonical_sha256(payload)
        return cls(**payload, source_id="requirement-source://sha256/" + digest, source_sha256=digest)


class RequirementSourceCatalog(BaseModel):
    """Source completeness carrier; cannot be substituted for RequirementRef."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["requirement_source_catalog_v1", "requirement_source_catalog_v2"] = "requirement_source_catalog_v1"
    authority: Literal["CANDIDATE_INPUT_ONLY"] = "CANDIDATE_INPUT_ONLY"
    task_id: str = Field(min_length=1)
    candidates: tuple[RequirementSourceCandidate, ...]
    advisories: tuple[RequirementSourceCandidate, ...]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _catalogue_shape(self):
        for role, entries in (("CANDIDATE", self.candidates), ("ADVISORY", self.advisories)):
            paths = tuple(source.field_path for source in entries)
            if paths != tuple(sorted(set(paths))):
                raise ValueError("Requirement source paths must be ordered and unique")
            if any(source.task_id != self.task_id or source.role != role for source in entries):
                raise ValueError("Requirement catalogue crossed task or source role")
        candidate_paths = {source.field_path for source in self.candidates}
        if not set(_CANDIDATE_PATHS) <= candidate_paths:
            raise ValueError("Requirement source catalogue lacks a required typed input field")
        advisory_paths = {source.field_path for source in self.advisories}
        allowed_advisory_paths = (
            {_ADVISORY_PATHS, _ADVISORY_PATHS | {_TARGET_ADVISORY_PATH}}
            if self.schema_version == "requirement_source_catalog_v2"
            else {_ADVISORY_PATHS}
        )
        if frozenset(advisory_paths) not in allowed_advisory_paths:
            raise ValueError("Requirement advisory field inventory is incomplete")
        for root in ("policy_requirements", "manual_boundaries"):
            indices = sorted(int(path.rsplit("/", 1)[1]) for path in candidate_paths if path.startswith("/" + root + "/"))
            if indices != list(range(len(indices))):
                raise ValueError("Requirement source list has an unaccounted index")
        payload = self.model_dump(mode="json", exclude={"catalog_sha256"})
        if canonical_sha256(payload) != self.catalog_sha256:
            raise ValueError("Requirement source catalogue hash mismatch")
        return self

    def verify_before_materialization(self, exact_upstream_input: ProjectInputSchema | dict):
        """Verify at the input owner, before canonical normalization/commit.

        A self-hash cannot prove that the final member of a source list was not
        omitted. The caller must supply the exact authoritative task input, not
        whichever ProjectInput happens to be latest. Validators must subsequently
        consume the canonical RequirementRef, never call this to create truth.
        """
        checked = type(self).model_validate_json(self.model_dump_json())
        expected = collect_requirement_sources(exact_upstream_input)
        if checked != expected:
            raise ValueError("Requirement catalogue differs from exact upstream source inputs")
        return checked

    @property
    def candidate_input_sha256(self) -> str:
        """Hash candidate material only; hints remain separate non-authority data."""
        return canonical_sha256({
            "schema_version": self.schema_version, "authority": self.authority,
            "task_id": self.task_id,
            "candidates": [source.model_dump(mode="json") for source in self.candidates],
        })


def _check_field_inventory(value: BaseModel, expected: frozenset[str], location: str) -> None:
    actual = frozenset(type(value).model_fields)
    if actual != expected:
        raise ValueError(
            f"Requirement field classification changed at {location}: "
            f"added={sorted(actual - expected)}, missing={sorted(expected - actual)}"
        )


def _generation_fields(project_input_version: str) -> frozenset[str]:
    try:
        return _GENERATION_FIELDS_BY_PROJECT_INPUT_VERSION[project_input_version]
    except KeyError as exc:
        raise ValueError("unsupported ProjectInput source schema version") from exc


def _at_path(data: dict, path: str):
    value = data
    for name in path.lstrip("/").split("/"):
        value = value[name]
    return value


def collect_requirement_sources(project_input: ProjectInputSchema | dict) -> RequirementSourceCatalog:
    """Collect exact candidates; classification/normalization remains upstream work.

    No interpretation of metadata or runtime-control values occurs here. In
    particular, a keyword such as ``max_tokens`` in user-authored text is retained;
    the identically named Writer execution knob is not promoted to an obligation.
    """
    if isinstance(project_input, ProjectInputSchema):
        _check_field_inventory(project_input, _PROJECT_FIELDS, "ProjectInput")
        _check_field_inventory(
            project_input.generation_requirements,
            _generation_fields(project_input.schema_version),
            "generation_requirements",
        )
        _check_field_inventory(project_input.output_schema, _OUTPUT_FIELDS, "output_schema")
        for boundary in project_input.manual_boundaries:
            _check_field_inventory(boundary, _MANUAL_FIELDS, "manual_boundaries")
        # Revalidate even a model_copy() or a mutated input. Do not use model_fields_set
        # as authoring evidence: full serialization legitimately expands that set.
        raw = project_input.model_dump(mode="json")
    else:
        raw = deepcopy_json_input(project_input)
    project = ProjectInputSchema.model_validate(raw)
    _check_field_inventory(project, _PROJECT_FIELDS, "ProjectInput")
    _check_field_inventory(
        project.generation_requirements,
        _generation_fields(project.schema_version),
        "generation_requirements",
    )
    _check_field_inventory(project.output_schema, _OUTPUT_FIELDS, "output_schema")
    for boundary in project.manual_boundaries:
        _check_field_inventory(boundary, _MANUAL_FIELDS, "manual_boundaries")
    if not project.user_query.strip():
        raise ValueError("Requirement sources require a nonempty authored query")
    if any(not value.strip() for value in project.policy_requirements):
        raise ValueError("blank policy source must not be silently discarded")
    if any(not value.item.strip() for value in project.manual_boundaries):
        raise ValueError("blank manual boundary must not be silently discarded")
    data = project.model_dump(mode="json")
    candidates = [RequirementSourceCandidate.from_value(
        task_id=project.task_id, field_path=path, value=_at_path(data, path),
    ) for path in _CANDIDATE_PATHS]
    for key in ("policy_requirements", "manual_boundaries"):
        candidates.extend(RequirementSourceCandidate.from_value(
            task_id=project.task_id, field_path=f"/{key}/{index}", value=value,
        ) for index, value in enumerate(data[key]))
    advisories = [RequirementSourceCandidate.from_value(
        task_id=project.task_id, field_path=path, value=_at_path(data, path),
    ) for path in _ADVISORY_PATHS]
    if project.schema_version == PROJECT_INPUT_V3 and data["generation_requirements"].get("target_section_chars") is not None:
        advisories.append(RequirementSourceCandidate.from_value(
            task_id=project.task_id,
            field_path=_TARGET_ADVISORY_PATH,
            value=data["generation_requirements"]["target_section_chars"],
        ))
    payload = {
        "schema_version": (
            "requirement_source_catalog_v2" if project.schema_version == PROJECT_INPUT_V3
            else "requirement_source_catalog_v1"
        ), "authority": "CANDIDATE_INPUT_ONLY",
        "task_id": project.task_id,
        "candidates": [source.model_dump(mode="json") for source in sorted(candidates, key=lambda source: source.field_path)],
        "advisories": [source.model_dump(mode="json") for source in sorted(advisories, key=lambda source: source.field_path)],
    }
    return RequirementSourceCatalog(**payload, catalog_sha256=canonical_sha256(payload))


def _requirement_id(prefix: str, source: RequirementSourceCandidate) -> str:
    return f"{prefix}://sha256/" + canonical_sha256({
        "source_id": source.source_id,
        "source_sha256": source.source_sha256,
        "field_path": source.field_path,
    })


def _derived_requirement_id(
    prefix: str,
    *,
    task_id: str,
    field_path: str,
    source_index: int | None,
    source_value: Any,
) -> str:
    """Stable identity for one typed member of an explicit source declaration."""
    return f"{prefix}://sha256/" + canonical_sha256({
        "task_id": task_id,
        "field_path": field_path,
        "source_index": source_index,
        "source_value": source_value,
    })


def normalize_requirement_entries(
    project_input: ProjectInputSchema | dict,
    *,
    catalog: RequirementSourceCatalog | None = None,
) -> tuple[PostWriteRequirementEntrySchema, ...]:
    """Mechanically normalize explicit authored sources into immutable entries.

    This function deliberately does *not* split free text or infer semantic rule
    families from keywords.  A composite authored policy remains one obligation;
    downstream semantic evaluation must judge the complete statement.  Structural
    declarations stay in the stable ``task-request`` constraint payload so the
    existing Coverage owner keeps its exact source contract.
    """
    raw = (
        project_input.model_dump(mode="json")
        if isinstance(project_input, ProjectInputSchema)
        else deepcopy_json_input(project_input)
    )
    project = ProjectInputSchema.model_validate(raw)
    exact_catalog = catalog or collect_requirement_sources(project)
    exact_catalog = exact_catalog.verify_before_materialization(project)
    sources = {item.field_path: item for item in exact_catalog.candidates}

    task_source = sources["/user_query"]
    goal_source = sources["/business_goal"]
    required_sections = tuple(
        project.generation_requirements.required_sections
        or project.output_schema.required_sections
    )
    task = PostWriteRequirementEntrySchema(
        requirement_id="task-request",
        requirement_kind="TASK_INTENT",
        statement=str(task_source.value),
        constraints={
            "source_id": task_source.source_id,
            "source_path": task_source.field_path,
            "business_goal": str(goal_source.value),
            "business_goal_source_id": goal_source.source_id,
            "business_goal_source_path": goal_source.field_path,
            "required_sections": list(required_sections),
            "need_citation": bool(project.generation_requirements.need_citation),
            "citation_required_sections": list(
                project.generation_requirements.citation_required_sections
            ),
            "required_fields": list(project.output_schema.required_fields),
        },
    )
    entries = [task]

    # RequirementMaterializer owns explicit presence/completeness decomposition.
    # These are mechanically typed declarations from ProjectInput; no free-text
    # keyword or business taxonomy inference is performed here.
    section_source = sources["/generation_requirements/required_sections"]
    for index, title in enumerate(required_sections):
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_derived_requirement_id(
                "coverage-required-section",
                task_id=project.task_id,
                field_path=section_source.field_path,
                source_index=index,
                source_value=title,
            ),
            requirement_kind="STRUCTURAL_PART_PRESENCE",
            statement=f"Required Section must be present: {title}",
            constraints={
                "coverage_semantics": "STRUCTURAL_PART",
                "section_title": title,
                "source_id": section_source.source_id,
                "source_path": section_source.field_path,
                "source_index": index,
            },
        ))

    field_source = sources["/output_schema/required_fields"]
    required_fields = tuple(str(value).strip() for value in project.output_schema.required_fields)
    if any(not value for value in required_fields):
        raise ValueError("blank required_fields entry cannot become a Coverage obligation")
    if len(required_fields) != len(set(required_fields)):
        raise ValueError("required_fields must be ordered-unique for canonical Coverage")
    for index, field_name in enumerate(required_fields):
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_derived_requirement_id(
                "coverage-required-field",
                task_id=project.task_id,
                field_path=field_source.field_path,
                source_index=index,
                source_value=field_name,
            ),
            requirement_kind="FIELD_OR_SLOT_PRESENCE",
            statement=f"Required output field or semantic slot must be present: {field_name}",
            constraints={
                "coverage_semantics": "FIELD_OR_SLOT",
                "field_name": field_name,
                "source_id": field_source.source_id,
                "source_path": field_source.field_path,
                "source_index": index,
            },
        ))

    citation_sections = tuple(project.generation_requirements.citation_required_sections)
    citation_source = sources["/generation_requirements/citation_required_sections"]
    if len(citation_sections) != len(set(citation_sections)):
        raise ValueError("citation_required_sections must be ordered-unique for canonical Coverage")
    for index, title in enumerate(citation_sections):
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_derived_requirement_id(
                "coverage-citation-section",
                task_id=project.task_id,
                field_path=citation_source.field_path,
                source_index=index,
                source_value=title,
            ),
            requirement_kind="CITATION_PRESENCE",
            statement=f"At least one citation occurrence is required in Section: {title}",
            constraints={
                "coverage_semantics": "CITATION_PRESENCE_TARGET",
                "section_title": title,
                "source_id": citation_source.source_id,
                "source_path": citation_source.field_path,
                "source_index": index,
            },
        ))

    if project.generation_requirements.need_citation and not citation_sections:
        global_citation_source = sources["/generation_requirements/need_citation"]
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_derived_requirement_id(
                "coverage-citation-document",
                task_id=project.task_id,
                field_path=global_citation_source.field_path,
                source_index=None,
                source_value=True,
            ),
            requirement_kind="CITATION_PRESENCE",
            statement="At least one citation occurrence is required in the Document",
            constraints={
                "coverage_semantics": "CITATION_PRESENCE_TARGET",
                "document_global": True,
                "source_id": global_citation_source.source_id,
                "source_path": global_citation_source.field_path,
            },
        ))

    for index, value in enumerate(project.policy_requirements):
        source = sources[f"/policy_requirements/{index}"]
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_requirement_id("policy-requirement", source),
            requirement_kind="POLICY_REQUIREMENT",
            statement=value,
            constraints={
                "source_id": source.source_id,
                "source_path": source.field_path,
            },
        ))

    for index, boundary in enumerate(project.manual_boundaries):
        source = sources[f"/manual_boundaries/{index}"]
        constraints = {
            "source_id": source.source_id,
            "source_path": source.field_path,
            "handled_by": boundary.handled_by,
        }
        if boundary.description is not None:
            constraints["description"] = boundary.description
        entries.append(PostWriteRequirementEntrySchema(
            requirement_id=_requirement_id("manual-boundary", source),
            requirement_kind="MANUAL_BOUNDARY",
            statement=boundary.item,
            constraints=constraints,
        ))

    ordered = tuple(sorted(entries, key=lambda item: item.requirement_id))
    ids = [item.requirement_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("normalized Post-write Requirement IDs collide")
    return ordered


def deepcopy_json_input(value: dict) -> dict:
    if not isinstance(value, dict):
        raise TypeError("Requirement sources require ProjectInput or a JSON object")
    # Pydantic does not mutate the original dictionary, but a serialization copy
    # also severs nested mutable source references and rejects non-JSON values.
    return json.loads(_json(value))


__all__ = [
    "RequirementSourceCandidate",
    "RequirementSourceCatalog",
    "collect_requirement_sources",
    "normalize_requirement_entries",
]
