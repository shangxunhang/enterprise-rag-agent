from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FINALIZER_PATH = PROJECT_ROOT / "scripts/finalize_stage_e_acceptance_v2.py"
FINALIZER_V3_PATH = PROJECT_ROOT / "scripts/finalize_stage_e_acceptance_v3.py"
LEGACY_FINALIZER_PATH = PROJECT_ROOT / "scripts/finalize_stage_e_acceptance_v1.py"
LEGACY_RUNTIME_PATH = PROJECT_ROOT / "scripts/run_stage_e_writer_acceptance.py"
MERGER_PATH = PROJECT_ROOT / "scripts/merge_junit_reports.py"


def _load(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _junit(
    path: Path,
    *,
    tests: int,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
    prefix: str = "test",
) -> None:
    cases = []
    for index in range(tests):
        child = ""
        if index < failures:
            child = '<failure message="failed" />'
        elif index < failures + errors:
            child = '<error message="error" />'
        elif index < failures + errors + skipped:
            child = '<skipped message="skipped" />'
        cases.append(
            f'<testcase classname="tests.{prefix}" name="{prefix}_{index}" '
            f'file="backend/tests/{prefix}.py" time="0.01">{child}</testcase>'
        )
    path.write_text(
        "<?xml version='1.0' encoding='utf-8'?>"
        f'<testsuites tests="{tests}" failures="{failures}" errors="{errors}" '
        f'skipped="{skipped}" time="0.1">'
        f'<testsuite name="{prefix}" tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}" time="0.1">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )


def test_finalizer_parses_actual_junit_counts_and_hash(tmp_path: Path) -> None:
    module = _load(FINALIZER_PATH, "stage_e_finalizer_v2_counts")
    report = tmp_path / "focused.xml"
    _junit(report, tests=3, skipped=1, prefix="focused")

    parsed = module.parse_junit_report(report)

    assert parsed["tests"] == 3
    assert parsed["failures"] == 0
    assert parsed["errors"] == 0
    assert parsed["skipped"] == 1
    assert len(parsed["sha256"]) == 64
    assert parsed["test_files"] == ["backend/tests/focused.py"]
    assert len(parsed["test_ids"]) == 3


def test_finalizer_rejects_failure_or_error_junit(tmp_path: Path) -> None:
    module = _load(FINALIZER_PATH, "stage_e_finalizer_v2_failure")
    failed = tmp_path / "failed.xml"
    _junit(failed, tests=2, failures=1, prefix="failed")
    parsed = module.parse_junit_report(failed)

    with pytest.raises(RuntimeError, match="not clean"):
        module.validate_clean_junit(parsed, label="focused")

    errored = tmp_path / "errored.xml"
    _junit(errored, tests=2, errors=1, prefix="errored")
    parsed_error = module.parse_junit_report(errored)
    with pytest.raises(RuntimeError, match="not clean"):
        module.validate_clean_junit(parsed_error, label="regression")


def test_finalizer_rejects_zero_test_junit(tmp_path: Path) -> None:
    module = _load(FINALIZER_PATH, "stage_e_finalizer_v2_zero")
    empty = tmp_path / "empty.xml"
    _junit(empty, tests=0, prefix="empty")
    parsed = module.parse_junit_report(empty)

    with pytest.raises(RuntimeError, match="contains no tests"):
        module.validate_clean_junit(parsed, label="focused")


def test_junit_merger_preserves_counts_and_testcases(tmp_path: Path) -> None:
    merger = _load(MERGER_PATH, "stage_e_junit_merger_v2")
    finalizer = _load(FINALIZER_PATH, "stage_e_finalizer_v2_merged")
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    merged = tmp_path / "merged.xml"
    _junit(first, tests=2, prefix="first")
    _junit(second, tests=3, skipped=1, prefix="second")

    totals = merger.merge_junit_reports([first, second], merged)
    parsed = finalizer.parse_junit_report(merged)

    assert totals == {
        "tests": 5,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
        "time": pytest.approx(0.2),
    }
    assert parsed["tests"] == 5
    assert parsed["skipped"] == 1
    assert len(parsed["test_ids"]) == 5
    assert set(parsed["source_junits"]) == {
        str(first.resolve()),
        str(second.resolve()),
    }


def test_v3_finalizer_records_reproducible_command_file_hash(
    tmp_path: Path,
) -> None:
    module = _load(FINALIZER_V3_PATH, "stage_e_finalizer_v3_commands")
    commands = tmp_path / "commands.txt"
    commands.write_text("python -m pytest -q backend/tests\n", encoding="utf-8")

    evidence = module._command_evidence(str(commands))

    assert evidence["path"] == str(commands.resolve())
    assert len(evidence["sha256"]) == 64
    assert evidence["content"] == "python -m pytest -q backend/tests"


def test_v3_finalizer_freezes_mandatory_identity_and_lineage_report() -> None:
    source = FINALIZER_V3_PATH.read_text(encoding="utf-8")
    assert "stage_e_final_acceptance_report_v3" in source
    assert '"section_order_required": True' in source
    assert '"document_id_required": True' in source
    assert '"run_id_required": True' in source
    assert '"evidence_contract_sha256_required": True' in source
    assert '"prompt_evidence_contract_sha256_required": True' in source
    assert '"formally_deliverable": False' in source
    assert '"stage_f_started": False' in source


def test_finalizer_has_no_manual_test_count_arguments() -> None:
    source = FINALIZER_PATH.read_text(encoding="utf-8")
    legacy_source = LEGACY_FINALIZER_PATH.read_text(encoding="utf-8")
    forbidden = (
        "--focused-tests-passed",
        "--regression-tests-passed",
        "--focused-tests-duration",
        "--regression-duration",
    )
    v3_source = FINALIZER_V3_PATH.read_text(encoding="utf-8")
    assert not any(item in source for item in forbidden)
    assert not any(item in v3_source for item in forbidden)
    assert not any(item in legacy_source for item in forbidden)
    assert "--focused-junit" in source
    assert "--regression-junit" in source
    assert "from finalize_stage_e_acceptance_v2 import main" in legacy_source
    assert (
        "from run_stage_e_writer_acceptance_v2 import main"
        in LEGACY_RUNTIME_PATH.read_text(encoding="utf-8")
    )
