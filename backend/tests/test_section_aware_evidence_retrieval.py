from __future__ import annotations

import json
import os
from dataclasses import replace

from apps.enterprise_document.services.scheme_writer.evidence_service import (
    DocumentCitationRegistry,
    SchemeEvidenceService,
)
from core.config import get_settings
from schemas.citation import CitationSchema
from run_demo_back import run_demo
from mainline_runtime import build_project_input


def _citation(citation_id: str, *, chunk_id: str, quote: str) -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        doc_id="doc_1",
        source_document_id="doc_1",
        child_chunk_id=chunk_id,
        chunk_id=chunk_id,
        title="政务云安全规范",
        section="安全设计",
        quote_text=quote,
    )


def test_document_citation_registry_deduplicates_sources_and_allocates_global_ids() -> None:
    registry = DocumentCitationRegistry()
    first, first_map = registry.register(
        [_citation("local_C1", chunk_id="chunk_1", quote="采用统一身份认证。")],
        scope="document",
        query="政务云建设方案",
    )
    second, second_map = registry.register(
        [
            _citation("C1", chunk_id="chunk_1", quote="采用统一身份认证。"),
            _citation("C2", chunk_id="chunk_2", quote="敏感数据应加密存储。"),
        ],
        scope="section",
        query="政务云安全设计",
    )

    assert first[0].citation_id == "C1"
    assert first_map == {"local_C1": "C1"}
    assert second_map == {"C1": "C1", "C2": "C2"}
    assert [item.citation_id for item in second] == ["C1", "C2"]
    assert [item.citation_id for item in registry.all()] == ["C1", "C2"]
    assert registry.all()[0].extra["retrieval_scopes"] == ["document", "section"]


def test_section_query_for_security_is_specific_and_recovery_is_stricter() -> None:
    project_input = build_project_input(
        "task_query",
        "生成一个政务云建设方案",
        allow_demo_defaults=True,
    )
    normal = SchemeEvidenceService._build_section_query(
        project_input, "安全设计", recovery=False
    )
    recovery = SchemeEvidenceService._build_section_query(
        project_input, "安全设计", recovery=True
    )

    assert "身份认证" in normal
    assert "敏感数据保护" in normal
    assert "日志审计" in normal
    assert "可绑定引用" in recovery
    assert normal != recovery


def test_fake_mainline_can_explicitly_enable_section_aware_retrieval(tmp_path) -> None:
    old_env = {
        key: os.environ.get(key)
        for key in (
            "USE_REAL_RAG_TOOL",
            "ENABLE_AGENT_SELF_RAG",
            "ENABLE_SEMANTIC_GATE",
        )
    }
    try:
        os.environ["USE_REAL_RAG_TOOL"] = "false"
        os.environ["ENABLE_AGENT_SELF_RAG"] = "false"
        os.environ["ENABLE_SEMANTIC_GATE"] = "false"
        base = get_settings(reload=True)
        settings = replace(
            base,
            data_root=tmp_path,
            run_trace_dir=tmp_path / "runs",
            data_capture_dir=tmp_path / "captures",
            eval_output_dir=tmp_path / "eval_outputs",
            task_state_dir=tmp_path / "tasks",
            default_model_name="fake_llm",
            supervisor_model_name="fake_llm",
            enable_llm_routing=False,
            trace_enabled=True,
            data_capture_enabled=True,
        )
        project_input = build_project_input(
            "task_section_aware",
            "生成一个政务云建设方案",
            allow_demo_defaults=True,
        ).model_dump()
        project_input["generation_requirements"]["extra"].update(
            {
                "enable_section_aware_retrieval": True,
                "enable_corrective_section_retrieval": False,
            }
        )

        summary = run_demo(
            user_input="生成一个政务云建设方案",
            run_id="run_section_aware",
            task_id="task_section_aware",
            output_root=tmp_path,
            clean_existing=True,
            settings=settings,
            retrieval_strategy="hybrid",
            enable_agent_self_rag=False,
            project_input=project_input,
            allow_demo_defaults=False,
        )
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings(reload=True)

    output = summary["scheme_writer_output"]
    bundles = output["section_evidence"]
    required = set(project_input["generation_requirements"]["citation_required_sections"])
    by_title = {item["section_title"]: item for item in bundles}

    assert output["extra"]["section_aware_retrieval_enabled"] is True
    assert len(bundles) == len(project_input["generation_requirements"]["required_sections"])
    assert required
    assert all(by_title[title]["retrieval_scope"] == "section" for title in required)
    assert all(len(by_title[title]["tool_call_ids"]) == 2 for title in required)
    assert [item["citation_id"] for item in output["citations"]] == ["C1"]

    trace_events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "run_section_aware_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    tool_started = [
        item for item in trace_events if item.get("event_type") == "tool_started"
    ]
    assert len(tool_started) == 1 + len(required)


def _minimal_trace_events() -> list[dict]:
    return [
        {"event_type": "run_started", "component_type": "runtime"},
        {"event_type": "workflow_started", "component_type": "workflow"},
        {
            "event_type": "agent_started",
            "component_type": "agent",
            "input_summary": {"graph_state_schema": "graph_state_v1"},
        },
        {
            "event_type": "tool_started",
            "component_type": "tool",
            "component_name": "FakeRAGTool",
        },
        {
            "event_type": "tool_finished",
            "component_type": "tool",
            "component_name": "FakeRAGTool",
        },
        {
            "event_type": "model_started",
            "component_type": "model",
            "component_name": "fake_llm",
            "model_name": "fake_llm",
            "input_summary": {"context_package_id": "ctx_1"},
        },
        {
            "event_type": "model_finished",
            "component_type": "model",
            "component_name": "fake_llm",
            "model_name": "fake_llm",
        },
        {"event_type": "agent_finished", "component_type": "agent"},
        {"event_type": "workflow_finished", "component_type": "workflow"},
        {"event_type": "run_finished", "component_type": "runtime"},
    ]


def test_console_validation_allows_only_business_gate_failure(tmp_path) -> None:
    from run_demo import _validate_end_to_end

    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in _minimal_trace_events()),
        encoding="utf-8",
    )
    task_state = tmp_path / "task.jsonl"
    task_state.write_text("{}\n", encoding="utf-8")
    summary = {
        "status": "failed",
        "scheme_draft": {"full_text": "部分方案正文"},
        "scheme_writer_output": {
            "hard_gate": {"passed": False},
            "error": {"error_code": "DOCUMENT_HARD_GATE_FAILED"},
        },
        "paths": {"trace": str(trace_path), "task_state": str(task_state)},
    }

    result = _validate_end_to_end(
        summary,
        expected_model_name="fake_llm",
        expected_real_rag=False,
        allow_business_failure=True,
    )
    assert result["business_gate_passed"] is False
    assert result["business_gate_failure"] is True


def test_console_validation_rejects_technical_failure_even_in_interactive_mode(
    tmp_path,
) -> None:
    import pytest
    from run_demo import _validate_end_to_end

    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in _minimal_trace_events()),
        encoding="utf-8",
    )
    task_state = tmp_path / "task.jsonl"
    task_state.write_text("{}\n", encoding="utf-8")
    summary = {
        "status": "failed",
        "scheme_draft": {"full_text": "部分方案正文"},
        "scheme_writer_output": {
            "error": {"error_code": "MODEL_RUNTIME_FAILED"},
        },
        "paths": {"trace": str(trace_path), "task_state": str(task_state)},
    }

    with pytest.raises(RuntimeError):
        _validate_end_to_end(
            summary,
            expected_model_name="fake_llm",
            expected_real_rag=False,
            allow_business_failure=True,
        )
