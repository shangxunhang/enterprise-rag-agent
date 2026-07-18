"""Shared runtime delegation for decomposed SchemeWriter services."""


from typing import Any


class RuntimeBoundService:
    """Delegate unknown attributes and calls to the owning agent runtime.

    The decomposition keeps the legacy private-method surface temporarily so
    existing tests and subclasses continue to work.  Business logic lives in
    service classes; calls that cross service boundaries are routed through the
    agent facade and therefore still honor subclass overrides.
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)
