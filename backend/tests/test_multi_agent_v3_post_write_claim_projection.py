"""343: consume exact upstream results in Post-write without re-running cognition."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.scheme_writer.section_execution import SectionExecutionResultSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_adherence import REQUIREMENT_ADHERENCE_CONTRACT
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests

TEXT = "应依据已核实的项目资料制定建设方案。"


def test_section_result_has_typed_verification_refs_for_post_write():
    assert "canonical_claim_verification_refs" in SectionExecutionResultSchema.model_fields


@pytest.fixture(scope="module", params=("SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED", "INDETERMINATE", "no-claims", "unknown-coverage", "partial-coverage", "commit-window"))
def projection_run(tmp_path_factory, request):
    scenario = request.param
    root = tmp_path_factory.mktemp("pw343-" + scenario)
    task_id, run_id = "task-pw343-" + scenario, "run-pw343-" + scenario
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    calls, observations, guarded = [], [], []
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        contract = req.extra.get("response_contract")
        if contract == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            state = "NO_APPLICABLE_FACTUAL_CLAIM" if scenario == "no-claims" else "INDETERMINATE" if scenario in {"unknown-coverage", "partial-coverage"} else "FACTUAL_CLAIMS_MATERIALIZED"
            claims = [] if scenario in {"no-claims", "unknown-coverage"} else [{"quote": TEXT, "occurrence_index": 0, "claim_type": "general_method"}]
            payload = {"units": [{"unit_id": unit["unit_id"], "accounting_state": state, "claims": claims} for unit in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if contract == support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs = [item["exact_source_ref"] for item in support_tests._json_message(req, "support_evidence")["support_evidence"]]
            assert refs
            relation = "SUPPORTED" if scenario in {"partial-coverage", "commit-window"} else scenario
            payload = {"relation": relation, "supporting_refs": refs[:1] if relation in {"SUPPORTED", "PARTIALLY_SUPPORTED"} else [],
                       "contradicting_refs": refs[:1] if relation == "CONTRADICTED" else [],
                       "certainty_relation": "ALIGNED",
                       "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]}
            return response.model_copy(update={"content": json.dumps(payload)}, deep=True)
        return response

    def complete(composition, **kwargs):
        before_calls = len(calls)
        before_relevancy = len([
            req for req in calls
            if req.extra.get("response_contract") == REQUIREMENT_ADHERENCE_CONTRACT
        ])
        if scenario == "commit-window" and kwargs["scope"].value == "SECTION":
            original_finalize = composition.validation_store.finalize_validator_execution
            def interrupted(event, record, **options):
                if record.validator_id == "post_write_faithfulness":
                    raise RuntimeError("test post-write Fact/Execution commit window")
                return original_finalize(event, record, **options)
            with patch.object(composition.validation_store, "finalize_validator_execution", interrupted):
                with pytest.raises(RuntimeError, match="commit window"):
                    original_complete(composition, **kwargs)
            assert len([
                req for req in calls
                if req.extra.get("response_contract") == REQUIREMENT_ADHERENCE_CONTRACT
            ]) == before_relevancy + 1
            guarded.append("commit-window-recovered")
        outcome = original_complete(composition, **kwargs)
        completion = composition.validation_store.resolve_completion(outcome[0])
        projected = [item for item in completion.items if item.expected_validation_item_ref.startswith("faithfulness:shared:")]
        assert len(projected) == 1, "actual Post-write must consume the upstream Claim/Support results"
        # 343 itself remains deterministic.  The enclosing validation now has
        # one legitimate Requirement-Adherence semantic call on first execution.
        after_relevancy = len([
            req for req in calls
            if req.extra.get("response_contract") == REQUIREMENT_ADHERENCE_CONTRACT
        ])
        assert after_relevancy == before_relevancy + 1
        assert len(calls) == before_calls + 1
        item = projected[0]
        facts = tuple(composition.validation_store.resolve_fact(ref) for ref in item.fact_refs)
        observations.append((kwargs["scope"].value, kwargs["artifact_ref"], completion, item, facts))
        # Replay the same committed input before the Run is terminalized.
        ledger_before = composition.claim_quality_projection.claim_runtime.ledger_path.read_bytes()
        replay_calls = len(calls)
        assert original_complete(composition, **kwargs) == outcome
        assert len(calls) == replay_calls
        assert composition.claim_quality_projection.claim_runtime.ledger_path.read_bytes() == ledger_before
        if kwargs["scope"].value == "SECTION" and scenario == "SUPPORTED":
            from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
            sections = kwargs["source_sections"]
            assert len(sections) == 1
            section = sections[0]
            variants = {
                "missing-verification": section.model_copy(update={"canonical_claim_verification_refs": []}, deep=True),
                "duplicate-verification": section.model_copy(update={"canonical_claim_verification_refs": section.canonical_claim_verification_refs * 2}, deep=True),
                "missing-coverage": section.model_copy(update={"canonical_claim_coverage_proof_refs": []}, deep=True),
                "duplicate-coverage": section.model_copy(update={"canonical_claim_coverage_proof_refs": section.canonical_claim_coverage_proof_refs * 2}, deep=True),
                "changed-body": section.model_copy(update={"section": section.section.model_copy(update={"content": section.section.content + "新增无来源承诺。"})}, deep=True),
                "changed-title": section.model_copy(update={"section": section.section.model_copy(update={"section_title": "篡改标题"})}, deep=True),
                "wrong-verification-hash": section.model_copy(update={"canonical_claim_verification_refs": [section.canonical_claim_verification_refs[0].model_copy(update={"verification_semantic_fingerprint": "0" * 64})]}, deep=True),
                "wrong-writer-hash": section.model_copy(update={"writer_generation_invocation_ref": section.writer_generation_invocation_ref.model_copy(update={"invocation_sha256": "0" * 64})}, deep=True),
            }
            for name, invalid in variants.items():
                with pytest.raises((PostWriteClaimProjectionError, ValueError)):
                    composition.claim_quality_projection.resolve_sources(
                        post_write=composition, artifact_ref=kwargs["artifact_ref"], scope=kwargs["scope"],
                        source_sections=(invalid,), assembly_input_manifest_ref=None,
                    )
                guarded.append(name)
            original_head = composition.claim_quality_projection.claim_runtime.resolve_claim_head
            def stale_head(ref):
                head = original_head(ref)
                return head.model_copy(update={"head_updated_by_operation_id": "test:other-verification"}, deep=True)
            with patch.object(composition.claim_quality_projection.claim_runtime, "resolve_claim_head", stale_head):
                with pytest.raises(PostWriteClaimProjectionError, match="current active Claim"):
                    composition.claim_quality_projection.resolve_sources(
                        post_write=composition, artifact_ref=kwargs["artifact_ref"], scope=kwargs["scope"],
                        source_sections=sections, assembly_input_manifest_ref=None,
                    )
            guarded.append("stale-verification")
        if kwargs["scope"].value == "DOCUMENT" and scenario == "SUPPORTED":
            from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
            with pytest.raises(PostWriteClaimProjectionError, match="Document assembly names another exact Writer invocation"):
                composition.claim_quality_projection.resolve_sources(
                    post_write=composition, artifact_ref=kwargs["artifact_ref"], scope=kwargs["scope"],
                    source_sections=(), assembly_input_manifest_ref=kwargs["assembly_input_manifest_ref"],
                )
            guarded.append("document-missing-section")
        return outcome

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True,
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", complete))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, clean_existing=True, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    if len(observations) != 2:
        top = workflow_tests._latest_w13_top_level_payload(factory.engines[0])
        raise AssertionError(top.terminal_error.model_dump(mode="json") if top.terminal_error else summary)
    assert workflow_tests._latest_w13_top_level_payload(factory.engines[0]).terminal_error is None
    return scenario, observations, guarded, summary, calls, factory.engines[0]


def test_projected_result_and_coverage_are_real_facts_for_each_scope(projection_run):
    scenario, observations, _, summary, calls, _ = projection_run
    expected = "PASS" if scenario == "no-claims" else "INDETERMINATE" if scenario == "unknown-coverage" else "FAIL"  # Fake Verifier has no VERIFIED oracle.
    assert {scope for scope, *_ in observations} == {"SECTION", "DOCUMENT"}
    subjects = set()
    for scope, artifact, completion, item, facts in observations:
        assert item.state.value == expected
        assert facts
        assert all(fact.subject_hash == completion.subject_hash and fact.validation_run_id == completion.validation_run_id for fact in facts)
        assert all(fact.family.value == "FAITHFULNESS" for fact in facts)
        subjects.add(completion.subject_hash)
        claim_facts = [fact for fact in facts if fact.contract_version == "faithfulness_verification_fact_v1"]
        assert len(claim_facts) == (0 if scenario in {"no-claims", "unknown-coverage"} else 1)
        assert all("support_relation" not in fact.payload for fact in facts)
        if claim_facts:
            assert claim_facts[0].payload["verification_verdict"] == ("CONFLICTED" if scenario == "CONTRADICTED" else "UNSUPPORTED")
            assert claim_facts[0].payload["schema_version"] == "faithfulness_verification_payload_v1"
        # FTH-03 is an independent required item. Support relation does not
        # determine certainty; incomplete Claim coverage remains unresolved.
        remaining = [row for row in completion.items if row.expected_validation_item_ref.startswith("faithfulness:item:")]
        expected_fth03 = (
            "INDETERMINATE" if scenario in {"unknown-coverage", "partial-coverage"}
            else "NOT_APPLICABLE" if scenario == "no-claims"
            else "PASS"
        )
        assert len(remaining) == 1 and remaining[0].state.value == expected_fth03
    assert len(subjects) == 2, "Document projection is not a reused Section PASS"
    assert summary["status"] == "partial_success"
    assert summary["target_release_publication"]["manifest"]["release_mode"] == "BEST_EFFORT"
    assert len([req for req in calls if req.extra.get("call_purpose") == "claim_evidence_support"]) <= 1


def test_missing_or_tampered_source_proofs_never_project_success(projection_run):
    scenario, _, guarded, _, _, _ = projection_run
    if scenario == "SUPPORTED":
        assert set(guarded) == {"missing-verification", "duplicate-verification", "missing-coverage", "duplicate-coverage", "changed-body", "changed-title", "wrong-verification-hash", "wrong-writer-hash", "stale-verification", "document-missing-section"}
    if scenario == "commit-window":
        assert guarded == ["commit-window-recovered"]


def test_terminal_projection_replay_never_reopens_run_or_inference(projection_run):
    from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import TargetPostWriteClaimProjection
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError, RunAttemptLifecycleService
    scenario, _, _, summary, _, engine = projection_run
    top = workflow_tests._latest_w13_top_level_payload(engine)
    coordinator = engine.stage_service.section_execution_coordinator
    services = coordinator.require_pre_write_services()
    source = engine.stage_service.target_post_write_composition
    reopened = TargetPostWriteComposition.create(
        runs_dir=Path(engine.checkpointer.path).parent, research_runtime=engine.research_runtime,
        writer_execution_store=services.writer_invocation.execution_store,
        budget_manager_provider=engine.budget_runtime,
        lifecycle_service=RunAttemptLifecycleService(engine.lifecycle_service.path),
    )
    projection = TargetPostWriteClaimProjection(claim_runtime=coordinator.claim_verification_runtime,
                     verifier_composition=coordinator.claim_verifier_composition)
    if source.requirement_adherence is not None:
        reopened.attach_requirement_adherence(
            model_gateway=source.requirement_adherence.gateway
        )
    if source.coverage_composition is not None:
        reopened.attach_coverage(
            model_gateway=source.coverage_composition.gateway
        )
    if source.consistency_composition is not None:
        reopened.attach_consistency(
            model_gateway=source.consistency_composition.gateway
        )
    reopened.attach_claim_quality_projection(projection)
    budget = engine.budget_runtime(top.task_id, top.run_id)
    before_budget = budget.canonical_state()
    before_lifecycle = engine.lifecycle_service.path.read_bytes()
    before_claims = projection.claim_runtime.ledger_path.read_bytes()
    support_path = projection.support.runtime.ledger_path
    before_support = support_path.read_bytes() if support_path.exists() else None
    trace = Path(summary["paths"]["trace"])
    before_trace = trace.read_bytes()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("terminal replay must not evaluate or re-commit Claims")
    with ExitStack() as stack:
        stack.enter_context(patch.object(FakeLLMClient, "generate", forbidden))
        stack.enter_context(patch.object(projection.claim_runtime, "commit_verification", forbidden))
        stack.enter_context(patch.object(projection.claim_runtime, "commit_claim_records", forbidden))
        stack.enter_context(patch.object(projection.support.runtime, "commit_support", forbidden))
        replay = reopened.complete_target_post_write(
            task_id=top.task_id, run_id=top.run_id, document_id=top.document_id,
            project_input=top.project_input, section_execution_results=tuple(top.section_execution_results),
        )
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert replay.published_release.manifest == top.target_release_publication.manifest
    assert replay.run_completion_assessment_ref == top.target_run_completion_assessment_ref
    assert budget.canonical_state() == before_budget
    assert engine.lifecycle_service.path.read_bytes() == before_lifecycle
    assert projection.claim_runtime.ledger_path.read_bytes() == before_claims
    assert (support_path.read_bytes() if support_path.exists() else None) == before_support
    assert trace.read_bytes() == before_trace
    # Read permission did not change the pre-existing write guard.
    section = top.section_execution_results[0]
    current = engine.lifecycle_service.resolve_active_lineage(top.run_id, section.active_execution_lineage.attempt_scope_ref)
    assert current.run_status.value == "success"
    with pytest.raises(LifecycleCurrentnessError):
        projection.claim_runtime.validate_operation_context(ClaimOperationContextSchema(
            active_lineage=current, authorization_ref="test:no-new-commit", operation_purpose="terminal-guard-test",
        ))
    if scenario == "SUPPORTED":
        from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
        from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
        original_resolve = reopened.validation_store.resolve_validator_execution_record
        def missing(ref, *, connection=None):
            if ref.startswith("validator-execution:shared-faithfulness:"):
                raise PostWritePersistenceIntegrityError("required ValidatorExecutionRecord is missing")
            return original_resolve(ref, connection=connection)
        with patch.object(reopened.validation_store, "resolve_validator_execution_record", missing):
            with patch.object(reopened.validation_store, "put_fact_if_absent_or_same", forbidden):
                with pytest.raises(PostWriteClaimProjectionError, match="terminal Run is missing"):
                    reopened.complete_target_post_write(
                        task_id=top.task_id, run_id=top.run_id, document_id=top.document_id,
                        project_input=top.project_input, section_execution_results=tuple(top.section_execution_results),
                    )
        assert engine.lifecycle_service.path.read_bytes() == before_lifecycle
        assert projection.claim_runtime.ledger_path.read_bytes() == before_claims
