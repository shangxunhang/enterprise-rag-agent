"""340: real default Claim materialization, before support/verification truth."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import TargetClaimExtractionRuntime
import test_multi_agent_v3_workflow_recovery as workflow_tests

CONTRACT = "canonical_claim_extraction_v1"
TEXT = "当前项目已配置2台服务器，交付时间尚待确认。"


def _task_contract(request):
    for item in request.messages:
        text = item["content"]
        position = text.find("{")
        if position < 0:
            continue
        try:
            value, _end = json.JSONDecoder().raw_decode(text[position:])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and "coverage_units" in value:
            return value
    raise AssertionError("missing locked Claim coverage units")


@pytest.fixture(scope="module", params=("normal", "fallback"))
def claim_mainline(tmp_path_factory, request):
    route = request.param
    root = tmp_path_factory.mktemp("claim340-" + route)
    task_id, run_id = "task340-" + route, "run340-" + route
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    calls = []
    original = FakeLLMClient.generate
    original_extract = TargetClaimExtractionRuntime.extract_and_commit

    def observe_extraction(extractor, **kwargs):
        result = original_extract(extractor, **kwargs)
        assert all(extractor.claim_runtime.resolve_claim_head(record.claim_ref) is None for record in result.records)
        return result

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 4}, deep=True)
        if req.extra.get("response_contract") == CONTRACT:
            units = _task_contract(req)["coverage_units"]
            body = {"units": [
                {"unit_id": unit["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                 "claims": [{"quote": TEXT, "occurrence_index": index,
                             "claim_type": "current_project_quantity"} for index in range(4)]}
                for unit in units
            ]}
            return response.model_copy(update={"content": json.dumps(body, ensure_ascii=False)}, deep=True)
        if route == "fallback" and req.extra.get("response_contract") == "criterion_support_evaluator_v1":
            body = json.loads(response.content)
            body.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(body)}, deep=True)
        return response

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm",
                       supervisor_model_name="fake_llm", enable_llm_routing=False,
                       trace_enabled=True, data_capture_enabled=True,
                       gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default", "FAKE_LLM_SCENARIO": "default",
            "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false",
        }))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetClaimExtractionRuntime, "extract_and_commit", observe_extraction))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    if summary["status"] != "partial_success":
        error = workflow_tests._latest_w13_top_level_payload(engine).terminal_error
        raise AssertionError(error.model_dump(mode="json") if error else summary["status"])
    return calls, engine, summary, route


def test_default_claim_extractor_is_actually_called_with_exact_text(claim_mainline):
    calls, _, _, _ = claim_mainline
    extraction = [req for req in calls if req.extra.get("response_contract") == CONTRACT]
    assert len(extraction) == 1
    assert extraction[0].prompt == ""
    units = _task_contract(extraction[0])["coverage_units"]
    assert len(units) == 1
    assert units[0]["text"] == TEXT * 4
    assert all('"writer_admission_decision"' not in msg["content"] for msg in extraction[0].messages)


def test_real_claim_records_and_lossless_coverage_do_not_manufacture_verification(claim_mainline):
    _, engine, summary, route = claim_mainline
    payload = workflow_tests._latest_w13_section_payload(engine)
    runtime = engine.section_execution_graph.claim_verification_runtime
    accounting = runtime.resolve_claim_coverage(payload.result.canonical_claim_coverage_proof_refs[0])
    assert accounting.accounting_state.value == "FACTUAL_CLAIMS_MATERIALIZED"
    assert len(accounting.coverage_units) == 1
    unit = accounting.coverage_units[0]
    assert (unit.start_offset, unit.end_offset) == (0, len(TEXT * 4))
    assert len(unit.claim_occurrence_refs) == 4
    assert len(set(unit.claim_occurrence_refs)) == 4
    for index, occurrence in enumerate(unit.claim_occurrence_refs):
        ref = runtime.derive_claim_ref(occurrence)
        record = runtime.resolve_claim(ref)
        assert record.normalized_value == TEXT
        assert record.text_span.text == TEXT
        assert record.text_span.start_offset == index * len(TEXT)
        assert record.normalized_value_hash == canonical_sha256(TEXT)
        # Extraction's no-verdict boundary is checked immediately above; a later
        # real canonical Verifier is now composed and its conservative result persists.
        assert runtime.resolve_claim_head(ref) is not None
    assert payload.retrieval_metadata["canonical_claim_count"] == 4
    assert payload.retrieval_metadata["canonical_claim_verifier_invoked"] is True
    assert payload.retrieval_metadata["canonical_claim_verification_count"] == 4
    assert summary["status"] == "partial_success"


def _unit_env(tmp_path):
    from types import SimpleNamespace
    from schemas.model import ModelResponseSchema
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import TargetClaimExtractionRuntime
    import test_multi_agent_v3_claim_verification_runtime as claim_tests
    env, paragraphs, runtime, version, manifest, *_ = claim_tests._setup_claim_runtime(tmp_path)
    context = claim_tests._context(env)
    binding = paragraphs.resolve_paragraph_binding(logical_ref=version.logical_ref, manifest_ref=manifest)
    class Gateway:
        def __init__(self):
            self.calls = []
            self.output = None
            self.failure = None
            self.hook = None
        def routing_capabilities(self, **_kwargs):
            return {"safe_context_window": 32768, "safe_max_output_tokens": 4096,
                    "candidate_profiles": ["test"], "candidate_models": ["fake_llm"]}
        def generate(self, request):
            self.calls.append(request.model_copy(deep=True))
            if self.failure is not None:
                raise self.failure
            if self.hook is not None:
                self.hook()
            return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
                run_id=request.run_id, model_name="fake_llm", success=True,
                content=self.output if isinstance(self.output, str) else json.dumps(self.output),
                finish_reason="stop", created_at=request.created_at)
    gateway = Gateway()
    extractor = TargetClaimExtractionRuntime(claim_runtime=runtime, model_gateway=gateway)
    units = extractor._units((binding,), context)
    gateway.output = {"units": [{"unit_id": units[0]["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                                 "claims": [{"quote": "The approved capacity is 100 MW.", "occurrence_index": 0,
                                             "claim_type": "current_project_configuration"}]}]}
    task_id = runtime.lifecycle_service.load_run_identity(context.active_lineage.run_id).task_id
    return SimpleNamespace(extractor=extractor, gateway=gateway, context=context, binding=binding, units=units,
                           runtime=runtime, paragraphs=paragraphs, version=version, manifest=manifest, task_id=task_id,
                           fixture_env=env)


def _extract(env):
    return env.extractor.extract_and_commit(task_id=env.task_id, bindings=(env.binding,), operation_context=env.context)


@pytest.mark.parametrize("defect", ["missing-unit", "duplicate-unit", "unknown-unit", "bad-quote", "wrong-occurrence",
                                     "duplicate-claim", "no-claim-with-values", "empty-factual", "extra-support", "invalid-type",
                                     "bad-json", "duplicate-json-key"])
def test_invalid_claim_proposal_never_creates_canonical_claims(tmp_path, defect):
    env = _unit_env(tmp_path)
    proposal = env.gateway.output
    unit = proposal["units"][0]
    claim = unit["claims"][0]
    if defect == "missing-unit":
        proposal["units"] = []
    elif defect == "duplicate-unit":
        proposal["units"].append(unit)
    elif defect == "unknown-unit":
        unit["unit_id"] = "another-unit"
    elif defect == "bad-quote":
        claim["quote"] = "invented 999 MW"
    elif defect == "wrong-occurrence":
        claim["occurrence_index"] = 1
    elif defect == "duplicate-claim":
        unit["claims"].append(claim)
    elif defect == "no-claim-with-values":
        unit["accounting_state"] = "NO_APPLICABLE_FACTUAL_CLAIM"
    elif defect == "empty-factual":
        unit["claims"] = []
    elif defect == "extra-support":
        claim["verified"] = True
    elif defect == "invalid-type":
        claim["claim_type"] = "quantity"
    elif defect == "bad-json":
        env.gateway.output = "invalid"
    else:
        env.gateway.output = '{"units":[],"units":[]}'
    with pytest.raises(ValueError):
        _extract(env)
    assert len(env.gateway.calls) == 1
    assert not env.runtime.ledger_path.exists(), "Bad model output cannot commit canonical existence or coverage"


def test_claim_materialization_replays_receipt_and_canonical_commit(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import TargetClaimExtractionRuntime
    from schemas.context import ContextPackageSchema
    env = _unit_env(tmp_path)
    first = _extract(env)
    before = env.runtime.ledger_path.read_bytes()
    env.extractor = TargetClaimExtractionRuntime(claim_runtime=env.runtime, model_gateway=env.gateway)
    second = _extract(env)
    assert first.records == second.records
    assert first.coverage_proof_ref == second.coverage_proof_ref
    assert env.runtime.ledger_path.read_bytes() == before
    assert len(env.gateway.calls) == 1
    ref = next(item for item in env.extractor.store.list_references() if item.artifact_type == "claim_extraction_input")
    receipt = env.extractor.store.get_json(ref.artifact_ref, task_id=env.task_id, run_id=env.context.active_lineage.run_id)
    package = ContextPackageSchema.model_validate(receipt["context_package"])
    actual = {"input_kind": "MESSAGES", "messages": env.gateway.calls[0].messages}
    assert package.model_input.final_input_material.inline_final_input_payload == actual
    assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
    assert {item.source_binding.source_family.value for item in package.candidate_decisions} == {"PARAGRAPH_ARTIFACT"}


@pytest.mark.parametrize("state", ["NO_APPLICABLE_FACTUAL_CLAIM", "INDETERMINATE"])
def test_zero_claim_state_requires_explicit_semantic_accounting(tmp_path, state):
    env = _unit_env(tmp_path)
    env.gateway.output["units"][0].update(accounting_state=state, claims=[])
    result = _extract(env)
    assert result.records == ()
    assert result.coverage_accounting.accounting_state.value == state
    assert len(env.gateway.calls) == 1


def test_partial_semantic_accounting_preserves_claims_without_complete_coverage(tmp_path):
    env = _unit_env(tmp_path)
    env.gateway.output["units"][0]["accounting_state"] = "INDETERMINATE"
    result = _extract(env)
    assert len(result.records) == 1
    assert result.coverage_accounting.accounting_state.value == "INDETERMINATE"
    assert env.runtime.resolve_claim_head(result.records[0].claim_ref) is None


def test_lost_extraction_result_never_blindly_redispatches(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import ClaimExtractionError
    env = _unit_env(tmp_path)
    env.gateway.failure = SystemExit("simulated process crash")
    with pytest.raises(SystemExit):
        _extract(env)
    env.gateway.failure = None
    with pytest.raises(ClaimExtractionError, match="no automatic redispatch"):
        _extract(env)
    assert len(env.gateway.calls) == 1


def test_known_extraction_failure_replays_failure_not_empty_claim_set(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import ClaimExtractionError
    from schemas.failure import FailureCategory
    env = _unit_env(tmp_path)
    env.gateway.output = "broken JSON"
    with pytest.raises(ValueError):
        _extract(env)
    with pytest.raises(ClaimExtractionError) as failure:
        _extract(env)
    assert failure.value.failure_category is FailureCategory.VALIDATION
    assert len(env.gateway.calls) == 1
    assert not env.runtime.ledger_path.exists()


def test_extraction_budget_failure_keeps_origin_on_replay(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import ClaimExtractionError
    from schemas.failure import FailureCategory, FailureOrigin
    env = _unit_env(tmp_path)
    env.gateway.failure = CanonicalBudgetCapacityExceeded("model_tokens_total", limit=0, remaining=0, required=1)
    with pytest.raises(CanonicalBudgetCapacityExceeded):
        _extract(env)
    with pytest.raises(ClaimExtractionError) as replay:
        _extract(env)
    assert replay.value.failure_category is FailureCategory.RESOURCE_EXHAUSTED
    assert replay.value.failure_origin is FailureOrigin.CANONICAL_BUDGET
    assert len(env.gateway.calls) == 1


def test_corrupt_claim_extraction_receipt_is_not_treated_as_missing(tmp_path):
    env = _unit_env(tmp_path)
    _extract(env)
    ref = next(item for item in env.extractor.store.list_references() if item.artifact_type == "claim_extraction_result")
    env.extractor.store._path_for_ref(ref.artifact_ref).write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        _extract(env)
    assert len(env.gateway.calls) == 1


def test_superseded_extraction_cannot_commit_old_claims(tmp_path):
    env = _unit_env(tmp_path)
    def supersede():
        lineage = env.context.active_lineage
        env.runtime.lifecycle_service.open_attempt(lineage.run_id, lineage.attempt_scope_ref, "replacement-snapshot")
    env.gateway.hook = supersede
    with pytest.raises(ValueError):
        _extract(env)
    assert len(env.gateway.calls) == 1
    assert not env.runtime.ledger_path.exists()


def test_repeated_quote_locator_preserves_unicode_offsets_and_qualifiers():
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import exact_quote_offsets
    text = "计划建设双路供电；计划建设双路供电，尚未验收。"
    start, end = exact_quote_offsets(text, "计划建设双路供电", 1)
    assert start == len("计划建设双路供电；")
    assert text[start:end] == "计划建设双路供电"
    with pytest.raises(ValueError):
        exact_quote_offsets(text, "已建成双路供电", 0)
    for quote, occurrence in ((" ", 0), ("计划", -1), ("计划", True), ("计划", 10**30)):
        with pytest.raises(ValueError):
            exact_quote_offsets(text, quote, occurrence)


def test_partial_manifest_cannot_be_claimed_as_complete_section_coverage(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import ClaimExtractionError
    from test_multi_agent_v3_paragraph_runtime import _candidate, _commit_promote, _append
    env = _unit_env(tmp_path)
    candidate = _candidate(env.fixture_env, env.paragraphs, slot="claim-second-paragraph", text="A second factual paragraph.")
    second, _ = _commit_promote(env.fixture_env, env.paragraphs, candidate)
    _append(env.fixture_env, env.paragraphs, env.manifest,
            expected_members=(env.version,), resulting_members=(env.version, second), revision=1,
            authorization_ref=candidate.operation_ref.authorization_ref, slot="append-second-claim-paragraph")
    env.binding = env.paragraphs.resolve_paragraph_binding(logical_ref=env.version.logical_ref, manifest_ref=env.manifest)
    with pytest.raises(ClaimExtractionError, match="complete current Paragraph manifest"):
        _extract(env)
    assert env.gateway.calls == []


def test_two_extractor_instances_share_one_execution_and_claim_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import TargetClaimExtractionRuntime
    env = _unit_env(tmp_path)
    started, release = Event(), Event()
    def held():
        started.set()
        assert release.wait(10)
    env.gateway.hook = held
    other = TargetClaimExtractionRuntime(claim_runtime=env.runtime, model_gateway=env.gateway)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_extract, env)
        assert started.wait(10)
        second = pool.submit(other.extract_and_commit, task_id=env.task_id, bindings=(env.binding,), operation_context=env.context)
        release.set()
        left, right = first.result(timeout=20), second.result(timeout=20)
    assert left.records == right.records
    assert left.coverage_proof_ref == right.coverage_proof_ref
    assert len(env.gateway.calls) == 1
