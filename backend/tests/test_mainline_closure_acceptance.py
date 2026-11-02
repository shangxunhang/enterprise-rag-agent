# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_bundle_by_title、_trace_events、test_corrective_retrieval_branch_runs_and_recovers、test_document_citation_registry_resolves_local_id_collisions、test_business_failure_preserves_partial_answer_and_report、test_step_16_audit_identifies_noop_gates_and_coarse_scheme_writer、test_request_level_technical_failure_is_preserved_without_reraising。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
import hashlib
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from run_demo import (
    persist_end_to_end_artifacts,
    _read_target_release,
    _validate_end_to_end,
)
from system_test.mainline_audit import audit_mainline
from system_test.mainline_closure import run_fake_mainline_scenario


# 阅读注释（函数）：处理 bundle by title 相关逻辑。
def _bundle_by_title(summary: dict) -> dict[str, dict]:
    """处理 bundle by title 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        dict[str, dict]

    阅读提示:
        主要直接调用：summary.get, str, item.get, output.get。
    """
    output = summary.get("scheme_writer_output") or {}
    return {
        str(item.get("section_title") or ""): item
        for item in (output.get("section_evidence") or [])
    }


# 阅读注释（函数）：处理 Trace events 相关逻辑。
def _section_by_title(summary: dict) -> dict[str, dict]:
    draft = summary.get("generated_draft") or {}
    return {
        str(item.get("section_title") or ""): item
        for item in (draft.get("sections") or [])
    }


def _trace_events(summary: dict) -> list[dict]:
    """处理 Trace events 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        list[dict]

    阅读提示:
        主要直接调用：Path, json.loads, splitlines, path.read_text, line.strip。
    """
    path = Path(summary["paths"]["trace"])
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# 阅读注释（函数）：处理 测试 纠错 检索 branch runs and recovers 相关逻辑。
def test_corrective_retrieval_branch_runs_and_recovers(tmp_path) -> None:
    """处理 测试 纠错 检索 branch runs and recovers 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：run_fake_mainline_scenario, _bundle_by_title, len, _trace_events, item.get, all, any。
    """
    summary = run_fake_mainline_scenario(
        tmp_path / "corrective",
        run_id="run_corrective_branch",
        rag_scenario="corrective_retrieval",
        llm_scenario="force_corrective_retrieval",
        enable_corrective_section_retrieval=True,
        citation_required_sections=["安全设计"],
    )

    assert summary["status"] == "success"
    safety = _bundle_by_title(summary)["安全设计"]
    safety_section = _section_by_title(summary)["安全设计"]
    safety_extra = safety_section.get("extra") or {}
    assert safety["recovery_count"] == 1
    assert safety_extra["evidence_scope"] == "recovery"
    assert len(safety["tool_call_ids"]) == 3
    assert "可绑定引用" in safety_extra["evidence_query"]

    events = _trace_events(summary)
    rag_started = [item for item in events if item.get("event_type") == "rag_started"]
    assert len(rag_started) == 3
    assert all(item.get("call_id") for item in rag_started)
    assert any("recovery_1" in item["call_id"] for item in rag_started)


# 阅读注释（函数）：处理 测试 文档 引用 注册表 resolves 本地 标识 collisions 相关逻辑。
def test_document_citation_registry_resolves_local_id_collisions(tmp_path) -> None:
    """处理 测试 文档 引用 注册表 resolves 本地 标识 collisions 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：run_fake_mainline_scenario, get, summary.get, len, set, _bundle_by_title。
    """
    summary = run_fake_mainline_scenario(
        tmp_path / "collision",
        run_id="run_citation_collision",
        rag_scenario="citation_collision",
        llm_scenario="always_grounded",
        enable_corrective_section_retrieval=False,
    )

    assert summary["status"] == "success"
    citations = (summary.get("scheme_writer_output") or {}).get("citations") or []
    ids = [item["citation_id"] for item in citations]
    assert ids == ["C1", "C2", "C3", "C4"]
    assert len(ids) == len(set(ids))
    assert len({item["source_document_id"] for item in citations}) == 4

    sections = _section_by_title(summary)
    required_ids = {
        title: list(sections[title].get("citation_ids") or [])
        for title in ("建设内容", "技术方案", "安全设计")
    }
    assert required_ids == {
        "建设内容": ["C2"],
        "技术方案": ["C3"],
        "安全设计": ["C4"],
    }


def test_self_rag_need_retrieve_more_runs_bounded_retrieval_and_recheck(
    tmp_path,
) -> None:
    summary = run_fake_mainline_scenario(
        tmp_path / "self_rag_retrieve_more",
        run_id="run_self_rag_retrieve_more",
        rag_scenario="citation_collision",
        llm_scenario="self_rag_retrieve_once",
        enable_corrective_section_retrieval=False,
        enable_agent_self_rag=True,
    )

    assert summary["status"] in {"success", "partial_success"}
    safety = _bundle_by_title(summary)["安全设计"]
    safety_section = _section_by_title(summary)["安全设计"]
    safety_extra = safety_section.get("extra") or {}
    generation_check = safety_extra.get("generation_check") or {}
    assert safety["recovery_count"] == 1
    assert safety_extra["evidence_scope"] == "self_rag_recovery"
    assert safety_extra["workflow_budget"]["retrieval_rounds"] == 1
    assert generation_check["need_retrieve_more"] is False
    assert len(safety["tool_call_ids"]) >= 3

    events = _trace_events(summary)
    section_order = int(safety_section["section_order"])
    self_rag_call_marker = f"section_{section_order:03d}_self_rag"
    self_rag_tool_calls = [
        item
        for item in events
        if item.get("event_type") == "tool_started"
        and self_rag_call_marker in str(item.get("call_id") or "")
    ]
    assert len(self_rag_tool_calls) == 1



@pytest.fixture(scope="module")
def target_business_failure_run(tmp_path_factory):
    """Unmodified native Fake scenario; not a synthetic approved publication."""
    return run_fake_mainline_scenario(
        tmp_path_factory.mktemp("native_business_failure"),
        run_id="run_business_gate_failure",
        rag_scenario="business_gate_failure",
        llm_scenario="force_business_gate_failure",
        enable_corrective_section_retrieval=True,
    )


def _persist_fake(summary, **kwargs):
    return persist_end_to_end_artifacts(
        summary, runtime_preflight={"mode": "fake", "model_name": "fake_llm"},
        expected_model_name="fake_llm", expected_real_rag=False, **kwargs,
    )


def test_business_failure_preserves_partial_answer_and_report(target_business_failure_run) -> None:
    """SRCREV-11: native technical completion does not upgrade Quality FAIL."""
    summary = deepcopy(target_business_failure_run)
    native = summary["target_release_publication"]["manifest"]
    assert summary["status"] == "partial_success"
    assert native["quality_state_passthrough"] == "FAIL"
    assert native["enforcement_action_passthrough"] == "OUTPUT_BEST_EFFORT"
    assert native["release_mode"] == "BEST_EFFORT"
    assert native["run_completion_assessment_ref"]["aggregate_result"] == "SUCCESS"
    assert summary["terminal_outcome"]["quality_state"] == "FAIL"
    assert summary["scheme_writer_output"]["hard_gate"]["passed"] is False
    package = _read_target_release(summary)
    before = {p: p.read_bytes() for p in package.source_paths}
    persisted = _persist_fake(summary)
    path = Path(persisted["best_effort_path"])
    assert persisted["validation_error"] is None
    assert persisted["deliverable_path"] is None
    assert persisted["rejected_draft_path"] is None
    assert path.name.endswith("_best_effort.md")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == native["release_payload_sha256"]
    assert path.read_text(encoding="utf-8").strip()
    report = json.loads(Path(persisted["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "partial_success"
    assert report["execution_integrity"] == "passed"
    assert report["business_quality"] == "failed"
    assert report["business_gate_failure"] is True
    assert report["hard_gate"]["passed"] is False
    assert report["target_release"]["quality_state"] == "FAIL"
    assert report["target_release"]["release_mode"] == "BEST_EFFORT"
    assert report["target_release"]["release_id"] == native["release_id"]
    assert report["target_release"]["run_completion_result"] == "SUCCESS"
    assert report["sections"], "The report must preserve per-section diagnostics"
    assert report["metrics"]["corrective_retrieval_count"] == sum(
        int(item.get("recovery_count") or 0)
        for item in summary["scheme_writer_output"].get("section_evidence", [])
    )
    assert report["paths"]["best_effort_markdown"] == str(path)
    assert report["paths"]["e2e_report"] == persisted["report_path"]
    assert {p: p.read_bytes() for p in before} == before



def test_shadow_business_failure_can_finish_workflow_without_becoming_deliverable(
    tmp_path,
) -> None:
    """Legacy adapter control, not a forged SUCCESS on a Target receipt."""
    summary = _legacy_adapter_summary(tmp_path, status="success", passed=False)
    assert summary["scheme_writer_output"]["hard_gate"]["passed"] is False

    persisted = persist_end_to_end_artifacts(
        summary,
        runtime_preflight={
            "mode": "fake",
            "model_name": "fake_llm",
            "rag_tool": "FakeRAGTool",
        },
        expected_model_name="fake_llm",
        expected_real_rag=False,
    )

    assert persisted["validation_error"] is None
    assert persisted["validation"]["status"] == "success"
    assert persisted["validation"]["execution_integrity"] == "passed"
    assert persisted["validation"]["business_gate_passed"] is False
    assert persisted["validation"]["business_gate_failure"] is True
    assert persisted["deliverable_path"] is None
    assert Path(persisted["rejected_draft_path"]).is_file()

    report = json.loads(Path(persisted["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "success"
    assert report["business_gate_failure"] is True
    assert report["hard_gate"]["passed"] is False
    assert report["artifact_lifecycle"]["deliverable_final_result"] is False
    assert report["artifact_lifecycle"]["rejected_artifact"] is True


def _legacy_adapter_summary(tmp_path, *, status="failed", passed=False):
    """Synthetic legacy-only adapter fixture; no Target owner claims."""
    trace = tmp_path / "legacy_trace.jsonl"
    task = tmp_path / "legacy_task.json"
    events = [{"event_type": name} for name in (
        "run_started", "run_finished", "workflow_started", "workflow_finished",
        "agent_started", "agent_finished", "rag_started", "rag_finished",
        "model_started", "model_finished",
    )]
    events += [
        {"event_type": "rag_started", "component_type": "rag", "component_name": "FakeRAGService"},
        {"event_type": "model_started", "component_type": "model", "model_name": "fake_llm",
         "input_summary": {"context_package_id": "legacy-test-context", "graph_state_schema": "graph_state_v1"}},
    ]
    trace.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    task.write_text("{}", encoding="utf-8")
    draft = {"full_text": "Legacy test draft, not a Target publication."}
    return {
        "task_id": "legacy_task", "run_id": "legacy_run", "status": status,
        "generated_draft": draft,
        "deliverable_final_result": {"draft": draft} if passed else None,
        "rejected_artifact": None if passed else {"rejection_code": "DOCUMENT_HARD_GATE_FAILED"},
        "scheme_writer_output": {"hard_gate": {"passed": passed}},
        "supervisor_result": {}, "paths": {"trace": str(trace), "task_state": str(task)},
    }


@pytest.mark.parametrize("status,passed", [("failed", False), ("success", True)])
def test_demo_legacy_artifact_contract_is_preserved(tmp_path, status, passed):
    result = _persist_fake(_legacy_adapter_summary(tmp_path, status=status, passed=passed))
    assert result["validation_error"] is None
    assert result["best_effort_path"] is None
    assert (result["deliverable_path"] is not None) is passed
    assert (result["rejected_draft_path"] is not None) is (not passed)
    assert result["report"]["business_quality"] == ("passed" if passed else "failed")


def test_demo_legacy_failed_gate_cannot_borrow_best_effort_label(tmp_path):
    summary = _legacy_adapter_summary(tmp_path, status="partial_success", passed=False)
    summary["deliverable_final_result"] = {"draft": summary["generated_draft"]}
    summary["rejected_artifact"] = None
    summary["scheme_writer_output"]["extra"] = {"target_release_mode": "BEST_EFFORT"}
    with pytest.raises(RuntimeError, match="Hard Gate failure must not expose"):
        _validate_end_to_end(summary, expected_model_name="fake_llm", expected_real_rag=False,
                             allow_business_failure=True)


@pytest.mark.parametrize("mutation", [
    "missing_publication", "missing_receipt", "forged_success", "foreign_task",
    "unverified_publication", "forged_quality", "forged_gate", "rejected_alias",
])
def test_demo_target_forgery_is_rejected(target_business_failure_run, mutation):
    summary = deepcopy(target_business_failure_run)
    if mutation == "missing_publication":
        summary["target_release_publication"] = None
    elif mutation == "missing_receipt":
        summary["terminal_outcome"] = None
    elif mutation == "forged_success":
        summary["status"] = "success"
    elif mutation == "foreign_task":
        summary["task_id"] = "foreign-task"
    elif mutation == "unverified_publication":
        summary["target_release_publication"]["verified_after_write"] = False
    elif mutation == "forged_quality":
        summary["terminal_outcome"]["quality_state"] = "PASS"
    elif mutation == "forged_gate":
        summary["scheme_writer_output"]["hard_gate"]["passed"] = True
    else:
        summary["rejected_artifact"] = {"rejection_code": "fake"}
    with pytest.raises(ValueError):
        _validate_end_to_end(summary, expected_model_name="fake_llm", expected_real_rag=False,
                             allow_business_failure=True)


@pytest.mark.parametrize("part", ["manifest", "proof", "payload"])
def test_demo_target_corruption_exports_no_markdown(target_business_failure_run, tmp_path, part):
    summary = deepcopy(target_business_failure_run)
    package = _read_target_release(summary)
    old_root = Path(summary["paths"]["trace"]).parent
    shutil.copytree(old_root / "target_release", tmp_path / "target_release")
    summary["paths"]["trace"] = str(tmp_path / "isolated_trace.jsonl")
    selected = package.source_paths[{"manifest": 0, "proof": 1, "payload": 2}[part]]
    broken = tmp_path / "target_release" / selected.relative_to(old_root / "target_release")
    envelope = json.loads(broken.read_text(encoding="utf-8"))
    envelope["content_base64"] = "dGFtcGVyZWQ="
    broken.write_text(json.dumps(envelope), encoding="utf-8")
    result = _persist_fake(summary, raise_on_validation_error=False)
    assert result["validation_error"] is not None
    assert result["deliverable_path"] is None
    assert result["best_effort_path"] is None
    assert result["rejected_draft_path"] is None
    assert not list(tmp_path.glob("*.md"))
    assert result["report"]["execution_integrity"] == "failed"
    assert result["report"]["business_quality"] != "passed"


def test_demo_target_export_uses_committed_payload_not_mutable_draft(target_business_failure_run):
    summary = deepcopy(target_business_failure_run)
    package = _read_target_release(summary)
    summary["generated_draft"] = {"full_text": "UNTRUSTED REPLACEMENT"}
    summary["deliverable_final_result"]["draft"] = {"full_text": "UNTRUSTED REPLACEMENT"}
    result = _persist_fake(summary)
    assert Path(result["best_effort_path"]).read_bytes() == package.payload_bytes
    assert result["report"]["business_quality"] == "failed"


# 阅读注释（函数）：处理 测试 step 16 audit identifies noop gates and coarse scheme writer 相关逻辑。
def test_step_16_audit_identifies_noop_gates_and_coarse_scheme_writer() -> None:
    """处理 测试 step 16 audit identifies noop gates and coarse scheme writer 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, audit_mainline。
    """
    project_root = Path(__file__).resolve().parents[2]
    report = audit_mainline(project_root)
    by_id = {item["id"]: item for item in report["findings"]}

    assert by_id["generation_checker_noop"]["present"] is False
    assert by_id["repair_strategy_noop"]["present"] is False
    assert by_id["evidence_assessor_noop"]["present"] is False
    assert by_id["section_aware_retrieval_present"]["present"] is True
    assert report["summary"]["evidence_assessor"] == "crag"


# 阅读注释（函数）：处理 测试 请求 level technical failure is preserved without reraising 相关逻辑。
def test_request_level_technical_failure_is_preserved_without_reraising(tmp_path) -> None:
    """处理 测试 请求 level technical failure is preserved without reraising 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：trace_path.write_text, json.dumps, task_state_path.write_text, str, persist_end_to_end_artifacts, json.loads, read_text, Path。
    """
    trace_path = tmp_path / "technical_failure_trace.jsonl"
    task_state_path = tmp_path / "technical_failure_task.json"
    trace_path.write_text(
        json.dumps(
            {
                "event_type": "run_finished",
                "phase": "error",
                "status": "failed",
                "error_message": "required rendered context cannot be truncated: citation_catalog",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    task_state_path.write_text("{}", encoding="utf-8")
    summary = {
        "task_id": "task_technical_failure",
        "run_id": "run_technical_failure",
        "status": "failed",
        "generated_draft": {"full_text": ""},
        "rejected_artifact": None,
        "deliverable_final_result": None,
        "scheme_writer_output": {},
        "supervisor_result": {
            "status": "failed",
            "error": {
                "error_code": "SCHEME_WRITER_FAILED",
                "error_type": "ContextBudgetExceededError",
                "message": "required rendered context cannot be truncated: citation_catalog",
                "failed_node": "section_generation",
                "retryable": False,
            },
        },
        "paths": {
            "trace": str(trace_path),
            "task_state": str(task_state_path),
        },
    }

    persisted = persist_end_to_end_artifacts(
        summary,
        runtime_preflight={"mode": "fake"},
        expected_model_name="fake_llm",
        expected_real_rag=False,
        raise_on_validation_error=False,
    )

    assert persisted["validation"] == {}
    assert persisted["validation_error"] is not None
    assert "ContextBudgetExceededError" in persisted["validation_error"]["message"]
    report = json.loads(Path(persisted["report_path"]).read_text(encoding="utf-8"))
    assert report["error"]["error_type"] == "ContextBudgetExceededError"
    assert persisted["deliverable_path"] is None
    assert persisted["rejected_draft_path"] is None


# BC33-D12: real historical native inputs, fresh controlled output directories.
# These are exporter/CLI replay proofs; they do not claim a new native Run.
_D12_HISTORY_ROOT = Path(
    "E:/rag-agent-tmp/BC33_FULL_OFFLINE_SWEEP_20261028/"
    "continuation/batch_058/slice_000/temp"
)


def _d12_historical_review(tmp_path, case=0):
    from application.run_workspace import RunWorkspace

    source = _D12_HISTORY_ROOT / (
        f"test_native_m6_refusal_continu{case}/offline_summary_roundtrip.json"
    )
    if not source.is_file():
        pytest.skip(f"D12 required historical input unavailable: {source}")
    raw = source.read_bytes()
    summary = json.loads(raw.decode("utf-8"))
    workspace = RunWorkspace(tmp_path / "delivery", summary["task_id"], summary["run_id"])
    before = {source: hashlib.sha256(raw).hexdigest()}
    # Copy exact frozen input bytes to a fresh legal workspace. Never write,
    # clean, or normalize the historical originals.
    old_paths = dict(summary["paths"])
    for key, target in (
        ("trace", workspace.trace_path), ("task_state", workspace.task_state_path),
    ):
        original = Path(old_paths[key])
        if not original.is_file():
            pytest.skip(f"D12 required historical {key} unavailable: {original}")
        data = original.read_bytes()
        before[original] = hashlib.sha256(data).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    summary["paths"] = workspace.paths()
    return summary, workspace, before


def _d12_assert_history_unchanged(before):
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before


def _d12_cli(summary, workspace, monkeypatch, capsys, *, strict=True):
    import sys
    from dataclasses import replace
    import run_demo as cli
    from core.config import get_settings

    settings = replace(
        get_settings(), data_root=workspace.output_root,
        default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    argv = [
        "run_demo.py", "--fake-runtime", "--user-input", "D12 frozen native review replay",
        "--run-id", workspace.run_id, "--output-root", str(workspace.output_root),
    ]
    if strict:
        argv.append("--strict")
    monkeypatch.setattr(sys, "argv", argv)
    # Only replace the already-executed Runtime boundary and local configuration.
    # Exporter, validation, report, projector, filesystem writes/readback are real.
    monkeypatch.setattr(cli, "run_demo", lambda **_kwargs: summary)
    monkeypatch.setattr(cli, "_configure_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "get_settings", lambda **_kwargs: settings)
    if strict:
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        exit_code = exit_info.value.code
    else:
        cli.main()
        exit_code = 0
    output = capsys.readouterr()
    assert not output.err
    return json.loads(output.out), exit_code, output.out


@pytest.mark.parametrize(
    "case", range(4),
    ids=["cold-8of8", "cold-7of8", "warm-8of8", "warm-7of8"],
)
@pytest.mark.parametrize("strict", [False, True], ids=["ordinary", "strict"])
def test_d12_historical_native_review_cli_readback(
    tmp_path, case, strict, monkeypatch, capsys,
):
    summary, workspace, before = _d12_historical_review(tmp_path, case)
    draft = deepcopy(summary["generated_draft"])
    terminal = deepcopy(summary["terminal_outcome"])
    body_bytes = draft["full_text"].encode("utf-8")
    expected_sections = 7 if case % 2 else 8
    assert len(draft["sections"]) == expected_sections
    assert body_bytes.strip()
    public, exit_code, output = _d12_cli(
        summary, workspace, monkeypatch, capsys, strict=strict,
    )
    assert exit_code == (1 if strict else 0)
    assert public["terminal_outcome"] == terminal
    assert summary["status"] == "failed"
    assert summary["target_release_publication"] is None
    assert summary["deliverable_final_result"] is None
    assert public["review_delivery"] == {
        "body_available": True, "body_delivered": True,
        "findings_delivered": True, "e2e_validation_failed": True,
    }
    paths = public["review_artifacts"]
    assert set(paths) == {"review_draft_markdown", "review_findings_json"}
    body_path = Path(paths["review_draft_markdown"])
    findings_path = Path(paths["review_findings_json"])
    for path in (body_path, findings_path):
        assert path.resolve().is_relative_to(workspace.runs_dir)
        assert path.is_file()
    assert body_path.read_bytes() == body_bytes
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    native_review = draft["extra"]["pwa004_review_draft"]
    assert findings["task_id"] == summary["task_id"]
    assert findings["run_id"] == summary["run_id"]
    assert findings["missing_sections"] == draft["missing_sections"]
    assert findings["missing_sections"] == (["待补充事项"] if case % 2 else [])
    assert len(findings["rejections"]) == len(native_review["rejections"]) == 4
    assert findings["rejections"] == native_review["rejections"]
    assert findings["hard_dependency_blocked"] == native_review["hard_dependency_blocked"]
    assert findings["formal_release_authorized"] is False
    assert findings["formal_post_write_assessed"] is False
    assert len(summary["generated_draft"]["sections"]) == expected_sections
    assert summary["generated_draft"] == draft
    assert "validation_error" not in public
    assert "supervisor_result" not in output
    assert "runtime_preflight" not in output
    assert "validation_report" not in output
    _d12_assert_history_unchanged(before)
    observation = {
        "case": case, "section_count": expected_sections,
        "body_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "readback_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
        "rejection_count": len(findings["rejections"]),
        "missing_sections": findings["missing_sections"],
        "strict": strict, "exit_code": exit_code, "cli_json": public,
        "source_hashes_unchanged": {str(path): value for path, value in before.items()},
    }
    (tmp_path / "d12_cli_observation.json").write_text(
        json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("D12_CLI_OBS", json.dumps(observation, ensure_ascii=False))


def test_d12_review_unknown_fields_are_not_exported(tmp_path):
    summary, workspace, before = _d12_historical_review(tmp_path)
    review = summary["generated_draft"]["extra"]["pwa004_review_draft"]
    review["settings"] = {"api_key": "D12_PRIVATE_TOP_SENTINEL"}
    review["rejections"][0]["debug"] = {"prompt": "D12_PRIVATE_NESTED_SENTINEL"}
    result = _persist_fake(
        summary, trusted_workspace=workspace, raise_on_validation_error=False,
    )
    assert result["review_findings_path"]
    raw = Path(result["review_findings_path"]).read_text(encoding="utf-8")
    exported = json.loads(raw)
    assert "D12_PRIVATE_" not in raw
    assert "settings" not in exported
    assert "debug" not in exported["rejections"][0]
    assert len(exported["rejections"]) == 4
    assert exported["rejections"][0]["support_ref"] == review["rejections"][0]["support_ref"]
    _d12_assert_history_unchanged(before)


@pytest.mark.parametrize("mutation", [
    "wrong_draft_run", "wrong_claim_run", "illegal_identity", "external_trace",
    "arbitrary_marker", "typed_nested_unknown", "wrong_attempt",
])
def test_d12_review_source_and_path_rejected(tmp_path, mutation):
    summary, workspace, before = _d12_historical_review(tmp_path)
    draft = summary["generated_draft"]
    review = draft["extra"]["pwa004_review_draft"]
    outside = tmp_path / "outside" / "trace.jsonl"
    if mutation == "wrong_draft_run":
        draft["run_id"] = "foreign_run"
    elif mutation == "wrong_claim_run":
        review["rejections"][0]["claim_ref"]["claim_occurrence_ref"]["run_id"] = "foreign_run"
    elif mutation == "illegal_identity":
        summary["run_id"] = "../illegal"
    elif mutation == "external_trace":
        summary["paths"]["trace"] = str(outside)
    elif mutation == "arbitrary_marker":
        draft["extra"]["pwa004_review_draft"] = {"review_only": True}
    elif mutation == "wrong_attempt":
        review["rejections"][0]["attempt_id"] = "foreign_attempt"
    else:
        review["rejections"][0]["support_ref"]["debug"] = {"api_key": "D12_PRIVATE_TYPED_SENTINEL"}
    result = _persist_fake(
        summary, trusted_workspace=workspace, raise_on_validation_error=False,
    )
    assert result["validation_error"] is not None
    assert result["review_draft_path"] is None
    assert result["review_findings_path"] is None
    assert not list(workspace.output_root.rglob("*_review_draft.md"))
    assert not list(workspace.output_root.rglob("*_review_findings.json"))
    assert not outside.exists()
    _d12_assert_history_unchanged(before)


def test_d12_missing_body_delivers_only_findings(tmp_path, monkeypatch, capsys):
    summary, workspace, before = _d12_historical_review(tmp_path)
    summary["generated_draft"]["full_text"] = ""
    summary["generated_draft"]["content"] = ""
    public, exit_code, _ = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert public["review_delivery"] == {
        "body_available": False, "body_delivered": False,
        "findings_delivered": True, "e2e_validation_failed": True,
    }
    assert set(public["review_artifacts"]) == {"review_findings_json"}
    findings = json.loads(Path(public["review_artifacts"]["review_findings_json"]).read_text(encoding="utf-8"))
    assert len(findings["rejections"]) == 4
    assert not list(workspace.runs_dir.glob("*_review_draft.md"))
    _d12_assert_history_unchanged(before)


def test_d12_write_failure_never_exposes_stale_paths(tmp_path, monkeypatch, capsys):
    summary, workspace, before = _d12_historical_review(tmp_path)
    stale = workspace.runs_dir / "previous_review.md"
    stale.write_bytes(b"OLD REVIEW")
    summary["paths"].update(
        review_draft_markdown=str(stale), review_findings_json=str(stale),
    )
    old_write_bytes, old_write_text = Path.write_bytes, Path.write_text

    def bytes_or_fail(path, data):
        if path.name.endswith(("_review_draft.md", "_review_findings.json")):
            raise OSError("D12_PRIVATE_EXCEPTION_API_KEY")
        return old_write_bytes(path, data)

    def text_or_fail(path, data, *args, **kwargs):
        if path.name.endswith(("_review_draft.md", "_review_findings.json")):
            raise OSError("D12_PRIVATE_EXCEPTION_API_KEY")
        return old_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", bytes_or_fail)
    monkeypatch.setattr(Path, "write_text", text_or_fail)
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert not public.get("review_artifacts")
    assert public["review_delivery"]["body_available"] is True
    assert public["review_delivery"]["body_delivered"] is False
    assert public["review_delivery"]["findings_delivered"] is False
    assert public["review_delivery"]["e2e_validation_failed"] is True
    assert "D12_PRIVATE_EXCEPTION_API_KEY" not in output
    assert str(stale) not in output
    assert stale.read_bytes() == b"OLD REVIEW"
    _d12_assert_history_unchanged(before)


def test_d12_nonpwa004_failure_ignores_stale_review_paths(tmp_path, monkeypatch, capsys):
    summary, workspace, before = _d12_historical_review(tmp_path)
    terminal = deepcopy(summary["terminal_outcome"])
    summary["generated_draft"]["extra"].pop("pwa004_review_draft")
    summary["supervisor_result"]["error"]["error_code"] = "SCHEME_WRITER_FAILED"
    stale = workspace.runs_dir / "previous_review.md"
    stale.write_bytes(b"OLD REVIEW")
    summary["paths"].update(
        review_draft_markdown=str(stale), review_findings_json=str(stale),
    )
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert public["terminal_outcome"] == terminal
    assert not public.get("review_artifacts")
    assert str(stale) not in output
    assert not list(workspace.runs_dir.glob("*_review_draft.md"))
    assert not list(workspace.runs_dir.glob("*_review_findings.json"))
    assert summary["target_release_publication"] is None
    _d12_assert_history_unchanged(before)


@pytest.mark.parametrize("action", ["OUTPUT", "OUTPUT_BEST_EFFORT"])
@pytest.mark.parametrize("corruption", [None, "manifest", "proof", "payload"])
def test_d12_target_immutable_chain_keeps_priority(tmp_path, action, corruption):
    from test_src_rev_03_readonly_source_adapter import build_release_fixture

    # Exact manifest/proof/payload file contract; no mocked publication reader.
    fixture = build_release_fixture(
        tmp_path, control_action=action,
        quality_state="PASS" if action == "OUTPUT" else "FAIL",
    )
    passed = action == "OUTPUT"
    summary = _legacy_adapter_summary(
        tmp_path, status="success" if passed else "partial_success", passed=passed,
    )
    summary.update(
        task_id=fixture.manifest.task_id, run_id=fixture.manifest.run_id,
        terminal_outcome=fixture.terminal_outcome.model_dump(mode="json"),
        rejected_artifact=None,
        target_release_publication={
            "schema_version": "post_write_published_release_v1",
            "manifest": fixture.manifest.model_dump(mode="json"),
            "manifest_artifact": json.loads(fixture.manifest_path.read_text(encoding="utf-8"))["reference"],
            "verified_after_write": True, "reuse_state": "NEW_COMMIT",
        },
    )
    summary["generated_draft"]["extra"] = {
        "pwa004_review_draft": {"review_only": True},
    }
    frozen = {
        path: path.read_bytes()
        for path in (fixture.manifest_path, fixture.proof_path, fixture.payload_path)
    }
    if corruption:
        path = getattr(fixture, f"{corruption}_path")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["content_base64"] = "dGFtcGVyZWQ="
        path.write_text(json.dumps(envelope), encoding="utf-8")
    before_export = {path: path.read_bytes() for path in frozen}
    result = _persist_fake(summary, raise_on_validation_error=False)
    assert result["review_draft_path"] is None
    assert result["review_findings_path"] is None
    if corruption:
        assert result["validation_error"] is not None
        assert result["deliverable_path"] is None
        assert result["best_effort_path"] is None
        assert not list(tmp_path.glob("*.md"))
    else:
        assert result["validation_error"] is None
        output_path = Path(result["deliverable_path"] if passed else result["best_effort_path"])
        assert output_path.read_bytes() == fixture.payload_bytes
        assert hashlib.sha256(output_path.read_bytes()).hexdigest() == fixture.manifest.release_payload_sha256
        assert result["report"]["business_quality"] == ("passed" if passed else "failed")
    assert {path: path.read_bytes() for path in frozen} == before_export



@pytest.mark.parametrize("failure", ["report", "manifest"])
def test_d12_cli_keeps_review_paths_when_diagnostics_write_fails(
    tmp_path, failure, monkeypatch, capsys,
):
    import run_demo as cli

    summary, workspace, before = _d12_historical_review(tmp_path)
    expected_body = summary["generated_draft"]["full_text"].encode("utf-8")
    terminal = deepcopy(summary["terminal_outcome"])

    def fail_diagnostics(*_args, **_kwargs):
        raise OSError("D12_PRIVATE_DIAGNOSTIC_SENTINEL")

    if failure == "report":
        monkeypatch.setattr(cli, "_write_e2e_report", fail_diagnostics)
    else:
        monkeypatch.setattr(cli.ExperimentRunManifestWriter, "write", fail_diagnostics)
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert public["terminal_outcome"] == terminal
    assert public["review_delivery"] == {
        "body_available": True, "body_delivered": True,
        "findings_delivered": True, "e2e_validation_failed": True,
    }
    paths = public["review_artifacts"]
    assert set(paths) == {"review_draft_markdown", "review_findings_json"}
    body_path = Path(paths["review_draft_markdown"])
    findings_path = Path(paths["review_findings_json"])
    assert body_path.resolve().is_relative_to(workspace.runs_dir)
    assert findings_path.resolve().is_relative_to(workspace.runs_dir)
    assert body_path.read_bytes() == expected_body
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert len(findings["rejections"]) == 4
    assert findings["formal_release_authorized"] is False
    assert "D12_PRIVATE_DIAGNOSTIC_SENTINEL" not in output
    assert "validation_error" not in public
    _d12_assert_history_unchanged(before)
    print("D12_DIAGNOSTIC_FAILURE_OBS", json.dumps({
        "failure": failure, "strict_exit_code": exit_code,
        "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
        "cli_json": public,
    }, ensure_ascii=False))


def test_d12_cli_ignores_old_files_without_current_export_receipts(
    tmp_path, monkeypatch, capsys,
):
    import run_demo as cli

    summary, workspace, before = _d12_historical_review(tmp_path)
    body_path = workspace.runs_dir / f"{workspace.run_id}_review_draft.md"
    findings_path = workspace.runs_dir / f"{workspace.run_id}_review_findings.json"
    body_path.write_bytes(b"OLD BODY")
    findings_path.write_bytes(b'{"old": true}')
    summary["paths"].update(
        review_draft_markdown=str(body_path), review_findings_json=str(findings_path),
    )

    def no_current_export(*_args, **_kwargs):
        return {
            "validation": {}, "validation_error": {
                "type": "OSError", "message": "D12_PRIVATE_NO_RECEIPT_SENTINEL",
            },
            "review_draft_path": None, "review_findings_path": None,
            "review_body_available": True,
        }

    monkeypatch.setattr(cli, "persist_end_to_end_artifacts", no_current_export)
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert not public.get("review_artifacts")
    assert public["review_delivery"] == {
        "body_available": True, "body_delivered": False,
        "findings_delivered": False, "e2e_validation_failed": True,
    }
    assert str(body_path) not in output
    assert str(findings_path) not in output
    assert "D12_PRIVATE_NO_RECEIPT_SENTINEL" not in output
    assert body_path.read_bytes() == b"OLD BODY"
    assert findings_path.read_bytes() == b'{"old": true}'
    _d12_assert_history_unchanged(before)



def test_d12_cli_keeps_body_when_only_findings_write_fails(
    tmp_path, monkeypatch, capsys,
):
    summary, workspace, before = _d12_historical_review(tmp_path)
    expected_body = summary["generated_draft"]["full_text"].encode("utf-8")
    original_write_text = Path.write_text

    def fail_findings_only(path, data, *args, **kwargs):
        if path.name.endswith("_review_findings.json"):
            raise OSError("D12_PRIVATE_FINDINGS_WRITE_SENTINEL")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_findings_only)
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys)
    assert exit_code == 1
    assert public["review_delivery"] == {
        "body_available": True, "body_delivered": True,
        "findings_delivered": False, "e2e_validation_failed": True,
    }
    assert set(public["review_artifacts"]) == {"review_draft_markdown"}
    body_path = Path(public["review_artifacts"]["review_draft_markdown"])
    assert body_path.resolve().is_relative_to(workspace.runs_dir)
    assert body_path.read_bytes() == expected_body
    assert not list(workspace.runs_dir.glob("*_review_findings.json"))
    assert "D12_PRIVATE_FINDINGS_WRITE_SENTINEL" not in output
    assert "validation_error" not in public
    _d12_assert_history_unchanged(before)


def _d09_projection_fixture(tmp_path):
    """Controlled CLI projection fixture sampled from exact saved native inputs.

    This is not a new Stage execution or a grant to read historical Sources.
    Native Stage provenance and currentness are exercised by the native suite.
    """
    import base64
    from core.hashing import canonical_sha256

    summary, workspace, before = _d12_historical_review(tmp_path)
    source = next(path for path in before if path.name == "offline_summary_roundtrip.json")
    saved = json.loads(source.read_text(encoding="utf-8"))
    original_runs = Path(saved["paths"]["trace"]).parent
    artifacts = {}
    for path in (original_runs / "artifacts").rglob("*.json"):
        envelope = json.loads(path.read_text(encoding="utf-8"))
        reference = envelope["reference"]
        if reference["artifact_type"] == "canonical_verifier_input":
            raw = base64.b64decode(envelope["content_base64"])
            assert reference["content_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
            artifacts[reference["artifact_ref"]] = (reference, json.loads(raw))
    review = summary["generated_draft"]["extra"]["pwa004_review_draft"]
    sections = {section["section_id"]: section for section in summary["generated_draft"]["sections"]}
    explanations = []
    for row in review["rejections"]:
        input_ref = row["candidate_ref"].removesuffix("/candidate") + "/input"
        reference, receipt = artifacts[input_ref]
        material = receipt["input_material"]
        assert receipt["input_fingerprint"] == row["verifier_semantic_input_sha256"]
        claim = material["claim"]
        assert claim["claim_ref"] == row["claim_ref"]
        value = dict(deepcopy(row), task_id=workspace.task_id, run_id=workspace.run_id,
            input_ref=input_ref, input_content_hash=reference["content_hash"],
            claim_text=claim["text_span"]["text"], paragraph_text=material["paragraph_text"],
            text_span_hash=claim["text_span_hash"],
            start_offset=claim["text_span"]["start_offset"], end_offset=claim["text_span"]["end_offset"],
            binding_facts=deepcopy(material["binding_facts"]),
            shared_support={key: deepcopy(material["shared_support"][key])
                for key in ("relation", "supporting_refs", "contradicting_refs")},
            evidence=[{key: deepcopy(item[key]) for key in (
                "exact_source_ref", "evidence_id", "doc_id", "title", "context_text", "context_text_sha256")}
                for item in material["evidence"]],
            candidate_verdict="VERIFIED", verification_result_committed=False,
            explanation=("原生 Candidate 提出了 VERIFIED；SharedSupport 为 " + row["support_relation"]
                + "，M6 规则 VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT 拒绝该候选。"
                "该拒绝不证明陈述为假，也不构成正式质量 FAIL。"),
            limitations=["未产生该候选的正式 VerificationResult。",
                "证据可见或被引用不等于已证明陈述成立。", "本说明不授予正式发布或人工签发权限。"])
        value["explanation_sha256"] = canonical_sha256(value)
        explanations.append(value)
        sections[row["section_id"]]["extra"].setdefault("pwa004_rejection_explanations", []).append(deepcopy(value))
    review["rejection_explanations"] = explanations
    return summary, workspace, before


def test_d09_legacy_review_keeps_body_and_marks_explanations_unavailable(tmp_path):
    summary, workspace, before = _d12_historical_review(tmp_path)
    original = deepcopy(summary["generated_draft"])
    persisted = _persist_fake(summary, trusted_workspace=workspace, raise_on_validation_error=False)
    findings = json.loads(Path(persisted["review_findings_path"]).read_text(encoding="utf-8"))
    assert findings["rejection_explanations_status"] == "UNAVAILABLE_LEGACY"
    assert findings["rejection_explanations"] == []
    assert findings["rejections"] == original["extra"]["pwa004_review_draft"]["rejections"]
    assert Path(persisted["review_draft_path"]).read_bytes() == original["full_text"].encode("utf-8")
    _d12_assert_history_unchanged(before)


@pytest.mark.parametrize("strict", [False, True], ids=["ordinary", "strict"])
def test_d09_cli_projection_delivers_each_original_occurrence(tmp_path, strict, monkeypatch, capsys):
    summary, workspace, before = _d09_projection_fixture(tmp_path)
    original = deepcopy(summary["generated_draft"])
    public, exit_code, output = _d12_cli(summary, workspace, monkeypatch, capsys, strict=strict)
    assert exit_code == (1 if strict else 0)
    assert public["status"] == "TECHNICAL_FAILURE"
    findings = json.loads(Path(public["review_artifacts"]["review_findings_json"]).read_text(encoding="utf-8"))
    review = original["extra"]["pwa004_review_draft"]
    assert findings["rejection_explanations_status"] == "AVAILABLE"
    assert findings["rejections"] == review["rejections"]
    assert findings["rejection_explanations"] == review["rejection_explanations"]
    assert len(findings["rejections"]) == len(findings["rejection_explanations"]) == 4
    assert len({value["claim_text"] for value in findings["rejection_explanations"]}) == 1
    assert len({value["claim_ref"]["claim_id"] for value in findings["rejection_explanations"]}) == 4
    assert Path(public["review_artifacts"]["review_draft_markdown"]).read_bytes() == original["full_text"].encode("utf-8")
    assert summary["generated_draft"] == original
    assert public["terminal_outcome"] == summary["terminal_outcome"]
    assert findings["formal_release_authorized"] is False
    assert "input_material" not in output and "settings" not in output
    _d12_assert_history_unchanged(before)


@pytest.mark.parametrize("mutation", [
    "wrong_run", "wrong_section", "wrong_attempt", "wrong_claim", "wrong_occurrence", "wrong_candidate",
    "wrong_fingerprint", "wrong_binding", "wrong_support", "unauthorized_evidence",
    "unknown_top", "nested_unknown", "binding_nested_unknown", "sensitive_field", "sensitive_text", "external_path", "missing_row",
    "extra_row", "empty_new_list", "summary_explanations_missing", "source_receipt_absent", "source_receipt_changed",
    "wrong_quote", "wrong_offset", "changed_evidence", "false_quality_verdict", "changed_hash",
])
def test_d09_cli_explanation_identity_and_scope_fail_closed(tmp_path, mutation):
    from core.hashing import canonical_sha256
    summary, workspace, before = _d09_projection_fixture(tmp_path)
    draft = summary["generated_draft"]
    review = draft["extra"]["pwa004_review_draft"]
    value = review["rejection_explanations"][0]
    section = next(s for s in draft["sections"] if s["section_id"] == value["section_id"])
    if mutation == "wrong_run": value["run_id"] = "foreign_run"
    elif mutation == "wrong_section": value["section_id"] = "foreign_section"
    elif mutation == "wrong_attempt": value["attempt_id"] = "foreign_attempt"
    elif mutation == "wrong_claim": value["claim_ref"]["claim_id"] = "claim://sha256/" + "0" * 64
    elif mutation == "wrong_occurrence": value["claim_occurrence_ref"]["end_offset"] += 1
    elif mutation == "wrong_candidate": value["candidate_ref"] = "artifact://foreign/candidate"
    elif mutation == "wrong_fingerprint": value["verifier_semantic_input_sha256"] = "0" * 64
    elif mutation == "wrong_binding": value["binding_facts_ref"]["binding_facts_id"] = "foreign-binding"
    elif mutation == "wrong_support": value["support_ref"]["claim_evidence_support_id"] = "foreign-support"
    elif mutation == "unauthorized_evidence": value["evidence"][0]["exact_source_ref"] = "evidence-item://sha256/" + "0" * 64
    elif mutation == "unknown_top": value["settings"] = {"api_key": "D09_PRIVATE_SENTINEL"}
    elif mutation == "nested_unknown": value["evidence"][0]["debug"] = {"prompt": "D09_PRIVATE_SENTINEL"}
    elif mutation == "binding_nested_unknown": value["binding_facts"][0]["debug"] = {"api_key": "D09_PRIVATE_SENTINEL"}
    elif mutation == "sensitive_field": value["shared_support"]["api_key"] = "D09_PRIVATE_SENTINEL"
    elif mutation == "sensitive_text": value["evidence"][0]["title"] = "api_key=D09_PRIVATE_SENTINEL"
    elif mutation == "external_path": value["input_ref"] = "C:/private/D09_PRIVATE_SENTINEL.json"
    elif mutation == "missing_row": review["rejection_explanations"].pop()
    elif mutation == "extra_row": review["rejection_explanations"].append(deepcopy(value))
    elif mutation == "empty_new_list": review["rejection_explanations"] = []
    elif mutation == "summary_explanations_missing": review.pop("rejection_explanations")
    elif mutation == "source_receipt_absent": section["extra"].pop("pwa004_rejection_explanations")
    elif mutation == "source_receipt_changed": section["extra"]["pwa004_rejection_explanations"][0]["input_content_hash"] = "sha256:" + "0" * 64
    elif mutation == "wrong_quote": value["claim_text"] = "D09 changed quote"
    elif mutation == "wrong_offset": value["start_offset"] += 1
    elif mutation == "changed_evidence": value["evidence"][0]["context_text"] += " D09_PRIVATE_SENTINEL"
    elif mutation == "false_quality_verdict": value["candidate_verdict"] = "REJECTED"
    elif mutation == "changed_hash": value["explanation_sha256"] = "0" * 64
    else: raise AssertionError(mutation)
    # Rehash and mirror malicious payloads so failures must reach semantic/
    # membership guards, rather than relying solely on a stale digest or twin.
    if mutation not in {"changed_hash", "summary_explanations_missing", "source_receipt_absent", "source_receipt_changed"}:
        for explanation in review["rejection_explanations"]:
            explanation["explanation_sha256"] = canonical_sha256({k: v for k, v in explanation.items() if k != "explanation_sha256"})
        section["extra"]["pwa004_rejection_explanations"] = deepcopy(review["rejection_explanations"])
    persisted = _persist_fake(summary, trusted_workspace=workspace, raise_on_validation_error=False)
    assert persisted["validation_error"] is not None
    assert persisted["review_draft_path"] is None
    assert persisted["review_findings_path"] is None
    assert not list(workspace.runs_dir.glob("*_review_findings.json"))
    assert not list(workspace.runs_dir.glob("*_review_draft.md"))
    assert "D09_PRIVATE_SENTINEL" not in json.dumps(persisted)
    _d12_assert_history_unchanged(before)
