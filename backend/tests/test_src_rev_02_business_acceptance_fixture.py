"""SRCREV-02 BIZ-01..04: frozen paired inputs through real Fact ownership.

The section fact requirements below choose what the Writer may see. They do not
create facts: the only values come from strict ProjectInput ingress, the live
StructuredFactExtractor, and a committed Task-bound FactSnapshot.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.project_input_factory import ProjectInputFactory
from application.task_factory import TaskFactory
from apps.enterprise_document.schemas.scheme_writer.planning import (
    PlannedFactRequirementSchema,
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import FactSnapshotRuntime
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import SectionPlanRuntime
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    TaskRequestFactSourceResolver,
    TargetWorkflowRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer
from apps.enterprise_document.services.project_input_normalization.fact_contract import (
    project_input_fact_field,
)
from apps.enterprise_document.services.project_input_normalization.fact_extractor import StructuredFactExtractor
from apps.enterprise_document.services.project_input_normalization.input_contract import read_saved_project_input
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from task.task_manager import JsonlTaskManager


FIXTURES = Path(__file__).parent / "fixtures" / "src_rev_02_business_acceptance"
CREATED_AT = "2026-09-28T00:00:00Z"


def _json(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def _field(data: dict, path: str):
    value = data
    for component in path.lstrip("/").split("/"):
        value = value[int(component)] if isinstance(value, list) else value[component]
    return value


def _diff_paths(left, right, path="") -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        result = set()
        for key in left.keys() | right.keys():
            child = path + "/" + key
            if key not in left or key not in right:
                result.add(child)
            else:
                result.update(_diff_paths(left[key], right[key], child))
        return result
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        result = set()
        for index, (a, b) in enumerate(zip(left, right)):
            result.update(_diff_paths(a, b, path + f"/{index}"))
        return result
    return {path} if left != right else set()


def _committed_fact_snapshot(tmp_path: Path, payload: dict, project):
    """Use the production Task, source resolver, normalization, and SIF-04 owner."""
    task_id = payload["task_id"]
    run_id = "run_" + task_id
    task = TaskFactory().build(
        task_id=task_id, run_id=run_id, user_input=payload["user_query"],
        created_at=CREATED_AT, project_input=payload, allow_demo_defaults=False,
    )
    manager = JsonlTaskManager(tmp_path / "tasks")
    manager.create_task(task)
    # TaskManager.get_task returns a TaskStateRecord, not the immutable Task
    # request payload. Read the latter from the TaskFactory result.
    saved_project = read_saved_project_input(task.project_input)
    assert saved_project == project

    rows = StructuredFactExtractor().extract(
        SimpleNamespace(task_id=task_id, run_id=run_id), saved_project, CREATED_AT,
    )
    lifecycle = RunAttemptLifecycleService(tmp_path / "lifecycle.jsonl")
    artifacts = ArtifactRuntime(tmp_path / "artifacts.jsonl", lifecycle_service=lifecycle)
    facts = FactSnapshotRuntime(
        artifact_runtime=artifacts,
        source_binding_resolver=TaskRequestFactSourceResolver(manager),
    )
    requirements = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(),
        task_contract=scheme_generation_minimal_task_contract(),
    )
    plans = SectionPlanRuntime(
        artifact_runtime=artifacts, fact_snapshot_runtime=facts,
        requirements_resolver=lambda _ref: requirements,
    )
    workflow = TargetWorkflowRuntime(
        task_manager=manager, lifecycle_service=lifecycle,
        fact_snapshot_runtime=facts, section_plan_runtime=plans,
        requirements=requirements,
    )
    binding = workflow.ensure_fact_snapshot(
        task_id=task_id, run_id=run_id, structured_facts=rows,
        project_input_version=saved_project.schema_version,
    )
    snapshot = facts.read_binding(binding, require_current=True)
    return task, rows, snapshot, run_id


def _writer_projection(snapshot, run_id: str, project, expected: dict):
    keys = dict.fromkeys(
        check["expected_fact_key"]
        for check in expected["checks"] if check.get("expected_fact_key")
    )
    requirements = tuple(
        PlannedFactRequirementSchema(
            requirement_id=f"src-rev-02-{key.rsplit('.', 1)[-1]}",
            fact_key=key,
            fact_type=project_input_fact_field(key).fact_type,
            importance="required",
            purpose="Verify only the current section's authorized ProjectInput fact.",
        ) for key in keys
    )
    section = SimpleNamespace(fact_requirements=requirements)
    return TargetWriterContextComposer._relevant_fact_payload(
        snapshot, section, run_id=run_id,
        project_input_version=project.schema_version,
    )


def test_biz_01_frozen_pair_has_only_declared_differences() -> None:
    freeze = _json("FROZEN_SHA256_20260928.json")
    for relative, digest in freeze["files"].items():
        assert sha256((FIXTURES / relative).read_bytes()).hexdigest() == digest
    normal = _json("normal_project_input.json")
    missing = _json("missing_project_input.json")
    paired = _json("paired_diff.json")
    declared = {
        item["field_path"] for item in paired["business_differences"]
    } | {
        item["field_path"] for item in paired["identity_differences"]
    }
    assert _diff_paths(normal, missing) == declared
    for path in paired["invariants"]:
        assert _field(normal, path) == _field(missing, path)
    assert normal["metadata"]["fixture_class"] == missing["metadata"]["fixture_class"] == "SIMULATED_ACCEPTANCE_FIXTURE"
    assert normal["source_materials"] == missing["source_materials"] == []
    assert normal["generation_requirements"]["target_section_chars"] == missing["generation_requirements"]["target_section_chars"] == 3000
    assert normal["generation_requirements"]["max_tokens_per_section"] == missing["generation_requirements"]["max_tokens_per_section"] == 1024


@pytest.mark.parametrize("variant", ("normal", "missing"))
def test_biz_01_to_04_strict_input_fact_snapshot_and_writer_projection(
    variant: str, tmp_path: Path,
) -> None:
    raw = _json(f"{variant}_project_input.json")
    expected = _json(f"expected/{variant}_expected.json")
    assert expected["classification"] == "TEST_EXPECTATION_ONLY"
    project = ProjectInputFactory().build(
        task_id=raw["task_id"], user_input=raw["user_query"],
        raw_project_input=raw, allow_demo_defaults=False,
    )
    assert project.schema_version == expected["input_identity"]["schema_version"]
    task, rows, snapshot, run_id = _committed_fact_snapshot(tmp_path, raw, project)
    assert task.metadata["execution_profile"] == expected["input_identity"]["expected_task_execution_profile"]
    assert snapshot.normalization_contract_version == expected["input_identity"]["expected_fact_normalization_version"]
    assert all(row.source_type == "project_input" and row.source_ids == [raw["task_id"]] for row in rows)
    assert all(fact.source_bindings == snapshot.source_bindings for fact in snapshot.facts)

    writer = _writer_projection(snapshot, run_id, project, expected)
    by_id = {fact.fact_id: fact for fact in snapshot.facts}
    writer_by_id = {fact["fact_id"]: fact for fact in writer["facts"]}
    model_visible_facts = json.dumps(writer, ensure_ascii=False, sort_keys=True)
    for check in expected["checks"]:
        path = check.get("field_path")
        if check["kind"] == "INPUT_AND_NORMALIZED_FACT":
            assert _field(raw, path) == check["expected_value"]
            assert _field(project.model_dump(mode="json"), path) == check["expected_value"]
        elif check["kind"] == "MISSING_SOURCE_AND_FACT":
            with pytest.raises(KeyError):
                _field(raw, path)
            assert _field(project.model_dump(mode="json"), path) is None
        if "expected_fact_key" in check:
            fact_id = project_input_fact_field(check["expected_fact_key"]).record_id(run_id)
            if "expected_fact_content_fragment" in check:
                assert check["expected_fact_content_fragment"] in by_id[fact_id].normalized_value
                assert check["expected_writer_context_fragment"] in writer_by_id[fact_id]["normalized_value"]
            if "forbidden_fact_content_fragment" in check:
                assert check["forbidden_fact_content_fragment"] not in by_id[fact_id].normalized_value
                assert check["forbidden_writer_context_fragment"] not in model_visible_facts
        if check["kind"] == "MISSING_DECLARATION":
            assert project.missing_information == check["expected_value"]
        if check["kind"] == "SOFT_TARGET":
            assert project.generation_requirements.target_section_chars == check["expected_value"]

    section = SectionPlanEntrySchema(
        section_id="section-sr02-compute", section_title="计算资源建设方案",
        section_order=4, applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=False,
    )
    _, instructions = TargetWriterContextComposer._instructions(
        project_input=project, section=section,
        gate_binding=None, route_decision=None, decision=None,
    )
    final_material = ApplicationContextRuntime._render_final_prompt(
        instruction_material=instructions, rendered_context=model_visible_facts,
    )
    final_text = json.dumps(final_material, ensure_ascii=False, sort_keys=True)
    task_input_text = json.dumps(task.project_input, ensure_ascii=False, sort_keys=True)
    forbidden = expected["source_policy"]
    for value in (forbidden["forbidden_model_input_marker"], forbidden["forbidden_model_input_path_fragment"]):
        assert value not in task_input_text
        assert value not in model_visible_facts
        assert value not in final_text
    if variant == "missing":
        assert "双链路政务专网接入" not in task_input_text
        assert "双链路政务专网接入" not in final_text
        assert "计算资源访问方式" in final_text
    else:
        assert "双链路政务专网接入" in final_text
    assert "target_section_chars" not in model_visible_facts
    assert not any("target_section_chars" in fact.normalized_value for fact in snapshot.facts)
