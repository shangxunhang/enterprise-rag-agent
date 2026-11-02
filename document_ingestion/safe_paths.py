"""Windows-aware filename validation and filesystem containment."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .exceptions import UnsafePathError

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def validate_safe_component(value: str, *, label: str = "path component") -> str:
    """Reject traversal, separators, ADS, drive injection and reserved names."""

    if not isinstance(value, str) or not value:
        raise UnsafePathError(f"{label} must be a non-empty string")
    if value in {".", ".."} or ".." in Path(value).parts:
        raise UnsafePathError(f"{label} contains traversal")
    if "/" in value or "\\" in value or Path(value).is_absolute() or _DRIVE_PREFIX.match(value):
        raise UnsafePathError(f"{label} must be a single relative component")
    if ":" in value:
        raise UnsafePathError(f"{label} contains a colon or ADS suffix")
    if value.endswith((".", " ")):
        raise UnsafePathError(f"{label} has a trailing dot or space")
    stem = value.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise UnsafePathError(f"{label} is a Windows reserved name")
    if any(ord(char) < 32 for char in value):
        raise UnsafePathError(f"{label} contains a control character")
    return value


def assert_within(root: Path, candidate: Path, *, must_exist: bool = False) -> Path:
    """Resolve a path and prove that it is within root."""

    resolved_root = root.resolve(strict=must_exist)
    resolved_candidate = candidate.resolve(strict=must_exist)
    try:
        common = Path(os.path.commonpath((resolved_root, resolved_candidate)))
    except ValueError as exc:
        raise UnsafePathError(f"path is on another drive: {candidate}") from exc
    if common != resolved_root:
        raise UnsafePathError(f"path escapes root {resolved_root}: {candidate}")
    return resolved_candidate


def safe_child(root: Path, *components: str) -> Path:
    current = root.resolve()
    for component in components:
        validate_safe_component(component)
        current = current / component
    return assert_within(root, current)
