from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.project_input_factory import ProjectInputFactory
from application.task_factory import TaskFactory
from application.existing_run_authorization import (
    ExistingRunAuthorizationError,
    ExistingRunAuthorizer,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.fact_extractor import (
    StructuredFactExtractor,
)
from apps.enterprise_document.services.project_input_normalization.input_reader import (
    ProjectInputReader,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    read_saved_project_input,
)
from schemas.status import ExecutionStatus
from schemas.task_state import TaskAuthorizationIdentitySchema, TaskStateRecordSchema


def _payload(*, business_goal_marker: object = ...) -> dict:
    value = {
        "task_id": "goal-version-task",
        "task_type": "scheme_generation",
        "user_query": "请根据现有资料编写政务云建设方案",
        "generation_requirements": {"required_sections": ["建设目标"]},
        "output_schema": {"required_sections": ["建设目标"]},
    }
    if business_goal_marker is not ...:
        value["business_goal"] = business_goal_marker
    return value


def test_new_factory_input_uses_v3_and_does_not_copy_user_query_to_goal() -> None:
    project = ProjectInputFactory().build(
        task_id="goal-version-task",
        user_input="请根据现有资料编写政务云建设方案",
        raw_project_input=_payload(),
    )

    assert project.schema_version == "project_input_v3"
    assert project.user_query == "请根据现有资料编写政务云建设方案"
    assert project.business_goal == ""

    facts = StructuredFactExtractor().extract(
        SimpleNamespace(task_id=project.task_id, run_id="goal-version-run"),
        project,
        "2026-09-26T00:00:00Z",
    )
    assert all(fact.fact_type != "business_goal" for fact in facts)


def test_explicit_v2_goal_is_preserved_even_when_equal_to_user_query() -> None:
    query = "请根据现有资料编写政务云建设方案"
    project = read_saved_project_input(
        {**_payload(), "schema_version": "project_input_v2", "business_goal": query}
    )

    assert project.business_goal == query

    facts = StructuredFactExtractor().extract(
        SimpleNamespace(task_id=project.task_id, run_id="goal-version-run"),
        project,
        "2026-09-26T00:00:00Z",
    )
    goal_fact = next(fact for fact in facts if fact.fact_type == "business_goal")
    assert goal_fact.content == query
    assert goal_fact.fact_id == "fact_goal-version-run_business_goal"
    assert goal_fact.source_type == "project_input"
    assert goal_fact.source_ids == [project.task_id]


@pytest.mark.parametrize("goal", ("", "   ", "\t\n"))
def test_v3_empty_and_whitespace_goal_remain_missing(goal: str) -> None:
    project = ProjectInputFactory().build(
        task_id="goal-version-task",
        user_input="请根据现有资料编写政务云建设方案",
        raw_project_input={**_payload(), "business_goal": goal},
    )

    assert project.business_goal == ""


@pytest.mark.parametrize("goal", (None, 17, False, ["目标"]))
def test_v3_rejects_non_string_goal_types(goal: object) -> None:
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            task_id="goal-version-task",
            user_input="请根据现有资料编写政务云建设方案",
            raw_project_input={**_payload(), "business_goal": goal},
        )


def test_saved_v1_input_keeps_original_goal_semantics() -> None:
    saved = ProjectInputSchema.model_validate(
        {**_payload(), "schema_version": "project_input_v1", "business_goal": ""}
    )
    restored = ProjectInputSchema.model_validate(saved.model_dump(mode="json"))

    assert saved.business_goal == "请根据现有资料编写政务云建设方案"
    assert restored.business_goal == saved.business_goal
    assert restored.schema_version == "project_input_v1"


def test_explicit_normalized_v1_payload_is_not_silently_upgraded_for_new_task() -> None:
    legacy = ProjectInputSchema.model_validate(
        {**_payload(), "schema_version": "project_input_v1"}
    )
    assert legacy.business_goal == legacy.user_query

    with pytest.raises(ValueError, match="explicit project_input_v1 requires caller-approved conversion"):
        ProjectInputFactory().build(
            task_id=legacy.task_id,
            user_input=legacy.user_query,
            raw_project_input=legacy.model_dump(mode="json"),
        )
    with pytest.raises(ValueError, match="explicit project_input_v1 requires caller-approved conversion"):
        ProjectInputFactory().build(
            task_id=legacy.task_id,
            user_input=legacy.user_query,
            raw_project_input=legacy.model_dump(mode="json"),
            allow_demo_defaults=True,
        )


def test_historical_v1_read_preserves_task_request_hash() -> None:
    current = TaskFactory().build(
        task_id="goal-version-task",
        run_id="goal-version-run",
        user_input="请根据现有资料编写政务云建设方案",
        created_at="2026-09-26T00:00:00Z",
        project_input=_payload(),
    )
    saved_v1 = current.model_copy(update={
        "project_input": {
            **current.project_input,
            "schema_version": "project_input_v1",
            "business_goal": current.user_input,
        },
        "metadata": {
            **current.metadata,
            "execution_profile": "project-input-v1-execution-v1",
        },
    })
    request_hash = saved_v1.canonical_request_sha256()

    historical = read_saved_project_input(saved_v1.project_input)

    assert historical.business_goal == saved_v1.user_input
    assert historical.schema_version == "project_input_v1"
    assert saved_v1.canonical_request_sha256() == request_hash


def test_v2_saved_input_repeated_reads_are_idempotent() -> None:
    saved = read_saved_project_input(
        {**_payload(), "schema_version": "project_input_v2"}
    ).model_dump(mode="json")
    state = SimpleNamespace(
        context_bundle=SimpleNamespace(
            business=SimpleNamespace(project_input=saved),
        ),
        requirements={},
        task={},
        contexts={},
    )
    reader = ProjectInputReader()

    first = reader.read(state)
    second = reader.read(state)
    assert first.schema_version == second.schema_version == "project_input_v2"
    assert first.model_dump(mode="json") == second.model_dump(mode="json") == saved


def test_task_factory_persists_v3_input_contract() -> None:
    task = TaskFactory().build(
        task_id="goal-version-task",
        run_id="goal-version-run",
        user_input="请根据现有资料编写政务云建设方案",
        created_at="2026-09-26T00:00:00Z",
        project_input=_payload(),
    )

    assert task.project_input["schema_version"] == "project_input_v3"
    assert task.project_input["business_goal"] == ""
    assert task.metadata["execution_profile"] == "project-input-v3-execution-v1"
    ExistingRunAuthorizer.authorize_execution_compatibility(task)


def test_v1_task_can_be_read_but_cannot_continue_under_v2_execution_profile() -> None:
    task = TaskFactory().build(
        task_id="goal-version-task",
        run_id="goal-version-run",
        user_input="请根据现有资料编写政务云建设方案",
        created_at="2026-09-26T00:00:00Z",
        project_input=_payload(),
    )
    legacy = task.model_copy(update={
        "project_input": {
            **task.project_input,
            "schema_version": "project_input_v1",
            "business_goal": "请根据现有资料编写政务云建设方案",
        },
        "metadata": {
            **task.metadata,
            "execution_profile": "project-input-v1-execution-v1",
        },
    })

    assert ProjectInputSchema.model_validate(legacy.project_input).business_goal == legacy.user_input
    with pytest.raises(ExistingRunAuthorizationError, match="execution contract is incompatible"):
        ExistingRunAuthorizer.authorize_execution_compatibility(legacy)


def test_item1_v2_task_can_be_read_but_cannot_continue_under_item2_profile() -> None:
    task = TaskFactory().build(
        task_id="goal-version-task",
        run_id="goal-version-run",
        user_input="请根据现有资料编写政务云建设方案",
        created_at="2026-09-26T00:00:00Z",
        project_input=_payload(),
    )
    item1_v2 = task.model_copy(update={
        "project_input": {**task.project_input, "schema_version": "project_input_v2"},
        "metadata": {
            **task.metadata,
            "execution_profile": "project-input-v2-execution-v1",
        },
    })

    assert read_saved_project_input(item1_v2.project_input).schema_version == "project_input_v2"
    with pytest.raises(ExistingRunAuthorizationError, match="execution contract is incompatible"):
        ExistingRunAuthorizer.authorize_execution_compatibility(item1_v2)


def test_previous_v2_task_is_readable_but_cannot_continue_under_consistency_profile() -> None:
    current = TaskFactory().build(
        task_id="goal-version-task",
        run_id="goal-version-run",
        user_input="请根据现有资料编写政务云建设方案",
        created_at="2026-09-26T00:00:00Z",
        project_input=_payload(),
    )
    previous = current.model_copy(update={
        "project_input": {**current.project_input, "schema_version": "project_input_v2"},
        "metadata": {
            **current.metadata,
            "execution_profile": "project-input-v2-execution-v2",
        },
    })
    saved_hash = previous.canonical_request_sha256()
    identity = TaskAuthorizationIdentitySchema(
        task_id=previous.task_id,
        run_id=previous.run_id,
        task_type=previous.task_type,
        tenant_id=previous.tenant_id,
        canonical_task_request_sha256=saved_hash,
    )
    inspection = {"graph_state": {
        "task_id": previous.task_id,
        "run_id": previous.run_id,
        "task_type": previous.task_type,
        "tenant_id": previous.tenant_id,
        "task": previous.model_dump(mode="json"),
    }}

    assert read_saved_project_input(previous.project_input).schema_version == "project_input_v2"
    assert ExistingRunAuthorizer.authorize_checkpoint(
        inspection=inspection,
        metadata=identity,
        authenticated_tenant=previous.tenant_id,
    ).canonical_request_sha256() == saved_hash
    assert previous.canonical_request_sha256() == saved_hash

    task_state = TaskStateRecordSchema(
        task_id=previous.task_id,
        run_id=previous.run_id,
        task_type=previous.task_type,
        tenant_id=previous.tenant_id,
        canonical_task_request_sha256=saved_hash,
        status=ExecutionStatus.PENDING,
        created_at=previous.created_at,
        updated_at=previous.created_at,
        metadata=previous.metadata,
    )
    with pytest.raises(ExistingRunAuthorizationError, match="execution contract is incompatible"):
        ExistingRunAuthorizer.authorize_task_state_execution_compatibility(task_state)
    with pytest.raises(ExistingRunAuthorizationError, match="execution contract is incompatible"):
        ExistingRunAuthorizer.authorize_execution_compatibility(previous)
