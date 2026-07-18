"""Clock abstraction used to make runtime code deterministic in tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_iso(self) -> str:
        """Return the current UTC time in ISO-8601 format."""


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
