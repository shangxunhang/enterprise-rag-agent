from __future__ import annotations

from pathlib import Path

import pytest

from document_ingestion.atomic_writer import _atomic_write, atomic_write_jsonl
from document_ingestion.exceptions import UnsafePathError
from document_ingestion.safe_paths import assert_within, safe_child, validate_safe_component


@pytest.mark.parametrize(
    "value",
    ["../escape", r"..\escape", r"C:\absolute", "C:ads", "CON", "nul.txt", "name.", "name "],
)
def test_unsafe_windows_components_are_rejected(value: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_safe_component(value)


def test_containment_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        assert_within(root, root / ".." / "escape")
    assert safe_child(root, "tables", "valid.json") == (root / "tables" / "valid.json").resolve()


def test_atomic_jsonl_publishes_complete_file(tmp_path: Path) -> None:
    target = tmp_path / "result.jsonl"
    atomic_write_jsonl(target, [{"value": 1}, {"value": 2}], root=tmp_path)
    assert target.read_text(encoding="utf-8").splitlines() == ['{"value":1}', '{"value":2}']


def test_atomic_failure_leaves_no_partial_formal_or_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "result.jsonl"

    def fail_after_partial_write(handle: object) -> None:
        handle.write("partial")  # type: ignore[attr-defined]
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        _atomic_write(target, fail_after_partial_write, root=tmp_path)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
