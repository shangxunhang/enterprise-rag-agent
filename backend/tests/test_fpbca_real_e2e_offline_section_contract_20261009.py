"""Narrow offline checks for the Oct-09 real business E2E failure.

No DeepSeek/network call; real historical Run files are read-only.
These tests do NOT claim full real Post-write acceptance or authorize replay.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    scan_citation_markers,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_coverage_composition import (
    TargetPostWriteCoverageComposition,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.scheme_writer.citation_integrity_service import (
    CitationOccurrenceIntegrityGate,
)
from apps.enterprise_document.services.scheme_writer.model_service import (
    SectionModelService,
)
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.model import ModelResponseSchema

import backend.tests.test_gate_v2_pre_write_content_boundary as target_case
import backend.tests.test_fpbca_bcd_local_closure_minimal as current_mainline


CAMPAIGN = Path(
    "D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T064923Z"
)
OBSERVED = CAMPAIGN / "business-runtime/observed_calls.jsonl"
BUSINESS_INPUT = CAMPAIGN / "audit/PROJECT_INPUT_V3.json"


class _CapabilityOnlyGateway:
    def __init__(self, *, provider_safe_limit=65536):
        self.provider_safe_limit = provider_safe_limit

    def routing_capabilities(self, *, model_role):
        assert model_role == "section_generation"
        return {
            "model_role": model_role,
            "safe_context_window": 1_000_000,
            "safe_max_output_tokens": self.provider_safe_limit,
            "candidate_profiles": ["deepseek"],
            "candidate_models": ["deepseek_api"],
            "candidate_capabilities": [],
        }


def _project(*, cap):
    base = target_case._project_input()
    requirements = base.generation_requirements.model_copy(
        update={"max_tokens_per_section": cap}
    )
    return base.model_copy(update={"generation_requirements": requirements})


def test_new_output_caps_change_sif10_capability_fingerprint_and_reservation():
    """Prevent reusing the old output-capability binding in a new Writer input."""
    composer = object.__new__(TargetWriterContextComposer)
    composer.model_gateway = _CapabilityOnlyGateway()
    bindings = {
        cap: composer._model_capability(project_input=_project(cap=cap))
        for cap in (1024, 2048, 4096)
    }
    assert [bindings[k].safe_max_output_tokens for k in bindings] == [
        1024, 2048, 4096
    ]
    assert len({
        row.model_capability_ref for row in bindings.values()
    }) == 3
    assert len({
        row.capability_semantic_fingerprint for row in bindings.values()
    }) == 3
    assert len({row.model_context_window for row in bindings.values()}) == 1


def test_new_citation_instruction_is_versioned_and_legacy_v5_is_unchanged():
    """V3 opt-in changes only the role binding; default v5 remains frozen."""
    section = SectionPlanEntrySchema(
        section_id=target_case.SECTION_ID,
        section_title="技术方案",
        section_order=1,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=False,
    )
    old = _project(cap=1024)
    new = old.model_copy(update={
        "generation_requirements": old.generation_requirements.model_copy(
            update={"citation_style": "citation_id_ascii_brackets_v2"}
        )
    })
    old_binding, old_material = TargetWriterContextComposer._instructions(
        project_input=old, section=section,
        gate_binding=None, route_decision=None, decision=None,
    )
    new_binding, new_material = TargetWriterContextComposer._instructions(
        project_input=new, section=section,
        gate_binding=None, route_decision=None, decision=None,
    )
    assert old_binding.role_instruction_version == "v5"
    assert old_binding.role_instruction_ref == "instruction://target-writer/role/v5"
    assert "exact ASCII square-bracket marker" not in old_material.role_instruction
    assert new_binding.role_instruction_version == "v6"
    assert new_binding.role_instruction_ref == "instruction://target-writer/role/v6"
    assert new_binding.role_instruction_sha256 != old_binding.role_instruction_sha256
    assert new_binding.task_output_contract_sha256 != old_binding.task_output_contract_sha256
    assert new_material.prompt_template == old_material.prompt_template
    assert "exact ASCII square-bracket marker [C1]" in new_material.role_instruction
    assert "[C1][C2]" in new_material.role_instruction
    assert "（C1、C2）" in new_material.role_instruction
    assert "citation_id_ascii_brackets_v2" in new_material.task_output_contract
    assert "If no selected source supports a statement, do not fabricate a" in new_material.role_instruction


def _model_service(*, gateway):
    model = object.__new__(SectionModelService)
    model.model_gateway = gateway
    model.agent_name = "SchemeWriterAgent"
    model.runtime_support = SimpleNamespace(
        now_iso=lambda: "2026-10-09T08:00:00+00:00"
    )
    return model


@pytest.mark.parametrize(
    "cap,provider_safe,effective",
    [
        (1024, 65536, 1024),
        (2048, 65536, 2048),
        (4096, 65536, 4096),
        (4096, 1536, 1536),
    ],
)
def test_real_model_boundary_uses_new_section_cap_without_bypassing_provider_safety(
    monkeypatch, cap, provider_safe, effective
):
    """Invoke actual SectionModelService cap logic with physical dispatch intercepted."""
    project = _project(cap=cap)
    gateway = _CapabilityOnlyGateway(provider_safe_limit=provider_safe)
    composer = object.__new__(TargetWriterContextComposer)
    composer.model_gateway = gateway
    context_capability = composer._model_capability(project_input=project)
    assert context_capability.safe_max_output_tokens == effective

    model = _model_service(gateway=gateway)
    state = target_case._shared_state()
    observed = []

    def fake_boundary_generate(_self, _prompt, **kwargs):
        observed.append(kwargs)
        return ModelResponseSchema(
            model_call_id=kwargs["model_call_id"],
            task_id=state.task_id,
            run_id=state.run_id,
            model_name="deepseek_api",
            success=True,
            content="合法的离线占位正文。",
            finish_reason="stop",
            created_at="2026-10-09T08:00:00+00:00",
        )

    monkeypatch.setattr(
        ModelCallBoundary, "generate_response", fake_boundary_generate
    )
    result = model.call_model(
        state,
        prompt="",
        section_id=target_case.SECTION_ID,
        section_title="技术方案",
        project_input=project,
        available_citation_ids=["C1"],
        purpose="scheme_section_generation",
        context_package={
            "schema_version": "context_package_v1",
            "package_ref": {"context_package_id": "offline-capacity-control"},
            "model_input": {"final_model_input_sha256": "f" * 64},
            "budget_binding": {"reserved_output_tokens": effective},
        },
        messages=[{"role": "system", "content": "已锁定的输入"}],
    )
    assert result.success
    assert len(observed) == 1
    assert observed[0]["max_new_tokens"] == effective
    assert observed[0]["messages"] == [
        {"role": "system", "content": "已锁定的输入"}
    ]
    assert state.generated_outputs[result.model_call_id]["finish_reason"] == "stop"


@pytest.mark.parametrize("cap", [2048, 4096])
@pytest.mark.parametrize("finish_reason,expected_success", [
    ("length", False),
    ("stop", True),
])
def test_sif10_locked_model_output_never_silently_recovers_or_accepts_length(
    monkeypatch, cap, finish_reason, expected_success
):
    """Use existing real Evidence/Prompt fixture; no model or provider dispatch."""
    project = _project(cap=cap)
    monkeypatch.setattr(target_case, "_project_input", lambda: project)
    result, calls, state, initial, late, target_prompt = target_case._run_target_case(
        primary_content="本章示例内容。",
        primary_finish_reason=finish_reason,
        recovery_content="不应获得二次模型机会。",
        advance_runtime_to_e2=True,
        canonical_messages=[{"role": "user", "content": "immutable SIF-10 payload"}],
        canonical_context_package={
            "package_ref": {"context_package_id": "original_locked_package"},
            "model_input": {"final_model_input_sha256": "a" * 64},
        },
        canonical_allowed_aliases=(),
    )
    assert len(calls) == 1
    assert calls[0]["project_input"].generation_requirements.max_tokens_per_section == cap
    assert calls[0]["messages"] == [
        {"role": "user", "content": "immutable SIF-10 payload"}
    ]
    assert result.success is expected_success
    assert result.recovery is None
    if expected_success:
        assert result.technical_failure_reason is None
        assert not result.truncation.truncated
    else:
        assert result.technical_failure_reason == (
            "target model output truncated under locked SIF-10 Context"
        )
        assert result.truncation.truncated


@pytest.mark.parametrize("cap", [2048, 4096])
def test_native_current_mainline_v3_cap_reaches_exact_fake_writer_request(
    tmp_path, monkeypatch, cap
):
    """Actual Mainline Writer ModelRequest; Fake provider, current legal Template."""
    original_project_factory = current_mainline._project_input
    original_fake_generate = FakeLLMClient.generate
    observed = []

    def expanded_project(task_id):
        project = original_project_factory(task_id)
        payload = project.model_dump(mode="json")
        payload["generation_requirements"]["max_tokens_per_section"] = cap
        payload["generation_requirements"]["citation_style"] = (
            "citation_id_ascii_brackets_v2"
        )
        return project.__class__.model_validate(payload)

    def observe(client, request):
        if request.extra.get("call_purpose") == "scheme_section_generation":
            observed.append(request.model_copy(deep=True))
        return original_fake_generate(client, request)

    with monkeypatch.context() as scoped:
        scoped.setattr(current_mainline, "_project_input", expanded_project)
        scoped.setattr(FakeLLMClient, "generate", observe)
        current_mainline.test_v08_current_template_reaches_actual_writer_model_request(
            tmp_path, scoped
        )

    assert len(observed) == 1
    request = observed[0]
    safe_limit = int(
        request.extra["model_capability_plan"]["safe_max_output_tokens"]
    )
    assert request.max_tokens == min(cap, safe_limit)
    summary = request.extra["llm_context_summary"]
    assert summary["context_authority"] == "SIF-10"
    assert summary["reserved_output_tokens"] == request.max_tokens
    assert summary["final_model_input_sha256"]
    assert request.extra["available_citation_ids"]
    assert request.messages
    combined = "\n".join(item["content"] for item in request.messages)
    assert "exact ASCII square-bracket marker [C1]" in combined
    assert "do not use Chinese parentheses such as （C1、C2）" in combined
    assert "citation_id_ascii_brackets_v2" in combined


def _saved_section_writer_pairs():
    """Read only exact retained physical observation bytes, never reconstruct authority."""
    if not OBSERVED.is_file():
        pytest.fail("the sealed real E2E observation file is missing")
    started = {}
    received = {}
    for line in OBSERVED.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        identity = item.get("model_call_id")
        if item.get("event") == "request_started" and (
            item.get("request") or {}
        ).get("model_role") == "section_generation":
            started[identity] = item["request"]
        if item.get("event") == "response_received":
            received[identity] = item["response"]
    return {
        int(req["extra"]["section_id"].rsplit("_", 1)[-1]): (
            req, received[identifier]
        )
        for identifier, req in started.items()
        if identifier in received
    }


def test_real_first_section_noncanonical_citation_and_second_section_positive():
    """Call two production parsers on actual stored model output; no source invention."""
    pairs = _saved_section_writer_pairs()
    assert set(pairs) == set(range(1, 7))
    project = json.loads(BUSINESS_INPUT.read_text(encoding="utf-8"))
    assert project["generation_requirements"]["need_citation"]
    required = project["generation_requirements"]["citation_required_sections"]
    assert len(required) == 8

    req1, resp1 = pairs[1]
    req2, resp2 = pairs[2]
    assert req1["extra"]["available_citation_ids"] == ["C1", "C2"]
    assert "\uff08C1\u3001C2\uff09" in resp1["content"]
    assert not re.findall(r"\[C[1-9][0-9]*\]", resp1["content"])
    assert scan_citation_markers(resp1["content"]) == ()
    assert CitationOccurrenceIntegrityGate.extract_marker_occurrences(
        section_id=req1["extra"]["section_id"],
        content=resp1["content"],
    ) == ()
    assert len(scan_citation_markers(resp2["content"])) == 3
    assert len(CitationOccurrenceIntegrityGate.extract_marker_occurrences(
        section_id=req2["extra"]["section_id"],
        content=resp2["content"],
    )) == 3
    # The explicit source aliases are not a license to invent grounding.
    assert {m.alias for m in scan_citation_markers(resp2["content"])} <= set(
        req2["extra"]["available_citation_ids"]
    )


def test_native_coverage_structural_citation_presence_from_real_saved_content():
    """Pure current coverage-owner matching: not a formal ValidationRun."""
    pairs = _saved_section_writer_pairs()
    project = json.loads(BUSINESS_INPUT.read_text(encoding="utf-8"))
    for index, expect in ((1, False), (2, True)):
        request, response = pairs[index]
        sid = request["extra"]["section_id"]
        title = project["generation_requirements"]["required_sections"][index - 1]
        assert title in project["generation_requirements"]["citation_required_sections"]
        occurrences = tuple(
            {"span": {"section_id": sid}, "token": marker.token}
            for marker in scan_citation_markers(response["content"])
        )
        row = SimpleNamespace(
            requirement_kind="CITATION_PRESENCE",
            constraints={"section_title": title, "document_global": False},
        )
        units = ({
            "unit_ref": "read-only-raw-section-unit",
            "section_title": title,
            "selector": {"section_id": sid},
        },)
        matched = TargetPostWriteCoverageComposition._structural_matches(
            row, units, occurrences
        )
        assert bool(matched) is expect
    # This does not assert Citation Integrity, Grounding, or formal Post-write PASS.
