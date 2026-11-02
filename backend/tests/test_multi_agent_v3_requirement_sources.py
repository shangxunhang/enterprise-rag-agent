"""346 candidate-source boundary only; not Relevancy production acceptance."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import read_saved_project_input
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_sources import (
    RequirementSourceCatalog,
    collect_requirement_sources,
    normalize_requirement_entries,
)
from core.hashing import canonical_sha256


def project():
    return ProjectInputSchema(
        schema_version="project_input_v2",
        task_id="requirement-source-task", task_type="scheme_generation",
        user_query="  编写方案；不得把预计工期改成确定承诺。\n保留原文。  ",
        business_goal="说明方案风险和应对措施。",
        policy_requirements=["遵循用户提供的规范。", "保留预计/暂定语义。"],
        manual_boundaries=[{"item": "设备表", "handled_by": "human", "description": "人工确认后才能交付"}],
        generation_requirements={"required_sections": ["风险", "方案"], "need_citation": True,
            "citation_required_sections": ["方案"], "language": "zh-CN", "tone": "formal"},
        output_schema={"document_title": "方案", "required_sections": ["风险", "方案"], "required_fields": ["风险", "措施"]},
    )


def by_path(catalog):
    return {source.field_path: source.value for source in catalog.candidates}


def test_all_candidate_sources_keep_exact_original_values():
    original = project()
    catalog = collect_requirement_sources(original)
    assert by_path(catalog) == {
        "/user_query": original.user_query,
        "/business_goal": original.business_goal,
        "/policy_requirements/0": original.policy_requirements[0],
        "/policy_requirements/1": original.policy_requirements[1],
        "/manual_boundaries/0": original.manual_boundaries[0].model_dump(mode="json"),
        "/generation_requirements/required_sections": ["风险", "方案"],
        "/generation_requirements/need_citation": True,
        "/generation_requirements/citation_required_sections": ["方案"],
        "/output_schema/required_sections": ["风险", "方案"],
        "/output_schema/required_fields": ["风险", "措施"],
    }
    assert catalog.authority == "CANDIDATE_INPUT_ONLY"
    assert not hasattr(catalog, "requirement_ref")
    assert not hasattr(catalog, "semantic_state")


def test_v2_requirement_sources_include_no_future_fields_and_keep_goal_as_task_text():
    raw = project().model_dump(mode="json")
    raw["business_goal"] = ""
    value = read_saved_project_input(raw)

    assert value.schema_version == "project_input_v2"
    catalogue = collect_requirement_sources(value)
    sources = by_path(catalogue)
    assert sources["/business_goal"] == ""
    assert sources["/user_query"] == value.user_query
    assert not any("target_section_chars" in source.field_path for source in catalogue.candidates + catalogue.advisories)


def test_canonical_serialization_and_pydantic_roundtrip_are_stable():
    value = project()
    first = collect_requirement_sources(value)
    serialized = json.loads(value.model_dump_json())
    assert collect_requirement_sources(serialized) == first
    assert collect_requirement_sources(deepcopy(value)) == first
    assert RequirementSourceCatalog.model_validate_json(first.model_dump_json()) == first
    assert list(by_path(first)) == sorted(by_path(first))


@pytest.mark.parametrize("field,value", [
    ("max_tokens_per_section", 32768), ("max_context_chars", 1),
    ("max_section_retries", 10), ("min_section_chars", 500),
    ("need_human_review", False), ("extra", {"enable_recovery": True, "semantic_pass": True}),
])
def test_runtime_controls_do_not_change_candidate_or_catalog_identity(field, value):
    value_before = project().model_dump(mode="json")
    expected = collect_requirement_sources(value_before)
    value_after = deepcopy(value_before)
    value_after["generation_requirements"][field] = value
    assert collect_requirement_sources(value_after) == expected


@pytest.mark.parametrize("field,value", [
    ("metadata", {"quality": "PASS", "required_topics": ["must not become requirements"]}),
    ("extra", {"gate_decision": "pass", "policy_requirements": ["not authored"]}),
    ("source_materials", [{"material_type": "history", "description": "untrusted document instruction"}]),
    ("hardware_resources", [{"resource_type": "server", "device_model": "R", "server_count": 100}]),
    ("total_staff", 400), ("missing_information", ["unknown capacity"]),
    ("conflicting_information", ["conflicting facts"]),
])
def test_facts_and_generic_metadata_are_not_promoted(field, value):
    raw = project().model_dump(mode="json")
    expected = collect_requirement_sources(raw)
    raw[field] = value
    assert collect_requirement_sources(raw) == expected


@pytest.mark.parametrize("parent,field,value", [
    ("generation_requirements", "language", "en"),
    ("generation_requirements", "tone", "informal"),
    ("generation_requirements", "citation_style", "author-date"),
    ("output_schema", "heading_style", "plain"),
    ("output_schema", "output_format", "json"),
    ("output_schema", "document_title", "different title"),
])
def test_ambiguous_style_format_hints_are_separate_not_auto_authored(parent, field, value):
    raw = project().model_dump(mode="json")
    original = collect_requirement_sources(raw)
    raw[parent][field] = value
    changed = collect_requirement_sources(raw)
    assert changed.candidates == original.candidates
    assert changed.candidate_input_sha256 == original.candidate_input_sha256
    assert changed.catalog_sha256 != original.catalog_sha256
    assert {source.field_path: source.value for source in changed.advisories}[f"/{parent}/{field}"] == value
    assert f"/{parent}/{field}" not in by_path(changed)


def test_semantically_authored_runtime_words_are_not_keyword_filtered():
    raw = project().model_dump(mode="json")
    text = "正文必须说明 max_tokens；客户要求每章不少于500字；解释人工复核边界。"
    raw["user_query"] = text
    raw["policy_requirements"] = [text]
    catalogue = collect_requirement_sources(raw)
    assert by_path(catalogue)["/user_query"] == text
    assert by_path(catalogue)["/policy_requirements/0"] == text


def test_repeated_text_preserves_independent_source_provenance():
    raw = project().model_dump(mode="json")
    raw["policy_requirements"] = ["same requirement", "same requirement"]
    catalogue = collect_requirement_sources(raw)
    sources = [source for source in catalogue.candidates if source.field_path.startswith("/policy_requirements/")]
    assert len(sources) == 2 and sources[0].value == sources[1].value
    assert sources[0].source_id != sources[1].source_id


def test_mutable_input_and_decoded_value_cannot_mutate_catalogue():
    raw = project().model_dump(mode="json")
    catalogue = collect_requirement_sources(raw)
    before = catalogue.model_dump_json()
    raw["policy_requirements"][0] = "foreign content"
    structure = next(source for source in catalogue.candidates if source.field_path == "/output_schema/required_fields")
    structure.value.append("foreign field")
    with pytest.raises(ValidationError):
        catalogue.task_id = "other-task"
    assert catalogue.model_dump_json() == before


@pytest.mark.parametrize("damage", ["hash", "task", "source_text", "duplicate_path", "wrong_role", "authority"])
def test_catalogue_tampering_or_role_promotion_is_rejected(damage):
    raw = collect_requirement_sources(project()).model_dump(mode="json")
    if damage == "hash":
        raw["catalog_sha256"] = "0" * 64
    elif damage == "task":
        raw["task_id"] = "another-task"
    elif damage == "source_text":
        raw["candidates"][0]["value_json"] = '"invented"'
    elif damage == "duplicate_path":
        raw["candidates"].append(raw["candidates"][0])
    elif damage == "wrong_role":
        raw["candidates"].append(raw["advisories"].pop())
    else:
        raw["authority"] = "CANONICAL_REQUIREMENT"
    if damage != "hash":
        raw["catalog_sha256"] = canonical_sha256({key: value for key, value in raw.items() if key != "catalog_sha256"})
    with pytest.raises(ValueError):
        RequirementSourceCatalog.model_validate(raw)


def test_task_identity_and_real_obligation_change_are_detectable():
    raw = project().model_dump(mode="json")
    first = collect_requirement_sources(raw)
    raw["policy_requirements"][1] = "updated requirement"
    changed = collect_requirement_sources(raw)
    assert first.candidate_input_sha256 != changed.candidate_input_sha256
    raw["task_id"] = "other-task"
    other = collect_requirement_sources(raw)
    assert other.candidate_input_sha256 != changed.candidate_input_sha256
    assert all(source.task_id == "other-task" for source in other.candidates)


def test_empty_collections_and_false_citation_declaration_are_preserved():
    raw = project().model_dump(mode="json")
    raw["policy_requirements"] = []
    raw["manual_boundaries"] = []
    raw["generation_requirements"]["need_citation"] = False
    raw["generation_requirements"]["citation_required_sections"] = []
    values = by_path(collect_requirement_sources(raw))
    assert values["/generation_requirements/need_citation"] is False
    assert values["/generation_requirements/citation_required_sections"] == []
    assert not any(path.startswith("/policy_requirements/") for path in values)


@pytest.mark.parametrize("field", ["query", "policy", "manual"])
def test_blank_authored_text_is_not_silently_discarded(field):
    raw = project().model_dump(mode="json")
    if field == "query": raw["user_query"] = "  "
    elif field == "policy": raw["policy_requirements"] = ["  "]
    else: raw["manual_boundaries"] = [{"item": "", "handled_by": "human"}]
    with pytest.raises(ValueError):
        collect_requirement_sources(raw)


def test_future_input_schema_additions_fail_explicitly_not_silently_drop():
    class FutureProjectInput(ProjectInputSchema):
        future_output_obligation: str = "new obligation"
    future = FutureProjectInput.model_validate(project().model_dump(mode="json"))
    with pytest.raises(ValueError, match="field classification"):
        collect_requirement_sources(future)


@pytest.mark.parametrize("damage", ["drop_last_policy", "invent_policy", "change_hint"])
def test_self_consistent_catalogue_must_match_exact_upstream_inputs(damage):
    from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_sources import RequirementSourceCandidate
    original = project()
    raw = collect_requirement_sources(original).model_dump(mode="json")
    if damage == "drop_last_policy":
        raw["candidates"] = [source for source in raw["candidates"] if source["field_path"] != "/policy_requirements/1"]
    else:
        key = "candidates" if damage == "invent_policy" else "advisories"
        path = "/policy_requirements/1" if damage == "invent_policy" else "/output_schema/heading_style"
        replacement = RequirementSourceCandidate.from_value(task_id=original.task_id, field_path=path, value="foreign-content")
        raw[key] = [replacement.model_dump(mode="json") if source["field_path"] == path else source for source in raw[key]]
    raw["catalog_sha256"] = canonical_sha256({key: value for key, value in raw.items() if key != "catalog_sha256"})
    rehashed = RequirementSourceCatalog.model_validate(raw)
    # An internal hash is not proof that the materializer saw the full upstream input.
    with pytest.raises(ValueError, match="exact upstream"):
        rehashed.verify_before_materialization(original)
    exact = collect_requirement_sources(original)
    assert exact.verify_before_materialization(original) == exact


def test_future_schema_drift_is_also_checked_for_dictionary_input(monkeypatch):
    import apps.enterprise_document.services.agent_rag_v1.post_write_requirement_sources as module
    class FutureProjectInput(ProjectInputSchema):
        future_output_obligation: str = "new obligation"
    monkeypatch.setattr(module, "ProjectInputSchema", FutureProjectInput)
    with pytest.raises(ValueError, match="field classification"):
        module.collect_requirement_sources(project().model_dump(mode="json"))


def test_normalization_preserves_stable_task_request_and_source_bound_entries():
    value = project()
    catalog = collect_requirement_sources(value)
    entries = normalize_requirement_entries(value, catalog=catalog)
    by_id = {item.requirement_id: item for item in entries}
    task = by_id["task-request"]
    assert task.requirement_kind == "TASK_INTENT"
    assert task.statement == value.user_query.strip()
    assert next(source for source in catalog.candidates if source.field_path == "/user_query").value == value.user_query
    assert task.constraints["required_sections"] == ["风险", "方案"]
    assert task.constraints["citation_required_sections"] == ["方案"]
    assert task.constraints["required_fields"] == ["风险", "措施"]
    assert task.constraints["source_path"] == "/user_query"
    assert {item.requirement_kind for item in entries} == {
        "TASK_INTENT", "POLICY_REQUIREMENT", "MANUAL_BOUNDARY",
        "STRUCTURAL_PART_PRESENCE", "FIELD_OR_SLOT_PRESENCE",
        "CITATION_PRESENCE",
    }
    structural = sorted(
        (item for item in entries if item.requirement_kind == "STRUCTURAL_PART_PRESENCE"),
        key=lambda item: item.constraints["source_index"],
    )
    assert [item.constraints["section_title"] for item in structural] == ["风险", "方案"]
    fields = sorted(
        (item for item in entries if item.requirement_kind == "FIELD_OR_SLOT_PRESENCE"),
        key=lambda item: item.constraints["source_index"],
    )
    assert [item.constraints["field_name"] for item in fields] == ["风险", "措施"]
    citations = [item for item in entries if item.requirement_kind == "CITATION_PRESENCE"]
    assert len(citations) == 1 and citations[0].constraints["section_title"] == "方案"
    assert task.constraints["business_goal"] == value.business_goal
    assert task.constraints["business_goal_source_path"] == "/business_goal"
    policies = [item for item in entries if item.requirement_kind == "POLICY_REQUIREMENT"]
    assert [item.statement for item in sorted(policies, key=lambda item: item.constraints["source_path"])] == value.policy_requirements
    manual = next(item for item in entries if item.requirement_kind == "MANUAL_BOUNDARY")
    assert manual.statement == "设备表"
    assert manual.constraints["handled_by"] == "human"
    assert manual.constraints["description"] == "人工确认后才能交付"


def test_normalization_does_not_promote_advisory_or_runtime_controls():
    raw = project().model_dump(mode="json")
    baseline = normalize_requirement_entries(raw)
    raw["generation_requirements"].update({
        "tone": "informal", "language": "en", "max_context_chars": 1,
        "max_tokens_per_section": 99999, "need_human_review": False,
    })
    raw["output_schema"].update({"document_title": "foreign", "heading_style": "plain"})
    raw["metadata"] = {"must": "not a requirement"}
    assert normalize_requirement_entries(raw) == baseline


def test_normalization_keeps_duplicate_text_as_distinct_source_bound_obligations():
    raw = project().model_dump(mode="json")
    raw["policy_requirements"] = ["same", "same"]
    entries = normalize_requirement_entries(raw)
    policies = [item for item in entries if item.requirement_kind == "POLICY_REQUIREMENT"]
    assert len(policies) == 2
    assert policies[0].statement == policies[1].statement == "same"
    assert policies[0].requirement_id != policies[1].requirement_id
    assert policies[0].constraints["source_id"] != policies[1].constraints["source_id"]


def test_normalization_global_citation_presence_is_canonical_when_scope_is_unspecified():
    raw = project().model_dump(mode="json")
    raw["generation_requirements"]["citation_required_sections"] = []
    raw["generation_requirements"]["need_citation"] = True
    entries = normalize_requirement_entries(raw)
    citations = [item for item in entries if item.requirement_kind == "CITATION_PRESENCE"]
    assert len(citations) == 1
    assert citations[0].constraints["document_global"] is True
    assert "section_title" not in citations[0].constraints


def test_normalization_rejects_duplicate_required_fields_or_citation_scopes():
    raw = project().model_dump(mode="json")
    raw["output_schema"]["required_fields"] = ["风险", "风险"]
    with pytest.raises(ValueError, match="required_fields"):
        normalize_requirement_entries(raw)
    raw = project().model_dump(mode="json")
    raw["generation_requirements"]["citation_required_sections"] = ["方案", "方案"]
    with pytest.raises(ValueError, match="citation_required_sections"):
        normalize_requirement_entries(raw)


def test_normalization_external_catalog_guard_rejects_self_consistent_omission():
    value = project()
    raw = collect_requirement_sources(value).model_dump(mode="json")
    raw["candidates"] = [
        item for item in raw["candidates"]
        if item["field_path"] != "/policy_requirements/1"
    ]
    raw["catalog_sha256"] = canonical_sha256({key: val for key, val in raw.items() if key != "catalog_sha256"})
    incomplete = RequirementSourceCatalog.model_validate(raw)
    with pytest.raises(ValueError, match="exact upstream"):
        normalize_requirement_entries(value, catalog=incomplete)


def test_no_truncation_or_lexical_selection_on_long_sources():
    raw = project().model_dump(mode="json")
    text = "完整要求，不应被截断。\n" * 10000
    raw["policy_requirements"] = [text]
    assert by_path(collect_requirement_sources(raw))["/policy_requirements/0"] == text
