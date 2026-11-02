"""Dependency-neutral public evidence primitives."""

from __future__ import annotations

from enum import Enum


class EvidenceStatus(str, Enum):
    """Canonical public status of completed evidence assessment."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"


__all__ = ["EvidenceStatus"]
