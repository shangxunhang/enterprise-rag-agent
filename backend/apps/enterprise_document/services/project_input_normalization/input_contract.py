"""Versioned ProjectInput decoding and new-task normalization boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from apps.enterprise_document.schemas.project_input_schema import (
    GenerationRequirementsV3Schema,
    ProjectInputSchema,
)


PROJECT_INPUT_V1 = "project_input_v1"
PROJECT_INPUT_V2 = "project_input_v2"
PROJECT_INPUT_V3 = "project_input_v3"
CURRENT_PROJECT_INPUT_VERSION = PROJECT_INPUT_V3

FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION = {
    PROJECT_INPUT_V1: "project-input-normalization-v1",
    PROJECT_INPUT_V2: "project-input-normalization-v2",
    PROJECT_INPUT_V3: "project-input-normalization-v2",
}
# Fact normalization-v2 represents the same Fact projection for V2 and V3;
# it cannot identify which ProjectInput version produced the snapshot. Keep V2
# as the historical representative for consumers that only verify Fact shape.
# Task-version authorization must instead use the saved ProjectInput version.
PROJECT_INPUT_VERSION_BY_FACT_NORMALIZATION = {
    "project-input-normalization-v1": PROJECT_INPUT_V1,
    "project-input-normalization-v2": PROJECT_INPUT_V2,
}
EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION = {
    PROJECT_INPUT_V1: "project-input-v1-execution-v1",
    PROJECT_INPUT_V2: "project-input-v2-execution-v3",
    PROJECT_INPUT_V3: "project-input-v3-execution-v1",
}
CURRENT_TASK_EXECUTION_PROFILE = EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION[
    CURRENT_PROJECT_INPUT_VERSION
]


def _payload(value: ProjectInputSchema | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ProjectInputSchema):
        return value.model_dump(mode="python")
    if not isinstance(value, Mapping):
        raise TypeError("ProjectInput must be a schema or mapping")
    return deepcopy(dict(value))


def read_saved_project_input(
    value: ProjectInputSchema | Mapping[str, Any],
) -> ProjectInputSchema:
    """Read an immutable saved input using its recorded schema semantics."""
    return ProjectInputSchema.model_validate(_payload(value), strict=True)


def normalize_new_project_input(
    value: ProjectInputSchema | Mapping[str, Any],
    *,
    strict: bool = True,
) -> ProjectInputSchema:
    """Convert caller-supplied old/current shape into the current new-task contract.

    Conversion is explicit and occurs before Task identity is created. Existing
    saved Tasks must use :func:`read_saved_project_input` instead.
    """
    payload = _payload(value)
    source_version = payload.get("schema_version", CURRENT_PROJECT_INPUT_VERSION)
    if source_version == PROJECT_INPUT_V1:
        raise ValueError(
            "explicit project_input_v1 requires caller-approved conversion before new Task ingress"
        )
    if source_version == PROJECT_INPUT_V2:
        raise ValueError(
            "explicit project_input_v2 requires caller-approved conversion before new Task ingress"
        )
    if source_version != CURRENT_PROJECT_INPUT_VERSION:
        raise ValueError("unsupported ProjectInput source schema version")
    payload["schema_version"] = CURRENT_PROJECT_INPUT_VERSION
    return ProjectInputSchema.model_validate(payload, strict=strict)


def fact_normalization_version(project_input_version: str) -> str:
    try:
        return FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION[project_input_version]
    except KeyError as exc:
        raise ValueError("unsupported ProjectInput schema version") from exc


def project_input_version_for_fact_normalization(
    normalization_version: str,
) -> str:
    try:
        return PROJECT_INPUT_VERSION_BY_FACT_NORMALIZATION[normalization_version]
    except KeyError as exc:
        raise ValueError("unsupported Fact normalization contract version") from exc


def requested_target_section_chars(project_input: ProjectInputSchema) -> int | None:
    """Return only the V3 authored soft target for initial writing messages."""
    if project_input.schema_version != PROJECT_INPUT_V3:
        return None
    if not isinstance(project_input.generation_requirements, GenerationRequirementsV3Schema):
        raise ValueError("project_input_v3 requires V3 generation requirements")
    return project_input.generation_requirements.target_section_chars


__all__ = [
    "CURRENT_PROJECT_INPUT_VERSION",
    "CURRENT_TASK_EXECUTION_PROFILE",
    "EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION",
    "FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION",
    "PROJECT_INPUT_VERSION_BY_FACT_NORMALIZATION",
    "PROJECT_INPUT_V1",
    "PROJECT_INPUT_V2",
    "PROJECT_INPUT_V3",
    "fact_normalization_version",
    "normalize_new_project_input",
    "project_input_version_for_fact_normalization",
    "read_saved_project_input",
    "requested_target_section_chars",
]
