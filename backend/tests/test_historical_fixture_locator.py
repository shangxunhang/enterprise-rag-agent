"""Read-only archive admission and synthetic-only locator refusal controls.

The tiny control files have no historical payload, envelope, or replay meaning.
Only the canonical test reads original bytes, and never copies/deserializes them.
"""
from __future__ import annotations

import builtins
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from backend.tests import historical_fixture_locator as locator


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tree_state(root: Path) -> dict:
    """Bind file content/size/mtime and directory identity before each call."""
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(), path.stat().st_size, path.stat().st_mtime_ns,
            None if path.is_dir() else _sha256(path.read_bytes()),
        )
        for path in (root, *root.rglob("*"))
    }


@contextmanager
def _deny_writes(monkeypatch):
    """Deny Python file writes and filesystem mutation during resolution."""
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append((args, kwargs))
        raise AssertionError("the read-only locator attempted filesystem mutation")

    def checked_open(original):
        def open_file(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                forbidden(file, mode)
            return original(file, mode, *args, **kwargs)
        return open_file

    original_os_open = os.open

    def checked_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            forbidden(path, flags)
        return original_os_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", checked_open(builtins.open))
        guard.setattr(io, "open", checked_open(io.open))
        guard.setattr(os, "open", checked_os_open)
        for name in ("mkdir", "makedirs", "remove", "unlink", "rmdir", "rename",
                     "replace", "truncate", "utime", "chmod", "link", "symlink"):
            guard.setattr(os, name, forbidden)
        yield
    assert attempted == []


@dataclass
class SyntheticLocatorControl:
    """Independent sentinel resources, never derived from Attempt4 payloads."""
    workspace: Path
    repo: Path
    mirror: Path
    fixture: Path
    closure: Path
    move: Path
    manifest: Path
    declared: PureWindowsPath
    rows: list[dict]
    manifest_data: dict
    monkeypatch: object

    def pin_bytes(self, path: Path, raw: bytes, constant: str):
        path.write_bytes(raw)
        self.monkeypatch.setattr(locator, constant, _sha256(raw))

    def pin_move(self):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=("RelativeRoot", "Source", "Archive", "RobocopyExit"))
        writer.writeheader()
        writer.writerows(self.rows)
        self.pin_bytes(self.move, output.getvalue().encode("utf-8"), "MOVE_RECEIPT_SHA256")

    def pin_manifest(self):
        self.pin_bytes(self.manifest, json.dumps(self.manifest_data).encode("utf-8"), "MANIFEST_SHA256")


@pytest.fixture
def synthetic_locator_control(tmp_path, monkeypatch):
    repo, mirror = tmp_path / "synthetic-control-repo", tmp_path / "relocated-control-mirror"
    fixture = mirror / locator.FIXTURE_RELATIVE_PATH
    fixture.mkdir(parents=True)
    closure = repo / locator.CLOSURE_RECEIPT_RELATIVE_PATH
    closure.parent.mkdir(parents=True)
    manifest = repo / locator.DESIGN_RELATIVE_PATH / "ARTIFACT_MANIFEST.json"
    manifest.parent.mkdir(parents=True)
    move = mirror / "_closure_receipt/move_receipt.csv"
    move.parent.mkdir(parents=True)
    declared = PureWindowsPath("E:/synthetic-locator-control-declared-mirror")
    relative = PureWindowsPath(locator.FIXTURE_RELATIVE_PATH.as_posix())
    rows = [{"RelativeRoot": locator.FIXTURE_RELATIVE_PATH.as_posix(),
             "Source": str(PureWindowsPath("D:/synthetic-locator-control-source") / relative),
             "Archive": str(declared / relative), "RobocopyExit": "1"}]
    entries = []
    for name in sorted(locator.EXPECTED_FILENAMES):
        sentinel = ("synthetic-only-locator-control:" + name + "\n").encode("ascii")
        (fixture / name).write_bytes(sentinel)
        entries.append({"path": "readonly_counterexample_copy/" + name,
                        "size_bytes": len(sentinel), "sha256": _sha256(sentinel)})
    control = SyntheticLocatorControl(tmp_path, repo, mirror, fixture, closure, move,
                                      manifest, declared, rows, {"files": entries}, monkeypatch)
    control.pin_bytes(closure, json.dumps({"external_evidence": {
        "source_mirror_archive": str(declared)}}).encode("utf-8"), "CLOSURE_RECEIPT_SHA256")
    control.pin_move()
    control.pin_manifest()
    monkeypatch.setenv(locator.SOURCE_MIRROR_ROOT_ENV, str(mirror))
    return control


def _assert_control_refusal(control, code):
    before = _tree_state(control.workspace)
    with _deny_writes(control.monkeypatch):
        with pytest.raises(locator.HistoricalFixtureError) as error:
            locator.resolve_attempt4_fixture(control.repo)
    assert error.value.code == code
    assert _tree_state(control.workspace) == before


def test_canonical_original_eight_files_match_frozen_manifest_without_mutation(monkeypatch):
    monkeypatch.delenv(locator.SOURCE_MIRROR_ROOT_ENV, raising=False)
    repo = Path(__file__).resolve().parents[2]
    closure_path = repo / locator.CLOSURE_RECEIPT_RELATIVE_PATH
    closure_raw = closure_path.read_bytes()
    assert _sha256(closure_raw) == locator.CLOSURE_RECEIPT_SHA256
    declared = Path(json.loads(closure_raw)["external_evidence"]["source_mirror_archive"])
    root = declared / locator.FIXTURE_RELATIVE_PATH
    manifest_path = repo / locator.DESIGN_RELATIVE_PATH / "ARTIFACT_MANIFEST.json"
    manifest_raw = manifest_path.read_bytes()
    assert _sha256(manifest_raw) == locator.MANIFEST_SHA256
    manifest = json.loads(manifest_raw)
    prefix = "readonly_counterexample_copy/"
    expected = {entry["path"][len(prefix):]: entry for entry in manifest["files"]
                if entry["path"].startswith(prefix)}
    assert set(expected) == locator.EXPECTED_FILENAMES
    before = _tree_state(root)
    metadata_before = {path: path.read_bytes() for path in (
        closure_path, manifest_path, declared / "_closure_receipt/move_receipt.csv")}
    with _deny_writes(monkeypatch):
        assert locator.resolve_attempt4_fixture(repo) == root
    for name, entry in expected.items():
        raw = (root / name).read_bytes()
        assert len(raw) == entry["size_bytes"]
        assert _sha256(raw) == entry["sha256"]
    assert _tree_state(root) == before
    assert {path: path.read_bytes() for path in metadata_before} == metadata_before


def test_synthetic_only_relocated_mirror_preserves_declared_move_identity(synthetic_locator_control):
    control = synthetic_locator_control
    assert str(control.mirror) != str(control.declared)
    assert PureWindowsPath(control.rows[0]["Archive"]) == (
        control.declared / PureWindowsPath(locator.FIXTURE_RELATIVE_PATH.as_posix()))
    legacy = control.repo / locator.FIXTURE_RELATIVE_PATH
    assert not legacy.exists()
    before = _tree_state(control.workspace)
    with _deny_writes(control.monkeypatch):
        assert locator.resolve_attempt4_fixture(control.repo) == control.fixture
    assert _tree_state(control.workspace) == before
    assert not legacy.exists()


@pytest.mark.parametrize("resource", ["mirror_root", "fixture_root", "move_receipt"])
def test_synthetic_only_missing_resource_refuses_without_creation(synthetic_locator_control, resource):
    control = synthetic_locator_control
    if resource == "mirror_root":
        missing = control.workspace / "never-created-mirror"
        control.monkeypatch.setenv(locator.SOURCE_MIRROR_ROOT_ENV, str(missing))
    elif resource == "fixture_root":
        control.fixture.rename(control.fixture.with_name("unrelated-sentinel-directory"))
        missing = control.fixture
    else:
        control.move.unlink()
        missing = control.move
    assert not missing.exists()
    _assert_control_refusal(control, "RESOURCE_MISSING")
    assert not missing.exists()


def test_synthetic_only_same_size_payload_tamper_refuses(synthetic_locator_control):
    control = synthetic_locator_control
    member = control.fixture / sorted(locator.EXPECTED_FILENAMES)[0]
    original = member.read_bytes()
    member.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert member.stat().st_size == len(original)
    _assert_control_refusal(control, "HASH_MISMATCH")


@pytest.mark.parametrize("metadata", ["closure", "move", "manifest"])
def test_synthetic_only_pinned_metadata_hash_mismatch_refuses(synthetic_locator_control, metadata):
    control = synthetic_locator_control
    path = getattr(control, metadata)
    path.write_bytes(path.read_bytes() + b"\n")
    _assert_control_refusal(control, "HASH_MISMATCH")


@pytest.mark.parametrize("rows", ["missing_exact", "identical_duplicate", "conflicting_duplicate"])
def test_synthetic_only_missing_or_ambiguous_exact_move_row_refuses(synthetic_locator_control, rows):
    control = synthetic_locator_control
    if rows == "missing_exact":
        control.rows[0]["RelativeRoot"] = "unrelated-synthetic-sentinel-root"
        expected = "RESOURCE_MISSING"
    else:
        duplicate = dict(control.rows[0])
        if rows == "conflicting_duplicate":
            duplicate["Source"] = str(PureWindowsPath("D:/other-synthetic-source") /
                                      PureWindowsPath(locator.FIXTURE_RELATIVE_PATH.as_posix()))
        control.rows.append(duplicate)
        expected = "AMBIGUOUS_RESOURCE"
    control.pin_move()
    _assert_control_refusal(control, expected)


@pytest.mark.parametrize("change", ["missing_member", "unexpected_ninth_member", "unexpected_subdirectory"])
def test_synthetic_only_nonexact_fixture_member_set_refuses(synthetic_locator_control, change):
    control = synthetic_locator_control
    if change == "missing_member":
        (control.fixture / sorted(locator.EXPECTED_FILENAMES)[0]).unlink()
    elif change == "unexpected_ninth_member":
        (control.fixture / "unrelated-synthetic-sentinel.txt").write_bytes(b"unrelated control")
    else:
        (control.fixture / "unrelated-synthetic-subdirectory").mkdir()
    _assert_control_refusal(control, "FILE_SET_MISMATCH")


def test_synthetic_only_duplicate_manifest_member_refuses(synthetic_locator_control):
    control = synthetic_locator_control
    control.manifest_data["files"].append(dict(control.manifest_data["files"][0]))
    control.pin_manifest()
    _assert_control_refusal(control, "AUTHORITY_INVALID")


@pytest.mark.parametrize("escape", ["declared_root", "source", "archive"])
def test_synthetic_only_authority_path_escape_refuses(synthetic_locator_control, escape):
    control = synthetic_locator_control
    if escape == "declared_root":
        control.pin_bytes(control.closure, json.dumps({"external_evidence": {
            "source_mirror_archive": "E:/synthetic-control/../escaped-mirror"
        }}).encode("utf-8"), "CLOSURE_RECEIPT_SHA256")
        code = "PATH_UNSAFE"
    else:
        field = "Source" if escape == "source" else "Archive"
        control.rows[0][field] = str(PureWindowsPath("E:/synthetic-control/../escaped-root") /
                                   PureWindowsPath(locator.FIXTURE_RELATIVE_PATH.as_posix()))
        control.pin_move()
        code = "AUTHORITY_INVALID"
    _assert_control_refusal(control, code)


@pytest.mark.parametrize(("configured", "code"), [
    (" ", "RESOURCE_MISSING"), ("relative-control-mirror", "PATH_UNSAFE"),
    ("parent_escape", "PATH_UNSAFE"),
])
def test_synthetic_only_invalid_physical_root_refuses(synthetic_locator_control, configured, code):
    control = synthetic_locator_control
    if configured == "parent_escape":
        configured = str(control.mirror / ".." / control.mirror.name)
    control.monkeypatch.setenv(locator.SOURCE_MIRROR_ROOT_ENV, configured)
    _assert_control_refusal(control, code)


@pytest.mark.parametrize("location", ["mirror_root", "ancestor"])
def test_synthetic_only_windows_reparse_chain_refuses(synthetic_locator_control, location):
    control = synthetic_locator_control
    rejected = control.mirror if location == "mirror_root" else control.workspace
    original_lstat = Path.lstat

    def marked_reparse(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == rejected:
            return SimpleNamespace(st_mode=info.st_mode,
                                   st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400)
        return info

    # Deterministic Windows lstat control exercises the actual ancestor guard;
    # no privileges, link creation, skipped cases, or historical roots involved.
    control.monkeypatch.setattr(Path, "lstat", marked_reparse)
    _assert_control_refusal(control, "PATH_UNSAFE")


def test_synthetic_only_legacy_checkout_candidate_is_ambiguous(synthetic_locator_control):
    control = synthetic_locator_control
    (control.repo / locator.FIXTURE_RELATIVE_PATH).mkdir()
    _assert_control_refusal(control, "AMBIGUOUS_RESOURCE")


@pytest.mark.parametrize("invalid", ["closure_json", "nonstring_manifest_path"])
def test_synthetic_only_invalid_metadata_has_typed_refusal(synthetic_locator_control, invalid):
    control = synthetic_locator_control
    if invalid == "closure_json":
        control.pin_bytes(control.closure, b"{invalid synthetic control", "CLOSURE_RECEIPT_SHA256")
    else:
        control.manifest_data["files"][0]["path"] = None
        control.pin_manifest()
    _assert_control_refusal(control, "AUTHORITY_INVALID")
