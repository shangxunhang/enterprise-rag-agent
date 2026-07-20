# =============================================================================
# 中文阅读说明：端到端演示入口：装配配置和依赖，构造示例 ProjectInput，运行 Agent-RAG 主链并输出结果。
# 主要定义：_normalize_status_value、_effective_runtime_error、_is_business_gate_failure、_resolve_console_question、_configure_runtime、_preflight_real_runtime、_load_trace_events、_validate_end_to_end、_write_answer_file、_section_report_rows等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run the Agent-RAG end-to-end console demonstration.

PyCharm users can run this file directly with no parameters. The script then
prompts for one user requirement and executes the complete native mainline:

user input -> ProjectInput -> Supervisor -> WorkflowEngine -> Agent nodes
-> real RAG -> Context Manager -> local LLM -> quality gates -> final document
-> Trace/DataCapture artifacts.

``run_pipeline.py`` remains the production-style non-interactive entry point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
for import_root in (SCRIPT_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from core.config import AppSettings, get_settings
from mainline_runtime import (
    MAINLINE_RUNTIME_VERSION,
    build_project_input,
    build_supervisor,
    build_task,
    run_mainline,
    resolve_project_path,
)
from rag.offline.resolver import ActiveIndexResolver

RUN_DEMO_VERSION = f"{MAINLINE_RUNTIME_VERSION}+e2e-console-v5"
DEFAULT_USER_INPUT = "根据资料生成企业级 RAG-Agent 系统建设方案"
REAL_MODEL_NAME = "local_qwen2_5_7b_gptq_int4"
SUCCESS_LIKE_STATUSES = {"success", "partial_success"}


# 阅读注释（函数）：规范化 状态 value。
def _normalize_status_value(value: Any) -> str:
    """Return a canonical lower-case execution status from enums or strings."""

    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip().lower()
    if text.startswith("executionstatus."):
        text = text.split(".", 1)[1]
    return text


# 阅读注释（函数）：处理 effective 运行时 错误 相关逻辑。
def _effective_runtime_error(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Return an error only when the final runtime status is actually failed."""

    final_status = _normalize_status_value(summary.get("status"))
    if final_status in SUCCESS_LIKE_STATUSES:
        return {}

    supervisor_result = summary.get("supervisor_result") or {}
    error = supervisor_result.get("error") or {}
    if error:
        return error

    sub_results = (supervisor_result.get("result") or {}).get("sub_agent_results") or []
    failed_sub_results = [
        item
        for item in sub_results
        if _normalize_status_value(item.get("status")) not in SUCCESS_LIKE_STATUSES
    ]
    if not failed_sub_results:
        return {}

    first_failed = failed_sub_results[0]
    return first_failed.get("error") or {
        "error_code": "SUB_AGENT_FAILED",
        "error_type": "AgentFailure",
        "message": first_failed.get("error_message") or "子 Agent 执行失败。",
        "failed_node": first_failed.get("agent_name"),
        "retryable": False,
    }




# 阅读注释（函数）：判断 business gate failure。 判断业务门控是否失败
def _is_business_gate_failure(summary: Dict[str, Any]) -> bool:
    """判断 business gate failure。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：summary.get, output.get, _effective_runtime_error, hard_gate.get, str, error.get。
    """
    output = summary.get("scheme_writer_output") or {}
    hard_gate = output.get("hard_gate") or {}
    error = output.get("error") or _effective_runtime_error(summary)
    return (
        hard_gate.get("passed") is False
        or str(error.get("error_code") or "") == "DOCUMENT_HARD_GATE_FAILED"
    )

# 阅读注释（函数）：解析并确定 console question。   处理用户输入
def _resolve_console_question(cli_value: Optional[str]) -> str:
    """Resolve one non-empty question from CLI or an interactive console."""

    question = str(cli_value or "").strip()
    if not question:
        question = input("\n请输入建设方案生成需求：\n> ").strip()
    if not question:
        raise ValueError("用户输入不能为空")
    return question


# 阅读注释（函数）：处理 configure 运行时 相关逻辑。
def _configure_runtime(*, fake_runtime: bool) -> None:
    """Make the console entry explicit about real versus fake dependencies.

    Environment variables outrank ``.env`` in ``core.config``. Therefore the
    direct PyCharm run path is deterministic and cannot silently use FakeRAG or
    FakeLLM when the user expects an end-to-end real run.
    """

    project_root = resolve_project_path(".")
    os.environ["RAG_PROJECT_ROOT"] = str(project_root)
    os.environ["ENABLE_LLM_ROUTING"] = "false"

    if fake_runtime:
        os.environ["USE_REAL_RAG_TOOL"] = "false"
        os.environ["DEFAULT_MODEL_NAME"] = "fake_llm"
        os.environ["SUPERVISOR_MODEL_NAME"] = "fake_llm"
        return

    os.environ["USE_REAL_RAG_TOOL"] = "true"
    os.environ["DEFAULT_MODEL_NAME"] = REAL_MODEL_NAME
    os.environ["SUPERVISOR_MODEL_NAME"] = REAL_MODEL_NAME


# 阅读注释（函数）：处理 preflight real 运行时 相关逻辑。
def _preflight_real_runtime(settings: AppSettings) -> Dict[str, Any]:
    """Fail before model loading when the real runtime is not deployable."""

    if settings.default_model_name != settings.local_qwen_model_name:
        raise RuntimeError(
            "端到端测试要求DEFAULT_MODEL_NAME与LOCAL_QWEN_MODEL_NAME一致："
            f"default={settings.default_model_name!r}, "
            f"local={settings.local_qwen_model_name!r}"
        )
    if not settings.local_qwen_model_path.is_dir():
        raise FileNotFoundError(
            f"本地LLM目录不存在：{settings.local_qwen_model_path}"
        )

    pointer_path = settings.project_root / "data/processed/indexes/active_index.json"
    index_info = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=True,
    ).resolve(pointer_path)

    static_spec = Path(
        os.getenv(
            "RAG_STATIC_RETRIEVAL_SPEC_FILE",
            "backend/rag/config/static_retrieval_v1.yaml",
        )
    ).expanduser()
    if not static_spec.is_absolute():
        static_spec = settings.project_root / static_spec
    static_spec = static_spec.resolve()
    if not static_spec.is_file():
        raise FileNotFoundError(f"RAG 静态检索规格不存在：{static_spec}")

    return {
        "mode": "real",
        "model_name": settings.default_model_name,
        "model_path": str(settings.local_qwen_model_path),
        "model_device": settings.local_qwen_device,
        "active_index_version": index_info["index_version"],
        "active_index_backend": index_info["backend"],
        "active_index_collection": index_info["collection_name"],
        "embedding_model": index_info["embedding_model"],
        "static_retrieval_spec": str(static_spec),
    }


# 阅读注释（函数）：加载 Trace events。
def _load_trace_events(trace_path: str | Path) -> list[Dict[str, Any]]:
    """加载 Trace events。

    参数:
        trace_path: Trace 路径，具体约束请结合类型标注和调用方确认。

    返回:
        list[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, path.is_file, FileNotFoundError, enumerate, splitlines, path.read_text, raw_line.strip, json.loads。
    """
    path = Path(trace_path)
    if not path.is_file():
        raise FileNotFoundError(f"Trace文件不存在：{path}")
    events: list[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Trace第{line_number}行不是合法JSON") from exc
        if isinstance(payload, dict):
            events.append(payload)
    return events


# 阅读注释（函数）：校验 end to end。
def _validate_end_to_end(
    summary: Dict[str, Any],
    *,
    expected_model_name: str,
    expected_real_rag: bool,
    allow_business_failure: bool = False,
) -> Dict[str, Any]:
    """Validate that the public entry actually traversed the whole mainline."""

    status = _normalize_status_value(summary.get("status"))
    business_gate_passed = status in SUCCESS_LIKE_STATUSES
    business_gate_failure = _is_business_gate_failure(summary)
    if not business_gate_passed:
        if not allow_business_failure or not business_gate_failure:
            error = _effective_runtime_error(summary)
            error_type = str(error.get("error_type") or "RuntimeFailure")
            message = str(
                error.get("message")
                or error.get("error_message")
                or f"主链终态不是成功：{status}"
            )
            failed_node = str(error.get("failed_node") or "unknown")
            raise RuntimeError(
                f"主链技术失败：{error_type}: {message} (failed_node={failed_node})"
            )

    scheme_draft = summary.get("scheme_draft") or {}
    content = str(
        scheme_draft.get("full_text")
        or scheme_draft.get("content")
        or ""
    ).strip()
    if not content:
        raise RuntimeError("主链已执行但最终方案正文为空")

    paths = summary.get("paths") or {}
    for key in ("trace", "task_state"):
        value = paths.get(key)
        if not value or not Path(value).is_file():
            raise FileNotFoundError(f"端到端产物缺失：{key}={value}")

    events = _load_trace_events(paths["trace"])
    event_types = {str(event.get("event_type") or "") for event in events}
    required_event_types = {
        "run_started",
        "run_finished",
        "workflow_started",
        "workflow_finished",
        "agent_started",
        "agent_finished",
        "rag_started",
        "rag_finished",
        "model_started",
        "model_finished",
    }
    missing_event_types = sorted(required_event_types - event_types)
    if missing_event_types:
        raise RuntimeError(
            "Trace没有覆盖完整主链事件：" + ", ".join(missing_event_types)
        )

    tool_components = {
        str(event.get("component_name") or event.get("tool_name") or "")
        for event in events
        if event.get("component_type") == "tool"
    }
    rag_components = {
        str(event.get("component_name") or "")
        for event in events
        if event.get("component_type") == "rag"
    }
    if expected_real_rag and "RAGService" not in rag_components:
        raise RuntimeError(
            "端到端测试期望RAGService，实际RAG组件："
            + ", ".join(sorted(rag_components))
        )
    if not expected_real_rag and "FakeRAGService" not in rag_components:
        raise RuntimeError(
            "Fake测试期望FakeRAGService，实际RAG组件："
            + ", ".join(sorted(rag_components))
        )

    model_components = {
        str(
            event.get("model_name")
            or event.get("component_name")
            or ""
        )
        for event in events
        if event.get("component_type") == "model"
    }
    if expected_model_name not in model_components:
        raise RuntimeError(
            f"端到端测试未观察到模型{expected_model_name!r}，实际模型："
            + ", ".join(sorted(model_components))
        )

    graph_events = [
        event
        for event in events
        if (event.get("input_summary") or {}).get("graph_state_schema")
        == "graph_state_v1"
    ]
    if not graph_events:
        raise RuntimeError("Trace中没有观察到graph_state_v1节点输入")

    context_managed_model_calls = [
        event
        for event in events
        if event.get("event_type") == "model_started"
        and (
            (event.get("input_summary") or {}).get("context_package_id")
            or (event.get("input_summary") or {}).get("context_package")
            or (event.get("metadata") or {}).get("context_package_id")
        )
    ]
    # Step 14's compatibility policy annotates every model call, but older
    # payload layouts may place the identifier one level deeper. Only require
    # one explicit managed call here; the Step 14 acceptance remains stricter.
    if not context_managed_model_calls:
        for event in events:
            if event.get("event_type") != "model_started":
                continue
            serialized = json.dumps(event, ensure_ascii=False)
            if "context_package" in serialized:
                context_managed_model_calls.append(event)
    if not context_managed_model_calls:
        raise RuntimeError("Trace中没有观察到Context Package关联的模型调用")

    return {
        "status": status,
        "task_status": status,
        "execution_integrity": "passed",
        "business_quality": (
            "passed" if business_gate_passed else "failed"
        ),
        "business_gate_passed": business_gate_passed,
        "business_gate_failure": business_gate_failure,
        "content_chars": len(content),
        "trace_event_count": len(events),
        "tool_components": sorted(tool_components),
        "rag_components": sorted(rag_components),
        "model_components": sorted(model_components),
        "graph_node_event_count": len(graph_events),
        "context_managed_model_call_count": len(context_managed_model_calls),
    }


# 阅读注释（函数）：写入 answer 文件。
def _write_answer_file(summary: Dict[str, Any], content: str) -> Path:
    """写入 answer 文件。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。
        content: 待处理内容。

    返回:
        Path

    阅读提示:
        主要直接调用：Path, trace_path.with_name, answer_path.parent.mkdir, answer_path.write_text。
    """
    trace_path = Path(summary["paths"]["trace"])
    answer_path = trace_path.with_name(f"{summary['run_id']}_answer.md")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text(content, encoding="utf-8")
    return answer_path


# 阅读注释（函数）：处理 章节 report rows 相关逻辑。
def _section_report_rows(summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    """处理 章节 report rows 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        list[Dict[str, Any]]

    阅读提示:
        主要直接调用：summary.get, str, item.get, output.get, isinstance, draft.get, section.get, bundles.get。
    """
    draft = summary.get("scheme_draft") or {}
    output = summary.get("scheme_writer_output") or {}
    bundles = {
        str(item.get("section_title") or ""): item
        for item in (output.get("section_evidence") or [])
        if isinstance(item, dict)
    }
    rows: list[Dict[str, Any]] = []
    for section in draft.get("sections") or []:
        title = str(section.get("section_title") or "")
        bundle = bundles.get(title) or {}
        eval_result = section.get("eval_result") or {}
        rows.append(
            {
                "section_id": section.get("section_id"),
                "section_title": title,
                "status": _normalize_status_value(section.get("status")),
                "content_chars": len(str(section.get("content") or "")),
                "citation_count": len(section.get("citation_bindings") or []),
                "citation_ids": list(section.get("citation_ids") or []),
                "evidence_scope": (section.get("extra") or {}).get("evidence_scope")
                or bundle.get("retrieval_scope"),
                "evidence_query": (section.get("extra") or {}).get("evidence_query")
                or bundle.get("query"),
                "recovery_count": int(bundle.get("recovery_count") or 0),
                "tool_call_ids": list(bundle.get("tool_call_ids") or []),
                "failures": list(eval_result.get("failures") or []),
                "warnings": list(eval_result.get("warnings") or []),
            }
        )
    return rows


# 阅读注释（函数）：构建 e2e report。
def _build_e2e_report(
    summary: Dict[str, Any],
    *,
    runtime_preflight: Dict[str, Any],
    validation: Optional[Dict[str, Any]],
    validation_error: Optional[Exception] = None,
) -> Dict[str, Any]:
    """构建 e2e report。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。
        runtime_preflight: 运行时 preflight，具体约束请结合类型标注和调用方确认。
        validation: validation，具体约束请结合类型标注和调用方确认。
        validation_error: validation 错误，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：summary.get, output.get, _section_report_rows, get, is_file, Path, _load_trace_events, str。
    """
    output = summary.get("scheme_writer_output") or {}
    hard_gate = output.get("hard_gate") or {}
    sections = _section_report_rows(summary)
    trace_events: list[Dict[str, Any]] = []
    trace_path = (summary.get("paths") or {}).get("trace")
    if trace_path and Path(trace_path).is_file():
        trace_events = _load_trace_events(trace_path)
    event_counts: Dict[str, int] = {}
    for event in trace_events:
        event_type = str(event.get("event_type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    error = _effective_runtime_error(summary)
    execution_integrity = (
        str((validation or {}).get("execution_integrity") or "")
        or ("failed" if validation_error is not None else "unknown")
    )
    business_quality = (
        str((validation or {}).get("business_quality") or "")
        or (
            "failed"
            if _is_business_gate_failure(summary)
            else "unknown"
        )
    )
    return {
        "schema_version": "mainline_e2e_report_v1",
        "run_demo_version": RUN_DEMO_VERSION,
        "task_id": summary.get("task_id"),
        "run_id": summary.get("run_id"),
        "status": _normalize_status_value(summary.get("status")),
        "task_status": _normalize_status_value(summary.get("status")),
        "execution_integrity": execution_integrity,
        "business_quality": business_quality,
        "business_gate_failure": _is_business_gate_failure(summary),
        "runtime_preflight": runtime_preflight,
        "validation": validation,
        "validation_error": (
            {
                "type": validation_error.__class__.__name__,
                "message": str(validation_error),
            }
            if validation_error is not None
            else None
        ),
        "hard_gate": hard_gate,
        "error": error or None,
        "metrics": {
            "section_count": len(sections),
            "successful_section_count": sum(
                1 for item in sections if item["status"] == "success"
            ),
            "partial_section_count": sum(
                1 for item in sections if item["status"] == "partial_success"
            ),
            "failed_section_count": sum(
                1
                for item in sections
                if item["status"] not in SUCCESS_LIKE_STATUSES
            ),
            "citation_count": len(output.get("citations") or []),
            "section_evidence_bundle_count": len(output.get("section_evidence") or []),
            "corrective_retrieval_count": sum(
                int(item.get("recovery_count") or 0)
                for item in (output.get("section_evidence") or [])
            ),
            "trace_event_count": len(trace_events),
            "rag_call_count": event_counts.get("rag_started", 0),
            "model_call_count": event_counts.get("model_started", 0),
            "error_event_count": sum(
                1
                for item in trace_events
                if str(item.get("phase") or "") == "error"
            ),
        },
        "event_type_counts": event_counts,
        "sections": sections,
        "paths": dict(summary.get("paths") or {}),
    }


# 阅读注释（函数）：写入 e2e report。
def _write_e2e_report(
    summary: Dict[str, Any],
    report: Dict[str, Any],
    *,
    explicit_path: Optional[str | Path] = None,
) -> Path:
    """写入 e2e report。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。
        report: report，具体约束请结合类型标注和调用方确认。
        explicit_path: explicit 路径，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：resolve, expanduser, Path, trace_path.with_name, report_path.parent.mkdir, report_path.write_text, json.dumps。
    """
    if explicit_path is not None:
        report_path = Path(explicit_path).expanduser().resolve()
    else:
        trace_path = Path(summary["paths"]["trace"])
        report_path = trace_path.with_name(f"{summary['run_id']}_e2e_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


# 阅读注释（函数）：处理 persist end to end artifacts 相关逻辑。
def persist_end_to_end_artifacts(
    summary: Dict[str, Any],
    *,
    runtime_preflight: Dict[str, Any],
    expected_model_name: str,
    expected_real_rag: bool,
    report_path: Optional[str | Path] = None,
    raise_on_validation_error: bool = True,
) -> Dict[str, Any]:
    """Persist answer and report before surfacing validation failures.

    A business hard-gate failure is a valid observed result: partial content,
    Trace and the report are preserved.  Technical validation failures are
    written into the report and then re-raised.
    """

    draft = summary.get("scheme_draft") or {}
    content = str(draft.get("full_text") or draft.get("content") or "")
    answer_path = _write_answer_file(summary, content)
    summary.setdefault("paths", {})["answer_markdown"] = str(answer_path)

    validation: Optional[Dict[str, Any]] = None
    validation_error: Optional[Exception] = None
    try:
        validation = _validate_end_to_end(
            summary,
            expected_model_name=expected_model_name,
            expected_real_rag=expected_real_rag,
            allow_business_failure=True,
        )
    except Exception as exc:  # report first, then preserve technical failure
        validation_error = exc
    # 建立端到端结果报告
    report = _build_e2e_report(
        summary,
        runtime_preflight=runtime_preflight,
        validation=validation,
        validation_error=validation_error,
    )
    # 写入端到端结果
    persisted_report_path = _write_e2e_report(
        summary,
        report,
        explicit_path=report_path,
    )
    summary["paths"]["e2e_report"] = str(persisted_report_path)
    # 把更新后的完整产物路径复制进报告对象。
    # 这里包括 answer、trace、task_state、e2e_report 等路径。
    report["paths"] = dict(summary["paths"])

    # 第二次覆盖写入报告，使报告文件本身也包含自己的最终路径。
    _write_e2e_report(
        summary,
        report,
        explicit_path=persisted_report_path,
    )

    if validation_error is not None and raise_on_validation_error:
        raise validation_error
    return {
        "validation": validation or {},
        "validation_error": (
            {
                "type": validation_error.__class__.__name__,
                "message": str(validation_error),
            }
            if validation_error is not None
            else None
        ),
        "report": report,
        "answer_path": str(answer_path),
        "report_path": str(persisted_report_path),
    }


# 阅读注释（函数）：执行 run 演示 的主流程。
def run_demo(
    user_input: str = DEFAULT_USER_INPUT,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    output_root: Optional[str | Path] = None,
    clean_existing: bool = False,
    settings: Optional[AppSettings] = None,
    enable_agent_self_rag: Optional[bool] = None,
    project_input: Optional[Dict[str, Any]] = None,
    allow_demo_defaults: bool = True,
) -> Dict[str, Any]:
    """Run the demo adapter while delegating execution to the shared mainline."""

    return run_mainline(
        user_input=user_input,
        run_id=run_id,
        task_id=task_id,
        output_root=output_root,
        clean_existing=clean_existing,
        settings=settings,
        enable_agent_self_rag=enable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=allow_demo_defaults,
    )


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, _resolve_console_question, _configure_runtime, str, resolve_project_path, get_settings。
    """
    ## 创建一个命令行解析参数
    parser = argparse.ArgumentParser(
        description="Run one complete Agent-RAG console request."
    )
    parser.add_argument(
        "--user-input",
        type=str,
        default=None,
        help="User requirement. When omitted, prompt in the console.",
    )
    parser.add_argument(
        "--fake-runtime",
        action="store_true",
        help="Use FakeRAGTool and FakeLLM for a fast smoke test.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional fixed run_id. If omitted, a timestamp-based run_id is generated.",
    )
    parser.add_argument(
        "--output-root",
        type=str,
        default=None,
        help="Output root directory. Default: PROJECT_ROOT/data.",
    )
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="Delete existing output files for the same run_id before running.",
    )
    parser.add_argument(
        "--disable-agent-self-rag",
        action="store_true",
        help="Disable Agent-level Self-RAG check after SchemeWriter generation.",
    )
    parser.add_argument(
        "--rag-static-spec",
        type=str,
        default=None,
        help=(
            "YAML/JSON static retrieval specification. "
            "Overrides RAG_STATIC_RETRIEVAL_SPEC_FILE for this process."
        ),
    )
    parser.add_argument(
        "--project-input-file",
        type=str,
        default=None,
        help=(
            "Optional JSON file containing a complete ProjectInput. "
            "When omitted, the CLI builds a minimal validated ProjectInput "
            "from the console question."
        ),
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=0,
        help="Print only the first N answer characters; 0 prints the full answer.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat a business hard-gate failure as process exit code 1. "
            "Without this flag, interactive mode still prints and saves the partial result."
        ),
    )
    parser.add_argument(
        "--e2e-report-path",
        type=str,
        default=None,
        help="Optional explicit path for the structured E2E JSON report.",
    )

    args = parser.parse_args()
    user_input = _resolve_console_question(args.user_input)
    _configure_runtime(fake_runtime=args.fake_runtime)

    # 接下用户自己上传的rag配置
    if args.rag_static_spec:
        os.environ["RAG_STATIC_RETRIEVAL_SPEC_FILE"] = str(
            resolve_project_path(args.rag_static_spec)
        )

    settings = get_settings(reload=True)

    print("run_demo_file:", Path(__file__).resolve())
    print("run_demo_version:", RUN_DEMO_VERSION)
    print("project_root:", settings.project_root)
    print("data_root:", settings.data_root)
    print("prompt_root:", settings.prompt_root)
    print("user_input:", user_input)

    runtime_preflight: Dict[str, Any]
    # 调用fake 不使用真实rag
    if args.fake_runtime:
        runtime_preflight = {
            "mode": "fake",
            "model_name": settings.default_model_name,
            "rag_tool": "FakeRAGTool",
        }
    else:
        runtime_preflight = _preflight_real_runtime(settings)

    print("\nRuntime preflight:")
    print(json.dumps(runtime_preflight, ensure_ascii=False, indent=2))

    project_input = None

    # 读取结构化项目输入
    if args.project_input_file:
        project_input_path = Path(args.project_input_file).expanduser().resolve()
        if not project_input_path.is_file():
            raise FileNotFoundError(
                f"ProjectInput file not found: {project_input_path}"
            )
        with project_input_path.open("r", encoding="utf-8") as file:
            project_input = json.load(file)
        if not isinstance(project_input, dict):
            raise ValueError(
                "ProjectInput file must contain one JSON object at the top level."
            )

    # 调用run_demo函数并返回结果
    summary = run_demo(
        user_input=user_input,
        run_id=args.run_id,
        output_root=args.output_root,
        clean_existing=args.clean_existing,
        settings=settings,
        enable_agent_self_rag=not args.disable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=project_input is None,
    )
    # 生成主链的草稿
    scheme_draft = summary.get("scheme_draft") or {}

    content = str(
        scheme_draft.get("full_text")
        or scheme_draft.get("content")
        or ""
    )
    sections = scheme_draft.get("sections") or []
    first_section = sections[0] if sections else {}
    first_section_extra = first_section.get("extra") or {}
    #主链已经执行完后，保存产物并验证这次运行是否真的走完了端到端链路
    persisted = persist_end_to_end_artifacts(
        summary,
        runtime_preflight=runtime_preflight,
        expected_model_name=settings.default_model_name,
        expected_real_rag=not args.fake_runtime,
        report_path=args.e2e_report_path,
        raise_on_validation_error=args.strict,
    )
    # 证明的是这次请求是否真实走过了完整主链，而不是只返回了一个伪造结果
    validation = persisted["validation"]
    # 端到端验证过程中是否发现了技术性错误。
    validation_error = persisted.get("validation_error")

    print("\n" + "=" * 80)
    print("Agent-RAG端到端运行完成")
    print("=" * 80)
    print(f"Status: {summary['status']}")
    print(f"Task ID: {summary['task_id']}")
    print(f"Run ID: {summary['run_id']}")

    print("\nEnd-to-end validation:")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    if validation_error:
        print("\nValidation failure captured (artifacts were preserved):")
        print(json.dumps(validation_error, ensure_ascii=False, indent=2))

    print("\nOutput files:")
    for name, path in summary["paths"].items():
        print(f"- {name}: {path}")

    print("\nPrompt:")
    print(
        json.dumps(
            {
                "prompt_id": (
                    scheme_draft.get("prompt_id")
                    or first_section_extra.get("prompt_id")
                ),
                "prompt_name": scheme_draft.get("prompt_name"),
                "prompt_version": (
                    scheme_draft.get("prompt_version")
                    or first_section_extra.get("prompt_version")
                ),
                "first_section": first_section.get("section_title"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n" + "=" * 80)
    print("最终回答")
    print("=" * 80)
    if args.preview_chars > 0:
        print(content[: args.preview_chars])
    else:
        print(content)

    error = _effective_runtime_error(summary)
    if error:
        print("\nFailure details:")
        print(
            json.dumps(
                {
                    "error_code": error.get("error_code"),
                    "error_type": error.get("error_type"),
                    "message": error.get("message"),
                    "failed_node": error.get("failed_node"),
                    "retryable": error.get("retryable"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\nJSON summary:")
    print(
        json.dumps(
            {
                "task_id": summary["task_id"],
                "run_id": summary["run_id"],
                "status": summary["status"],
                "paths": summary["paths"],
                "runtime_preflight": runtime_preflight,
                "end_to_end_validation": validation,
                "validation_error": validation_error,
                "error": error or None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if (
        args.strict
        and _normalize_status_value(summary["status"]) not in SUCCESS_LIKE_STATUSES
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
