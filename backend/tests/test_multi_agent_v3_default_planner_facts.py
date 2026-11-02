"""339: default production Planner cognition, not a patched Planner candidate."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.context import ContextPackageSchema, ContextSourceFamily
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    CURRENT_TASK_EXECUTION_PROFILE,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import (
    _restore_context_package_semantic_snapshot,
)
import test_multi_agent_v3_writer_fact_requirements as fact_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests

CONTRACT = "planner_fact_requirements_v1"
SENTINEL = "NO_RAW_METADATA_IN_PLANNER_339"


def _planning_contract(request):
    for message in request.messages:
        try:
            value = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and "section_contracts" in value:
            return value
    raise AssertionError("Planner received no typed section/task contract")


def _project(task_id):
    raw = fact_tests._project_input(task_id).model_dump(mode="json")
    titles = ["资源现状", "组织与分工"]
    raw["generation_requirements"]["required_sections"] = titles
    raw["output_schema"]["required_sections"] = titles
    raw["metadata"] = {"untrusted": SENTINEL}
    raw["extra"] = {"untrusted": SENTINEL}
    return ProjectInputSchema.model_validate(raw)


@pytest.fixture(scope="module", params=("normal", "fallback"))
def default_planner_run(tmp_path_factory, request):
    route = request.param
    root = tmp_path_factory.mktemp("default-planner-" + route)
    task_id, run_id = "task339-" + route, "run339-" + route
    project = _project(task_id)
    calls, engines = [], []
    original = FakeLLMClient.generate

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original(client, req)
        if req.extra.get("response_contract") == CONTRACT:
            sections = _planning_contract(req)["section_contracts"]
            # Only the external model's typed output is controlled. No Planner,
            # canonical Fact/Plan, route, Writer input or lifecycle is patched.
            choices = ["project_input.hardware_resources", "project_input.organization_scale"]
            body = {"sections": [
                {"section_id": section["section_id"], "fact_requirements": [
                    {"fact_key": choices[index], "importance": "required", "purpose": "核实本章条件"}
                ]} for index, section in enumerate(sections)
            ]}
            return response.model_copy(update={"content": json.dumps(body, ensure_ascii=False)}, deep=True)
        if route == "fallback" and req.extra.get("response_contract") == "criterion_support_evaluator_v1":
            body = json.loads(response.content)
            body.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(body)}, deep=True)
        return response

    class InspectingFactory(SupervisorFactory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs)
            engines.append(supervisor.workflow_engine)
            return supervisor

    settings = replace(
        get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True,
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0,
    )
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false",
            "ENABLE_SEMANTIC_GATE": "false",
        }))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        summary = MainlineApplicationService(supervisor_factory=InspectingFactory()).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            run_id=run_id, task_id=task_id, output_root=root, clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    if summary["status"] != "partial_success":
        error = workflow_tests._latest_w13_top_level_payload(engines[0]).terminal_error
        raise AssertionError(error.model_dump(mode="json") if error is not None else summary["status"])
    return project, run_id, calls, engines[0], summary, route


def test_default_factory_dispatches_one_planner_call_for_all_sections(default_planner_run):
    project, run_id, calls, engine, summary, _ = default_planner_run
    planner = [item for item in calls if item.extra.get("response_contract") == CONTRACT]
    assert len(planner) == 1, "Default Planner must actually produce requirements"
    req = planner[0]
    assert req.caller_agent == "PlannerAgent"
    assert req.model_role == "general"
    assert req.prompt == ""
    assert SENTINEL not in json.dumps(req.messages, ensure_ascii=False)
    task = _planning_contract(req)
    assert "business_goal" not in task["task"]
    assert "Treat user_query as the document task" in json.dumps(req.messages, ensure_ascii=False)
    assert len(task["section_contracts"]) == 2
    assert len(task["fact_field_catalog"]) == 5
    assert task["task"]["user_query"] == project.user_query
    assert "当前未提供硬件资源信息" not in json.dumps(req.messages, ensure_ascii=False)
    assert "2台服务器X" in json.dumps(req.messages, ensure_ascii=False)
    state = engine.budget_runtime(project.task_id, run_id).canonical_state()
    assert state.used.logical_model_calls >= len(planner)
    assert summary["status"] == "partial_success", "Planning is not quality PASS"


def test_default_planner_requirements_reach_actual_writer_and_preserve_missing(default_planner_run):
    _, run_id, calls, engine, _, route = default_planner_run
    top = workflow_tests._latest_w13_top_level_payload(engine)
    results = top.section_execution_results
    assert len(results) == 2
    store = engine.section_execution_graph.coordinator.require_pre_write_services().writer_invocation.execution_store
    expected = ("project_input.hardware_resources", "project_input.organization_scale")
    for index, result in enumerate(results):
        invocation = store.resolve_writer_generation_invocation(result.writer_generation_invocation_ref)
        package = _restore_context_package_semantic_snapshot(invocation.unified_writer_input_snapshot["target_context_package"])
        families = {item.candidate_id: item.source_binding.source_family for item in package.candidate_decisions}
        payload = next(item.materialized_payload for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.FACT_SNAPSHOT)
        assert [item["fact_key"] for item in payload["fact_requirements"]] == [expected[index]]
        if index == 0:
            assert [item["fact_id"] for item in payload["facts"]] == [f"fact_{run_id}_hardware"]
            assert payload["unresolved_fact_requirements"] == []
        else:
            assert payload["facts"] == []
            assert payload["unresolved_fact_requirements"] == payload["fact_requirements"]
        actual = next(item for item in calls if item.extra.get("call_purpose") == "scheme_section_generation" and item.extra.get("section_id") == result.section.section_id)
        model_input = {"input_kind": "MESSAGES", "messages": actual.messages}
        assert canonical_sha256(model_input) == package.model_input.final_model_input_sha256
        assert invocation.generation_class.value == ("normal_write" if route == "normal" else "fallback_write")


def _proposal(plan, key="project_input.hardware_resources"):
    return {"sections": [
        {"section_id": section.section_id, "fact_requirements": [
            {"fact_key": key, "importance": "required", "purpose": "本章事实条件"}
        ]} for section in plan.sections
    ]}


def _planner_unit(tmp_path):
    from types import SimpleNamespace
    from schemas.task import TaskSchema
    from schemas.model import ModelResponseSchema
    from task.task_manager import JsonlTaskManager
    from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
    from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import FactSnapshotRuntime
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    from apps.enterprise_document.services.agent_rag_v1.plan_runtime import SectionPlanRuntime
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import TargetWorkflowRuntime, TaskRequestFactSourceResolver
    from apps.enterprise_document.services.agent_rag_v1.planner_fact_requirements import TargetPlannerFactRequirements
    from apps.enterprise_document.services.project_input_normalization.fact_extractor import StructuredFactExtractor
    from apps.enterprise_document.services.scheme_writer.document_planning_service import DocumentPlanningService
    from apps.enterprise_document.schemas.agent_rag_v1 import AgentExecutionContextSchema, AgentRole, PlannerExecutionMode
    from apps.enterprise_document.services.scheme_writer.writer_admission import (
        EvidenceRequirementsMaterializer, scheme_generation_minimal_profile, scheme_generation_minimal_task_contract,
    )

    project, run_id, document_id = _project("task339-unit"), "run339-unit", "document_run339-unit"
    tasks = JsonlTaskManager(tmp_path / "tasks")
    tasks.create_task(TaskSchema(
        task_id=project.task_id, run_id=run_id, tenant_id="default", task_type="scheme_generation",
        user_input=project.user_query, project_input=project.model_dump(mode="json"), created_at="2026-09-08T00:00:00Z",
        metadata={"execution_profile": CURRENT_TASK_EXECUTION_PROFILE},
    ))
    lifecycle = RunAttemptLifecycleService(tmp_path / "lifecycle.jsonl")
    artifact = ArtifactRuntime(tmp_path / "artifact.jsonl", lifecycle_service=lifecycle)
    facts = FactSnapshotRuntime(artifact_runtime=artifact, source_binding_resolver=TaskRequestFactSourceResolver(tasks))
    requirements = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(), task_contract=scheme_generation_minimal_task_contract(),
    )
    plans = SectionPlanRuntime(artifact_runtime=artifact, fact_snapshot_runtime=facts, requirements_resolver=lambda _ref: requirements)
    workflow = TargetWorkflowRuntime(task_manager=tasks, lifecycle_service=lifecycle, fact_snapshot_runtime=facts,
                                     section_plan_runtime=plans, requirements=requirements)
    rows = StructuredFactExtractor().extract(SimpleNamespace(task_id=project.task_id, run_id=run_id), project, "2026-09-08T00:00:00Z")
    binding, lineage = workflow.ensure_initial_plan_prerequisites(
        task_id=project.task_id, run_id=run_id, document_id=document_id, project_input=project,
        required_sections=project.generation_requirements.required_sections, structured_facts=rows,
    )
    plan = DocumentPlanningService.build_document_plan(
        run_id=run_id, document_id=document_id, project_input=project,
        required_sections=project.generation_requirements.required_sections, created_at="2026-09-08T00:00:00Z",
    )
    context = AgentExecutionContextSchema(
        invocation_id="planner-invocation-339", logical_operation_id=f"{run_id}:plan_document",
        task_id=project.task_id, run_id=run_id, agent_role=AgentRole.PLANNER,
        execution_mode=PlannerExecutionMode.INITIAL_PLAN.value, execution_id="planner-execution-339",
        attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version,
        expected_domain_versions={}, task_context_ref="task://" + project.task_id,
        source_snapshot_ref=binding.version_ref.content_ref, authorized_artifact_refs=[binding.version_ref.content_ref],
        authorized_tool_names=[], writable_state_paths=[], budget={}, trigger={}, trace_context={},
    )

    class Gateway:
        def __init__(self):
            self.calls = []
            self.output = _proposal(plan)
            self.failure = None
            self.hook = None

        def routing_capabilities(self, **_kwargs):
            return {"safe_context_window": 32768, "safe_max_output_tokens": 4096,
                    "candidate_profiles": ["test-profile"], "candidate_models": ["fake_llm"]}

        def generate(self, request):
            self.calls.append(request.model_copy(deep=True))
            if self.failure is not None:
                raise self.failure
            if self.hook is not None:
                self.hook()
            return ModelResponseSchema(
                model_call_id=request.model_call_id, task_id=request.task_id, run_id=request.run_id,
                model_name="fake_llm", success=True, content=(self.output if isinstance(self.output, str) else json.dumps(self.output)),
                finish_reason="stop", created_at=request.created_at,
            )

    gateway = Gateway()
    producer = TargetPlannerFactRequirements(workflow_runtime=workflow, model_gateway=gateway)
    return SimpleNamespace(project=project, context=context, plan=plan, workflow=workflow,
                           rows=rows, binding=binding, lineage=lineage, gateway=gateway, producer=producer, artifact=artifact)


def _enrich(env):
    return env.producer.enrich(context=env.context, project_input=env.project, document_plan=env.plan)


@pytest.mark.parametrize("defect", ["unknown-key", "unknown-section", "duplicate-section", "missing-section", "duplicate-fact", "extra-value", "invalid-importance", "bad-json", "duplicate-json-key"])
def test_planner_rejects_invalid_proposals_without_committing_plan(tmp_path, defect):
    from apps.enterprise_document.services.agent_rag_v1.planner_fact_requirements import validate_planner_fact_choices
    env = _planner_unit(tmp_path)
    value = _proposal(env.plan)
    if defect == "unknown-key":
        value["sections"][0]["fact_requirements"][0]["fact_key"] = "arbitrary.secret"
    elif defect == "unknown-section":
        value["sections"][0]["section_id"] = "other-section"
    elif defect == "duplicate-section":
        value["sections"].append(value["sections"][0])
    elif defect == "missing-section":
        value["sections"].pop()
    elif defect == "duplicate-fact":
        value["sections"][0]["fact_requirements"] *= 2
    elif defect == "extra-value":
        value["sections"][0]["fact_requirements"][0]["value"] = "invented 999 servers"
    elif defect == "invalid-importance":
        value["sections"][0]["fact_requirements"][0]["importance"] = "PASS"
    elif defect == "bad-json":
        value = "not JSON"
    else:
        value = '{"sections":[],"sections":[]}'
    with pytest.raises(ValueError):
        validate_planner_fact_choices(value, env.plan)
    assert env.gateway.calls == []


def test_planner_receipt_replay_reopen_and_exact_context(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.planner_fact_requirements import TargetPlannerFactRequirements
    env = _planner_unit(tmp_path)
    first = _enrich(env)
    env.gateway.output = _proposal(env.plan, "project_input.organization_scale")
    second = _enrich(env)
    env.producer = TargetPlannerFactRequirements(workflow_runtime=env.workflow, model_gateway=env.gateway)
    third = _enrich(env)
    assert first == second == third
    assert len(env.gateway.calls) == 1
    marker = env.producer.store.get_json(first.metadata["planner_input_ref"], task_id=env.context.task_id, run_id=env.context.run_id)
    package = ContextPackageSchema.model_validate(marker["context_package"])
    assert {item.source_binding.source_family for item in package.candidate_decisions} == {
        ContextSourceFamily.FACT_SNAPSHOT, ContextSourceFamily.WORKFLOW_EXECUTION_METADATA,
    }
    actual = {"input_kind": "MESSAGES", "messages": env.gateway.calls[0].messages}
    assert actual == package.model_input.final_input_material.inline_final_input_payload
    assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
    assert package.invocation_subject.attempt_id == env.lineage.attempt_id
    assert SENTINEL not in json.dumps(actual)


def test_planning_attempt_only_completes_after_plan_and_replay_does_not_call_model(tmp_path):
    env = _planner_unit(tmp_path)
    candidate = _enrich(env)
    before = env.workflow.lifecycle_service.resolve_active_lineage(env.context.run_id, env.lineage.attempt_scope_ref)
    assert before.attempt_status.value == "running"
    projection = env.workflow.ensure_section_plan(
        task_id=env.context.task_id, run_id=env.context.run_id, document_plan=candidate,
        structured_facts=env.rows, project_input_version=env.project.schema_version,
    )
    env.workflow.complete_initial_plan(lineage=env.lineage, projection=projection)
    after = env.workflow.initial_planning_lineage(env.context.run_id, env.plan.document_id)
    assert after.attempt_status.value == "completed"
    assert _enrich(env) == candidate
    assert len(env.gateway.calls) == 1
    env.workflow.complete_initial_plan(lineage=env.lineage, projection=projection)
    assert env.workflow.initial_planning_lineage(env.context.run_id, env.plan.document_id) == after
    binding, restored = env.workflow.ensure_initial_plan_prerequisites(
        task_id=env.project.task_id, run_id=env.context.run_id, document_id=env.plan.document_id,
        project_input=env.project, required_sections=env.project.generation_requirements.required_sections,
        structured_facts=env.rows,
    )
    assert restored.attempt_id == after.attempt_id
    assert restored.attempt_status.value == "completed"
    assert binding == env.binding


def test_planner_intent_without_result_never_redispatches(tmp_path):
    from apps.enterprise_document.agents.agent_rag_v1.base import AgentReasonerFailure
    env = _planner_unit(tmp_path)
    env.gateway.failure = SystemExit("simulated process exit after durable intent")
    with pytest.raises(SystemExit):
        _enrich(env)
    env.gateway.failure = None
    with pytest.raises(AgentReasonerFailure) as failure:
        _enrich(env)
    assert failure.value.workflow_error.error_code == "PLANNER_EXECUTION_INDETERMINATE"
    assert failure.value.workflow_error.retryability.value == "not_retryable"
    assert len(env.gateway.calls) == 1


def test_planner_budget_denial_is_not_empty_requirements(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
    from apps.enterprise_document.agents.agent_rag_v1.base import AgentReasonerFailure
    env = _planner_unit(tmp_path)
    env.gateway.failure = CanonicalBudgetCapacityExceeded("model_tokens_total", limit=0, remaining=0, required=1)
    with pytest.raises(AgentReasonerFailure) as failure:
        _enrich(env)
    assert failure.value.workflow_error.category.value == "budget_exhausted"
    assert failure.value.workflow_error.failure_origin.value == "canonical_budget"
    result_ref = next(ref.artifact_ref for ref in env.producer.store.list_references() if ref.artifact_type == "planner_candidate_result")
    receipt = env.producer.store.get_json(result_ref, task_id=env.context.task_id, run_id=env.context.run_id)
    assert receipt["outcome"] == "failed" and receipt["proposal"] is None
    env.gateway.failure = None
    with pytest.raises(AgentReasonerFailure) as repeated:
        _enrich(env)
    assert repeated.value.workflow_error == failure.value.workflow_error
    assert len(env.gateway.calls) == 1


def test_invalid_model_output_has_durable_exact_failure_not_empty_plan(tmp_path):
    from apps.enterprise_document.agents.agent_rag_v1.base import AgentReasonerFailure
    env = _planner_unit(tmp_path)
    env.gateway.output = "broken JSON"
    with pytest.raises(AgentReasonerFailure) as first:
        _enrich(env)
    assert first.value.workflow_error.error_code == "PLANNER_FACT_PROPOSAL_INVALID"
    env.gateway.output = _proposal(env.plan)
    with pytest.raises(AgentReasonerFailure) as second:
        _enrich(env)
    assert second.value.workflow_error == first.value.workflow_error
    assert len(env.gateway.calls) == 1


def test_planner_changed_input_cannot_reuse_receipt(tmp_path):
    env = _planner_unit(tmp_path)
    _enrich(env)
    env.project = env.project.model_copy(update={"business_goal": "different task"}, deep=True)
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
    with pytest.raises(WorkflowCutoverIntegrityError, match="initial snapshot"):
        _enrich(env)
    assert len(env.gateway.calls) == 1


def test_planner_corrupt_result_never_requeries_model(tmp_path):
    env = _planner_unit(tmp_path)
    candidate = _enrich(env)
    # Deliberate storage corruption confined to the temporary test root.
    env.producer.store._path_for_ref(candidate.metadata["planner_candidate_ref"]).write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        _enrich(env)
    assert len(env.gateway.calls) == 1


def test_planner_request_candidate_concurrency_is_one_model_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    env = _planner_unit(tmp_path)
    started, release = Event(), Event()
    def held_model():
        started.set()
        assert release.wait(10)
    env.gateway.hook = held_model
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_enrich, env)
        assert started.wait(10)
        from apps.enterprise_document.services.agent_rag_v1.planner_fact_requirements import TargetPlannerFactRequirements
        other = TargetPlannerFactRequirements(workflow_runtime=env.workflow, model_gateway=env.gateway)
        second = pool.submit(other.enrich, context=env.context, project_input=env.project, document_plan=env.plan)
        release.set()
        assert first.result(timeout=20) == second.result(timeout=20)
    assert len(env.gateway.calls) == 1


def test_model_return_after_attempt_supersession_does_not_commit_candidate(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
    env = _planner_unit(tmp_path)
    def supersede():
        env.workflow.lifecycle_service.open_attempt(
            env.context.run_id, env.lineage.attempt_scope_ref, "successor-snapshot",
        )
    env.gateway.hook = supersede
    with pytest.raises(WorkflowCutoverIntegrityError):
        _enrich(env)
    assert len(env.gateway.calls) == 1
    assert not [ref for ref in env.producer.store.list_references() if ref.artifact_type == "planner_candidate_result"]


def test_changed_task_cannot_open_another_initial_attempt_implicitly(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
    env = _planner_unit(tmp_path)
    changed = env.project.model_copy(update={"user_query": "different initial task"}, deep=True)
    with pytest.raises(WorkflowCutoverIntegrityError, match="input changed"):
        env.workflow.ensure_initial_plan_prerequisites(
            task_id=changed.task_id, run_id=env.context.run_id, document_id=env.plan.document_id,
            project_input=changed, required_sections=changed.generation_requirements.required_sections,
            structured_facts=env.rows,
        )
    assert env.gateway.calls == []


def test_actual_factory_budget_denial_starts_no_model_and_preserves_origin(tmp_path):
    import yaml
    project, run_id = _project("task339-denied"), "run339-denied"
    path = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy["canonical_budget"]["logical_model_calls"] = 0
    policy["canonical_budget"]["policy_ref"] = "budget-policy://test/planner-zero-calls"
    policy_file = tmp_path / "planner-test-policy.yaml"
    policy_file.write_text(yaml.safe_dump(policy), encoding="utf-8")
    calls, engines = [], []
    class Factory(SupervisorFactory):
        def build(self, **kwargs):
            result = super().build(**kwargs)
            engines.append(result.workflow_engine)
            return result
    def forbidden_provider(_client, req):
        calls.append(req)
        raise AssertionError("provider must not run without canonical budget")
    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm",
                       supervisor_model_name="fake_llm", enable_llm_routing=False, trace_enabled=True)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "GROUNDED_GENERATION_POLICY_FILE": str(policy_file), "USE_REAL_RAG_TOOL": "false",
            "FAKE_RAG_SCENARIO": "default", "FAKE_LLM_SCENARIO": "default",
            "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false",
        }))
        stack.enter_context(patch.object(FakeLLMClient, "generate", forbidden_provider))
        summary = MainlineApplicationService(supervisor_factory=Factory()).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=project.task_id, run_id=run_id, output_root=tmp_path / "run", clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"), allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
    assert summary["status"] == "failed"
    assert calls == []
    payload = workflow_tests._latest_w13_top_level_payload(engines[0])
    assert payload.terminal_error.failure_category.value == "resource_exhausted"
    detail = payload.terminal_error.details["workflow_error"]
    assert detail["failure_origin"] == "canonical_budget"
    assert detail["error_code"] == "CanonicalBudgetCapacityExceeded"
    assert payload.target_release_publication is None


def test_new_dispatch_rejects_changed_task_even_without_an_existing_receipt(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
    env = _planner_unit(tmp_path)
    env.project = env.project.model_copy(update={"business_goal": "unbound replacement task"}, deep=True)
    with pytest.raises(WorkflowCutoverIntegrityError, match="initial snapshot"):
        _enrich(env)
    assert env.gateway.calls == []
    assert not [ref for ref in env.producer.store.list_references() if ref.artifact_type == "planner_invocation_input"]


def test_planner_input_version_changes_when_bound_facts_advance(tmp_path):
    from core.runtime.side_effects import LogicalOperationRef
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
    env = _planner_unit(tmp_path)
    runtime = env.workflow.fact_snapshot_runtime
    content = runtime.read_binding(env.binding, require_current=True)
    first = content.facts[0].model_copy(update={"normalized_value": "changed canonical fact"}, deep=True)
    changed = content.model_copy(update={"facts": [first, *content.facts[1:]]}, deep=True)
    logical = env.binding.version_ref.logical_ref
    operation = LogicalOperationRef(logical_operation_id="test339-fact-change", operation_kind="ARTIFACT_VERSION_COMMIT",
                                    run_id=env.context.run_id, operation_scope_ref=logical.logical_artifact_id)
    version = runtime.commit_snapshot(logical_ref=logical, content=changed, operation_ref=operation)
    runtime.promote_snapshot(
        logical_ref=logical, target_version_ref=version,
        expected_head_revision=env.binding.resolved_head_revision,
        expected_active_version_id=env.binding.version_ref.artifact_version_id,
        operation_ref=LogicalOperationRef(logical_operation_id="test339-fact-promote", operation_kind="ARTIFACT_ACTIVE_PROMOTION",
                                         run_id=env.context.run_id, operation_scope_ref=logical.logical_artifact_id),
    )
    with pytest.raises(WorkflowCutoverIntegrityError, match="initial snapshot"):
        _enrich(env)
    assert env.gateway.calls == []


def test_planner_model_cannot_weaken_authored_requirement(tmp_path):
    from apps.enterprise_document.agents.agent_rag_v1.base import AgentReasonerFailure
    env = _planner_unit(tmp_path)
    raw = env.plan.model_dump(mode="json")
    raw["sections"][0]["fact_requirements"] = [{
        "requirement_id": "authored-hardware", "fact_key": "project_input.hardware_resources",
        "fact_type": "hardware_resource", "importance": "required", "purpose": "authored",
    }]
    from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema
    env.plan = DocumentPlanSchema.model_validate(raw)
    env.gateway.output = _proposal(env.plan)
    env.gateway.output["sections"][0]["fact_requirements"][0]["importance"] = "optional"
    with pytest.raises(AgentReasonerFailure) as failure:
        _enrich(env)
    assert failure.value.workflow_error.error_code == "PLANNER_FACT_PROPOSAL_INVALID"
