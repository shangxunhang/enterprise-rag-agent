"""Strict base model shared by enterprise-document schema packages.

This module deliberately lives outside ``agent_rag_v1`` so publication,
writer, and agent contracts can depend on the same base class without
initializing each other's package-level export graphs.
"""

from __future__ import annotations

from pydantic import ConfigDict

from schemas.common import SchemaBase


class ContractSchema(SchemaBase):
    """Reject undeclared fields at persistence and API contract boundaries."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
    )


__all__ = ["ContractSchema"]
