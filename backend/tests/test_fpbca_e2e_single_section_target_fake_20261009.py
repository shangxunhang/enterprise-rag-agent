"""One-section V3 E2E diagnostic with Fake-only models (no DeepSeek).

This is a fresh Task/Run with first-section-only business requirements.
It must not be confused with the real eight-section final business acceptance,
and a Fake partial_success is not evidence of formal Target Release.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sqlite3
import pytest
from uuid import uuid4
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.integrations.langchain.chat_model_client import LangChainChatModelClient
from schemas.rag import RetrievalAccessScopeSchema
from bootstrap.supervisor_factory import SupervisorFactory
from bootstrap.document_catalog_adapter import DocumentProfileCatalogAdapter

ROOT = Path(__file__).resolve().parents[2]
FROZEN = Path("D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T112450Z")
FIRST = "项目背景"


@pytest.mark.parametrize('scenario', ('insufficient', 'supported'))
def test_first_section_new_project_input_reaches_target_verifier_without_paid_provider(
    tmp_path, request, scenario
):
    assert FROZEN.joinpath("audit/PROJECT_INPUT_V3.json").is_file()
    value = json.loads(FROZEN.joinpath("audit/PROJECT_INPUT_V3.json").read_text(
        encoding="utf-8"
    ))
    value["task_id"] = "task-e2esm-one-section-probe"
    value["generation_requirements"]["required_sections"] = [FIRST]
    value["generation_requirements"]["citation_required_sections"] = [FIRST]
    value["output_schema"]["required_sections"] = [FIRST]
    value["generation_requirements"].setdefault("extra", {})["verifier_decision_profile"] = (
        "claim-complete-support-v1"
    )
    project = ProjectInputSchema.model_validate(value)
    assert project.schema_version == "project_input_v3"
    assert project.generation_requirements.max_tokens_per_section == 4096
    # The canonical disk guard correctly rejects new durable Runs on the
    # nearly full C: pytest-temp volume; allocate an isolated E: probe folder.
    runtime_root = Path("E:/rag-agent-tmp") / ("e2esm-single-target-" + uuid4().hex)
    runtime_root.mkdir(parents=False, exist_ok=False)
    request.addfinalizer(lambda: shutil.rmtree(runtime_root, ignore_errors=True))
    settings = replace(
        get_settings(),
        data_root=runtime_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=False,
        data_capture_enabled=False,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=0,
    )
    calls = []
    original_fake = FakeLLMClient.generate
    quote = ("政务云建设方案应明确项目目标、建设范围、实施边界和验收要求"
             if scenario == "supported" else "当前项目的资源配置数量尚未确认")
    writer_content = (
        (quote + "。[C1]" if scenario == "supported" else
         "当前项目的资源配置数量尚未确认。关于未获确认的资源，"
         "本方案仅能形成待核实的建议，不作为已完成建设事实。")
    ) * 4

    def capture_fake(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_fake(client, request)
        purpose = request.extra.get("call_purpose")
        if purpose == "scheme_section_generation":
            return response.model_copy(update={"content": writer_content}, deep=True)
        if purpose == "claim_materialization":
            task = None
            for message in request.messages:
                text = message["content"]
                idx = text.find("{")
                if idx < 0:
                    continue
                try:
                    raw, _ = json.JSONDecoder().raw_decode(text[idx:])
                except (ValueError, TypeError):
                    continue
                if isinstance(raw, dict) and "coverage_units" in raw:
                    task = raw
                    break
            assert task is not None
            units = []
            for unit in task["coverage_units"]:
                claims = [
                    {"quote": quote, "occurrence_index": i,
                     "claim_type": "general_method" if scenario == "supported"
                                   else "current_project_fact"}
                    for i in range(unit["text"].count(quote))
                ]
                units.append({
                    "unit_id": unit["unit_id"],
                    "accounting_state": (
                        "FACTUAL_CLAIMS_MATERIALIZED" if claims
                        else "NO_APPLICABLE_FACTUAL_CLAIM"
                    ),
                    "claims": claims,
                })
            return response.model_copy(update={
                "content": json.dumps({"units": units}, ensure_ascii=False),
            }, deep=True)
        if purpose == "claim_support":
            supporting = []
            if scenario == "supported":
                from backend.tests.test_multi_agent_v3_claim_support_mainline import _json_message
                evidence = _json_message(request, "support_evidence")["support_evidence"]
                supporting = [
                    item["exact_source_ref"] for item in evidence
                    if quote in str(item.get("context_text", ""))
                ]
                assert supporting, "Cannot call a fake claim SUPPORTED without its exact source"
            return response.model_copy(update={"content": json.dumps({
                "relation": "SUPPORTED" if scenario == "supported" else "INDETERMINATE",
                "supporting_refs": sorted(set(supporting)),
                "contradicting_refs": [],
                "certainty_relation": "ALIGNED" if scenario == "supported" else "INDETERMINATE",
                "certainty_reason_codes": [
                    "EXACT_FAKE_SOURCE_CONTAINS_CLAIM" if scenario == "supported"
                    else "FAKE_UNRESOLVED_SUPPORT"
                ],
            })}, deep=True)
        if purpose == "claim_verification":
            positive = []
            if scenario == "supported":
                from backend.tests.test_multi_agent_v3_claim_support_mainline import _json_message
                allowed = _json_message(request, "admissible_positive_basis_refs")
                positive = [r for r in allowed["admissible_positive_basis_refs"]
                            if r.startswith("evidence-item://")]
                assert positive, "No M6-admissible Evidence positive basis exists"
            return response.model_copy(update={"content": json.dumps({
                "verdict": "VERIFIED" if scenario == "supported" else "UNSUPPORTED",
                "rationale": (
                    "The exact synthetic source contains the whole quoted statement."
                    if scenario == "supported" else
                    "Synthetic complete claim lacks material evidence support."
                ),
                "positive_basis_refs": positive,
                "issues": [] if scenario == "supported" else [{
                    "issue_code": "FAKE_UNRESOLVED_SUPPORT",
                    "severity": "MAJOR",
                    "message": "Support remains indeterminate for this claim.",
                    "source_refs": [],
                }],
            })}, deep=True)
        return response

    def forbid_real(*_args, **_kwargs):
        raise AssertionError("Real model is forbidden in one-section offline probe")

    env = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "citation_collision" if scenario == "supported" else "default",
        "FAKE_LLM_SCENARIO": "default",
        "DEFAULT_MODEL_NAME": "fake_llm",
        "SUPERVISOR_MODEL_NAME": "fake_llm",
        "ENABLE_AGENT_SELF_RAG": "true",
        "ENABLE_SEMANTIC_GATE": "false",
        "GATE_ENFORCEMENT_MODE": "strict",
        "GROUNDED_GENERATION_POLICY_FILE": str(FROZEN / "audit/REAL_E2E_POLICY.json"),
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, env))
        stack.enter_context(patch.object(FakeLLMClient, "generate", capture_fake))
        stack.enter_context(patch.object(LangChainChatModelClient, "generate", forbid_real))
        summary = MainlineApplicationService(
            supervisor_factory=SupervisorFactory(),
            document_catalog=DocumentProfileCatalogAdapter(),
        ).run(
            project_root=ROOT,
            task_id=project.task_id,
            run_id="run-e2esm-one-section-probe",
            user_input=project.user_query,
            output_root=runtime_root,
            clean_existing=True,
            settings=settings,
            project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=True,
            durable_memory_required=False,
            retrieval_access_scope=RetrievalAccessScopeSchema(
                tenant_id="default",
                authorized_kb_ids=[
                    "historical_case_kb",
                    "policy_standard_kb",
                    "project_fact_kb",
                    "technical_reference_kb",
                    "template_style_kb",
                ],
            ),
        )
    call_types = [x.extra.get("call_purpose") for x in calls]
    print("ONE_SECTION_SUMMARY_STATUS=", summary.get("status"))
    print("ONE_SECTION_TERMINAL_STATUS=", (summary.get("terminal_outcome") or {}).get("status"))
    print("ONE_SECTION_MODEL_CALL_TYPES=", call_types)
    print("ONE_SECTION_MODEL_CALL_COUNT=", len(calls))
    print("ONE_SECTION_SUMMARY_KEYS=", list(summary))
    print("ONE_SECTION_RELEASE_TYPE=", type(summary.get("target_release_publication")).__name__)
    release = summary.get("target_release_publication") or {}
    print("ONE_SECTION_TARGET_RELEASE=", str({
        k: release.get(k) for k in ("status", "reason_code", "quality_state", "control_action", "error")
    })[:1500] if isinstance(release, dict) else str(release)[:1000])
    print("ONE_SECTION_WRITER_STATE=", str(summary.get("scheme_writer_output"))[:1800])
    disk = runtime_root / "runs/post_write.sqlite3"
    if disk.exists():
        with sqlite3.connect("file:" + disk.as_posix() + "?mode=ro", uri=True) as connection:
            tables = [
                name for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('validation_completions','validation_facts','post_write_entries','post_write_control_decisions','validation_runs')"
                ).fetchall()
            ]
            print("ONE_SECTION_POSTWRITE_TABLE_ROWS=", {
                table: connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in tables
            })
            for table in ("validation_completions", "post_write_control_decisions"):
                if table in tables:
                    cols = [row[1] for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()]
                    print("ONE_SECTION_POSTWRITE_COLS_",table,"=",cols)
                    field = ("completion_json" if table == "validation_completions"
                             else "decision_json")
                    rows = [json.loads(row[0]) for row in connection.execute(
                        "SELECT " + field + " FROM " + table
                    ).fetchall()]
                    print("ONE_SECTION_POSTWRITE_JSON_",table,"=", [
                        {"keys": sorted(row), "control": {
                            k: str(v)[:320] for k, v in row.items() if k in (
                                "enforcement_action", "quality_state_passthrough",
                                "blocking_fact_refs","unresolved_reason_refs",
                                "control_reason_codes","availability_projection",
                                "delivery_gate_decision_ref",
                            )
                        }, "items": {
                            "n":len(row.get("items") or []),
                            "statuses":[{
                                "keys":list(x),
                                "completion_status":x.get("completion_status"),
                                "target_ref":str(x.get("target_ref"))[:130],
                                "failure":str(x.get("failure"))[:160],
                            } for x in (row.get("items") or [])[:8]]
                        }} for row in rows
                    ])
            if "validation_facts" in tables:
                cols = [x[1] for x in connection.execute("PRAGMA table_info(validation_facts)")]
                print("ONE_SECTION_FACT_COLS=",cols)
                column = next((col for col in cols if col.endswith("_json")), None)
                if column:
                    facts = [json.loads(row[0]) for row in connection.execute(
                        "SELECT " + column + " FROM validation_facts"
                    )]
                    from collections import Counter
                    print("ONE_SECTION_FACT_SCHEMA_KEYS=",list(facts[0]) if facts else [])
                    print("ONE_SECTION_VALIDATION_FAMILY_STATES=", sorted(
                        (family,dict(Counter(
                            f.get("semantic_state") for f in facts if f.get("family")==family
                        ))) for family in sorted({f.get("family") for f in facts})
                    ))
                    for field in ("dimension", "fact_type", "validator_id", "state", "status",
                                  "result", "outcome", "verdict","quality_state","result_state",
                                  "validator_kind","validation_dimension","reason_code"):
                        counts = Counter(str(x.get(field)) for x in facts if x.get(field) is not None)
                        if counts:
                            print("ONE_SECTION_FACT_FIELD",field,counts)
                    print("ONE_SECTION_FACT_EXAMPLE=", [
                        {k:str(v)[:170] for k,v in f.items()} for f in facts[:3]
                    ])
    print("ONE_SECTION_ERROR=", summary.get("error_message"))
    print("ONE_SECTION_TASK_ERROR=", str((summary.get("task_state") or {}).get("error"))[:1500])
    print("ONE_SECTION_SUPERVISOR_ERR=", str((summary.get("supervisor_result") or {}).get("error"))[:1000])
    print("ONE_SECTION_TERMINAL_REASON=", {
        "status": (summary.get("terminal_outcome") or {}).get("status"),
        "quality_state": (summary.get("terminal_outcome") or {}).get("quality_state"),
        "diagnostic_codes": [
            r.get("code") for r in ((summary.get("terminal_outcome") or {}).get("diagnostics") or [])
        ],
    })
    verifier = [x for x in calls if x.extra.get("call_purpose") == "claim_verification"]
    assert verifier, (
        "Current one-section Fake Mainline did not reach target Claim Verifier. "
        f"status={summary.get('status')}; call_types={call_types}"
    )
    for req in verifier:
        assert req.extra["prompt_version"] == "v3-claim-complete-support-v1"
        assert any('"admissible_verdicts"' in x["content"] for x in req.messages)
