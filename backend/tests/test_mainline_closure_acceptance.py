# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_bundle_by_title、_trace_events、test_corrective_retrieval_branch_runs_and_recovers、test_document_citation_registry_resolves_local_id_collisions、test_business_failure_preserves_partial_answer_and_report、test_step_16_audit_identifies_noop_gates_and_coarse_scheme_writer、test_request_level_technical_failure_is_preserved_without_reraising。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

from run_demo import persist_end_to_end_artifacts
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
    assert safety["recovery_count"] == 1
    assert safety["retrieval_scope"] == "recovery"
    assert len(safety["tool_call_ids"]) == 3
    assert "可绑定引用" in safety["query"]

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

    bundles = _bundle_by_title(summary)
    required_ids = {
        title: [item["citation_id"] for item in bundles[title]["citations"]]
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
    rounds = safety["metadata"]["self_rag_retrieval_rounds"]
    assert len(rounds) == 1
    assert rounds[0]["success"] is True
    assert rounds[0]["check_before"]["need_retrieve_more"] is True
    assert rounds[0]["check_after"]["need_retrieve_more"] is False
    assert safety["retrieval_scope"] == "self_rag_recovery"

    sections = (summary.get("scheme_writer_output") or {}).get("scheme_draft", {}).get(
        "sections", []
    )
    safety_section = next(
        item for item in sections if item.get("section_title") == "安全设计"
    )
    assert safety_section["extra"]["workflow_budget"]["retrieval_rounds"] == 1



# 阅读注释（函数）：处理 测试 business failure preserves partial answer and report 相关逻辑。
def test_business_failure_preserves_partial_answer_and_report(tmp_path) -> None:
    """处理 测试 business failure preserves partial answer and report 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：run_fake_mainline_scenario, persist_end_to_end_artifacts, Path, answer_path.is_file, strip, answer_path.read_text, report_path.is_file, json.loads。
    """
    summary = run_fake_mainline_scenario(
        tmp_path / "business_failure",
        run_id="run_business_gate_failure",
        rag_scenario="business_gate_failure",
        llm_scenario="force_business_gate_failure",
        enable_corrective_section_retrieval=True,
    )

    assert summary["status"] == "failed"
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

    answer_path = Path(persisted["answer_path"])
    report_path = Path(persisted["report_path"])
    assert answer_path.is_file()
    assert answer_path.read_text(encoding="utf-8").strip()
    assert report_path.is_file()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["business_gate_failure"] is True
    assert report["hard_gate"]["passed"] is False
    assert report["metrics"]["failed_section_count"] >= 1
    assert report["metrics"]["corrective_retrieval_count"] >= 1
    assert report["paths"]["answer_markdown"] == str(answer_path)
    assert report["paths"]["e2e_report"] == str(report_path)


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
        "scheme_draft": {"full_text": ""},
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
    assert Path(persisted["answer_path"]).is_file()
