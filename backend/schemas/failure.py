"""Canonical failure categories shared across runtime boundaries.

The contract classifies *what kind of failure happened*.  It intentionally does
not encode business retry/delivery policy; callers or future policy modules may
decide those actions from the category plus runtime context.
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    AVAILABILITY = "availability"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    CONFIGURATION = "configuration"
    VALIDATION = "validation"
    BUSINESS_QUALITY = "business_quality"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    CANCELLED = "cancelled"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNKNOWN = "unknown"
