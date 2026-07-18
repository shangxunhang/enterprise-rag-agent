"""Compatibility import for the active enterprise-document workflow."""

from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)

__all__ = ["build_scheme_generation_workflow"]
