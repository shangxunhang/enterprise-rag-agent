"""Neutral deterministic hashing helpers shared across architecture layers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_sha256(value: Any) -> str:
    """Return the legacy canonical JSON SHA-256 used by Evidence and tracing.

    The serialization contract is intentionally frozen: UTF-8 JSON with
    ``ensure_ascii=False``, sorted keys and ``default=str``.  Do not replace
    this with persistence-layer canonical hashing, which has different wire
    semantics.
    """

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["canonical_sha256"]
