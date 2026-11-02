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
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine as TopLevelBusinessGraph
from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from backend.tests.test_bca014_finalizing_lifecycle import _barrier, _finalize
from backend.tests.test_multi_agent_v3_lifecycle_commit import _service, _running_run, REQUEST_HASH
from backend.tests.test_multi_agent_v3_release_lineage import _assessment_fixture, _barrier_closure
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


class _CapturedSectionValidation(BaseException):
    """Test-owned cut after the exact native Section validation commit."""


def _attempt(service, run_id, scope="section:s1"):
    opened = service.open_attempt(run_id, scope, "snapshot-v1")
    return service.activate_attempt(opened.identity.attempt_id, opened.lifecycle_revision)


def _success(service, running, root):
    """Commit a legal V2 terminal fixture through barrier + exact Assessment."""
    fx = _assessment_fixture(root)
    payload = fx["barrier"].model_dump(mode="json", exclude={"barrier_sha256"})
    payload["run_id"] = running.identity.run_id
    barrier = RunFinalizationBarrierSchema(**payload, barrier_sha256=canonical_sha256(payload))
    record = fx["builder"].build_success(
        run_id=running.identity.run_id, section_completion_refs=fx["section_refs"],
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=fx["final_control"],
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=(_barrier_closure(barrier),), active_repair_attempt_refs=(),
    )
    ref = fx["store"].put_if_absent_or_same(record)
    service.run_completion_assessment_resolver = fx["store"].resolve_exact
    finalizing = service.transition_run(running.identity.run_id, running.lifecycle_revision,
        RS.FINALIZING, run_finalization_barrier=barrier)
    return service.transition_run(running.identity.run_id, finalizing.lifecycle_revision,
        RS.SUCCESS, run_completion_assessment_ref=ref)


@pytest.mark.parametrize("status", [RS.RUNNING, RS.FINALIZING, RS.SUCCESS])
@pytest.mark.parametrize("attempt_status", [AS.RUNNING, AS.COMPLETED])
def test_exact_read_lineage_accepts_only_read_eligible_states(tmp_path, status, attempt_status):
    service = _service(tmp_path)
    run = _running_run(service, run_id="run-wave11")
    attempt = _attempt(service, run.identity.run_id)
    if attempt_status is AS.COMPLETED:
        service.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision, AS.COMPLETED)
    if status is RS.FINALIZING:
        _finalize(service, run)
    elif status is RS.SUCCESS:
        _success(service, run, tmp_path / "assessment")
    before = service.path.read_bytes()
    lineage = RunAttemptLifecycleService(service.path).resolve_read_lineage(
        run.identity.run_id, "section:s1", expected_attempt_id=attempt.identity.attempt_id)
    assert lineage.run_status is status and lineage.attempt_status is attempt_status
    assert lineage.input_snapshot_version == "snapshot-v1"
    with pytest.raises(LifecycleCurrentnessError):
        service.resolve_read_lineage(run.identity.run_id, "section:s1", expected_attempt_id="wrong")
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("status", [RS.PENDING, RS.PAUSED, RS.RETRYABLE_FAILED,
    RS.FAILED, RS.CANCELLED, RS.PARTIAL_SUCCESS])
def test_read_lineage_rejects_unavailable_states(tmp_path, status):
    service = _service(tmp_path)
    if status is RS.PENDING:
        service.claim_new_run(RunIdentitySchema(run_id="run-1", task_id="task-1", tenant_id="t",
            canonical_request_sha256=REQUEST_HASH))
        attempt_id = "absent"
    else:
        run = _running_run(service)
        attempt_id = _attempt(service, "run-1").identity.attempt_id
        service.transition_run("run-1", run.lifecycle_revision, status)
    with pytest.raises(LifecycleCurrentnessError):
        service.resolve_read_lineage("run-1", "section:s1", expected_attempt_id=attempt_id)


def test_finalizing_same_fixture_reads_but_rejects_every_normal_lifecycle_write(tmp_path):
    service = _service(tmp_path)
    run = _running_run(service)
    attempt = _attempt(service, "run-1")
    pending = service.open_attempt("run-1", "section:s2", "snapshot-v1")
    _finalize(service, run)
    lineage = service.resolve_read_lineage("run-1", "section:s1", expected_attempt_id=attempt.identity.attempt_id)
    assert lineage.run_status is RS.FINALIZING and not lineage.commit_eligible
    assert service.resolve_active_lineage("run-1", "section:s1") == lineage
    before = service.path.read_bytes()
    writes = (
        lambda: service.assert_run_committable("run-1"),
        lambda: service.assert_attempt_committable(run_id="run-1", attempt_scope_ref="section:s1",
            attempt_id=attempt.identity.attempt_id, input_snapshot_version="snapshot-v1"),
        lambda: service.open_attempt("run-1", "section:s3", "snapshot-v1"),
        lambda: service.activate_attempt(pending.identity.attempt_id, pending.lifecycle_revision),
        lambda: service.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision, AS.COMPLETED),
    )
    for write in writes:
        with pytest.raises(LifecycleCurrentnessError):
            write()
        assert service.path.read_bytes() == before


def test_read_lineage_does_not_accept_superseded_or_ineligible_attempts(tmp_path):
    service = _service(tmp_path)
    run = _running_run(service)
    first = _attempt(service, "run-1")
    latest = service.open_attempt("run-1", "section:s1", "snapshot-v2")
    _finalize(service, run)
    for identity in (first.identity.attempt_id, latest.identity.attempt_id):
        with pytest.raises(LifecycleCurrentnessError):
            service.resolve_read_lineage("run-1", "section:s1", expected_attempt_id=identity)


@pytest.fixture
def canonical_section(tmp_path):
    """Fresh V3 input; stop after Section validation, before deferred I8 orchestration."""
    task_id, run_id = "task-i2", "run-wave11"
    raw = workflow_tests._w13_single_section_project_input(task_id).model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    project = ProjectInputSchema.model_validate(raw)
    text = "应依据已核实的项目资料制定建设方案。"
    captured = {}
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation
    original_error_from_outcome = TopLevelBusinessGraph._error_from_outcome

    def provider(client, req):
        response = original_generate(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": text * 6})
        contract = req.extra.get("response_contract")
        if contract == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": unit["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                "claims": [{"quote": text, "occurrence_index": 0, "claim_type": "general_method"}]} for unit in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)})
        if contract == support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs = [item["exact_source_ref"] for item in support_tests._json_message(req, "support_evidence")["support_evidence"]]
            return response.model_copy(update={"content": json.dumps({"relation": "SUPPORTED", "supporting_refs": refs[:1],
                "contradicting_refs": [], "certainty_relation": "ALIGNED", "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]})})
        return response

    def capture(composition, **kwargs):
        outcome = original_complete(composition, **kwargs)
        captured.update(post_write=composition, arguments=kwargs, outcome=outcome)
        raise _CapturedSectionValidation("I2 fixture stops after committed Section validation")

    def relay_capture(graph, *, node_name, outcome):
        # The native worker retains BaseException in its outcome. Relay only
        # this fixture's cut before product error/C1 projection; every real
        # product outcome still uses the original adapter without alteration.
        if type(outcome.exception) is _CapturedSectionValidation:
            raise outcome.exception
        return original_error_from_outcome(graph, node_name=node_name, outcome=outcome)

    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True,
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    factory = workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", capture))
        stack.enter_context(patch.object(TopLevelBusinessGraph, "_error_from_outcome", relay_capture))
        with pytest.raises(_CapturedSectionValidation, match="I2 fixture stops after committed Section validation"):
            MainlineApplicationService(supervisor_factory=factory).run(
                project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
                task_id=task_id, run_id=run_id, output_root=tmp_path, clean_existing=True, settings=settings,
                project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False)
    assert captured, "fresh canonical Section did not reach PostWrite validation"
    lifecycle = captured["post_write"].claim_quality_projection.claim_runtime.lifecycle_service
    source = captured["arguments"]["source_sections"][0].active_execution_lineage
    assert lifecycle.load_run(run_id).status is RS.RUNNING
    lineage = lifecycle.resolve_active_lineage(run_id, source.attempt_scope_ref)
    assert lineage.run_status is RS.RUNNING and lineage.attempt_status is AS.RUNNING
    assert lineage.attempt_id == source.attempt_id
    assert lineage.input_snapshot_version == source.input_snapshot_version
    captured["root"] = tmp_path
    return captured


@pytest.mark.parametrize("status", [RS.RUNNING, RS.FINALIZING, RS.SUCCESS])
def test_claim_and_post_write_exact_current_reads_do_not_mutate(canonical_section, status):
    fx = canonical_section
    pw, args = fx["post_write"], fx["arguments"]
    projection = pw.claim_quality_projection
    lifecycle = projection.claim_runtime.lifecycle_service
    section = args["source_sections"][0]
    run_id = section.active_execution_lineage.run_id
    run = lifecycle.load_run(run_id)
    if status is RS.FINALIZING:
        lifecycle.transition_run(run_id, run.lifecycle_revision, status, run_finalization_barrier=_barrier(run_id))
    elif status is RS.SUCCESS:
        _success(lifecycle, run, fx["root"] / "assessment")
    source_args = dict(post_write=pw, artifact_ref=args["artifact_ref"], scope=args["scope"],
        source_sections=(section,), assembly_input_manifest_ref=None)
    before = {p: p.read_bytes() for p in fx["root"].rglob("*") if p.is_file() and p.suffix in {".jsonl", ".sqlite", ".db"}}
    def forbidden(*_args, **_kwargs):
        pytest.fail("read-current entered a mutation or Provider path")
    with ExitStack() as stack:
        for owner, name in ((projection.claim_runtime, "commit_verification"), (projection.claim_runtime, "commit_claim_records"),
            (projection.claim_runtime, "commit_binding_facts"), (projection.support.runtime, "commit_support"),
            (pw.validation_store, "put_fact_if_absent_or_same"), (pw.validation_store, "finalize_validator_execution"),
            (pw.validation_intermediate_runtime, "commit"), (projection.paragraph_runtime.artifact_runtime, "promote_active"),
            (FakeLLMClient, "generate")):
            stack.enter_context(patch.object(owner, name, forbidden))
        snapshot = projection.resolve_sources(**source_args)
        completion = pw.validation_store.resolve_completion(fx["outcome"][0])
        citation_args = dict(validation_run_id=completion.validation_run_id,
            subject_hash=completion.subject_hash, artifact_ref=args["artifact_ref"], scope=args["scope"],
            source_sections=(section,), evidence_snapshot_ref=args["evidence_snapshot_ref"])
        assert pw.citation_integrity.read_committed_inputs(**citation_args)[0]
        assert snapshot.sections[0].claim_records
        claim = snapshot.sections[0].claim_records[0]
        sources = projection.verifier.sources
        lineage = sources.resolve_claim_read_lineage(claim)
        assert lineage.run_status is status
        rows = sources.read_current_sources(claim, lineage)
        assert rows
        candidate = sources._candidate_from_sources(claim, rows)
        sources.validate_read_binding_sources(candidate, lineage)
        with pytest.raises((TargetClaimCompositionError, LifecycleCurrentnessError)):
            sources.read_current_sources(claim, lineage.model_copy(update={"attempt_id": "wrong"}))
        with pytest.raises(TargetClaimCompositionError):
            sources.read_current_sources(claim, lineage.model_copy(update={"input_snapshot_version": "wrong"}))
        bad = candidate.model_copy(update={"source_set_hash": "0" * 64})
        with pytest.raises(TargetClaimCompositionError):
            sources.validate_read_binding_sources(bad, lineage)
        version = claim.paragraph_version_ref
        original_head = sources.artifact_runtime.resolve_active
        def stale(ref):
            return None if ref == version.logical_ref else original_head(ref)
        with patch.object(sources.artifact_runtime, "resolve_active", stale):
            with pytest.raises(TargetClaimCompositionError):
                sources.read_current_sources(claim, lineage)
        bad_section = section.model_copy(update={"canonical_claim_verification_refs": []})
        with pytest.raises(PostWriteClaimProjectionError):
            projection.resolve_sources(**{**source_args, "source_sections": (bad_section,)})
        wrong_ref = section.canonical_claim_verification_refs[0].model_copy(
            update={"verification_semantic_fingerprint": "0" * 64})
        with pytest.raises(ValueError):
            projection.resolve_sources(**{**source_args, "source_sections": (section.model_copy(
                update={"canonical_claim_verification_refs": [wrong_ref]}),)})
        original_claim_head = projection.claim_runtime.resolve_claim_head
        def stale_claim_head(ref):
            return original_claim_head(ref).model_copy(update={"head_updated_by_operation_id": "stale"})
        with patch.object(projection.claim_runtime, "resolve_claim_head", stale_claim_head):
            with pytest.raises(PostWriteClaimProjectionError):
                projection.resolve_sources(**source_args)
        wrong_section = section.model_copy(update={"active_execution_lineage":
            section.active_execution_lineage.model_copy(update={"input_snapshot_version": "wrong"})})
        with pytest.raises(CitationProjectionIntegrityError):
            pw.citation_integrity.read_committed_inputs(**{**citation_args, "source_sections": (wrong_section,)})
        if status is RS.FINALIZING:
            with pytest.raises(LifecycleCurrentnessError):
                sources.materialize_sources(claim, lineage)
            with pytest.raises(PostWriteClaimProjectionError):
                projection.is_terminal_replay((section,))
            from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
            original_resolve = pw.validation_store.resolve_validator_execution_record
            def missing(ref):
                if ref.startswith("validator-execution:shared-faithfulness:"):
                    raise PostWritePersistenceIntegrityError("required ValidatorExecutionRecord is missing")
                return original_resolve(ref)
            with patch.object(pw.validation_store, "resolve_validator_execution_record", missing):
                with pytest.raises(PostWriteClaimProjectionError):
                    projection.complete_item(post_write=pw, validation_run_id=completion.validation_run_id,
                        subject_hash=completion.subject_hash, artifact_ref=args["artifact_ref"], scope=args["scope"],
                        source_sections=(section,))
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("status", [RS.PAUSED, RS.RETRYABLE_FAILED, RS.FAILED, RS.CANCELLED, RS.PARTIAL_SUCCESS])
def test_claim_and_post_write_unavailable_current_reads_fail_closed(canonical_section, status):
    fx = canonical_section
    pw, args = fx["post_write"], fx["arguments"]
    projection = pw.claim_quality_projection
    section = args["source_sections"][0]
    lifecycle = projection.claim_runtime.lifecycle_service
    lineage = lifecycle.resolve_active_lineage(section.active_execution_lineage.run_id,
        section.active_execution_lineage.attempt_scope_ref)
    snapshot = projection.resolve_sources(post_write=pw, artifact_ref=args["artifact_ref"],
        scope=args["scope"], source_sections=(section,))
    run = lifecycle.load_run(lineage.run_id)
    lifecycle.transition_run(lineage.run_id, run.lifecycle_revision, status)
    with pytest.raises(LifecycleCurrentnessError):
        projection.verifier.sources.read_current_sources(snapshot.sections[0].claim_records[0], lineage)
    with pytest.raises(PostWriteClaimProjectionError):
        projection.resolve_sources(post_write=pw, artifact_ref=args["artifact_ref"],
            scope=args["scope"], source_sections=(section,))
    completion = pw.validation_store.resolve_completion(fx["outcome"][0])
    with pytest.raises(CitationProjectionIntegrityError):
        pw.citation_integrity.read_committed_inputs(validation_run_id=completion.validation_run_id,
            subject_hash=completion.subject_hash, artifact_ref=args["artifact_ref"], scope=args["scope"],
            source_sections=(section,), evidence_snapshot_ref=args["evidence_snapshot_ref"])
