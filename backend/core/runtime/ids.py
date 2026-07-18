"""Identifier generation abstraction."""

from __future__ import annotations

import uuid
from typing import Protocol


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str:
        """Create a new prefixed identifier."""


class UuidIdGenerator:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TimestampedUuidIdGenerator:
    """Generate the historical ``prefix_YYYYmmddTHHMMSSZ_<8hex>`` ids."""

    def __init__(self, clock=None) -> None:
        from core.runtime.clock import SystemClock
        self.clock = clock or SystemClock()

    def new_id(self, prefix: str) -> str:
        from datetime import datetime
        timestamp = datetime.fromisoformat(self.clock.now_iso()).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8]}"
