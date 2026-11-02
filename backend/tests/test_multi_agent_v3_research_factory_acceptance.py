"""W15-S01 positive section retrieval through the unmodified production factory."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.agent_rag_v1.tool_gateway import ToolGateway
from apps.enterprise_document.schemas.agent_rag_v1 import ToolResultStatus
from core.config import get_settings
from mainline_runtime import build_project_input
from test_phase7_document_closure_mainline import _InspectingFactory


def test_production_factory_executes_canonical_section_retrieval(tmp_path, monkeypatch) -> None:
    """A missing provider is a failed positive acceptance, never a passing substitute."""
    for name, value in {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    task_id = "task_w15_positive_section_retrieval"
    run_id = "run_w15_positive_section_retrieval"
    project = build_project_input(
        task_id, "生成政务云安全设计章节", allow_demo_defaults=True,
    ).model_dump(mode="json")
    project["generation_requirements"].update({
        "required_sections": ["安全设计"],
        "need_citation": True,
        "citation_required_sections": ["安全设计"],
    })
    project["generation_requirements"]["extra"].update({
        "enable_section_aware_retrieval": True,
        "enable_corrective_section_retrieval": False,
    })
    project["output_schema"]["required_sections"] = ["安全设计"]
    factory = _InspectingFactory()
    original_invoke = ToolGateway.invoke
    observed_calls = []

    def observed_invoke(gateway, context, **kwargs):
        result = original_invoke(gateway, context, **kwargs)
        if result.status is ToolResultStatus.SUCCESS:
            manager = gateway.budget_runtime.budget_manager
            used = manager.canonical_state().used.logical_tool_calls
            replay = original_invoke(gateway, context, **kwargs)
            assert replay.tool_operation_id == result.tool_operation_id
            assert replay.data == result.data
            assert manager.canonical_state().used.logical_tool_calls == used
            for key, replacement in (("query", "unauthorized query"), ("section_id", "other-section")):
                bad_kwargs = {**kwargs, "arguments": {**kwargs["arguments"], key: replacement}}
                denied = original_invoke(gateway, context, **bad_kwargs)
                assert denied.status is ToolResultStatus.REJECTED
                assert denied.error.error_code == "TOOL_CAPABILITY_GRANT_REJECTED"
                assert manager.canonical_state().used.logical_tool_calls == used
            forged = context.model_copy(update={"task_id": "other-task"})
            denied = original_invoke(gateway, forged, **kwargs)
            assert denied.status is ToolResultStatus.REJECTED
            assert manager.canonical_state().used.logical_tool_calls == used
            observed_calls.append((gateway, context, kwargs, result))
        return result

    # Observe the real factory-produced gateway; do not replace its provider,
    # validators, stores or execution results.
    monkeypatch.setattr(ToolGateway, "invoke", observed_invoke)
    summary = MainlineApplicationService(supervisor_factory=factory).run(
        project_root=Path(__file__).resolve().parents[2],
        user_input="生成政务云安全设计章节",
        task_id=task_id,
        run_id=run_id,
        output_root=tmp_path,
        clean_existing=True,
        settings=settings,
        project_input=project,
        allow_demo_defaults=False,
        enable_agent_self_rag=False,
    )
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / f"{run_id}_trace.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    research = [
        event for event in events
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "ResearchAgent"
    ]
    # Verify that the positive scenario really reached the intended section,
    # rather than accepting an unrelated early configuration failure.
    assert research, summary
    assert all(event.get("status") == "success" for event in research), research
    adapter = factory.engines[0].stage_service.section_execution_coordinator.section_agent_adapter
    assert adapter is not None and adapter.research_tool_gateway is not None
    assert callable(adapter.research_tool_gateway.canonical_gateway_provider)
    assert len(observed_calls) == 1
    gateway, context, arguments, result = observed_calls[0]
    assert result.budget_settlement is not None
    assert result.tool_operation_id
    budget = gateway.budget_runtime.budget_manager
    assert budget.canonical_state().used.logical_tool_calls == 1
    assert (tmp_path / "runs" / "research_tool_results.jsonl").is_file()
    # Reuse must not bypass lifecycle after the application has finalized Run.
    stale = original_invoke(gateway, context, **arguments)
    assert stale.status is ToolResultStatus.STALE
    assert stale.error.error_code == "TOOL_CURRENTNESS_REJECTED"
    assert budget.canonical_state().used.logical_tool_calls == 1
    section_starts = [
        event for event in events
        if event.get("event_type") == "rag_started"
        and (event.get("input_summary") or {}).get("scope") == "section"
        and "安全设计" in str((event.get("input_summary") or {}).get("query") or "")
    ]
    assert section_starts, "successful Research must execute section-scoped RAG, not reuse document projection"
    call_ids = {event["call_id"] for event in section_starts}
    assert None not in call_ids
    section_rag = [
        event for event in events
        if event.get("event_type") == "rag_finished"
        and event.get("call_id") in call_ids
    ]
    assert {event["call_id"] for event in section_rag} == call_ids
    assert all(event.get("status") == "success" for event in section_rag), section_rag
    assert all(
        int((event.get("output_summary") or {}).get("selected_evidence_count", 0)) > 0
        for event in section_rag
    ), section_rag
