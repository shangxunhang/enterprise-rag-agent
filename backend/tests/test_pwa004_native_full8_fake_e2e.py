"""PWA-004: one fresh, complete Mainline Run with genuine M6 refusal.

The only injected facts are deterministic FakeLLM responses and the explicitly
opted-in ProjectInput. No Section/Stage result is mocked, no authoritative
Claim, Support or VerificationResult is synthesized.
Run ONLY on an isolated E: pytest --basetemp; no real Provider or TLS.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
from apps.enterprise_document.services.scheme_writer.agent_integration.planner_adapter import MainlinePlannerAdapter
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime, VerifierSupportVerdictAdmissionError,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph, PWA004_REJECTION_KEY, PWA004_REVIEW_POLICY,
)
from model_gateway.fake_llm_client import FakeLLMClient
import bca014_i10_capacity_fixtures as fake_policy
from test_phase3_section_execution_subgraph import _run_fake

FACTUAL_SENTENCE = "当前项目已配置2台服务器，计划于2027年启动资源扩容。"
SECTION7_BODY = FACTUAL_SENTENCE * 4
EXTRACTION = "canonical_claim_extraction_v1"
VERIFIER = "canonical_claim_verifier_v2"


def _extract_units(request):
    for message in request.messages:
        content = str(message.get("content") or "")
        left = content.find("{")
        if left < 0:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[left:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "coverage_units" in obj:
            return obj["coverage_units"]
    raise AssertionError("real Claim extraction request lacks complete coverage units")


def _verifier_task_material(request):
    for message in request.messages:
        content = str(message.get("content") or "")
        try:
            obj = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and "shared_support" in obj and "allowed_authority_refs" in obj:
            return obj
    raise AssertionError("real verifier request lacks admitted Support/authority task contract")


@pytest.mark.parametrize('hard_dep', [False, True], ids=['independent-eighth', 'hard-dependent-eighth'])
@pytest.mark.parametrize('warm_replay', [False, True], ids=['cold-full8','review-checkpoint-reuse'])
def test_native_m6_refusal_continues_full8_and_returns_unreleased_review_draft(tmp_path, warm_replay, hard_dep):
    _run_native_m6_refusal(tmp_path, warm_replay=warm_replay, hard_dep=hard_dep)


def _run_native_m6_refusal(tmp_path, *, warm_replay, hard_dep, d09_capture=None):
    """Reuse the unchanged native assertions for bounded D09 acceptance."""
    original_fake = FakeLLMClient.generate
    original_plan = MainlinePlannerAdapter.plan
    original_section_execute = SectionExecutionGraph.execute
    recovered = []
    original_read = SchemeInputService.read_inputs
    original_policy = fake_policy.explicit_fake_startup_policy
    original_m6 = ClaimVerificationRuntime.validate_v2_provenance
    counts = Counter()
    seen_model_inputs = []
    m6_actual_refusals = []

    def fake_budget(root, run_id):
        path, default, before = original_policy(root, run_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["canonical_budget"]["logical_model_calls"] = 80
        payload["canonical_budget"]["model_tokens_total"] = 160000
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path, default, before

    def policy(service, shared_state):
        project, tables, facts = original_read(service, shared_state)
        req = project.generation_requirements
        updated = req.model_copy(update={"extra": {
            **dict(req.extra or {}),
            "verifier_rejection_policy": PWA004_REVIEW_POLICY,
            "verifier_decision_profile": "claim-complete-support-v1",
        }}, deep=True)
        return project.model_copy(update={"generation_requirements": updated}, deep=True), tables, facts

    def make_hard_dependency(adapter, **kwargs):
        outcome = original_plan(adapter, **kwargs)
        members = list(outcome.document_plan.sections)
        assert len(members) == 8
        members[7] = members[7].model_copy(
            update={"depends_on": [members[6].section_id]}, deep=True)
        return replace(outcome, document_plan=outcome.document_plan.model_copy(
            update={"sections": members}, deep=True))

    def model(client, request):
        contract = request.extra.get("response_contract")
        purpose = request.extra.get("call_purpose")
        section_id = str(request.extra.get("section_id") or "")
        counts["all_model_calls"] += 1
        counts["contract:" + str(contract)] += 1
        if purpose == "scheme_section_generation":
            seen_model_inputs.append((section_id, request.extra.get("section_title"),
                                      request.model_call_id))
            if section_id.endswith("_008"):
                # Check actual Writer invocation, not only Stage.previous_sections.
                assert FACTUAL_SENTENCE not in request.prompt
                assert all(FACTUAL_SENTENCE not in str(row.get("content") or "")
                           for row in request.messages), (
                    "unverified Section 7 body contaminated Section 8 Writer Context"
                )
        response = original_fake(client, request)
        if purpose == "scheme_section_generation" and section_id.endswith("_007"):
            counts["writer7"] += 1
            return response.model_copy(update={"content": SECTION7_BODY}, deep=True)
        if contract == EXTRACTION and section_id.endswith("_007"):
            units = _extract_units(request)
            # Every exact immutable Paragraph is accounted for; only the one
            # carrying the factual sentence gets a true Claim.
            rows = []
            for unit in units:
                text = unit["text"]
                if FACTUAL_SENTENCE in text:
                    rows.append({"unit_id": unit["unit_id"],
                                 "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                                 "claims": [{"quote": FACTUAL_SENTENCE,
                                             "occurrence_index": index,
                                             "claim_type": "current_project_quantity"}
                                            for index in range(text.count(FACTUAL_SENTENCE))]})
                else:
                    rows.append({"unit_id": unit["unit_id"],
                                 "accounting_state": "INDETERMINATE", "claims": []})
            counts["materialized_units7"] += len(rows)
            return response.model_copy(update={"content": json.dumps({"units": rows}, ensure_ascii=False)}, deep=True)
        if contract == VERIFIER and section_id.endswith("_007"):
            task = _verifier_task_material(request)
            assert task["shared_support"]["relation"] in {"INDETERMINATE", "CONTRADICTED"}
            # The ProjectInput basis is admitted by the candidate schema but
            # cannot override unresolved/contradictory Shared Support.
            allowed = task["allowed_authority_refs"]
            chosen = next((ref for ref in allowed if "project" in ref.lower()), allowed[-1])
            candidate = {
                "verdict": "VERIFIED",
                "rationale": "Synthetic invalid verdict to exercise actual M6 admission.",
                "positive_basis_refs": [chosen],
                "issues": [],
            }
            counts["illegal_verifier7"] += 1
            return response.model_copy(update={"content": json.dumps(candidate, ensure_ascii=False)}, deep=True)
        return response

    def observed_m6(binding, support, candidate):
        try:
            return original_m6(binding, support, candidate)
        except VerifierSupportVerdictAdmissionError as exc:
            m6_actual_refusals.append({
                "claim": exc.claim_ref.claim_id,
                "attempt_id": exc.active_lineage.attempt_id,
                "support_relation": exc.support_relation,
            })
            raise

    def execute_and_recover(graph, request, *, shared_state, citation_registry, workflow_namespace):
        result = original_section_execute(
            graph, request, shared_state=shared_state, citation_registry=citation_registry,
            workflow_namespace=workflow_namespace)
        if d09_capture is not None and result.section.section_order == 7:
            from copy import deepcopy
            d09_capture["native_rejections_before_stage"] = deepcopy(
                result.section.extra[PWA004_REJECTION_KEY])
        if warm_replay and result.section.section_order == 7:
            before = counts["all_model_calls"]
            reused = graph.recover_or_reuse(
                workflow_namespace=workflow_namespace,
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                document_id=request.document_id,
                section_id=result.section.section_id,
                project_input=request.project_input,
                section_plan=request.section_plan,
                shared_state=shared_state,
                citation_registry=citation_registry,
                expected_canonical_plan_binding=request.canonical_plan_binding,
            )
            assert reused == result, "recovered review-only Section differs"
            assert counts["all_model_calls"] == before, "warm reuse redispatched Fake Provider"
            recovered.append(result.section.section_id)
            # A checkpoint's descriptive metadata cannot authorize a forged
            # Candidate. Corrupt only an in-memory clone; no canonical bytes
            # or writer artifacts are modified.
            history = graph.checkpoint_history(
                workflow_namespace=workflow_namespace,
                run_id=shared_state.run_id, section_id=result.section.section_id)
            persisted = None
            for row in history:
                values = row.checkpoint.get("channel_values", {})
                if isinstance(values.get("section_payload"), dict):
                    persisted = graph.codec.restore_payload({
                        "section_graph_state_version": values["section_graph_state_version"],
                        "section_payload": values["section_payload"],
                    })
                    break
            assert persisted is not None and persisted.completed
            rows = persisted.retrieval_metadata[PWA004_REJECTION_KEY]
            fake_rows = [dict(item) for item in rows]
            fake_rows[0]["candidate_ref"] = "artifact://forged-candidate/candidate"
            forged = persisted.model_copy(update={
                "retrieval_metadata": {
                    **persisted.retrieval_metadata,
                    PWA004_REJECTION_KEY: fake_rows,
                },
            }, deep=True)
            with pytest.raises((RuntimeError, ValueError)):
                graph._pwa004_validate_recovered_rejections(forged)
            assert counts["all_model_calls"] == before
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(fake_policy, "explicit_fake_startup_policy", fake_budget))
        stack.enter_context(patch.object(SchemeInputService, "read_inputs", policy))
        if hard_dep:
            stack.enter_context(patch.object(
                MainlinePlannerAdapter, "plan", make_hard_dependency))
        stack.enter_context(patch.object(FakeLLMClient, "generate", model))
        if warm_replay:
            stack.enter_context(patch.object(SectionExecutionGraph, "execute", execute_and_recover))
        stack.enter_context(patch.object(ClaimVerificationRuntime, "validate_v2_provenance",
                                         staticmethod(observed_m6)))
        if d09_capture is not None:
            from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
            original_explanation = SchemeGenerationStageService._pwa004_rejection_explanation

            def guarded_explanation(stage, *, payload, section_result, row):
                if "live_stage_guards" not in d09_capture:
                    d09_capture["live_stage_guards"] = _d09_probe_live_stage_guards(
                        stage, original_explanation, payload=payload,
                        section_result=section_result, row=row, isolated_root=tmp_path)
                return original_explanation(stage, payload=payload,
                    section_result=section_result, row=row)

            stack.enter_context(patch.object(SchemeGenerationStageService,
                "_pwa004_rejection_explanation", guarded_explanation))
        summary, factory = _run_fake(tmp_path, run_id="pwa004_native_m6_full8_fake")

    print("PWA004_INTEGRATION_OBS", json.dumps({
        "status": summary["status"],
        "warm_replay": warm_replay, "hard_dep": hard_dep,
        "model_calls": dict(counts),
        "real_m6_refusals": m6_actual_refusals,
        "writers": seen_model_inputs,
        "top_error": (summary.get("supervisor_result") or {}).get("error", {}).get("error_code"),
        "draft_sections": len((summary.get("generated_draft") or {}).get("sections", [])),
    }, ensure_ascii=False, default=str))
    assert recovered == ([summary['generated_draft']['sections'][6]['section_id']] if warm_replay else [])
    assert counts["writer7"] >= 1, "FakeWriter did not actually produce target factual paragraph"
    assert counts["illegal_verifier7"] >= 1, "Real Verifier was never reached on target Section"
    assert m6_actual_refusals, "Original M6 never rejected the genuine candidate"
    assert summary["status"] == "failed"
    draft = summary["generated_draft"]
    assert draft
    assert len(draft["sections"]) == (7 if hard_dep else 8)
    assert draft["sections"][6]["section_order"] == 7
    assert FACTUAL_SENTENCE in draft["sections"][6]["content"]
    if hard_dep:
        assert len(draft["missing_sections"]) == 1
        assert not any(section_id.endswith("_008") for section_id, *_ in seen_model_inputs)
    else:
        assert draft["sections"][7]["section_order"] == 8
        assert any(section_id.endswith("_008") for section_id, *_ in seen_model_inputs)
    review = draft["extra"]["pwa004_review_draft"]
    if hard_dep:
        assert len(review["hard_dependency_blocked"]) == 1
        assert review["hard_dependency_blocked"][0]["reason_code"] == "PWA004_HARD_DEPENDENCY_UNVERIFIED"
        assert review["hard_dependency_blocked"][0]["blocked_by"] == [draft["sections"][6]["section_id"]]
    else:
        assert review["hard_dependency_blocked"] == []
    assert review["rejections"]
    assert review["rejections"][0]["claim_ref"]["claim_id"] == m6_actual_refusals[0]["claim"]
    assert review["rejections"][0]["attempt_id"] == m6_actual_refusals[0]["attempt_id"]
    assert summary["supervisor_result"]["error"]["error_code"] == "VERIFIER_ADMISSION_INCOMPLETE"
    assert summary["supervisor_result"]["error"]["details"]["c1_triggering_attempt_id"] == m6_actual_refusals[0]["attempt_id"]
    assert summary["target_release_publication"] is None
    assert summary["deliverable_final_result"] is None
    assert summary["task_state"]["status"] == "failed"

    # Read from the actual canonical owners after the terminal event. A
    # Writer's successful generation is not a formal Section acceptance.
    from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRefSchema
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
    engine = factory.engines[0]
    m6 = engine.section_execution_graph.claim_verification_runtime
    lifecycle = engine.stage_service.target_workflow_runtime.lifecycle_service
    run = lifecycle.load_run_lifecycle(summary["run_id"])
    assert run.status is RunLifecycleStatus.FAILED
    assert run.run_completion_assessment_ref is None
    owner = engine.stage_service.target_workflow_runtime.section_dependencies
    native_sources = [
        ref for ref in owner.store.list_references()
        if ref.run_id == summary["run_id"] and ref.artifact_type == "section_source_projection"
    ]
    assert all(ref.section_id != draft["sections"][6]["section_id"] for ref in native_sources)
    assert m6.ledger_path.is_file()
    for row in review["rejections"]:
        claim = ClaimRefSchema.model_validate(row["claim_ref"])
        assert m6.resolve_claim_head(claim) is None
        assert row["rule_code"] == "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT"
        assert row["support_relation"] == "INDETERMINATE"
        assert row["candidate_ref"].endswith("/candidate")
        assert row["attempt_id"] == m6_actual_refusals[0]["attempt_id"]
    budget = engine.budget_runtime(summary["task_id"], summary["run_id"]).canonical_state()
    assert budget.used.logical_model_calls == counts["all_model_calls"]
    assert not any(budget.active_reserved.model_dump().values())
    assert not summary.get("rejected_artifact")
    assert summary["task_state"]["status"] == "failed"

    # Exercise the real business worker's serialization helper, but never
    # invoke its online entrypoint, credential bootstrap or Provider hooks.
    import importlib.util
    worker_file = (Path(__file__).resolve().parents[2] /
        "docs/post_r13_business_improvements_audits/"
        "POST_BCA014_REAL_BUSINESS_E2E_20261003_161008_c0090159/"
        "real_business_worker.py")
    spec = importlib.util.spec_from_file_location("pwa004_worker_serializer", worker_file)
    worker_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker_module)
    saved = tmp_path / "offline_summary_roundtrip.json"
    with saved.open("x", encoding="utf-8") as output:
        output.write(json.dumps(worker_module.serializable(summary), ensure_ascii=False))
    reread = json.loads(saved.read_text(encoding="utf-8"))
    assert reread["generated_draft"] == draft
    assert reread["supervisor_result"]["error"]["error_code"] == "VERIFIER_ADMISSION_INCOMPLETE"
    assert reread["target_release_publication"] is None
    return summary, factory, dict(counts)


def _d09_probe_live_stage_guards(stage, original, *, payload, section_result, row, isolated_root):
    """Exercise real guards while this new test's rejected Attempt is still live.

    These expected refusals are method-level guard probes, not failed Run or
    terminal-recovery claims. Only this invocation's temporary input is altered;
    its exact original bytes are restored before the genuine Stage continues.
    """
    import base64
    from copy import deepcopy
    from hashlib import sha256
    from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageFailure

    verifier = stage.section_execution_graph.coordinator.claim_verifier_composition
    source_ref = row["candidate_ref"].rsplit("/", 1)[0] + "/input"
    source_path = verifier.store._path_for_ref(source_ref).resolve()
    source_path.relative_to(isolated_root.resolve())
    source_bytes = source_path.read_bytes()
    source_sha = sha256(source_bytes).hexdigest()
    lifecycle = stage.target_workflow_runtime.lifecycle_service
    assert lifecycle.load_run_lifecycle(payload.run_id).status.value == "running"
    observations = []

    def rejected(label, candidate_row):
        with pytest.raises(SchemeGenerationStageFailure) as failure:
            original(stage, payload=payload, section_result=section_result, row=candidate_row)
        assert failure.value.error.error_code == "PWA004_REVIEW_EXPLANATION_INVALID"
        assert failure.value.error.failed_node == "execute_sections"
        assert failure.value.error.failure_category.value == "validation"
        observations.append({
            "case": label, "TARGET_ASSERTION_REACHED": True,
            "error_type": failure.value.error.error_type,
            "error_code": failure.value.error.error_code,
        })
        return failure.value

    for defect in (
        "wrong_run", "wrong_attempt", "wrong_claim", "wrong_occurrence",
        "wrong_candidate", "wrong_fingerprint", "wrong_binding", "wrong_support",
    ):
        altered = deepcopy(row)
        if defect == "wrong_run":
            altered["claim_occurrence_ref"]["run_id"] = "other-d09-run"
        elif defect == "wrong_attempt":
            altered["attempt_id"] = "other-d09-attempt"
        elif defect == "wrong_claim":
            altered["claim_ref"]["claim_id"] = "claim://sha256/" + "0" * 64
        elif defect == "wrong_occurrence":
            altered["claim_occurrence_ref"]["start_offset"] += 1
        elif defect == "wrong_candidate":
            altered["candidate_ref"] = "artifact://canonical-verifier-v2/" + "0" * 64 + "/candidate"
        elif defect == "wrong_fingerprint":
            altered["verifier_semantic_input_sha256"] = "0" * 64
        elif defect == "wrong_binding":
            altered["binding_facts_ref"]["binding_facts_id"] = "other-d09-binding"
        else:
            altered["support_ref"]["claim_evidence_support_id"] = "other-d09-support"
        rejected(defect, altered)

    for defect in ("missing_input", "input_hash_tamper", "input_rehashed_tamper", "unauthorized_evidence"):
        try:
            if defect == "missing_input":
                source_path.unlink()
            else:
                envelope = json.loads(source_bytes)
                material = json.loads(base64.b64decode(envelope["content_base64"]))
                if defect == "unauthorized_evidence":
                    assert material["input_material"]["evidence"]
                    material["input_material"]["evidence"][0]["exact_source_ref"] = "evidence://other-run/d09"
                else:
                    material["input_material"]["paragraph_text"] += " D09_TAMPER_SENTINEL"
                content = json.dumps(material, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode("utf-8")
                envelope["content_base64"] = base64.b64encode(content).decode("ascii")
                if defect != "input_hash_tamper":
                    envelope["reference"]["content_hash"] = "sha256:" + sha256(content).hexdigest()
                    envelope["reference"]["size_bytes"] = len(content)
                source_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            failure = rejected(defect, deepcopy(row))
            if defect in {"input_rehashed_tamper", "unauthorized_evidence"}:
                assert failure.error.error_type == "ValueError"
                assert str(failure.__cause__) == (
                    "PWA004 explanation input differs from its canonical visible sources")
        finally:
            source_path.write_bytes(source_bytes)
        assert sha256(source_path.read_bytes()).hexdigest() == source_sha
    return {
        "scope": "live Stage method guards; not complete failed Run",
        "input_ref": source_ref, "input_envelope_sha256": source_sha,
        "input_restored_exactly": True, "cases": observations,
    }


def _d09_cold_checkpoint_snapshot(output_root, task_id, run_id):
    """Run in a fresh Python process; authenticate and only read checkpoints."""
    import os
    from dataclasses import replace
    from application.mainline_service import MainlineApplicationService
    from core.config import get_settings
    from schemas.rag import RetrievalAccessScopeSchema

    root = Path(output_root)
    settings = replace(get_settings(), data_root=root,
        default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=False)
    kwargs = dict(project_root=Path(__file__).resolve().parents[2],
        task_id=task_id, run_id=run_id, output_root=root, settings=settings,
        allow_demo_defaults=False, enable_agent_self_rag=False,
        retrieval_access_scope=RetrievalAccessScopeSchema(
            tenant_id="default", authorized_kb_ids=["kb-authorized"]))
    with patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false",
            "ENABLE_AGENT_SELF_RAG": "false"}), patch.object(FakeLLMClient,
            "generate", side_effect=AssertionError("D09 cold read must not dispatch")):
        service = MainlineApplicationService()
        inspection = service.inspect_run(**kwargs)
        terminal = service.read_terminal_result(**kwargs)
    return {
        "kind": "new-process authenticated checkpoint cold read; no recovery",
        "generated_draft": terminal["generated_draft"],
        "graph_generated_draft": inspection["graph_state"]["generated_draft"],
        "terminal_outcome": terminal["terminal_outcome"],
        "status": terminal["status"],
        "provider_redispatch": False,
    }


def _d09_read_checkpoint_in_fresh_process(output_root, task_id, run_id):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
        "USE_REAL_RAG_TOOL": "false", "ENABLE_AGENT_SELF_RAG": "false"}
    env["PYTHONPATH"] = os.pathsep.join([
        str(root / "backend"), str(root / "backend/tests"),
        str(root / "scripts"), str(root),
        *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []),
    ])
    program = (
        "import json,sys; "
        "from test_pwa004_native_full8_fake_e2e import _d09_cold_checkpoint_snapshot; "
        "print('D09_COLD_JSON='+json.dumps(_d09_cold_checkpoint_snapshot("
        "sys.argv[1],sys.argv[2],sys.argv[3]),ensure_ascii=False))"
    )
    result = subprocess.run([sys.executable, "-B", "-c", program,
        str(output_root), task_id, run_id], cwd=root, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line.removeprefix("D09_COLD_JSON=") for line in result.stdout.splitlines()
        if line.startswith("D09_COLD_JSON=")]
    assert len(lines) == 1, result.stdout + result.stderr
    return json.loads(lines[0])


@pytest.mark.parametrize("hard_dep", [False, True], ids=["d09-full8", "d09-hard-dependent"])
def test_d09_native_refusal_explanations_are_independent_and_exact(
    tmp_path, hard_dep, monkeypatch, capsys,
):
    from copy import deepcopy
    from hashlib import sha256
    from application.run_workspace import RunWorkspace
    from core.hashing import canonical_sha256
    from test_mainline_closure_acceptance import _d12_cli

    capture = {}
    summary, factory, counts = _run_native_m6_refusal(
        tmp_path, warm_replay=True, hard_dep=hard_dep, d09_capture=capture)
    draft = summary["generated_draft"]
    review = draft["extra"]["pwa004_review_draft"]
    assert len(review["rejections"]) == 4
    explanations = review.get("rejection_explanations")
    assert isinstance(explanations, list) and len(explanations) == 4, (
        "D09 requires one native-source explanation per unchanged M6 rejection")
    before_rows = capture["native_rejections_before_stage"]
    assert [{key: value for key, value in row.items()
        if key not in {"section_id", "section_title"}}
        for row in review["rejections"]] == before_rows
    section = draft["sections"][6]
    assert section["extra"][PWA004_REJECTION_KEY] == before_rows
    assert section["extra"]["pwa004_rejection_explanations"] == explanations
    expected_body_sha = ("f62657f607b693105d6df7399d698a059a88de2c2f7a986e9c748237525ea906"
        if hard_dep else "df1e9efb961c8c2cb0660c07a8c7c25ee94c7feef287f428876a537ca3aef130")
    body_bytes = draft["full_text"].encode("utf-8")
    assert sha256(body_bytes).hexdigest() == expected_body_sha
    verifier = factory.engines[0].section_execution_graph.coordinator.claim_verifier_composition
    native_inputs = []
    offsets = []
    for row, explanation in zip(review["rejections"], explanations):
        assert {key: explanation[key] for key in row} == row
        assert (explanation["task_id"], explanation["run_id"]) == (
            summary["task_id"], summary["run_id"])
        input_ref = row["candidate_ref"].rsplit("/", 1)[0] + "/input"
        reference, native = verifier.store.get_json_with_reference(input_ref,
            task_id=summary["task_id"], run_id=summary["run_id"], section_id=row["section_id"])
        material = native["input_material"]
        occurrence = row["claim_occurrence_ref"]
        assert explanation["input_ref"] == input_ref
        assert explanation["input_content_hash"] == reference.content_hash
        assert native["input_fingerprint"] == row["verifier_semantic_input_sha256"]
        assert explanation["claim_text"] == material["claim"]["text_span"]["text"] == FACTUAL_SENTENCE
        assert explanation["paragraph_text"] == material["paragraph_text"] == SECTION7_BODY
        for field in ("start_offset", "end_offset", "text_span_hash"):
            assert explanation[field] == occurrence[field]
        assert explanation["paragraph_text"][explanation["start_offset"]:explanation["end_offset"]] == FACTUAL_SENTENCE
        assert sha256(FACTUAL_SENTENCE.encode("utf-8")).hexdigest() == explanation["text_span_hash"]
        assert explanation["binding_facts"] == material["binding_facts"]
        assert explanation["shared_support"] == {
            name: material["shared_support"][name]
            for name in ("relation", "supporting_refs", "contradicting_refs")
        }
        assert explanation["shared_support"]["relation"] == "INDETERMINATE"
        expected_evidence = [{key: item[key] for key in (
            "exact_source_ref", "evidence_id", "doc_id", "title",
            "context_text", "context_text_sha256")} for item in material["evidence"]]
        for item in expected_evidence:
            # The original Source owner hashes this JSON string canonically;
            # Claim spans and Markdown bytes use their separate raw SHA contract.
            assert item["context_text_sha256"] == canonical_sha256(item["context_text"])
        assert explanation["evidence"] == expected_evidence
        assert expected_evidence, "the original admitted Evidence content must actually be shown"
        assert explanation["candidate_verdict"] == "VERIFIED"
        assert explanation["verification_result_committed"] is False
        assert explanation["explanation"] and len(explanation["limitations"]) == 3
        assert explanation["explanation_sha256"] == canonical_sha256({
            key: value for key, value in explanation.items() if key != "explanation_sha256"})
        offsets.append((explanation["start_offset"], explanation["end_offset"]))
        native_inputs.append({
            "candidate_ref": row["candidate_ref"], "input_ref": input_ref,
            "input_content_hash": reference.content_hash,
            "input_fingerprint": native["input_fingerprint"],
            "claim_text": explanation["claim_text"],
            "evidence": explanation["evidence"],
            "explanation_sha256": explanation["explanation_sha256"],
        })
    assert len(set(offsets)) == 4, "four native refusals refer to four physical occurrences"
    assert capture["live_stage_guards"]["input_restored_exactly"] is True
    assert len(capture["live_stage_guards"]["cases"]) == 12

    # The live Section recovery in the native helper and this new-process cold
    # read exercise distinct contracts; neither is a replay of a saved summary.
    cold = _d09_read_checkpoint_in_fresh_process(tmp_path, summary["task_id"], summary["run_id"])
    assert cold["generated_draft"] == draft
    assert cold["graph_generated_draft"] == draft
    assert cold["terminal_outcome"] == summary["terminal_outcome"]
    assert cold["status"] == "failed" and cold["provider_redispatch"] is False

    # Reuse the genuinely executed native summary. Only the completed runtime
    # boundary is replayed; exporter, CLI main, validator and file IO are real.
    capsys.readouterr()
    cli_observations = []
    for strict in (False, True):
        workspace = RunWorkspace(tmp_path / ("cli_strict" if strict else "cli_ordinary"),
            summary["task_id"], summary["run_id"])
        delivered = deepcopy(summary)
        for key, destination in (("trace", workspace.trace_path),
                ("task_state", workspace.task_state_path)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(Path(summary["paths"][key]).read_bytes())
        delivered["paths"] = workspace.paths()
        with monkeypatch.context() as patcher:
            public, exit_code, output = _d12_cli(
                delivered, workspace, patcher, capsys, strict=strict)
        assert exit_code == (1 if strict else 0)
        assert public["status"] == "TECHNICAL_FAILURE"
        assert public["terminal_outcome"] == summary["terminal_outcome"]
        assert public["review_delivery"]["body_delivered"] is True
        assert public["review_delivery"]["findings_delivered"] is True
        markdown = Path(public["review_artifacts"]["review_draft_markdown"])
        findings_path = Path(public["review_artifacts"]["review_findings_json"])
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        assert markdown.read_bytes() == body_bytes
        assert findings["rejections"] == review["rejections"]
        assert findings["rejection_explanations"] == explanations
        assert findings["missing_sections"] == draft["missing_sections"]
        assert findings["hard_dependency_blocked"] == review["hard_dependency_blocked"]
        assert findings["formal_release_authorized"] is False
        assert findings["formal_post_write_assessed"] is False
        assert all(forbidden not in output for forbidden in (
            "input_material", "context_package", "stack_trace", "api_key", "settings"))
        cli_observations.append({
            "strict": strict, "exit_code": exit_code, "public": public,
            "markdown_sha256": sha256(markdown.read_bytes()).hexdigest(),
            "findings_sha256": sha256(findings_path.read_bytes()).hexdigest(),
            "file_readback": True,
        })
    observation = {
        "TARGET_ASSERTION_REACHED": True, "hard_dep": hard_dep,
        "sections": len(draft["sections"]), "body_sha256": expected_body_sha,
        "native_rejection_count": 4, "native_rows_unchanged": True,
        "explanation_count": len(explanations), "native_inputs": native_inputs,
        "live_guard_probes": capture["live_stage_guards"],
        "section_checkpoint_recovery": "real recover_or_reuse while current; exact rows; no redispatch",
        "new_process_checkpoint_cold_read": True, "model_calls": counts["all_model_calls"],
        "terminal_status": summary["terminal_outcome"]["status"],
        "publication_exists": summary["terminal_outcome"]["publication_exists"],
        "cli": cli_observations,
    }
    receipt = tmp_path / "d09_native_acceptance_observation.json"
    receipt.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    with capsys.disabled():
        print("D09_NATIVE_ACCEPTANCE", json.dumps({
            "receipt": str(receipt), "hard_dep": hard_dep,
            "body_sha256": expected_body_sha, "native_rows_unchanged": True,
            "explanations": len(explanations), "guard_probes": 12,
            "new_process_cold_read": True,
        }, ensure_ascii=False))


@pytest.mark.parametrize("case", [0, 1], ids=["old-full8-checkpoint", "old-hard-dependent-checkpoint"])
def test_d09_old_checkpoint_remains_readable_without_explanations(tmp_path, case):
    from hashlib import sha256
    import shutil

    source = Path("E:/rag-agent-tmp/BC33_D12_IMPLEMENTATION_20261031_R01/final_source_stable") / (
        "test_native_m6_refusal_continu" + str(case))
    assert source.is_dir(), "required accepted historical checkpoint is unavailable: " + str(source)

    def hashes(root):
        return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}

    before = hashes(source)
    historical = json.loads((source / "offline_summary_roundtrip.json").read_text(encoding="utf-8"))
    old_draft = historical["generated_draft"]
    assert "rejection_explanations" not in old_draft["extra"]["pwa004_review_draft"]
    clone = tmp_path / "old_checkpoint_clone"
    shutil.copytree(source, clone)
    cold = _d09_read_checkpoint_in_fresh_process(clone, historical["task_id"], historical["run_id"])
    assert cold["generated_draft"] == old_draft
    assert cold["graph_generated_draft"] == old_draft
    assert len(old_draft["sections"]) == (7 if case else 8)
    assert len(old_draft["extra"]["pwa004_review_draft"]["rejections"]) == 4
    assert cold["terminal_outcome"] == historical["terminal_outcome"]
    assert cold["provider_redispatch"] is False
    assert hashes(source) == before
    receipt = {
        "TARGET_ASSERTION_REACHED": True, "history_root": str(source),
        "clone_root": str(clone), "historical_files": len(before),
        "history_unchanged": True, "kind": "new-process checkpoint cold read; no recovery",
        "body_sha256": sha256(old_draft["full_text"].encode("utf-8")).hexdigest(),
        "sections": len(old_draft["sections"]), "native_rejection_count": 4,
        "explanation_count": 0, "explanations_fabricated": False,
        "provider_redispatch": False,
    }
    receipt_path = tmp_path / "d09_old_checkpoint_observation.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print("D09_OLD_CHECKPOINT", json.dumps(receipt, ensure_ascii=False))
