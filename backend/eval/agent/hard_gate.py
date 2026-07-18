"""Compatibility exports for the runtime document gate.

The canonical implementation lives in the enterprise-document application
module.  Offline evaluation consumes it; runtime code no longer depends on the
``eval`` package.
"""
from apps.enterprise_document.services.document_gate import (
    evaluate_scheme_draft,
    extract_runtime_hard_failures,
)

__all__ = ["evaluate_scheme_draft", "extract_runtime_hard_failures"]
