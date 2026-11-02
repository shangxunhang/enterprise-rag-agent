"""P0 rejection tests for external identifiers used in filesystem paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from application.run_workspace import RunWorkspace
from apps.enterprise_document.runtime.agent_rag_v1.standalone_adapters import (
    MarkdownDocumentExporter,
)
from core.persistence.safe_paths import (
    PathContainmentError,
    SafeExternalIdentifier,
    SafePathResolver,
    UnsafeExternalIdentifierError,
)
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from task.task_manager import JsonlTaskManager


MALICIOUS_IDENTIFIERS = [
    "",
    "   ",
    ".",
    "..",
    "../",
    "..\\",
    "../../",
    "..\\..\\",
    "a/../b",
    "a\\..\\b",
    "/absolute/path",
    r"C:\absolute",
    "C:/absolute",
    "C:relative",
    r"\\server\share",
    r"\\?\C:\path",
    r"\\.\NUL",
    "NUL",
    "nul",
    "NUL.txt",
    "CON",
    "con.json",
    "AUX",
    "PRN",
    "COM1",
    "com9.log",
    "LPT1",
    "lpt9.data",
    "name.",
    "name ",
    "name:stream",
    "%2e%2e",
    "%2E%2E",
    "%2f",
    "%5c",
    "%3a",
    "%252e%252e",
    "prefix%252f..%255cvictim",
    "mixed%2f..\\victim",
    "nul\x00tail",
    "line\nfeed",
]


@pytest.mark.parametrize("identifier", MALICIOUS_IDENTIFIERS)
def test_safe_external_identifier_rejects_path_semantics(identifier: str) -> None:
    with pytest.raises(UnsafeExternalIdentifierError):
        SafeExternalIdentifier.validate(identifier, field_name="run_id")


def test_valid_identifiers_are_preserved_and_paths_remain_below_root(
    tmp_path: Path,
) -> None:
    task_id = "task_A-01.valid"
    run_id = "run_A-01.valid"
    assert SafeExternalIdentifier.validate(task_id) == task_id
    assert SafeExternalIdentifier.validate(run_id) == run_id

    workspace = RunWorkspace(tmp_path / "root", task_id, run_id)
    workspace.write_model_usage(
        {
            "schema_version": "model_usage_v1",
            "task_id": task_id,
            "run_id": run_id,
            "created_at": "2026-08-02T00:00:00+00:00",
            "finalized": True,
            "usage": {"total_tokens": 3},
        }
    )
    assert workspace.read_model_usage()["total_tokens"] == 3
    trusted_root = workspace.output_root.resolve()
    for raw_path in workspace.paths().values():
        resolved = Path(raw_path).resolve(strict=False)
        assert resolved == trusted_root or trusted_root in resolved.parents


@pytest.mark.parametrize("identifier", MALICIOUS_IDENTIFIERS)
def test_all_known_file_boundaries_reject_malicious_ids(
    tmp_path: Path,
    identifier: str,
) -> None:
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        RunWorkspace(tmp_path / "workspace", "task-safe", identifier)

    capture = JsonlDataCaptureRecorder(tmp_path / "captures")
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        capture.record("raw_interactions", "task-safe", identifier)

    trace = JsonlRunTraceRecorder(tmp_path / "traces")
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        trace.record(
            "task-safe",
            identifier,
            "test_event",
            "test",
            "path_security",
        )

    manager = JsonlTaskManager(tmp_path / "tasks")
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        manager.get_task_path(identifier)
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        manager.get_authorization_identity_path(identifier)

    exporter = MarkdownDocumentExporter(tmp_path / "exports")
    with pytest.raises((UnsafeExternalIdentifierError, TypeError)):
        exporter.export(
            task_id="task-safe",
            run_id=identifier,
            document_version="v1",
            document_content=b"safe",
            generation_report={},
        )


@pytest.mark.parametrize(
    "attack",
    ["../../victim", r"..\..\victim", "%252e%252e%252f%252e%252e%252fvictim"],
)
def test_workspace_clean_cannot_delete_or_create_outside_root(
    tmp_path: Path,
    attack: str,
) -> None:
    output_root = tmp_path / "trusted" / "output"
    victim = tmp_path / "victim_model_usage.json"
    victim.write_text("do-not-touch", encoding="utf-8")

    with pytest.raises(UnsafeExternalIdentifierError):
        workspace = RunWorkspace(output_root, "task-safe", attack)
        workspace.clean()

    assert victim.read_text(encoding="utf-8") == "do-not-touch"
    assert not output_root.exists()


def test_workspace_revalidates_identity_before_deletion(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("preserved", encoding="utf-8")
    workspace = RunWorkspace(tmp_path / "root", "task-safe", "run-safe")
    object.__setattr__(workspace, "run_id", "../../victim")

    with pytest.raises(UnsafeExternalIdentifierError):
        workspace.clean()
    assert victim.read_text(encoding="utf-8") == "preserved"


def test_resolved_symlink_escape_is_rejected_when_platform_allows_it(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    link = trusted / "junction_like_link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(PathContainmentError):
        SafePathResolver(trusted).resolve("junction_like_link", "payload.json")
