"""BC-10..12: deterministic synthetic inputs through a *real* Full8 owner chain.

No real Provider, no real RAG, no production code/Claim/Support/Release mocks.
The only alterations are FakeLLM responses and explicit test ProjectInput
policy; all Claim, Binding, Support, M6, Section, Lifecycle, Budget, DOCUMENT
and Release owners run unchanged. Read scenario contracts from the frozen
business coverage matrix instead of inventing expected outcomes in test code.

Run with --basetemp=E:/rag-agent-tmp/<fresh per-invocation test directory>.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime, VerifierSupportVerdictAdmissionError,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRefSchema
from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
from apps.enterprise_document.schemas.project_input_schema import (
    ProjectInputSchema, GenerationRequirementsV3Schema,
)
from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from test_phase3_section_execution_subgraph import _InspectingFactory
from apps.enterprise_document.services.scheme_writer.agent_integration.planner_adapter import (
    MainlinePlannerAdapter,
)
from apps.enterprise_document.workflows.section_execution_graph import PWA004_REVIEW_POLICY
from model_gateway.fake_llm_client import FakeLLMClient
import bca014_i10_capacity_fixtures as fake_budget
from test_pwa004_native_full8_fake_e2e import _extract_units, _verifier_task_material
from test_phase3_section_execution_subgraph import _run_fake

ROOT = Path(__file__).resolve().parents[2]
MATRIX_FILE = ROOT / (
    "docs/full8_r04_launch_preparation_20261026_r01/"
    "32_FULL8_FAKE_BUSINESS_COVERAGE_20261028_R01/01_COVERAGE_MATRIX.json"
)
MODEL_EXTRACT = "canonical_claim_extraction_v1"
MODEL_VERIFY = "canonical_claim_verifier_v2"


def _cases():
    document = json.loads(MATRIX_FILE.read_text(encoding="utf-8"))
    assert document["schema"] == "full8_fake_business_coverage_matrix_v1"
    return {case["id"]: case for case in document["synthetic_fixtures"]}


@pytest.fixture(scope="module")
def cases():
    return _cases()


@pytest.mark.parametrize(
    ("scenario_id", "hard_dep"),
    [
        pytest.param("SYN-BIZ-001", False, id="mixed-valid-negative-illegal-full8"),
        pytest.param("SYN-BIZ-001", True, id="mixed-hard-dependency-closure"),
        pytest.param("SYN-BIZ-002", False, id="malformed-verifier-must-not-continue"),
        pytest.param("SYN-BIZ-003", False, id="all-legal-negative-with-formal-results"),
    ],
)
def test_synthetic_business_outcomes_through_original_full8(
    tmp_path: Path, cases, scenario_id: str, hard_dep: bool,
):
    case = cases[scenario_id]
    original_fake = FakeLLMClient.generate
    original_plan = MainlinePlannerAdapter.plan
    original_policy = fake_budget.explicit_fake_startup_policy
    original_m6 = ClaimVerificationRuntime.validate_v2_provenance
    seen = Counter()
    verifier_observations = []
    writer_orders = []
    forbidden_rejections = []

    def expanded_fake_only_capacity(root, run_id):
        policy_path, default_path, original_bytes = original_policy(root, run_id)
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["canonical_budget"]["logical_model_calls"] = 80
        policy["canonical_budget"]["model_tokens_total"] = 160000
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        return policy_path, default_path, original_bytes

    def legal_plan_hard_dependency(adapter, **kwargs):
        result = original_plan(adapter, **kwargs)
        plans = list(result.document_plan.sections)
        assert len(plans) == 8
        plans[7] = plans[7].model_copy(
            update={"depends_on": [plans[6].section_id]}, deep=True)
        return replace(result, document_plan=result.document_plan.model_copy(
            update={"sections": plans}, deep=True))

    def model(client, request):
        extra = request.extra
        section_id = str(extra.get("section_id") or "")
        contract = extra.get("response_contract")
        purpose = extra.get("call_purpose")
        seen["calls"] += 1
        seen[str(contract)] += 1
        if purpose == "scheme_section_generation":
            writer_orders.append(section_id)
            if section_id.endswith("_008"):
                assert case["body"] not in request.prompt
                assert all(case["body"] not in str(msg.get("content") or "")
                           for msg in request.messages), (
                    "Unaccepted seventh Section contaminated eighth Writer"
                )
        raw = original_fake(client, request)
        if section_id.endswith("_007") and purpose == "scheme_section_generation":
            seen["writer7"] += 1
            return raw.model_copy(update={
                "content": case["body"] * case["copies"]}, deep=True)
        if section_id.endswith("_007") and contract == MODEL_EXTRACT:
            units = _extract_units(request)
            rows = []
            for unit in units:
                text = unit["text"]
                count = text.count(case["body"])
                if count:
                    rows.append({"unit_id": unit["unit_id"],
                                 "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                                 "claims": [{"quote": case["body"],
                                             "occurrence_index": index,
                                             "claim_type": case["claim_type"]}
                                            for index in range(count)]})
                else:
                    rows.append({"unit_id": unit["unit_id"],
                                 "accounting_state": "INDETERMINATE",
                                 "claims": []})
            assert sum(len(row["claims"]) for row in rows) == case["copies"]
            return raw.model_copy(update={
                "content": json.dumps({"units": rows}, ensure_ascii=False)}, deep=True)
        if section_id.endswith("_007") and contract == MODEL_VERIFY:
            task = _verifier_task_material(request)
            assert task["shared_support"]["relation"] == case["support_relation_expected"]
            n = seen["target_verifier_calls"]
            seen["target_verifier_calls"] += 1
            decisions = case["verdict_sequence"]
            decision = decisions[n] if n < len(decisions) else decisions[-1]
            verifier_observations.append({
                "claim_ref": task["verification_target"],
                "decision": decision,
                "support_relation": task["shared_support"]["relation"],
            })
            if decision == "MALFORMED_JSON":
                return raw.model_copy(update={"content": "{illegal-json"}, deep=True)
            if decision == "VERIFIED":
                allowed = task["allowed_authority_refs"]
                assert allowed
                chosen = next((ref for ref in allowed if "project" in ref.lower()), allowed[-1])
                reply = {
                    "verdict": "VERIFIED",
                    "rationale": "Deliberately illegal verdict from deterministic test model.",
                    "positive_basis_refs": [chosen],
                    "issues": [],
                }
                return raw.model_copy(update={
                    "content": json.dumps(reply, ensure_ascii=False)}, deep=True)
            assert decision == "UNSUPPORTED"
            # Keep the existing Fake model's original, structurally legal
            # UNSUPPORTED result and its actual issue list/refs.
            return raw
        return raw

    def original_m6_with_observation(binding, support, candidate):
        try:
            return original_m6(binding, support, candidate)
        except VerifierSupportVerdictAdmissionError as error:
            forbidden_rejections.append({
                "claim_ref": error.claim_ref.model_dump(mode="json"),
                "attempt_id": error.active_lineage.attempt_id,
                "rule_code": error.rule_code,
            })
            raise

    run_id = "syn_bus_" + scenario_id.lower().replace("-", "_") + ("_dep" if hard_dep else "")
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            fake_budget, "explicit_fake_startup_policy", expanded_fake_only_capacity))
        if hard_dep:
            stack.enter_context(patch.object(
                MainlinePlannerAdapter, "plan", legal_plan_hard_dependency))
        stack.enter_context(patch.object(FakeLLMClient, "generate", model))
        stack.enter_context(patch.object(
            ClaimVerificationRuntime, "validate_v2_provenance",
            staticmethod(original_m6_with_observation)))
        # Supply policy *before* TaskFactory and canonical RunIdentity
        # are materialized. Never mutate ProjectInput after that point.
        sections = [
            "项目背景", "建设目标", "总体架构", "计算资源建设方案",
            "存储资源建设方案", "网络资源建设方案", "安全设计", "实施与验收建议",
        ]
        project = ProjectInputSchema(
            schema_version="project_input_v3",
            task_id="task_" + run_id,
            tenant_id="default",
            task_type="scheme_generation",
            user_query="生成企业级 RAG-Agent 系统建设方案",
            generation_requirements=GenerationRequirementsV3Schema(
                required_sections=sections,
                need_citation=False,
                citation_required_sections=[],
                need_human_review=False,
                extra={
                    "verifier_decision_profile": "claim-complete-support-v1",
                    "verifier_rejection_policy": PWA004_REVIEW_POLICY,
                },
            ),
            output_schema={"document_title": "合成业务八章测试",
                           "required_sections": sections},
            metadata={"test_only_business_fixture": True},
        )
        source_profile, default_path, original_bytes = expanded_fake_only_capacity(
            tmp_path, run_id)
        from pathlib import Path as _Path
        from unittest.mock import patch as _patch
        from dataclasses import replace as _replace
        settings = _replace(
            get_settings(), data_root=tmp_path,
            default_model_name="fake_llm", supervisor_model_name="fake_llm",
            enable_llm_routing=False, trace_enabled=True,
            data_capture_enabled=True,
        )
        factory = _InspectingFactory()
        with _patch.dict(__import__("os").environ, {
            "GROUNDED_GENERATION_POLICY_FILE": str(source_profile),
            "USE_REAL_RAG_TOOL": "false",
            "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default",
            "ENABLE_AGENT_SELF_RAG": "false",
            "ENABLE_SEMANTIC_GATE": "false",
        }):
            summary = MainlineApplicationService(supervisor_factory=factory).run(
                project_root=ROOT, user_input=project.user_query,
                run_id=run_id, task_id=project.task_id, output_root=tmp_path,
                clean_existing=True, settings=settings,
                project_input=project.model_dump(mode="json"),
                allow_demo_defaults=False, enable_agent_self_rag=False,
            )
        assert default_path.read_bytes() == original_bytes

    engine = factory.engines[0]
    m6 = engine.section_execution_graph.claim_verification_runtime
    observed = dict(seen)
    reviewed = (summary.get("generated_draft") or {}).get("extra", {}).get(
        "pwa004_review_draft")
    error = (summary.get("supervisor_result") or {}).get("error") or {}
    print("BC_NATIVE_RUN", json.dumps({
        "scenario": scenario_id,
        "hard_dep": hard_dep,
        "status": summary.get("status"),
        "error": error.get("error_code"),
        "model_calls": observed,
        "verifier_replies": [{"decision": row["decision"],
                              "claim_suffix": row["claim_ref"]["claim_id"][-9:]}
                             for row in verifier_observations],
        "real_m6_rejections": len(forbidden_rejections),
        "writer_sections": len(writer_orders),
        "draft_sections": len((summary.get("generated_draft") or {}).get("sections", [])),
        "review_rejections": len(reviewed["rejections"]) if reviewed else None,
        "target_release_exists": summary.get("target_release_publication") is not None,
    }, ensure_ascii=False, default=str))

    assert seen["writer7"] == 1
    assert len(verifier_observations) >= 1
    assert len(forbidden_rejections) == case["expected"]["native_m6_rejection_count"]
    # Test the real native M6 heads, not an arbitrary test-side fabricated
    # 'VerificationResult'. All accepted ClaimHeads must have been committed.
    legal = [o for o in verifier_observations if o["decision"] == "UNSUPPORTED"]
    invalid = [o for o in verifier_observations if o["decision"] == "VERIFIED"]
    if scenario_id != "SYN-BIZ-002":
        assert len(legal) == case["expected"]["legal_negative_result_count"]
        assert len(invalid) == case["expected"].get("native_m6_rejection_count", 0)
        for row in legal:
            ref = ClaimRefSchema.model_validate(row["claim_ref"])
            head = m6.resolve_claim_head(ref)
            assert head is not None, "Legal negative verdict was lost"
        for row in invalid:
            assert m6.resolve_claim_head(ClaimRefSchema.model_validate(
                row["claim_ref"])) is None, "Illegal VERIFIED gained ClaimHead"
    if scenario_id == "SYN-BIZ-001":
        assert summary["status"] == "failed"
        assert error["error_code"] == "VERIFIER_ADMISSION_INCOMPLETE"
        assert reviewed is not None
        assert len(reviewed["rejections"]) == 2
        assert len((summary.get("generated_draft") or {})["sections"]) == (7 if hard_dep else 8)
        assert all(row["rule_code"] == "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT"
                   for row in reviewed["rejections"])
        assert summary["target_release_publication"] is None
        assert summary["deliverable_final_result"] is None
        assert all(row["attempt_id"] == forbidden_rejections[0]["attempt_id"]
                   for row in reviewed["rejections"])
        if hard_dep:
            assert len(reviewed["hard_dependency_blocked"]) == 1
            assert not any(section_id.endswith("_008") for section_id in writer_orders)
        else:
            assert any(section_id.endswith("_008") for section_id in writer_orders)
    elif scenario_id == "SYN-BIZ-002":
        assert error.get("error_code") != "VERIFIER_ADMISSION_INCOMPLETE"
        assert summary["status"] in ("failed", "retryable_failed")
        assert not any(section_id.endswith("_008") for section_id in writer_orders)
        assert summary.get("target_release_publication") is None
        assert not forbidden_rejections
    elif scenario_id == "SYN-BIZ-003":
        assert not forbidden_rejections
        assert reviewed is None
        assert len(legal) == case["expected"]["legal_negative_result_count"]
        assert summary["status"] == "partial_success"
        assert not error
        assert summary.get("target_release_publication") is not None
        assert summary["terminal_outcome"]["status"] == "OUTPUT_BEST_EFFORT"
        # A legitimate UNSUPPORTED Result must not impersonate VERIFIED. This
        # scenario tests formal admission, not downstream quality acceptance.
        assert all(m6.resolve_claim_head(
            ClaimRefSchema.model_validate(row["claim_ref"])) is not None for row in legal)
    budget = engine.budget_runtime(summary["task_id"], summary["run_id"]).canonical_state()
    assert budget.used.logical_model_calls == seen["calls"]
    assert not any(budget.active_reserved.model_dump().values())
