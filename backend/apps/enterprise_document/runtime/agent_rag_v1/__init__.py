"""Production Agent-RAG runtime surface after Round-8 retirement.

Only recovery primitives used by the existing Mainline graphs are public.
Former Standalone Orchestrator/RuntimeFactory/ApplicationService modules remain
as archived fixture references and are intentionally not re-exported here.
"""

from .mainline_recovery import (
    MainlineAttemptGuard,
    MainlineReplanAttempt,
    MainlineRunningInvocationRecovery,
    StaleMainlineResultError,
)

LEGACY_STANDALONE_FIXTURE_ONLY = True

__all__ = [
    "LEGACY_STANDALONE_FIXTURE_ONLY",
    "MainlineAttemptGuard",
    "MainlineReplanAttempt",
    "MainlineRunningInvocationRecovery",
    "StaleMainlineResultError",
]
