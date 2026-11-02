"""350 controlled production dependency, Context, and canonical replay proofs."""
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyRuntime
from apps.enterprise_document.services.agent_rag_v1.cross_section_context import CrossSectionClaimContextProvider
import test_multi_agent_v3_workflow_recovery as w13
import test_multi_agent_v3_claim_materialization_mainline as extraction
import test_multi_agent_v3_claim_support_mainline as support

TEXT = "应依据已核实的项目资料制定建设方案。"


class DependencyPlanner:
    def __init__(self, delegate):
        self.delegate = delegate

    def plan(self, **kwargs):
        outcome = self.delegate.plan(**kwargs)
        sections = [s.model_copy(update={"depends_on": [outcome.document_plan.sections[0].section_id]
                    if i == 1 else [], "can_run_in_parallel": False}, deep=True)
                    for i, s in enumerate(outcome.document_plan.sections)]
        return replace(outcome, document_plan=outcome.document_plan.model_copy(update={"sections": sections}, deep=True))


class Factory(w13._W13InspectingFactory):
    def __init__(self):
        super().__init__(normalization=w13._W13TwoSectionNormalization())

    def build(self, **kwargs):
        self.build_kwargs = dict(kwargs)
        supervisor = super().build(**kwargs)
        stage = supervisor.workflow_engine.stage_service
        stage.planner_adapter = DependencyPlanner(stage.planner_adapter)
        return supervisor



def _run_dependency_sections(root, project, factory, settings, *, task_id, run_id):
    """Native graph cut after real Sections; no failure used as a pause."""
    from application.task_factory import TaskFactory
    from apps.enterprise_document.workflows.scheme_generation import build_scheme_generation_workflow
    from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema
    from agent.runtime.graph_state import GraphStateSchema
    from bootstrap.runtime_options import RuntimeOptions
    from schemas.status import ExecutionStatus
    from task.task_manager import JsonlTaskManager

    now = "2026-10-31T00:00:00+00:00"
    task = TaskFactory().build(task_id=task_id, run_id=run_id,
        user_input=project.user_query, created_at=now,
        project_input=project.model_dump(mode="json"), allow_demo_defaults=False)
    task_manager = JsonlTaskManager(root / "tasks")
    task_manager.create_task(task)
    workflow = build_scheme_generation_workflow(created_at=now)
    options = RuntimeOptions.resolve(settings, Path(w13.__file__).resolve().parents[2],
        enable_agent_self_rag=False, durable_memory_required=False)
    supervisor = factory.build(workflow=workflow, runs_dir=root / "runs",
        captures_dir=root / "captures", task_manager=task_manager, settings=settings,
        options=options, allow_legacy_unscoped_rag=False, retrieval_access_scope=None)
    try:
        supervisor.lifecycle.mark_running(task)
        state = GraphStateSchema(task_id=task_id, run_id=run_id,
            task_type=task.task_type, user_input=task.user_input, tenant_id=task.tenant_id,
            task=task.model_dump(), workflow=workflow.model_dump(),
            requirements={**task.generation_requirements, "project_input": task.project_input},
            context_bundle=supervisor.context_factory.build(task),
            created_at=task.created_at, updated_at=task.updated_at or task.created_at)
        engine = supervisor.workflow_engine
        engine._assert_new_run_storage_admission(run_id)
        state.workflow_engine_name, state.workflow_engine_version = engine.engine_name, engine.engine_version
        state.status = ExecutionStatus.RUNNING
        initial = engine.runtime_state_codec.build_initial_state(workflow, state)
        payload = SchemeGenerationGraphPayloadSchema(task_id=task_id, run_id=run_id,
            workflow_namespace=engine.runtime_state_codec.workflow_namespace(workflow),
            started_at=engine.stage_service.runtime_support.now_iso())
        initial.update(engine.business_state_codec.build_initial_update(payload))
        engine.business_state_codec.assert_json_serializable(initial)
        engine.build_graph(workflow).invoke(initial,
            config=engine._invoke_config(workflow, run_id=run_id),
            durability="sync", interrupt_after=["execute_sections"])
        # This is deliberately a nonterminal fixture, not a fabricated AgentResult.
        return {"task_id": task_id, "run_id": run_id, "status": "running",
                "fixture_scope": "native_graph_interrupt_after_execute_sections"}
    finally:
        supervisor.close()


def _dependency_source_snapshot(runtime, results, engine):
    """Read immutable body/Claim/Support/Verification evidence through real owners."""
    import hashlib

    support_runtime = engine.section_execution_graph.coordinator.claim_support_composition.runtime
    records = []
    for result, source in results:
        writer = runtime.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
        assert writer.artifact is not None
        claims = []
        for ref in source.verification_refs:
            verification = runtime.claim_runtime.resolve_verification_result(ref)
            claims.append({
                "claim": runtime.claim_runtime.resolve_claim(ref.claim_ref).model_dump(mode="json"),
                "verification": verification.model_dump(mode="json"),
                "support": support_runtime.resolve_support(
                    verification.claim_evidence_support_ref).model_dump(mode="json"),
            })
        records.append({
            "section_id": source.plan_binding.section_id,
            "body_sha256": hashlib.sha256(writer.artifact.content.encode("utf-8")).hexdigest(),
            "writer": writer.model_dump(mode="json"),
            "paragraphs": [runtime.paragraph_runtime.read_paragraph(ref).model_dump(mode="json")
                           for ref in source.paragraph_manifest.members],
            "coverage": [runtime.claim_runtime.resolve_claim_coverage(ref).model_dump(mode="json")
                         for ref in source.coverage_proof_refs],
            "claims": claims,
        })
    paths = (runtime.claim_runtime.ledger_path, support_runtime.ledger_path,
             runtime.paragraph_runtime.manifest_ledger_path,
             runtime.paragraph_runtime.artifact_runtime.ledger_path)
    return {
        "records": records,
        "ledger_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }


def _run_dependency_mainline(tmp_path_factory, scenario, *, pause_before_terminal=False,
                             stop_after_sections=False, document_failure=None,
                             failure_status_projection=False):
    root = tmp_path_factory.mktemp("dependency350-" + scenario)
    original_project = w13._w13_two_section_project_input
    def project(task_id):
        raw = original_project(task_id).model_dump(mode="json")
        raw["schema_version"] = "project_input_v3"
        titles = ["350 producer A", "350 dependent B", "350 unrelated C"]
        raw["generation_requirements"]["required_sections"] = titles
        raw["output_schema"]["required_sections"] = titles
        return type(original_project(task_id)).model_validate(raw)
    calls, results, candidates, authorization_checks = [], [], [], []
    runtime = []
    original_generate = FakeLLMClient.generate
    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        contract = req.extra.get("response_contract")
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if contract == extraction.CONTRACT:
            units = extraction._task_contract(req)["coverage_units"]
            content = {"units": [{"unit_id": u["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                       "claims": [{"quote": TEXT, "occurrence_index": 0, "claim_type": "general_method"}]} for u in units]}
            return response.model_copy(update={"content": json.dumps(content)}, deep=True)
        if contract == support.SUPPORT_RESPONSE_CONTRACT:
            refs = [x["exact_source_ref"] for x in support._json_message(req, "support_evidence")["support_evidence"]]
            relation = "CONTRADICTED" if scenario == "CONFLICTED" else "SUPPORTED"
            content = {"relation": relation, "supporting_refs": refs[:1] if relation == "SUPPORTED" else [],
                       "contradicting_refs": refs[:1] if relation == "CONTRADICTED" else [],
                       "certainty_relation": "ALIGNED", "certainty_reason_codes": ["TEST_ALIGNED"]}
            return response.model_copy(update={"content": json.dumps(content)}, deep=True)
        if contract == "canonical_claim_verifier_v2":
            # Explicit independent scenario oracle on the actual V2 model call.
            # Committed Support supplies allowed source refs, not validity truth.
            shared = support._json_message(req, "shared_support")["shared_support"]
            allowed = support._json_message(req, "allowed_authority_refs")["allowed_authority_refs"]
            basis = shared["supporting_refs"][:1] if scenario == "VERIFIED" else []
            assert all(ref in allowed for ref in basis)
            assert scenario != "VERIFIED" or basis
            content = {"verdict": scenario, "rationale": "Controlled 350 source truth.",
                       "positive_basis_refs": basis,
                       "issues": [] if scenario == "VERIFIED" else [{"issue_code": "SOURCE_UNRESOLVED", "severity": "MAJOR", "message": "Controlled unresolved scope.", "source_refs": []}]}
            return response.model_copy(update={"content": json.dumps(content)}, deep=True)
        return response
    original_complete = SectionDependencyRuntime.complete_source
    def complete(owner, result):
        source = original_complete(owner, result)
        runtime[:] = [owner]
        results.append((result, source))
        if scenario == "VERIFIED" and len(results) == 3:
            _check_consumer_authorization(dict(runtime=owner, candidates=candidates, results=results))
            authorization_checks.append(True)
        return source
    original_candidates = CrossSectionClaimContextProvider.candidates
    def collect(provider, **kwargs):
        value = original_candidates(provider, **kwargs)
        candidates.extend(value)
        return value
    factory = Factory()
    failure_observation = {}
    if pause_before_terminal or document_failure is not None:
        from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
        from apps.enterprise_document.services.agent_rag_v1.post_write_review_draft import TargetReviewDraftReader

        def capture_failure(composition, materialization, final_document, current=None):
            preimage = composition.lifecycle_service.budget_preimage(materialization.source_sections[0].active_execution_lineage.run_id)
            failure_observation.update({
                "composition": composition, "materialization": materialization,
                "final_document": final_document, "preimage": preimage,
            })
            try:
                failure_observation["source_snapshot"] = _dependency_source_snapshot(runtime[0], results, factory.engines[0])
                failure_observation["review"] = TargetReviewDraftReader(composition).read(
                    document_artifact_ref=final_document.artifact_state_ref,
                    control_decision_ref=final_document.control_decision_ref,
                    task_id=preimage.run.identity.task_id, run_id=preimage.run.identity.run_id)
            except Exception as exc:
                # Evidence capture must not replace the deliberately injected primary.
                failure_observation["capture_error"] = {"type": type(exc).__name__, "message": str(exc)}

        def before_terminalization(composition, **kwargs):
            capture_failure(composition, kwargs["materialization"], kwargs["final_document"])
            raise RuntimeError("controlled-completed-source-fixture-before-terminal")

        original_section = TargetPostWriteComposition._section
        section_calls = []

        def section_loop_failure(composition, **kwargs):
            from schemas.common import ErrorSchema
            from schemas.failure import FailureCategory

            section_calls.append(kwargs["result"].section.section_id)
            fail_index = 1 if document_failure == "section-loop-first" else 3
            if len(section_calls) == fail_index:
                preimage = composition.lifecycle_service.budget_preimage(kwargs["run_id"])
                failure_observation.update({
                    "preimage": preimage, "section_calls": tuple(section_calls),
                    "completed_before_failure": fail_index - 1,
                    "source_snapshot": _dependency_source_snapshot(runtime[0], results, factory.engines[0]),
                })
                message = "r02-native-" + document_failure
                cause = RuntimeError(message)
                cause.error = ErrorSchema(error_code="R02_NATIVE_SECTION_LOOP_FAILURE",
                    error_type="RuntimeError", message=message,
                    failed_node="evaluate_document_gate", failure_category=FailureCategory.UNKNOWN,
                    retryable=False, details={
                        "bc33_document_failure_intent": {
                            "external_marker": True, "kind": "DOCUMENT_NONCANCEL_FAILURE",
                        },
                    })
                raise cause
            return original_section(composition, **kwargs)

        def after_finalization(composition, current, **kwargs):
            from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import read_postwrite_finalization
            values = read_postwrite_finalization(composition, current.run_finalization_barrier)
            materialization, final_document = composition._finalization_results(
                values, tuple(kwargs["source_sections"]), kwargs["project_input"])
            capture_failure(composition, materialization, final_document, current)
            raise RuntimeError("controlled-completed-source-fixture-after-finalization")
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True, gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(w13, "_w13_two_section_project_input", project))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(SectionDependencyRuntime, "complete_source", complete))
        stack.enter_context(patch.object(CrossSectionClaimContextProvider, "candidates", collect))
        if pause_before_terminal or document_failure == "before-finalization":
            stack.enter_context(patch.object(TargetPostWriteComposition, "_commit_and_terminalize_run",
                before_terminalization))
        elif document_failure == "after-finalization":
            stack.enter_context(patch.object(TargetPostWriteComposition, "_finish_finalization", after_finalization))
        elif document_failure in {"section-loop-first", "section-loop-last"}:
            stack.enter_context(patch.object(TargetPostWriteComposition, "_section", section_loop_failure))
        if failure_status_projection:
            from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
            original_source_mode = SchemeGenerationStageService._target_status_source_mode

            def failure_projection_read(stage, shared_state, payload, *, node_name):
                if node_name == "Target failure status":
                    raise RuntimeError("r02-native-failure-status-projection-read")
                return original_source_mode(stage, shared_state, payload, node_name=node_name)

            stack.enter_context(patch.object(SchemeGenerationStageService,
                "_target_status_source_mode", failure_projection_read))
        task_id, run_id = "task350-" + scenario, "run350-" + scenario
        if stop_after_sections:
            summary = _run_dependency_sections(root, project(task_id), factory, settings,
                task_id=task_id, run_id=run_id)
        else:
            summary = MainlineApplicationService(supervisor_factory=factory).run(
                project_root=Path(w13.__file__).resolve().parents[2], user_input=project(task_id).user_query,
                task_id=task_id, run_id=run_id, output_root=root, clean_existing=True, settings=settings,
                project_input=project(task_id).model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False)
    top = w13._latest_w13_top_level_payload(factory.engines[0])
    assert runtime, top.terminal_error
    return dict(scenario=scenario, root=root, runtime=runtime[0], results=results, candidates=candidates,
                calls=calls, top=top, summary=summary, engine=factory.engines[0], authorization_checks=authorization_checks,
                factory_build_kwargs=factory.build_kwargs, failure_observation=failure_observation)


@pytest.fixture(scope="module")
def verified_mainline(tmp_path_factory):
    return _run_dependency_mainline(tmp_path_factory, "VERIFIED")


@pytest.fixture(scope="module", params=["VERIFIED", "UNSUPPORTED", "CONFLICTED"])
def dependency_mainline(tmp_path_factory, request, verified_mainline):
    return verified_mainline if request.param == "VERIFIED" else _run_dependency_mainline(tmp_path_factory, request.param)


def test_350_explicit_dependency_requires_canonical_acceptance(dependency_mainline):
    data = dependency_mainline
    results = data["results"]
    if data["scenario"] == "VERIFIED":
        assert len(results) == 3, data["top"].terminal_error
        assert all(s.workflow_status == "ACCEPTED" for _, s in results)
        assert data["candidates"]
    else:
        # SUCCESS producer and independent C finish; B never starts on support alone.
        assert len(results) == 2, data["top"].terminal_error
        assert all(s.workflow_status == "VERIFYING" for _, s in results)
        assert not data["candidates"]
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus, RunLifecycleStatus
        owner = data["runtime"]
        for _, source in results:
            lineage = owner.lifecycle.resolve_read_lineage(source.plan_binding.plan_version_ref.logical_ref.run_id,
                "section:" + source.plan_binding.section_id, expected_attempt_id=source.producer_attempt_id)
            assert lineage.run_status is RunLifecycleStatus.RUNNING
            assert lineage.attempt_status is AttemptLifecycleStatus.RUNNING
        assert owner.lifecycle.load_latest_attempt(data["top"].run_id,
            "section:" + data["top"].document_plan.sections[1].section_id) is None
        assert all(r.section.status.value == "success" for r, _ in results)


def test_350_only_semantic_authorized_claim_reaches_writer(dependency_mainline):
    data = dependency_mainline
    writers = [r for r in data["calls"] if r.extra.get("call_purpose") == "scheme_section_generation"]
    messages = [json.dumps(r.messages, ensure_ascii=False) for r in writers]
    projected = [s for s in messages if 'verified_statement' in s]
    assert len(projected) == (1 if data["scenario"] == "VERIFIED" else 0)
    if projected:
        assert TEXT in projected[0]
        for forbidden in ("accepted_source", "consumer_plan_binding", "coverage_proof_refs", "claim_head_revision"):
            assert forbidden not in projected[0]


def test_350_projection_replay_does_not_call_models_or_commit_truth(verified_mainline):
    data = verified_mainline
    runtime = data["runtime"]
    paths = (runtime.claim_runtime.ledger_path, runtime.lifecycle.path, runtime.paragraph_runtime.manifest_ledger_path)
    before = [p.read_bytes() for p in paths]
    calls = len(data["calls"])
    for result, source in data["results"]:
        assert runtime.complete_source(result) == source
        assert runtime.validate_source(source, require_accepted=True) == source
    assert [p.read_bytes() for p in paths] == before
    assert len(data["calls"]) == calls


@pytest.mark.parametrize("defect", ["wrong-run", "wrong-artifact", "wrong-section", "wrong-attempt", "stale-input",
                                    "missing-verification", "missing-coverage", "open-manifest", "forged-hash"])
def test_350_source_substitution_fails_closed(verified_mainline, defect):
    from core.hashing import canonical_sha256
    from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyIntegrityError
    data = verified_mainline
    source = data["results"][0][1].model_dump(mode="json")
    if defect == "wrong-run":
        source["plan_binding"]["plan_version_ref"]["logical_ref"]["run_id"] = "other-run"
    elif defect == "wrong-artifact":
        source["plan_binding"]["plan_version_ref"]["artifact_version_id"] = "other-artifact"
    elif defect == "wrong-section":
        source["plan_binding"]["section_id"] = "other-section"
    elif defect == "wrong-attempt":
        source["producer_attempt_id"] = "other-attempt"
    elif defect == "stale-input":
        source["input_snapshot_version"] = "stale-input"
    elif defect == "missing-verification":
        source["verification_refs"] = []
    elif defect == "missing-coverage":
        source["coverage_proof_refs"] = []
    elif defect == "open-manifest":
        source["paragraph_manifest"]["generation_membership_state"] = "OPEN"
    source["source_sha256"] = canonical_sha256({k: v for k, v in source.items() if k not in {"source_ref", "source_sha256"}})
    if defect == "forged-hash":
        source["source_sha256"] = "0" * 64
    with pytest.raises(SectionDependencyIntegrityError):
        data["runtime"].validate_source(source, require_accepted=True)


def _check_consumer_authorization(data):
    from schemas.context import ContextInvocationSubjectSchema
    from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyIntegrityError
    binding = data["candidates"][0].source_binding
    exact = binding.exact_source_ref
    provider = CrossSectionClaimContextProvider(data["runtime"])
    subject = ContextInvocationSubjectSchema(run_id=binding.run_id, attempt_id=exact["consumer_attempt_id"],
        invocation_id="350-test-consumer", logical_operation_id="350-test-consumer",
        agent_role="WRITER", execution_mode="NORMAL", context_purpose="SECTION_GENERATION",
        consumer_scope_ref="section:" + exact["consumer_plan_binding"]["section_id"])
    assert provider.hard_eligible(binding, subject) == (True, ())
    # ApplicationContextRuntime runs source_binding_resolver immediately before
    # this hook; it must not repeat the full canonical source traversal.
    with patch.object(provider, "_resolve", side_effect=AssertionError("resolved hard eligibility reread source")):
        assert provider.hard_eligible_after_resolution(binding, subject) == (True, ())
    for field, wrong in {"agent_role": "VERIFIER", "consumer_scope_ref": "section:unauthorized-C",
                         "run_id": "wrong-run", "attempt_id": "wrong-attempt",
                         "context_purpose": "RESEARCH", "execution_mode": "REPAIR"}.items():
        wrong_subject = subject.model_copy(update={field: wrong})
        with pytest.raises(SectionDependencyIntegrityError):
            provider.hard_eligible(binding, wrong_subject)
        with pytest.raises(SectionDependencyIntegrityError):
            provider.hard_eligible_after_resolution(binding, wrong_subject)
    # C has a real accepted source but is not in B's explicit dependency authority.
    unrelated = data["results"][2][1]
    forged = dict(exact, accepted_source=unrelated.model_dump(mode="json"),
                  claim_ref=unrelated.verification_refs[0].claim_ref.model_dump(mode="json"))
    with pytest.raises(SectionDependencyIntegrityError):
        provider.hard_eligible(binding.model_copy(update={"exact_source_ref": forged,
            "scope_ref": "section:" + unrelated.plan_binding.section_id}), subject)


def test_350_consumer_authorization_and_unrelated_source_are_fail_closed(verified_mainline):
    from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyIntegrityError
    data = verified_mainline
    assert data["authorization_checks"] == [True]
    # Consumer replay cannot authorize a new Writer after canonical Run completion.
    with pytest.raises(SectionDependencyIntegrityError, match="dispatch-current"):
        CrossSectionClaimContextProvider(data["runtime"]).resolve_binding_current(data["candidates"][0].source_binding)


def test_350_repeated_same_claim_transport_has_no_new_authority(verified_mainline):
    data = verified_mainline
    runtime = data["runtime"]
    source = data["results"][0][1]
    before = runtime.claim_runtime.ledger_path.read_bytes()
    with patch.object(runtime, "_evaluate", wraps=runtime._evaluate) as evaluate:
        first = runtime.read_active_verified_source(source.verification_refs[0].claim_ref, source)
        second = runtime.read_active_verified_source(source.verification_refs[0].claim_ref, source)
    assert evaluate.call_count == 2  # one complete current evaluation per consumer read
    assert first == second
    assert first[0].claim_ref == source.verification_refs[0].claim_ref
    assert runtime.claim_runtime.ledger_path.read_bytes() == before
    assert len([r for r in data["calls"] if r.extra.get("call_purpose") == "claim_verification"]) == 3


@pytest.mark.parametrize("status", ["partial_success", "failed", "retryable_failed", "cancelled", "skipped"])
def test_350_complete_verified_claim_scope_cannot_override_unsuccessful_producer(verified_mainline, status):
    data = verified_mainline
    runtime = data["runtime"]
    original = data["results"][0][1]
    payload = {name: getattr(original, name) for name in type(original).model_fields if name != "source_sha256"}
    payload.update(producer_execution_status=status, workflow_status="VERIFYING")
    candidate = runtime._create(**payload)
    before = runtime.claim_runtime.ledger_path.read_bytes()
    accepted, _, _ = runtime._evaluate(candidate)
    assert not accepted
    assert runtime.claim_runtime.ledger_path.read_bytes() == before


@pytest.mark.parametrize("replay_case", ["exact", "stale_dependency", "wrong_dependency_plan"])
def test_350_reopened_factory_reuses_terminal_dependent_checkpoint_without_writes(verified_mainline, replay_case):
    import hashlib
    import sqlite3
    from apps.enterprise_document.services.scheme_writer.document_citation_registry import DocumentCitationRegistry
    data = verified_mainline
    top = data["top"]
    source = data["results"][1][1]
    plan = next(s for s in top.document_plan.sections if s.section_id == source.plan_binding.section_id)
    def durable_snapshot():
        files = {str(p.relative_to(data["root"])): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in data["root"].rglob("*") if p.is_file() and p.suffix in {".jsonl", ".json"}}
        databases = {}
        for p in (data["root"] / "runs").glob("*.sqlite3"):
            with sqlite3.connect(p) as con:
                databases[p.name] = tuple(con.iterdump())
        return files, databases
    before = durable_snapshot()
    with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("terminal replay redispatched model")):
        supervisor = Factory().build(**data["factory_build_kwargs"])
        try:
            engine = supervisor.workflow_engine
            graph = engine.section_execution_graph
            assert graph.claim_verification_runtime is not data["runtime"].claim_runtime
            assert engine.lifecycle_service is not data["runtime"].lifecycle
            history = graph.checkpoint_history(workflow_namespace=top.workflow_namespace,
                run_id=top.run_id, section_id=plan.section_id)
            payload = next(graph.codec.restore_payload(row.checkpoint["channel_values"])
                           for row in history if row.checkpoint.get("channel_values", {}).get("section_payload", {}).get("completed"))
            state = graph.codec.restore_graph_state(payload)
            def recover():
                return graph.recover_or_reuse(workflow_namespace=top.workflow_namespace,
                    task_id=top.task_id, run_id=top.run_id, document_id=top.document_id,
                    section_id=plan.section_id, project_input=top.project_input, section_plan=plan,
                    shared_state=state, citation_registry=DocumentCitationRegistry(),
                    expected_canonical_plan_binding=source.plan_binding)
            with patch.object(graph, "resume", side_effect=AssertionError("terminal replay resumed graph")):
                if replay_case == "exact":
                    replay = recover()
                    assert replay == payload.result
                    assert replay.canonical_section_source_binding == source
                    assert replay.active_execution_lineage == payload.active_execution_lineage
                else:
                    store = graph.coordinator.section_dependencies.store
                    original_read = store.get_json
                    def corrupt_dependency(ref, **identity):
                        proof = original_read(ref, **identity)
                        if ref.endswith("/dependency-input"):
                            proof = dict(proof)
                            if replay_case == "stale_dependency":
                                proof["dependency_fingerprint"] = "0" * 64
                            else:
                                proof["plan_binding"] = data["results"][0][1].plan_binding.model_dump(mode="json")
                        return proof
                    with patch.object(store, "get_json", side_effect=corrupt_dependency):
                        with pytest.raises(ValueError, match="dependency observation is stale"):
                            recover()
            assert durable_snapshot() == before
        finally:
            supervisor.close()


def test_350_completed_source_adapter_then_supersession(verified_mainline, tmp_path_factory):
    """Actual Lifecycle transitions, exact durable source re-open; no truth rewrite."""
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus, RunLifecycleStatus
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
    from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyIntegrityError
    # Stop the real compiled graph after successful Section execution. This
    # fixture never uses a technical exception to simulate a RUNNING pause.
    data = _run_dependency_mainline(tmp_path_factory, "VERIFIED", stop_after_sections=True)
    assert data["top"].terminal_error is None
    assert len(data["results"]) == 3
    runtime = data["runtime"]
    source = data["results"][0][1]
    current = runtime.lifecycle.load_attempt(source.producer_attempt_id)
    assert runtime.lifecycle.load_run(data["top"].run_id).status is RunLifecycleStatus.RUNNING
    runtime.lifecycle.transition_attempt(source.producer_attempt_id, current.lifecycle_revision, AttemptLifecycleStatus.COMPLETED)
    current = runtime.lifecycle.load_attempt(source.producer_attempt_id)
    assert current.status is AttemptLifecycleStatus.COMPLETED
    before = (runtime.lifecycle.path.read_bytes(), runtime.claim_runtime.ledger_path.read_bytes())
    reopened = SectionDependencyRuntime(plan_runtime=runtime.plan_runtime, claim_runtime=runtime.claim_runtime,
        verifier_composition=runtime.verifier, writer_execution_store=runtime.writer_execution_store)
    assert reopened.resolve_section_source(data["results"][1][1].plan_binding, source.plan_binding.section_id) == source
    claim, _, view = reopened.read_active_verified_source(source.verification_refs[0].claim_ref, source)
    assert view.active_verified_claim
    with pytest.raises(LifecycleCurrentnessError):
        runtime.lifecycle.assert_attempt_committable(run_id=source.plan_binding.plan_version_ref.logical_ref.run_id,
            attempt_scope_ref="section:" + source.plan_binding.section_id, attempt_id=source.producer_attempt_id,
            input_snapshot_version=source.input_snapshot_version)
    assert (runtime.lifecycle.path.read_bytes(), runtime.claim_runtime.ledger_path.read_bytes()) == before
    # The ordinary native fixture independently proves the terminal write fence.
    terminal_runtime = verified_mainline["runtime"]
    terminal_source = verified_mainline["results"][0][1]
    assert terminal_runtime.lifecycle.load_run(verified_mainline["top"].run_id).status is RunLifecycleStatus.SUCCESS
    terminal_before = terminal_runtime.lifecycle.path.read_bytes()
    with pytest.raises(LifecycleCurrentnessError, match="RUNNING canonical Run"):
        terminal_runtime.lifecycle.open_attempt(verified_mainline["top"].run_id,
            "section:" + terminal_source.plan_binding.section_id, "350-replacement-input")
    assert terminal_runtime.lifecycle.path.read_bytes() == terminal_before


@pytest.mark.parametrize("failure_point", ["before-finalization", "after-finalization"])
def test_r02_native_document_failure_preserves_primary_and_all_sources(tmp_path_factory, failure_point):
    """Original native 350 boundary, independently of the RUNNING source fixture."""
    import hashlib
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
        AttemptLifecycleStatus, RunLifecycleStatus,
    )
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    from apps.enterprise_document.services.agent_rag_v1.post_write_review_draft import TargetReviewDraftReader

    data = _run_dependency_mainline(tmp_path_factory, "VERIFIED", document_failure=failure_point)
    observed = data["failure_observation"]
    print("R02_NATIVE_TARGET_REACHED", json.dumps({
        "failure_point": failure_point, "TARGET_ASSERTION_REACHED": bool(observed),
        "first_error": data["top"].terminal_error.model_dump(mode="json") if data["top"].terminal_error else None,
        "failed_node": data["top"].failed_business_node,
    }, ensure_ascii=False))
    assert observed, data["top"].terminal_error
    assert "capture_error" not in observed, observed.get("capture_error")
    preimage = observed["preimage"]
    expected_message = ("controlled-completed-source-fixture-before-terminal"
        if failure_point == "before-finalization"
        else "controlled-completed-source-fixture-after-finalization")
    assert len(preimage.current_children) == 3
    assert all(row.status is AttemptLifecycleStatus.RUNNING for row in preimage.current_children)
    assert preimage.run.status is (
        RunLifecycleStatus.RUNNING if failure_point == "before-finalization" else RunLifecycleStatus.FINALIZING)
    if failure_point == "after-finalization":
        assert preimage.run.run_finalization_barrier is not None
        assert preimage.run.run_finalization_barrier_ref == preimage.run.run_finalization_barrier.as_ref()

    top, summary = data["top"], data["summary"]
    assert top.terminal_error.message == expected_message
    assert top.terminal_error.error_type == "RuntimeError"
    assert top.failed_business_node == "evaluate_document_gate"
    assert top.terminal_agent_result is not None
    assert top.terminal_agent_result.error.message == expected_message
    errors = [summary["task_state"]["error"], summary["supervisor_result"]["error"],
        summary["supervisor_result"]["result"]["workflow_execution"]["error"]]
    assert all(error["message"] == expected_message and error["error_type"] == "RuntimeError" for error in errors)
    assert all("LifecycleIntegrityError" not in error["message"]
        and "no terminal AgentResult" not in error["message"]
        and "without terminal AgentResult" not in error["message"] for error in errors)

    lifecycle = data["runtime"].lifecycle
    canonical = lifecycle.load_run_lifecycle(top.run_id)
    assert canonical.status is RunLifecycleStatus.FAILED
    assert canonical.run_finalization_barrier == preimage.run.run_finalization_barrier
    children = tuple(lifecycle.load_attempt(row.identity.attempt_id) for row in preimage.current_children)
    assert all(row.status is AttemptLifecycleStatus.CANCELLED for row in children)
    assert all(row.failure_ref == "run_document_failure_cancel" for row in children)
    assert not lifecycle.budget_preimage(top.run_id).current_children
    _, frames = lifecycle._decode_history_unlocked()
    frames = [frame for frame in frames if frame["request"]["kind"] == "DOCUMENT_NONCANCEL_FAILURE"]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["before_run"] == preimage.run.model_dump(mode="json", round_trip=True)
    assert frame["request"]["expected_attempt_revisions"] == {
        row.identity.attempt_id: row.lifecycle_revision for row in preimage.current_children}
    assert frame["request"]["triggering_attempt_id"] is None
    assert frame["resulting_run"] == canonical.model_dump(mode="json", round_trip=True)
    assert {row["status"] for row in frame["resulting_attempts"]} == {"cancelled"}
    cold = RunAttemptLifecycleService(lifecycle.path)
    assert cold.load_run_lifecycle(top.run_id) == canonical
    assert tuple(cold.load_attempt(row.identity.attempt_id) for row in preimage.current_children) == children
    bindings = [error["details"]["lifecycle_composite_transition"] for error in errors]
    assert all(binding == bindings[0] for binding in bindings)
    assert bindings[0]["event_sha256"] == frame["event_sha256"]
    assert bindings[0]["resulting_run"] == frame["resulting_run"]

    # Exact historical owner reads remain valid without authorizing new dispatch.
    assert _dependency_source_snapshot(data["runtime"], data["results"], data["engine"]) == observed["source_snapshot"]
    materialization, final_document = observed["materialization"], observed["final_document"]
    post_write = observed["composition"]
    review = TargetReviewDraftReader(post_write).read(
        document_artifact_ref=final_document.artifact_state_ref,
        control_decision_ref=final_document.control_decision_ref,
        task_id=top.task_id, run_id=top.run_id)
    assert review.validated_output_bytes == observed["review"].validated_output_bytes
    assert review.editable_review_text == observed["review"].editable_review_text
    assert review.formal_quality_eligible == observed["review"].formal_quality_eligible
    assert len(materialization.section_results) == 3
    for result, source in data["results"]:
        writer = data["runtime"].writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
        assert writer.artifact.content == result.section.content
    assert top.generated_draft is not None
    assert all(result.section.content in top.generated_draft.full_text for result, _ in data["results"])
    assert summary["target_release_publication"] is None
    assert summary["deliverable_final_result"] is None
    assert top.target_release_publication is None
    evidence = {
        "failure_point": failure_point, "primary_error": expected_message,
        "run_status": canonical.status.value,
        "attempt_statuses": {row.identity.attempt_id: row.status.value for row in children},
        "before_run_revision": preimage.run.lifecycle_revision,
        "composite_event_sha256": frame["event_sha256"],
        "composite_operation_key": frame["operation_key"],
        "section_body_sha256": {row["section_id"]: row["body_sha256"] for row in observed["source_snapshot"]["records"]},
        "canonical_ledger_sha256_unchanged": observed["source_snapshot"]["ledger_sha256"],
        "review_payload_sha256": hashlib.sha256(review.validated_output_bytes).hexdigest(),
        "review_reader_readback": True, "TARGET_ASSERTION_REACHED": True,
        "task_error": errors[0], "workflow_error": errors[2],
    }
    (data["root"] / "r02_native_failure_observation.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("R02_NATIVE_FAILURE_OBS", json.dumps(evidence, ensure_ascii=False))


@pytest.mark.parametrize("failure_point", ["section-loop-first", "section-loop-last"])
def test_r02_native_section_loop_cannot_authorize_document_failure(tmp_path_factory, failure_point):
    """A real incomplete native _section prefix overrides any external marker."""
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus

    data = _run_dependency_mainline(tmp_path_factory, "VERIFIED", document_failure=failure_point)
    observed = data["failure_observation"]
    top, summary = data["top"], data["summary"]
    assert observed, top.terminal_error
    preimage = observed["preimage"]
    assert len(preimage.current_children) == 3
    assert preimage.run.status is RunLifecycleStatus.RUNNING
    expected_prefix = 0 if failure_point == "section-loop-first" else 2
    assert observed["completed_before_failure"] == expected_prefix
    assert len(observed["section_calls"]) == expected_prefix + 1
    expected_message = "r02-native-" + failure_point
    assert top.terminal_error.message == expected_message
    assert top.terminal_error.error_type == "RuntimeError"
    assert top.failed_business_node == "evaluate_document_gate"
    assert top.terminal_agent_result is not None
    assert top.terminal_agent_result.error.message == expected_message
    assert top.terminal_agent_result.metadata["r02_failure_diagnostics"]
    assert any(d.code == "R02_DOCUMENT_FAILURE_UNPROVEN" for d in top.terminal_outcome.diagnostics)
    assert any(d.code == "R02_FAILURE_CLEANUP_DIAGNOSTIC" for d in top.terminal_outcome.diagnostics)
    assert top.terminal_outcome.run_status is RunLifecycleStatus.RUNNING
    errors = [summary["task_state"]["error"], summary["supervisor_result"]["error"],
        summary["supervisor_result"]["result"]["workflow_execution"]["error"]]
    for error in errors:
        assert error["message"] == expected_message
        assert error["error_type"] == "RuntimeError"
        assert "bc33_document_failure_intent" not in error["details"]
        assert "lifecycle_composite_transition" not in error["details"]
    # Task.error intentionally retains the first failed node's primary error.
    # Cleanup must be reachable through actual terminal results, not mirrored
    # into every historical error packet or backed by a fabricated receipt.
    from application.terminal_outcome_projector import public_terminal_result
    from core.hashing import canonical_sha256

    packet = top.terminal_agent_result.metadata["r02_failure_diagnostics"]
    assert any(row["operation"] == "lifecycle_cleanup" for row in packet)
    assert top.terminal_agent_result.error.details["r02_failure_diagnostics"] == packet
    assert top.terminal_agent_result.result["r02_failure_diagnostics"] == packet
    assert errors[2]["details"]["r02_failure_diagnostics"] == packet
    supervisor_result = summary["supervisor_result"]
    finalizers = [row for row in supervisor_result["result"]["sub_agent_results"]
        if row["result_type"] == "scheme_writer_output" and row["metadata"].get("technical_failure")]
    assert len(finalizers) == 1
    assert finalizers[0]["result"]["r02_failure_diagnostics"] == packet
    assert finalizers[0]["metadata"]["r02_failure_diagnostics"] == packet
    assert finalizers[0]["error"]["details"]["r02_failure_diagnostics"] == packet
    assert summary["task_state"]["result_id"] == supervisor_result["result_id"]
    public = public_terminal_result(summary)
    cleanup_refs = [row["diagnostic_ref"] for row in public["terminal_outcome"]["diagnostics"]
        if row["code"] == "R02_FAILURE_CLEANUP_DIAGNOSTIC"]
    assert cleanup_refs == ["sha256:" + canonical_sha256(packet)]
    persisted = w13._latest_w13_top_level_payload(data["engine"])
    assert persisted.terminal_agent_result.metadata["r02_failure_diagnostics"] == packet
    assert persisted.terminal_agent_result.result["r02_failure_diagnostics"] == packet
    assert persisted.terminal_agent_result.error.details["r02_failure_diagnostics"] == packet
    assert [d.diagnostic_ref for d in persisted.terminal_outcome.diagnostics
        if d.code == "R02_FAILURE_CLEANUP_DIAGNOSTIC"] == cleanup_refs
    lifecycle = data["runtime"].lifecycle
    after = lifecycle.budget_preimage(top.run_id)
    assert after == preimage
    assert all(lifecycle.load_attempt(row.identity.attempt_id) == row
        for row in preimage.current_children)
    _, frames = lifecycle._decode_history_unlocked()
    assert not any(frame["request"]["kind"] == "DOCUMENT_NONCANCEL_FAILURE" for frame in frames)
    assert _dependency_source_snapshot(data["runtime"], data["results"], data["engine"]) == observed["source_snapshot"]
    assert summary["target_release_publication"] is None
    assert summary["deliverable_final_result"] is None
    evidence = {
        "failure_point": failure_point, "primary_error": expected_message,
        "failed_node": top.failed_business_node, "TARGET_ASSERTION_REACHED": True,
        "native_completed_prefix": expected_prefix,
        "external_marker_rejected": True, "document_composite_count": 0,
        "run_status": after.run.status.value,
        "attempt_statuses_unchanged": {row.identity.attempt_id: row.status.value for row in after.current_children},
        "cleanup_diagnostics": packet,
        "cleanup_task_error_mirrored": any(row["operation"] == "lifecycle_cleanup"
            for row in errors[0]["details"].get("r02_failure_diagnostics", [])),
        "cleanup_result_paths": [
            "workflow_execution.error.details.r02_failure_diagnostics",
            "sub_agent_results[finalizer].error/details/result/metadata",
            "durable_checkpoint.terminal_agent_result",
            "public.terminal_outcome.diagnostics.R02_FAILURE_CLEANUP_DIAGNOSTIC",
        ],
        "public_cleanup_diagnostic_refs": cleanup_refs,
        "durable_checkpoint_verified": True,
        "task_result_id": summary["task_state"]["result_id"],
        "task_error": errors[0],
    }
    (data["root"] / "r02_native_section_negative_observation.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("R02_NATIVE_SECTION_NEGATIVE_OBS", json.dumps(evidence, ensure_ascii=False))


def test_r02_native_projection_read_failure_retains_document_intent_on_replay(tmp_path_factory):
    """Presentation failure cannot erase the native fixed failure request."""
    from agent.runtime.graph_state import GraphStateSchema
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
        AttemptLifecycleStatus, RunLifecycleStatus,
    )
    from core.hashing import canonical_sha256

    data = _run_dependency_mainline(tmp_path_factory, "VERIFIED",
        document_failure="before-finalization", failure_status_projection=True)
    top, summary = data["top"], data["summary"]
    observed = data["failure_observation"]
    assert observed, top.terminal_error
    assert "capture_error" not in observed, observed.get("capture_error")
    primary = "controlled-completed-source-fixture-before-terminal"
    assert top.terminal_error.message == primary
    assert top.terminal_error.error_type == "RuntimeError"
    assert top.terminal_agent_result is not None
    packet = top.terminal_agent_result.metadata["r02_failure_diagnostics"]
    assert any(row["operation"] == "failure_status_projection"
        and row["message"] == "r02-native-failure-status-projection-read" for row in packet)
    intent = top.terminal_error.details["bc33_document_failure_intent"]
    intent_ref = "sha256:" + canonical_sha256(intent)
    assert [row.diagnostic_ref for row in top.terminal_outcome.diagnostics
        if row.code == "R02_DOCUMENT_FAILURE_INTENT"] == [intent_ref]
    binding = top.terminal_agent_result.result["lifecycle_composite_transition"]
    lifecycle = data["runtime"].lifecycle
    canonical = lifecycle.load_run_lifecycle(top.run_id)
    assert canonical.status is RunLifecycleStatus.FAILED
    before_children = observed["preimage"].current_children
    assert len(before_children) == 3
    assert all(lifecycle.load_attempt(row.identity.attempt_id).status is AttemptLifecycleStatus.CANCELLED
        for row in before_children)
    journal = lifecycle.path.read_bytes()
    # Restore the actual public runtime state and use the unchanged real Stage
    # finalizer after the scoped projection-read fault has ended.
    state = GraphStateSchema.model_validate(summary["supervisor_result"]["result"]["shared_state"])
    replay = data["engine"].stage_service.finalize_workflow_failure(state, top)
    assert lifecycle.path.read_bytes() == journal
    assert replay.terminal_error.message == primary
    assert replay.terminal_error.error_type == "RuntimeError"
    assert replay.terminal_agent_result.result["lifecycle_composite_transition"] == binding
    assert replay.terminal_agent_result.error.details["lifecycle_composite_transition"] == binding
    assert replay.terminal_outcome.run_status is RunLifecycleStatus.FAILED
    assert [row.diagnostic_ref for row in replay.terminal_outcome.diagnostics
        if row.code == "R02_DOCUMENT_FAILURE_INTENT"] == [intent_ref]
    assert lifecycle.load_run_lifecycle(top.run_id) == canonical
    assert all(lifecycle.load_attempt(row.identity.attempt_id).status is AttemptLifecycleStatus.CANCELLED
        for row in before_children)
    assert summary["task_state"]["error"]["message"] == primary
    assert summary["supervisor_result"]["error"]["message"] == primary
    assert summary["target_release_publication"] is None
    evidence = {
        "primary_error": primary, "TARGET_ASSERTION_REACHED": True,
        "projection_read_failure": packet, "document_intent_diagnostic_ref": intent_ref,
        "same_composite_binding_after_replay": True, "journal_bytes_unchanged": True,
        "run_status": canonical.status.value,
        "attempt_statuses": {row.identity.attempt_id: lifecycle.load_attempt(row.identity.attempt_id).status.value
            for row in before_children},
        "composite_event_sha256": binding["event_sha256"],
    }
    (data["root"] / "r02_native_projection_replay_observation.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("R02_NATIVE_PROJECTION_REPLAY_OBS", json.dumps(evidence, ensure_ascii=False))
