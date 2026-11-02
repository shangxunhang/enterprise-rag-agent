"""SRCREV-02 version identity, Fact projection, and advisory source boundaries."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.existing_run_authorization import (
    ExistingRunAuthorizationError,
    ExistingRunAuthorizer,
)
from application.project_input_factory import ProjectInputFactory
from application.task_factory import TaskFactory
from apps.enterprise_document.schemas.project_input_schema import (
    GenerationRequirementsSchema,
    GenerationRequirementsV3Schema,
    ProjectInputSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import FactSnapshotRuntime
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import SectionPlanRuntime
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_sources import (
    RequirementSourceCatalog,
    collect_requirement_sources,
    normalize_requirement_entries,
)
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    TaskRequestFactSourceResolver,
    TargetWorkflowRuntime,
)
from apps.enterprise_document.services.project_input_normalization.fact_contract import (
    PROJECT_INPUT_FACT_FIELDS,
)
from apps.enterprise_document.services.project_input_normalization.fact_extractor import (
    StructuredFactExtractor,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    CURRENT_PROJECT_INPUT_VERSION,
    CURRENT_TASK_EXECUTION_PROFILE,
    EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION,
    PROJECT_INPUT_V1,
    PROJECT_INPUT_V2,
    PROJECT_INPUT_V3,
    fact_normalization_version,
    project_input_version_for_fact_normalization,
    read_saved_project_input,
    requested_target_section_chars,
)
from apps.enterprise_document.services.scheme_writer.section_technical_length_policy import (
    build_section_technical_length_policy,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from core.hashing import canonical_sha256
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


def _raw(*, version: str | None = None, target: object = ...) -> dict:
    value = {
        "task_id": "srcrev02-history",
        "task_type": "scheme_generation",
        "user_query": "请编写方案",
        "generation_requirements": {"required_sections": ["项目概述"]},
        "output_schema": {"required_sections": ["项目概述"]},
    }
    if version is not None:
        value["schema_version"] = version
    if target is not ...:
        value["generation_requirements"]["target_section_chars"] = target
    return value


def _historical_task(version: str) -> TaskSchema:
    raw = _raw(version=version)
    if version == PROJECT_INPUT_V1:
        raw["business_goal"] = raw["user_query"]
    project = read_saved_project_input(raw)
    return TaskSchema(
        task_id=project.task_id,
        run_id="srcrev02-run",
        tenant_id=project.tenant_id,
        task_type=project.task_type,
        task_name=f"{project.task_type}:{project.project_name or project.task_id}",
        project_name=project.project_name,
        user_input=project.user_query,
        project_input=project.model_dump(),
        source_materials=[],
        generation_requirements=project.generation_requirements.model_dump(),
        output_schema=project.output_schema.model_dump(),
        metadata={"execution_profile": EXECUTION_PROFILE_BY_PROJECT_INPUT_VERSION[version]},
        created_at="2026-09-28T00:00:00Z",
    )


@pytest.mark.parametrize(
    "version,project_sha,task_sha",
    [
        (
            PROJECT_INPUT_V1,
            "2cb867724fa7e8042d5d4d1c7f79eaaf2b02f4d9099f5513dedc271960d0780c",
            "d01e536aef783ec2438a5cde9c3d106db4af805e154bbf55344815e1cd362ac2",
        ),
        (
            PROJECT_INPUT_V2,
            "06b7d5ca5d7c41a14aa9486ae473a3ece72dd538d5ee5948a6d1671bb3019d51",
            "a2fdd538884f6931a1cd85c42e3be6c37cc827dd461e4f809d1c5ef91c813d9d",
        ),
    ],
)
def test_saved_legacy_roundtrip_preserves_start_head_project_and_task_identity(
    version: str, project_sha: str, task_sha: str,
) -> None:
    task = _historical_task(version)
    saved = deepcopy(task.project_input)
    restored = read_saved_project_input(saved)

    assert isinstance(restored.generation_requirements, GenerationRequirementsSchema)
    assert not isinstance(restored.generation_requirements, GenerationRequirementsV3Schema)
    assert restored.schema_version == version
    assert restored.model_dump(mode="json") == saved
    assert "target_section_chars" not in saved["generation_requirements"]
    assert canonical_sha256(saved) == project_sha
    assert task.canonical_request_sha256() == task_sha
    assert requested_target_section_chars(restored) is None


@pytest.mark.parametrize("version", [PROJECT_INPUT_V1, PROJECT_INPUT_V2])
@pytest.mark.parametrize("target", [None, 3000])
def test_legacy_target_field_is_rejected_and_old_input_needs_explicit_new_task_conversion(
    version: str, target: int | None,
) -> None:
    with pytest.raises(ValidationError):
        read_saved_project_input(_raw(version=version, target=target))
    with pytest.raises(ValueError, match="caller-approved conversion"):
        ProjectInputFactory().build(
            task_id="srcrev02-history", user_input="请编写方案",
            raw_project_input=_raw(version=version),
        )
    with pytest.raises(ValueError, match="caller-approved conversion"):
        ProjectInputFactory().build(
            task_id="srcrev02-history", user_input="请编写方案",
            raw_project_input=_raw(version=version), allow_demo_defaults=True,
        )


@pytest.mark.parametrize("target", [0, -1, True, 1.5, "1000", "", [], {}])
def test_v3_rejects_invalid_soft_target_without_coercion(target: object) -> None:
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            task_id="srcrev02-history", user_input="请编写方案",
            raw_project_input=_raw(version=PROJECT_INPUT_V3, target=target),
        )


@pytest.mark.parametrize("target", [None, 1000, 3000])
def test_v3_new_task_saved_read_and_request_roundtrip(target: int | None) -> None:
    raw = _raw(target=target) if target is not None else _raw()
    task = TaskFactory().build(
        task_id="srcrev02-history", run_id="srcrev02-run",
        user_input="请编写方案", created_at="2026-09-28T00:00:00Z",
        project_input=raw,
    )
    saved_hash = task.canonical_request_sha256()
    restored = read_saved_project_input(task.project_input)

    assert CURRENT_PROJECT_INPUT_VERSION == task.project_input["schema_version"] == PROJECT_INPUT_V3
    assert CURRENT_TASK_EXECUTION_PROFILE == task.metadata["execution_profile"] == "project-input-v3-execution-v1"
    assert isinstance(restored.generation_requirements, GenerationRequirementsV3Schema)
    assert restored.business_goal == ""  # V2 goal/task separation continues in V3.
    assert restored.generation_requirements.target_section_chars == target
    assert requested_target_section_chars(restored) == target
    assert restored.model_dump(mode="json") == task.project_input
    assert TaskSchema.model_validate_json(task.model_dump_json()).canonical_request_sha256() == saved_hash
    assert ("target_section_chars" in task.generation_requirements) is (target is not None)
    ExistingRunAuthorizer.authorize_execution_compatibility(task)


def test_v3_explicit_null_target_roundtrips_as_unspecified() -> None:
    project = read_saved_project_input(_raw(version=PROJECT_INPUT_V3, target=None))
    saved = project.model_dump(mode="json")
    assert project.generation_requirements.target_section_chars is None
    assert "target_section_chars" not in saved["generation_requirements"]
    assert read_saved_project_input(saved).model_dump(mode="json") == saved


def test_saved_v2_is_readable_but_cannot_hot_continue_under_v3() -> None:
    task = _historical_task(PROJECT_INPUT_V2)
    assert read_saved_project_input(task.project_input).schema_version == PROJECT_INPUT_V2
    with pytest.raises(ExistingRunAuthorizationError, match="execution contract is incompatible"):
        ExistingRunAuthorizer.authorize_execution_compatibility(task)


def _task_bound_fact_snapshot(tmp_path: Path, task: TaskSchema, rows: list):
    """Commit the extracted rows through the real Task-bound Fact owner."""
    manager = JsonlTaskManager(tmp_path / "tasks")
    manager.create_task(task)
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
        task_id=task.task_id, run_id=task.run_id, structured_facts=rows,
        project_input_version=PROJECT_INPUT_V3,
    )
    return binding, facts.read_binding(binding, require_current=True)


def test_v3_soft_target_does_not_change_fact_projection_or_normalization_version(
    tmp_path: Path,
) -> None:
    first_raw = _raw(version=PROJECT_INPUT_V3, target=1000)
    second_raw = _raw(version=PROJECT_INPUT_V3, target=3000)
    first_raw["business_goal"] = second_raw["business_goal"] = "建设可信证据方案"
    first_task = TaskFactory().build(
        task_id="srcrev02-history", run_id="srcrev02-run", user_input="请编写方案",
        created_at="2026-09-28T00:00:00Z", project_input=first_raw,
    )
    second_task = TaskFactory().build(
        task_id="srcrev02-history", run_id="srcrev02-run", user_input="请编写方案",
        created_at="2026-09-28T00:00:00Z", project_input=second_raw,
    )
    before = read_saved_project_input(first_task.project_input)
    after = read_saved_project_input(second_task.project_input)
    state = SimpleNamespace(task_id=before.task_id, run_id="srcrev02-run")
    extractor = StructuredFactExtractor()
    first = extractor.extract(state, before, "2026-09-28T00:00:00Z")
    second = extractor.extract(state, after, "2026-09-28T00:00:00Z")

    first_records = [item.model_dump(mode="json") for item in first]
    second_records = [item.model_dump(mode="json") for item in second]
    assert first_records
    assert first_records == second_records
    assert canonical_sha256(first_records) == canonical_sha256(second_records)
    assert all("target_section_chars" not in item.content for item in first)
    first_task_sha = first_task.canonical_request_sha256()
    second_task_sha = second_task.canonical_request_sha256()
    assert first_task_sha != second_task_sha
    first_request_without_target = deepcopy(first_task.canonical_request_projection())
    second_request_without_target = deepcopy(second_task.canonical_request_projection())
    for request in (first_request_without_target, second_request_without_target):
        request["project_input"]["generation_requirements"].pop("target_section_chars")
        request["generation_requirements"].pop("target_section_chars")
    assert first_request_without_target == second_request_without_target

    first_binding, first_snapshot = _task_bound_fact_snapshot(
        tmp_path / "target_1000", first_task, first,
    )
    second_binding, second_snapshot = _task_bound_fact_snapshot(
        tmp_path / "target_3000", second_task, second,
    )
    assert first_snapshot.normalization_contract_version == second_snapshot.normalization_contract_version
    assert first_snapshot.normalization_contract_version == fact_normalization_version(PROJECT_INPUT_V3)
    assert first_snapshot.normalization_contract_version == "project-input-normalization-v2"

    # FACT_SEMANTIC_PROJECTION_TEST_V1 is an audit observation only. It does not
    # replace the canonical, provenance-bearing FactSnapshot identity.
    def semantic_projection(snapshot) -> list[dict]:
        return [
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "normalized_value": fact.normalized_value,
                "unit": fact.unit,
                "confidence": fact.confidence,
            }
            for fact in sorted(snapshot.facts, key=lambda item: item.fact_id)
        ]

    first_semantics = semantic_projection(first_snapshot)
    second_semantics = semantic_projection(second_snapshot)
    assert first_semantics
    assert first_semantics == second_semantics
    assert canonical_sha256(first_semantics) == canonical_sha256(second_semantics)
    assert {item["fact_id"] for item in first_semantics} == {
        item["fact_id"] for item in second_semantics
    }
    assert all("target_section_chars" not in item["fact_id"] for item in first_semantics)
    assert all(field.fact_key != "project_input.target_section_chars" for field in PROJECT_INPUT_FACT_FIELDS)

    first_source, = first_snapshot.source_bindings
    second_source, = second_snapshot.source_bindings
    assert first_source.source_type == second_source.source_type == "project_input"
    assert first_source.source_ref == second_source.source_ref == "project-input://task/srcrev02-history"
    assert first_source.source_version_ref == f"task-request://sha256/{first_task_sha}"
    assert second_source.source_version_ref == f"task-request://sha256/{second_task_sha}"
    assert first_source.source_version_ref != second_source.source_version_ref
    assert all(fact.source_bindings == [first_source] for fact in first_snapshot.facts)
    assert all(fact.source_bindings == [second_source] for fact in second_snapshot.facts)

    # Once the exact Task source version is omitted for comparison, the full
    # canonical content is otherwise identical. Full record hashes may differ.
    first_content = first_snapshot.model_dump(mode="json")
    second_content = second_snapshot.model_dump(mode="json")
    for content in (first_content, second_content):
        for source in content["source_bindings"]:
            source.pop("source_version_ref")
        for fact in content["facts"]:
            for source in fact["source_bindings"]:
                source.pop("source_version_ref")
    assert first_content == second_content
    assert canonical_sha256([
        fact.model_dump(mode="json") for fact in first_snapshot.facts
    ]) != canonical_sha256([
        fact.model_dump(mode="json") for fact in second_snapshot.facts
    ])

    logical_ref = first_binding.version_ref.logical_ref
    assert logical_ref == second_binding.version_ref.logical_ref
    assert FactSnapshotRuntime.semantic_fingerprint(logical_ref, first_snapshot) != (
        FactSnapshotRuntime.semantic_fingerprint(logical_ref, second_snapshot)
    )
    assert build_section_technical_length_policy(
        before.generation_requirements.max_tokens_per_section
    ) == build_section_technical_length_policy(
        after.generation_requirements.max_tokens_per_section
    )
    # A Fact normalization version alone cannot distinguish V2 from V3 Tasks.
    assert project_input_version_for_fact_normalization("project-input-normalization-v2") == PROJECT_INPUT_V2


def test_v3_target_is_advisory_only_and_unspecified_target_has_no_source() -> None:
    unspecified = ProjectInputSchema.model_validate(_raw(version=PROJECT_INPUT_V3))
    specified = ProjectInputSchema.model_validate(_raw(version=PROJECT_INPUT_V3, target=3000))
    none_catalog = collect_requirement_sources(unspecified)
    target_catalog = collect_requirement_sources(specified)
    target_path = "/generation_requirements/target_section_chars"

    assert none_catalog.schema_version == target_catalog.schema_version == "requirement_source_catalog_v2"
    assert all(source.field_path != target_path for source in none_catalog.candidates + none_catalog.advisories)
    target_sources = [source for source in target_catalog.advisories if source.field_path == target_path]
    assert len(target_sources) == 1
    assert target_sources[0].role == "ADVISORY"
    assert target_sources[0].source_kind == "WRITING_SOFT_TARGET"
    assert target_sources[0].value == 3000
    assert target_catalog.candidates == none_catalog.candidates
    assert target_catalog.candidate_input_sha256 == none_catalog.candidate_input_sha256
    assert normalize_requirement_entries(specified) == normalize_requirement_entries(unspecified)
    assert all(target_path not in item.model_dump_json() for item in normalize_requirement_entries(specified))
    assert RequirementSourceCatalog.model_validate_json(target_catalog.model_dump_json()) == target_catalog


@pytest.mark.parametrize(
    "version,catalog_sha,candidate_sha",
    [
        (
            PROJECT_INPUT_V1,
            "a2f2ea590410317912f0e61d16875c4bbf105c77efec5c6f2f9fa8f3533177f0",
            "c8e79285a5e0497ba483f0b6d7166e88505484fe3649c7606479db9a65441368",
        ),
        (
            PROJECT_INPUT_V2,
            "ded748cf3715c1b1b73d58b34ccd388edeff0a716eeb34d773f28a32e30e51d1",
            "fdf68df0df5488397e284326a66dc896657f230d48e25167953a838aeb092dce",
        ),
    ],
)
def test_legacy_requirement_inventory_remains_v1_without_target(
    version: str, catalog_sha: str, candidate_sha: str,
) -> None:
    project = read_saved_project_input(_raw(version=version))
    catalog = collect_requirement_sources(project)
    assert catalog.schema_version == "requirement_source_catalog_v1"
    assert catalog.catalog_sha256 == catalog_sha
    assert catalog.candidate_input_sha256 == candidate_sha
    assert all("target_section_chars" not in source.field_path for source in catalog.candidates + catalog.advisories)


def test_explicit_hard_length_in_user_query_and_policy_remains_candidate() -> None:
    raw = _raw(version=PROJECT_INPUT_V3, target=3000)
    raw["user_query"] = "请编写方案；每章至少500字符"
    raw["policy_requirements"] = ["全文不得少于4000字符"]
    project = ProjectInputSchema.model_validate(raw)
    entries = normalize_requirement_entries(project)
    assert any(item.statement == raw["user_query"] for item in entries)
    assert any(item.statement == raw["policy_requirements"][0] for item in entries)
