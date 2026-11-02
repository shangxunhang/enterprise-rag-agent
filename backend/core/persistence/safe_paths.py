"""Reject unsafe external identifiers and contain derived filesystem paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence
from urllib.parse import unquote


class UnsafeExternalIdentifierError(ValueError):
    """Raised when an external identifier can carry filesystem semantics."""


class PathContainmentError(ValueError):
    """Raised when a derived path resolves outside its trusted root."""


class SafeExternalIdentifier:
    """Validate external identity without rewriting or normalizing it."""

    _WINDOWS_DEVICES = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    _MAX_URL_DECODE_ROUNDS = 32

    @classmethod
    def validate(cls, value: str, *, field_name: str = "identifier") -> str:
        """Return ``value`` unchanged when it has no path interpretation."""

        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        if not value or not value.strip():
            raise UnsafeExternalIdentifierError(f"{field_name} cannot be empty")

        candidate = value
        for _ in range(cls._MAX_URL_DECODE_ROUNDS + 1):
            cls._validate_decoded(candidate, field_name=field_name)
            decoded = unquote(candidate)
            if decoded == candidate:
                return value
            candidate = decoded

        raise UnsafeExternalIdentifierError(
            f"{field_name} contains excessive nested URL encoding"
        )

    @classmethod
    def _validate_decoded(cls, value: str, *, field_name: str) -> None:
        if value in {".", ".."}:
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot be a path traversal segment"
            )
        if any(character in value for character in ("/", "\\")):
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot contain path separators"
            )
        if ":" in value:
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot contain a colon or alternate data stream"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot contain control characters"
            )
        if value.endswith(".") or value.endswith(" "):
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot end with a dot or space"
            )

        windows_path = PureWindowsPath(value)
        if windows_path.is_absolute() or windows_path.drive or windows_path.root:
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot be an absolute, drive, UNC, or device path"
            )
        if PurePosixPath(value).is_absolute():
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot be an absolute path"
            )

        device_stem = value.split(".", 1)[0].upper()
        if device_stem in cls._WINDOWS_DEVICES:
            raise UnsafeExternalIdentifierError(
                f"{field_name} cannot use a reserved Windows device name"
            )


class SafePathResolver:
    """Construct paths below one trusted root and verify resolved containment."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)

    def resolve(self, *relative_parts: str) -> Path:
        """Resolve trusted relative components and reject root escape."""

        candidate = self.root.joinpath(*relative_parts)
        resolved = candidate.resolve(strict=False)
        if resolved != self.root and self.root not in resolved.parents:
            raise PathContainmentError("resolved path escapes its trusted root")
        return resolved

    def path_for_identifier(
        self,
        identifier: str,
        *,
        field_name: str,
        directory: Sequence[str] = (),
        prefix: str = "",
        suffix: str = "",
    ) -> Path:
        """Validate an identifier, build its filename, and contain the result."""

        safe_identifier = SafeExternalIdentifier.validate(
            identifier,
            field_name=field_name,
        )
        return self.resolve(*directory, f"{prefix}{safe_identifier}{suffix}")


__all__ = [
    "PathContainmentError",
    "SafeExternalIdentifier",
    "SafePathResolver",
    "UnsafeExternalIdentifierError",
]
