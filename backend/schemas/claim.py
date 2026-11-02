"""Shared public claim taxonomy used across RAG and application boundaries."""

from __future__ import annotations

from enum import Enum


class ClaimType(str, Enum):
    """Canonical claim taxonomy for retrieval, gate, and writer contracts."""

    GENERAL_METHOD = "general_method"
    TYPICAL_ARCHITECTURE = "typical_architecture"
    REFERENCE_CASE = "reference_case"
    CURRENT_PROJECT_FACT = "current_project_fact"
    CURRENT_PROJECT_COST = "current_project_cost"
    CURRENT_PROJECT_QUANTITY = "current_project_quantity"
    CURRENT_PROJECT_SCHEDULE = "current_project_schedule"
    CURRENT_PROJECT_CONFIGURATION = "current_project_configuration"
    POLICY_REQUIREMENT = "policy_requirement"
    TECHNICAL_SPECIFICATION = "technical_specification"
    PROCUREMENT_REQUIREMENT = "procurement_requirement"
    TEMPLATE_STRUCTURE = "template_structure"
    STYLE_GUIDANCE = "style_guidance"


CURRENT_PROJECT_CLAIM_TYPES = frozenset(
    {
        ClaimType.CURRENT_PROJECT_FACT,
        ClaimType.CURRENT_PROJECT_COST,
        ClaimType.CURRENT_PROJECT_QUANTITY,
        ClaimType.CURRENT_PROJECT_SCHEDULE,
        ClaimType.CURRENT_PROJECT_CONFIGURATION,
    }
)

TEMPLATE_ONLY_CLAIM_TYPES = frozenset(
    {ClaimType.TEMPLATE_STRUCTURE, ClaimType.STYLE_GUIDANCE}
)


__all__ = [
    "ClaimType",
    "CURRENT_PROJECT_CLAIM_TYPES",
    "TEMPLATE_ONLY_CLAIM_TYPES",
]
