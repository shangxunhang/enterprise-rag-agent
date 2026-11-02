"""Grouped, no-overwrite delivery for derived Markdown reading exports.

This module handles only caller-supplied bytes in an isolated output directory.
It does not read or modify any business store.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Callable, Literal, Mapping, Sequence


READING_NAME = "FULL_DOCUMENT_READING.md"
REPORT_NAME = "EXPORT_REPORT.md"
MANIFEST_NAME = "EXPORT_MANIFEST.json"
EXPORT_NAMES = (READING_NAME, REPORT_NAME, MANIFEST_NAME)


class DeliveryError(ValueError):
    """A reading export cannot be delivered as requested."""


class DeliveryInvalidInput(DeliveryError):
    """The supplied files or manifest do not describe one valid export."""


class DeliveryUnsafePath(DeliveryError):
    """The destination could escape its isolated output location."""


class DeliveryConflict(DeliveryError):
    """The final destination already contains different or incomplete data."""


@dataclass(frozen=True)
class DeliveryReceipt:
    status: Literal["NEW", "REUSE"]
    final_dir: Path
    export_identity: str
    file_sha256s: tuple[tuple[str, str], ...]


def _no_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryInvalidInput(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise DeliveryInvalidInput(f"non-JSON manifest constant: {value}")


def _validate_files(
    files: Mapping[str, bytes], expected_export_identity: str
) -> dict[str, bytes]:
    if not isinstance(expected_export_identity, str) or not expected_export_identity:
        raise DeliveryInvalidInput("expected export identity is required")
    if set(files) != set(EXPORT_NAMES) or len(files) != len(EXPORT_NAMES):
        raise DeliveryInvalidInput("reading export requires exactly its three fixed files")
    frozen: dict[str, bytes] = {}
    for name in EXPORT_NAMES:
        value = files[name]
        if not isinstance(value, bytes):
            raise DeliveryInvalidInput(f"{name} must be bytes")
        frozen[name] = bytes(value)
    try:
        manifest = json.loads(
            frozen[MANIFEST_NAME].decode("utf-8"),
            object_pairs_hook=_no_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryInvalidInput("export manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise DeliveryInvalidInput("export manifest must be a JSON object")
    if manifest.get("export_identity") != expected_export_identity:
        raise DeliveryInvalidInput("export manifest identity differs from expected identity")
    inventory = manifest.get("files")
    if not isinstance(inventory, list) or len(inventory) != 2:
        raise DeliveryInvalidInput("manifest must inventory reading and report only")
    seen: set[str] = set()
    for row in inventory:
        if not isinstance(row, dict):
            raise DeliveryInvalidInput("manifest file entry must be a JSON object")
        name = row.get("path")
        if not isinstance(name, str) or name not in {READING_NAME, REPORT_NAME} or name in seen:
            raise DeliveryInvalidInput("manifest file inventory has an invalid or duplicate path")
        seen.add(name)
        actual_size = len(frozen[name])
        actual_hash = sha256(frozen[name]).hexdigest()
        if type(row.get("bytes")) is not int or row["bytes"] != actual_size:
            raise DeliveryInvalidInput(f"manifest byte count differs for {name}")
        if row.get("sha256") != actual_hash:
            raise DeliveryInvalidInput(f"manifest SHA-256 differs for {name}")
    if seen != {READING_NAME, REPORT_NAME}:
        raise DeliveryInvalidInput("manifest file inventory is incomplete")
    return frozen


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    if getattr(metadata, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction(path)) if is_junction is not None else False


def _assert_no_reparse_components(path: Path) -> None:
    for component in (path, *path.parents):
        if _is_reparse_point(component):
            raise DeliveryUnsafePath(f"symlink or junction in export destination: {component}")


def _canonical(path: Path) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(path.resolve(strict=False))))
    except (OSError, RuntimeError) as exc:
        raise DeliveryUnsafePath(f"cannot resolve export path: {path}") from exc


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:  # Different Windows volumes.
        return False


def _assert_safe_destination(final_dir: Path, forbidden_roots: Sequence[Path]) -> None:
    if not final_dir.is_absolute() or ".." in final_dir.parts or not final_dir.name:
        raise DeliveryUnsafePath("export destination must be an absolute directory path")
    _assert_no_reparse_components(final_dir)
    if not final_dir.parent.is_dir():
        raise DeliveryUnsafePath("export destination parent must already exist")
    final_canonical = _canonical(final_dir)
    for forbidden in forbidden_roots:
        root = Path(forbidden)
        if not root.is_absolute():
            raise DeliveryUnsafePath("forbidden roots must be absolute paths")
        root_canonical = _canonical(root)
        if _is_within(final_canonical, root_canonical) or _is_within(
            root_canonical, final_canonical
        ):
            raise DeliveryUnsafePath(f"export destination overlaps forbidden root: {root}")


def _read_exact_regular_file(path: Path) -> bytes:
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or _is_reparse_point(path):
            raise DeliveryConflict(f"existing export has a non-regular file: {path.name}")
        return path.read_bytes()
    except (FileNotFoundError, PermissionError) as exc:
        raise DeliveryConflict(f"existing export cannot be verified: {path.name}") from exc


def _assert_existing_matches(final_dir: Path, frozen: Mapping[str, bytes]) -> None:
    if _is_reparse_point(final_dir):
        raise DeliveryUnsafePath("existing export directory is a symlink or junction")
    if not final_dir.is_dir():
        raise DeliveryConflict("export destination exists but is not a directory")
    try:
        names = {entry.name for entry in final_dir.iterdir()}
    except (OSError, PermissionError) as exc:
        raise DeliveryConflict("existing export directory cannot be inspected") from exc
    if names != set(EXPORT_NAMES):
        raise DeliveryConflict("existing export directory is incomplete or has extra files")
    for name in EXPORT_NAMES:
        if _read_exact_regular_file(final_dir / name) != frozen[name]:
            raise DeliveryConflict(f"existing export bytes differ: {name}")


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename fails if the destination directory already exists.
        os.rename(source, destination)
        return
    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise DeliveryError("atomic no-replace directory rename is unavailable")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
        return
    raise DeliveryError("atomic no-replace directory rename is unavailable")


def _cleanup_owned_stage(stage: Path, created: Sequence[str], identity: tuple[int, int]) -> None:
    try:
        metadata = os.lstat(stage)
    except FileNotFoundError:
        return
    if (metadata.st_dev, metadata.st_ino) != identity or _is_reparse_point(stage):
        return
    for name in created:
        try:
            (stage / name).unlink()
        except FileNotFoundError:
            pass
    try:
        stage.rmdir()
    except OSError:
        # Unknown files are left intact; never recursively remove an unowned path.
        pass


def deliver(
    final_dir: Path,
    files: Mapping[str, bytes],
    expected_export_identity: str,
    *,
    forbidden_roots: Sequence[Path] = (),
    fault_injector: Callable[[str], None] | None = None,
) -> DeliveryReceipt:
    """Deliver one complete export, reusing only byte-identical completed output.

    The destination parent must exist. Staging is created beside ``final_dir``
    so the final no-replace directory rename stays on the same filesystem.
    """
    target = Path(final_dir)
    frozen = _validate_files(files, expected_export_identity)
    _assert_safe_destination(target, forbidden_roots)
    hashes = tuple((name, sha256(frozen[name]).hexdigest()) for name in EXPORT_NAMES)

    def receipt(status: Literal["NEW", "REUSE"]) -> DeliveryReceipt:
        return DeliveryReceipt(status, target, expected_export_identity, hashes)

    if os.path.lexists(target):
        _assert_existing_matches(target, frozen)
        return receipt("REUSE")

    stage = Path(tempfile.mkdtemp(prefix=".src03-stage-", dir=target.parent))
    metadata = os.lstat(stage)
    stage_identity = (metadata.st_dev, metadata.st_ino)
    created: list[str] = []
    stage_owned = True
    try:
        for name in EXPORT_NAMES:
            created.append(name)
            _write_exclusive(stage / name, frozen[name])
            if fault_injector is not None:
                fault_injector(f"after_write:{name}")
        _assert_existing_matches(stage, frozen)
        _fsync_directory(stage)
        if fault_injector is not None:
            fault_injector("before_commit")
        _assert_safe_destination(target, forbidden_roots)
        try:
            _rename_directory_no_replace(stage, target)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            _assert_existing_matches(target, frozen)
            return receipt("REUSE")
        stage_owned = False
        _fsync_directory(target.parent)
        _assert_existing_matches(target, frozen)
        return receipt("NEW")
    finally:
        if stage_owned:
            _cleanup_owned_stage(stage, created, stage_identity)
