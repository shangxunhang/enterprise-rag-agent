# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_citation、test_document_citation_registry_deduplicates_sources_and_allocates_global_ids、test_section_query_for_security_is_specific_and_recovery_is_stricter、test_fake_mainline_can_explicitly_enable_section_aware_retrieval、_minimal_trace_events、test_console_validation_allows_only_business_gate_failure、test_console_validation_rejects_technical_failure_even_in_interactive_mode。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
import os
from dataclasses import replace
from types import SimpleNamespace

from apps.enterprise_document.services.scheme_writer.evidence_service import (
    DocumentCitationRegistry,
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
)
from core.config import get_settings
from schemas.citation import CitationSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus
from run_demo import run_demo
from mainline_runtime import build_project_input


# 阅读注释（函数）：处理 引用 相关逻辑。
def _citation(citation_id: str, *, chunk_id: str, quote: str) -> CitationSchema:
    """处理 引用 相关逻辑。

    参数:
        citation_id: 引用 标识，具体约束请结合类型标注和调用方确认。
        chunk_id: 文本块 标识，具体约束请结合类型标注和调用方确认。
        quote: quote，具体约束请结合类型标注和调用方确认。

    返回:
        CitationSchema

    阅读提示:
        主要直接调用：CitationSchema。
    """
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


# 阅读注释（函数）：处理 测试 文档 引用 注册表 deduplicates sources and allocates global 标识集合 相关逻辑。
def test_document_citation_registry_deduplicates_sources_and_allocates_global_ids() -> None:
    """处理 测试 文档 引用 注册表 deduplicates sources and allocates global 标识集合 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：DocumentCitationRegistry, registry.register, _citation, registry.all。
    """
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


# 阅读注释（函数）：处理 测试 章节 查询 for security is specific and recovery is stricter 相关逻辑。
def test_section_query_for_security_is_specific_and_recovery_is_stricter() -> None:
    """处理 测试 章节 查询 for security is specific and recovery is stricter 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_project_input, SchemeEvidenceService._build_section_query。
    """
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


# 阅读注释（函数）：处理 测试 fake 主链 can explicitly enable 章节 aware 检索 相关逻辑。
def test_insufficient_evidence_section_blocks_normal_generation() -> None:
    service = object.__new__(SectionGenerationService)
    service.runtime_support = SchemeWriterRuntimeSupport()
    project_input = build_project_input(
        "task_insufficient",
        "生成一个政务云建设方案",
        allow_demo_defaults=True,
    )

    section = service._build_insufficient_evidence_section(
        SimpleNamespace(task_id="task_insufficient", run_id="run_insufficient"),
        project_input=project_input,
        section_title="安全设计",
        section_order=6,
        rag_context=RAGContextSchema(context_text="", used_context_chars=0),
        citations=[],
        assessment={
            "status": "insufficient",
            "details": {
                "final_assessment": {
                    "reason": "retrieval confidence below threshold"
                }
            },
        },
    )

    assert section.status == ExecutionStatus.PARTIAL_SUCCESS
    assert section.model_output == ""
    assert "证据不足" in section.content
    assert section.extra["generation_blocked"] is True
    assert section.extra["generation_block_reason"] == "evidence_insufficient"
    assert section.eval_result.checks["normal_generation_blocked"] is True
    assert section.eval_result.checks["evidence_sufficient"] is False


def test_fake_mainline_can_explicitly_enable_section_aware_retrieval(tmp_path) -> None:
    """处理 测试 fake 主链 can explicitly enable 章节 aware 检索 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：os.environ.get, get_settings, replace, model_dump, build_project_input, update, run_demo, old_env.items。
    """
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
    rag_started = [
        item for item in trace_events if item.get("event_type") == "rag_started"
    ]
    assert len(rag_started) == 1 + len(required)


# 阅读注释（函数）：处理 minimal Trace events 相关逻辑。
def _minimal_trace_events() -> list[dict]:
    """处理 minimal Trace events 相关逻辑。

    返回:
        list[dict]
    """
    return [
        {"event_type": "run_started", "component_type": "runtime"},
        {"event_type": "workflow_started", "component_type": "workflow"},
        {
            "event_type": "agent_started",
            "component_type": "agent",
            "input_summary": {"graph_state_schema": "graph_state_v1"},
        },
        {
            "event_type": "rag_started",
            "component_type": "rag",
            "component_name": "FakeRAGService",
        },
        {
            "event_type": "rag_finished",
            "component_type": "rag",
            "component_name": "FakeRAGService",
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


# 阅读注释（函数）：处理 测试 console validation allows only business gate failure 相关逻辑。
def test_console_validation_allows_only_business_gate_failure(tmp_path) -> None:
    """处理 测试 console validation allows only business gate failure 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：trace_path.write_text, join, json.dumps, _minimal_trace_events, task_state.write_text, str, _validate_end_to_end。
    """
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


# 阅读注释（函数）：处理 测试 console validation rejects technical failure even in interactive mode 相关逻辑。
def test_console_validation_rejects_technical_failure_even_in_interactive_mode(
    tmp_path,
) -> None:
    """处理 测试 console validation rejects technical failure even in interactive mode 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：trace_path.write_text, join, json.dumps, _minimal_trace_events, task_state.write_text, str, pytest.raises, _validate_end_to_end。
    """
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
