"""I2 exact reads and write separation, with real canonical source owners."""
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    AttemptLifecycleStatus as AS, RunLifecycleStatus as RS,
    RunFinalizationBarrierSchema, RunIdentitySchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError, RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
from apps.enterprise_document.services.agent_rag_v1.claim_composition import TargetClaimCompositionError
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import CitationProjectionIntegrityError
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
from backend.tests.test_bca014_finalizing_lifecycle import _barrier, _finalize
from backend.tests.test_multi_agent_v3_lifecycle_commit import _service, _running_run, REQUEST_HASH
from backend.tests.test_multi_agent_v3_release_lineage import _assessment_fixture, _barrier_closure
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


def make_section(tmp_path, relation="SUPPORTED", verdict="VERIFIED", certainty="ALIGNED", no_claims=False):
    """Fresh V3 input; stop after Section validation, before deferred I8 orchestration."""
    task_id, run_id = "task-i2", "run-wave11"
    raw = workflow_tests._w13_single_section_project_input(task_id).model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    project = ProjectInputSchema.model_validate(raw)
    text = "应依据已核实的项目资料制定建设方案。"
    captured = {}
    fixture_stop = "I2 fixture stops after committed Section validation"
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation
    original_failure = SchemeGenerationStageService._terminalize_noncancel_failed_run

    def provider(client, req):
        response = original_generate(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": text * 6})
        contract = req.extra.get("response_contract")
        if contract == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": unit["unit_id"], "accounting_state": "NO_APPLICABLE_FACTUAL_CLAIM" if no_claims else "FACTUAL_CLAIMS_MATERIALIZED",
                "claims": [] if no_claims else [{"quote": text, "occurrence_index": 0, "claim_type": "general_method"}]} for unit in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)})
        if contract == support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs = [item["exact_source_ref"] for item in support_tests._json_message(req, "support_evidence")["support_evidence"]]
            return response.model_copy(update={"content": json.dumps({"relation": relation, "supporting_refs": refs[:1] if relation in {"SUPPORTED", "PARTIALLY_SUPPORTED"} else [],
                "contradicting_refs": [], "certainty_relation": certainty, "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]})})
        return response

    original_parse=TargetClaimVerifierComposition.parse_candidate
    def parse(_payload, *, invocation, occurrence, receipt_ref):
        # The fixture supplies a controlled semantic oracle; canonical V2
        # parsing, admission/currentness and durable commit remain production.
        payload={"verdict":verdict,"positive_basis_refs":
            [invocation.verifier_project_input_basis.project_input_basis_ref] if verdict=="VERIFIED" else [],
            "rationale":"I7 controlled semantic fixture","issues":[]}
        return original_parse(payload,invocation=invocation,occurrence=occurrence,receipt_ref=receipt_ref)

    def capture(composition, **kwargs):
        outcome = original_complete(composition, **kwargs)
        captured.update(post_write=composition, arguments=kwargs, outcome=outcome)
        raise RuntimeError(fixture_stop)

    def isolate_fixture_stop(stage, *, payload, error, failed_node):
        # Only this fixture's deliberate Section boundary pause is isolated.
        # Real failures still use C1 and every native currentness oracle is retained.
        if (captured and (payload.task_id, payload.run_id) == (task_id, run_id)
                and failed_node == "evaluate_document_gate"
                and error.error_code == "SCHEME_GENERATION_STAGE_FAILED"
                and error.error_type == "RuntimeError" and error.message == fixture_stop):
            captured["intentional_fixture_stop_isolated"] = True
            return None
        return original_failure(stage, payload=payload, error=error, failed_node=failed_node)

    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True,
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    factory = workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetClaimVerifierComposition,"parse_candidate",side_effect=parse))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", capture))
        stack.enter_context(patch.object(SchemeGenerationStageService,
            "_terminalize_noncancel_failed_run", isolate_fixture_stop))
        MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=tmp_path, clean_existing=True, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False)
    assert captured, "fresh canonical Section did not reach PostWrite validation"
    assert captured["intentional_fixture_stop_isolated"], "fixture stop did not match its exact isolated boundary"
    assert captured["post_write"].lifecycle_service.load_run_lifecycle(run_id).status is RS.RUNNING
    captured["root"] = tmp_path
    return captured
