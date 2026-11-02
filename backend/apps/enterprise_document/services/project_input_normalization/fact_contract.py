"""Stable keys for the existing ProjectInput normalization projections.

This is a physical key/type/ID mapping, not a section-selection policy. A Planner
requirement may name a field even when no corresponding fact was extracted.
No values, relevance rules, facts, or new canonical store are defined here.
"""
from __future__ import annotations

from dataclasses import dataclass
from .input_contract import (
    FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION,
    fact_normalization_version,
)


PROJECT_INPUT_FACT_NORMALIZATION_VERSION = FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION[
    "project_input_v1"
]
PROJECT_INPUT_FACT_NORMALIZATION_V2 = FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION[
    "project_input_v2"
]
PROJECT_INPUT_FACT_NORMALIZATION_VERSIONS = FACT_NORMALIZATION_BY_PROJECT_INPUT_VERSION
project_input_fact_normalization_version = fact_normalization_version


@dataclass(frozen=True)
class ProjectInputFactField:
    fact_key: str
    fact_type: str
    record_suffix: str
    unavailable_value: str | None = None

    def record_id(self, run_id: str) -> str:
        exact_run = str(run_id or "").strip()
        if not exact_run or exact_run != run_id:
            raise ValueError("ProjectInput fact mapping requires an exact nonempty Run")
        return f"fact_{exact_run}_{self.record_suffix}"


PROJECT_INPUT_FACT_FIELDS = (
    ProjectInputFactField("project_input.business_goal", "business_goal", "business_goal"),
    ProjectInputFactField("project_input.organization_scale", "project_scale", "organization_scale"),
    ProjectInputFactField(
        "project_input.hardware_resources", "hardware_resource", "hardware",
        # The existing summary deliberately emits this absence statement. A record
        # of missing input is not a present hardware value satisfying a requirement.
        unavailable_value="当前未提供硬件资源信息。",
    ),
    ProjectInputFactField("project_input.manual_boundaries", "manual_boundary", "manual_boundary"),
    ProjectInputFactField("project_input.missing_information", "missing_information", "missing_information"),
)


def project_input_fact_field(fact_key: str) -> ProjectInputFactField:
    for field in PROJECT_INPUT_FACT_FIELDS:
        if field.fact_key == fact_key:
            return field
    raise ValueError(f"unregistered ProjectInput fact requirement key: {fact_key}")
