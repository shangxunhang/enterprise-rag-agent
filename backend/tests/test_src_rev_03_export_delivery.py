"""SRCREV-03: isolated grouped delivery, with no business-store fixtures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import threading

import pytest

from application.reading_export import delivery as delivery_module
from application.reading_export.delivery import (
    DeliveryConflict,
    DeliveryInvalidInput,
    DeliveryUnsafePath,
    MANIFEST_NAME,
    READING_NAME,
    REPORT_NAME,
    deliver,
)


EXPORT_ID = "sha256:" + "a" * 64
READING = "# 阅读导出版\n\n## 1、项目背景\n\n原数值 42。\n".encode("utf-8")
REPORT = "# 导出报告\n\n标题抑制：一次。\n".encode("utf-8")


def _files(*, export_identity: str = EXPORT_ID) -> dict[str, bytes]:
    """Write the expected file inventory independently from the delivery code."""
    manifest = {
        "export_identity": export_identity,
        "files": [
            {"path": READING_NAME, "bytes": len(READING), "sha256": sha256(READING).hexdigest()},
            {"path": REPORT_NAME, "bytes": len(REPORT), "sha256": sha256(REPORT).hexdigest()},
        ],
    }
    return {
        READING_NAME: READING,
        REPORT_NAME: REPORT,
        MANIFEST_NAME: json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    }


def _manifest_changed(files: dict[str, bytes], change) -> dict[str, bytes]:
    altered = dict(files)
    manifest = json.loads(altered[MANIFEST_NAME])
    change(manifest)
    altered[MANIFEST_NAME] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return altered


def _business_source(tmp_path: Path) -> Path:
    root = tmp_path / "business-store"
    root.mkdir()
    (root / "canonical.md").write_bytes(b"immutable canonical bytes\n")
    return root


@pytest.mark.unit
def test_grouped_creation_and_byte_exact_reuse_preserve_business_source(tmp_path: Path) -> None:
    source = _business_source(tmp_path)
    before = (source / "canonical.md").read_bytes()
    target = tmp_path / "reading-export"
    files = _files()

    created = deliver(target, files, EXPORT_ID, forbidden_roots=(source,))
    assert created.status == "NEW"
    assert created.final_dir == target
    assert created.export_identity == EXPORT_ID
    assert dict(created.file_sha256s) == {
        name: sha256(payload).hexdigest() for name, payload in files.items()
    }
    assert {item.name for item in target.iterdir()} == set(files)
    assert {name: (target / name).read_bytes() for name in files} == files
    assert (source / "canonical.md").read_bytes() == before

    reused = deliver(target, files, EXPORT_ID, forbidden_roots=(source,))
    assert reused.status == "REUSE"
    assert reused.file_sha256s == created.file_sha256s
    assert {name: (target / name).read_bytes() for name in files} == files
    assert (source / "canonical.md").read_bytes() == before


@pytest.mark.unit
@pytest.mark.parametrize("damage", ("missing", "extra", "hash", "size", "self", "identity"))
def test_bad_file_set_or_manifest_is_rejected_before_destination_creation(
    tmp_path: Path, damage: str
) -> None:
    files = _files()
    if damage == "missing":
        files.pop(REPORT_NAME)
    elif damage == "extra":
        files["UNAUTHORIZED.txt"] = b"extra"
    elif damage == "hash":
        files = _manifest_changed(files, lambda data: data["files"][0].update(sha256="0" * 64))
    elif damage == "size":
        files = _manifest_changed(files, lambda data: data["files"][1].update(bytes=0))
    elif damage == "self":
        files = _manifest_changed(
            files,
            lambda data: data["files"].append(
                {"path": MANIFEST_NAME, "bytes": 0, "sha256": "0" * 64}
            ),
        )
    else:
        files = _manifest_changed(files, lambda data: data.update(export_identity="wrong"))
    target = tmp_path / "export"
    with pytest.raises(DeliveryInvalidInput):
        deliver(target, files, EXPORT_ID)
    assert not target.exists()
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
@pytest.mark.parametrize("malformed_path", ([], {}, 7, None))
def test_malformed_manifest_path_is_invalid_input_without_staging(
    tmp_path: Path, malformed_path: object
) -> None:
    files = _manifest_changed(
        _files(), lambda data: data["files"][0].update(path=malformed_path)
    )
    target = tmp_path / "export"
    with pytest.raises(DeliveryInvalidInput):
        deliver(target, files, EXPORT_ID)
    assert not target.exists()
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
@pytest.mark.parametrize("damage", ("partial", "changed", "extra", "file"))
def test_existing_incomplete_or_different_destination_is_conflict(
    tmp_path: Path, damage: str
) -> None:
    target = tmp_path / "export"
    files = _files()
    if damage == "file":
        target.write_bytes(b"occupied")
    else:
        target.mkdir()
        for name, payload in files.items():
            if damage == "partial" and name == MANIFEST_NAME:
                continue
            (target / name).write_bytes(b"different" if damage == "changed" and name == READING_NAME else payload)
        if damage == "extra":
            (target / "stray.txt").write_bytes(b"stray")
    with pytest.raises(DeliveryConflict):
        deliver(target, files, EXPORT_ID)
    if damage == "file":
        assert target.read_bytes() == b"occupied"
    else:
        assert target.exists()
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
@pytest.mark.parametrize("failure_stage", (f"after_write:{REPORT_NAME}", "before_commit"))
def test_staging_failure_never_exposes_partial_final_and_retry_can_complete(
    tmp_path: Path, failure_stage: str
) -> None:
    target = tmp_path / "export"
    files = _files()

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        deliver(target, files, EXPORT_ID, fault_injector=fail)
    assert not target.exists()
    assert list(tmp_path.glob(".src03-stage-*")) == []
    assert deliver(target, files, EXPORT_ID).status == "NEW"


@pytest.mark.unit
def test_mid_write_failure_removes_owned_partial_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "export"
    files = _files()
    original_write = delivery_module._write_exclusive

    def fail_during_report_write(path: Path, payload: bytes) -> None:
        if path.name == REPORT_NAME:
            path.write_bytes(payload[:3])
            raise OSError("injected write failure")
        original_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(delivery_module, "_write_exclusive", fail_during_report_write)
        with pytest.raises(OSError, match="injected write failure"):
            deliver(target, files, EXPORT_ID)
    assert not target.exists()
    assert list(tmp_path.glob(".src03-stage-*")) == []
    assert deliver(target, files, EXPORT_ID).status == "NEW"


@pytest.mark.unit
def test_race_with_preexisting_conflicting_destination_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "export"
    files = _files()

    def competing_creator(stage: str) -> None:
        if stage == "before_commit":
            target.mkdir()
            (target / "other.txt").write_bytes(b"another caller")

    with pytest.raises(DeliveryConflict):
        deliver(target, files, EXPORT_ID, fault_injector=competing_creator)
    assert (target / "other.txt").read_bytes() == b"another caller"
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
def test_concurrent_same_export_has_one_creator_and_exact_reuse(tmp_path: Path) -> None:
    target = tmp_path / "export"
    files = _files()
    barrier = threading.Barrier(4)

    def invoke() -> str:
        def synchronize(stage: str) -> None:
            if stage == "before_commit":
                barrier.wait(timeout=10)

        return deliver(target, files, EXPORT_ID, fault_injector=synchronize).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: invoke(), range(4)))
    assert sorted(statuses) == ["NEW", "REUSE", "REUSE", "REUSE"]
    assert {name: (target / name).read_bytes() for name in files} == files
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
def test_concurrent_different_exports_conflict_without_mixing_files(tmp_path: Path) -> None:
    target = tmp_path / "export"
    other_identity = "sha256:" + "b" * 64
    candidates = ((EXPORT_ID, _files()), (other_identity, _files(export_identity=other_identity)))
    barrier = threading.Barrier(2)

    def invoke(identity: str, files: dict[str, bytes]) -> str:
        def synchronize(stage: str) -> None:
            if stage == "before_commit":
                barrier.wait(timeout=10)

        try:
            return deliver(target, files, identity, fault_injector=synchronize).status
        except DeliveryConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, identity, files) for identity, files in candidates]
        statuses = [future.result() for future in futures]
    assert sorted(statuses) == ["CONFLICT", "NEW"]
    actual = {name: (target / name).read_bytes() for name in candidates[0][1]}
    assert actual in (candidates[0][1], candidates[1][1])
    assert list(tmp_path.glob(".src03-stage-*")) == []


@pytest.mark.unit
def test_forbidden_business_roots_and_ancestor_destination_are_rejected(tmp_path: Path) -> None:
    source = _business_source(tmp_path)
    files = _files()
    with pytest.raises(DeliveryUnsafePath):
        deliver(source / "export", files, EXPORT_ID, forbidden_roots=(source,))
    with pytest.raises(DeliveryUnsafePath):
        deliver(tmp_path, files, EXPORT_ID, forbidden_roots=(source,))
    assert (source / "canonical.md").read_bytes() == b"immutable canonical bytes\n"


@pytest.mark.unit
def test_destination_symlink_cannot_redirect_export_into_business_store(tmp_path: Path) -> None:
    source = _business_source(tmp_path)
    link = tmp_path / "linked-store"
    try:
        os.symlink(source, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable on this host")
    with pytest.raises(DeliveryUnsafePath):
        deliver(link / "export", _files(), EXPORT_ID, forbidden_roots=(source,))
    assert not (source / "export").exists()


@pytest.mark.unit
def test_existing_final_directory_symlink_is_never_reused(tmp_path: Path) -> None:
    target = tmp_path / "export"
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    try:
        os.symlink(alternate, target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable on this host")
    with pytest.raises(DeliveryUnsafePath):
        deliver(target, _files(), EXPORT_ID)
    assert list(alternate.iterdir()) == []


@pytest.mark.unit
def test_existing_final_file_symlink_is_never_reused(tmp_path: Path) -> None:
    target = tmp_path / "export"
    target.mkdir()
    source = tmp_path / "source.md"
    source.write_bytes(READING)
    files = _files()
    for name, payload in files.items():
        if name != READING_NAME:
            (target / name).write_bytes(payload)
    try:
        os.symlink(source, target / READING_NAME)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks unavailable on this host")
    with pytest.raises(DeliveryConflict):
        deliver(target, files, EXPORT_ID)
    assert source.read_bytes() == READING
