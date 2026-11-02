"""Dependency-neutral public source-profile enum authority."""

from __future__ import annotations

from enum import Enum


class DocumentType(str, Enum):
    CURRENT_PROJECT_REQUIREMENT = "current_project_requirement"
    CURRENT_PROJECT_DECISION = "current_project_decision"
    CURRENT_PROJECT_INVENTORY = "current_project_inventory"
    HISTORICAL_FEASIBILITY_STUDY = "historical_feasibility_study"
    HISTORICAL_CONSTRUCTION_SCHEME = "historical_construction_scheme"
    POLICY_STANDARD = "policy_standard"
    TECHNICAL_WHITEPAPER = "technical_whitepaper"
    TENDER_REQUIREMENT = "tender_requirement"
    DOCUMENT_TEMPLATE = "document_template"
    UNKNOWN = "unknown"


class EvidenceRole(str, Enum):
    PROJECT_FACT = "project_fact"
    HISTORICAL_CASE = "historical_case"
    POLICY_STANDARD = "policy_standard"
    TECHNICAL_REFERENCE = "technical_reference"
    PROCUREMENT_REQUIREMENT = "procurement_requirement"
    TEMPLATE_STYLE = "template_style"
    UNCLASSIFIED = "unclassified"


class ProjectRelation(str, Enum):
    CURRENT_PROJECT = "current_project"
    OTHER_PROJECT = "other_project"
    GENERAL = "general"
    UNKNOWN = "unknown"


class AuthorityLevel(str, Enum):
    AUTHORITATIVE = "authoritative"
    OFFICIAL = "official"
    CONFIRMED = "confirmed"
    REFERENCE = "reference"
    DRAFT = "draft"
    UNKNOWN = "unknown"


__all__ = [
    "AuthorityLevel",
    "DocumentType",
    "EvidenceRole",
    "ProjectRelation",
]
