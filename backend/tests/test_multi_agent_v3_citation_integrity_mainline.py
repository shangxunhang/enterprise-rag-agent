"""344: immutable citation catalogs, structural checks, no grounding shortcut."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
import test_multi_agent_v3_workflow_recovery as workflow_tests
import test_multi_agent_v3_post_write_semantic_quality as quality_tests

TEXT = "应依据已核实的项目资料制定建设方案。"


@pytest.mark.parametrize("text,expected", (
    ("甲[C1]乙[C1]", [("[C1]", "C1", True), ("[C1]", "C1", True)]),
    ("甲[C99]", [("[C99]", "C99", True)]),
    ("甲[C 1]", [("[C 1]", "C 1", False)]),
    ("甲[C1\n乙", [("[C1", "C1", False)]),
    ("甲[doc:part_1-2]", [("[doc:part_1-2]", "doc:part_1-2", True)]),
    ("普通正文，不含引用。", []),
))
def test_reserved_marker_materialization_is_lossless_and_source_independent(text, expected):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import scan_citation_markers
    rows = scan_citation_markers(text)
    assert [(row.token, row.alias, row.well_formed) for row in rows] == expected
    assert all(text[row.start:row.end] == row.token for row in rows)
    assert rows == scan_citation_markers(text)
    assert len({(row.start, row.end) for row in rows}) == len(rows)


def _catalog(section_id, stable_ref, alias="C1"):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import CitationAliasCatalog, CitationAliasEntry
    origin = {"section_id": section_id, "source": "test:immutable:" + section_id}
    return CitationAliasCatalog.build(
        scope="SECTION", source_refs=(origin,),
        entries=(CitationAliasEntry(alias=alias, citation_ref=stable_ref, exact_evidence_ref="evidence:" + stable_ref,
            source_identity_consistent=True, origin_refs=(origin,)),),
    )


def test_document_catalog_remaps_conflicting_local_aliases_and_preserves_source_origins():
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import assemble_document_citation_projection, validate_document_citation_projection
    payload = {"payload_kind": "document", "document_logical_id": "doc", "included_sections": [
        {"section_key": name, "visible_payload": {"payload_kind": "section", "section_id": name,
         "section_title": name, "content": "有依据[C1]。"}} for name in ("a", "b", "c")],
        "missing_section_keys": [], "withheld_section_keys": [], "partial": False}
    catalogs = (_catalog("a", "source-a"), _catalog("b", "source-b"), _catalog("c", "source-a"))
    before = json.loads(json.dumps(payload))
    result = assemble_document_citation_projection(payload, catalogs)
    assert payload == before, "assembling a rendering projection must not mutate source payloads"
    assert [item["visible_payload"]["content"] for item in result["included_sections"]] == ["有依据[C1]。", "有依据[C2]。", "有依据[C1]。"]
    entries = result["citation_projection"]["catalog"]["entries"]
    assert len(entries) == 2
    assert len(entries[0]["origin_refs"]) == 2
    assert validate_document_citation_projection(result, before, catalogs) is not None
    changed = json.loads(json.dumps(result))
    changed["included_sections"][1]["visible_payload"]["content"] = "有依据[C1]。"
    with pytest.raises(ValueError):
        validate_document_citation_projection(changed, before, catalogs)
    assert assemble_document_citation_projection(before, catalogs) == result


def test_remapping_never_turns_unknown_alias_into_another_sections_valid_source():
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import assemble_document_citation_projection
    payload = {"payload_kind": "document", "document_logical_id": "doc", "included_sections": [
        {"section_key": "a", "visible_payload": {"payload_kind": "section", "section_id": "a", "section_title": "A", "content": "错误来源[C2]，正确来源[C1]。"}},
        {"section_key": "b", "visible_payload": {"payload_kind": "section", "section_id": "b", "section_title": "B", "content": "另一个正确来源[C1]。"}},
    ], "missing_section_keys": [], "withheld_section_keys": [], "partial": False}
    result = assemble_document_citation_projection(payload, (_catalog("a", "source-a"), _catalog("b", "source-b")))
    assert result["included_sections"][0]["visible_payload"]["content"] == "错误来源[C2]，正确来源[C1]。"
    assert result["included_sections"][1]["visible_payload"]["content"] == "另一个正确来源[C3]。"
    assert "C2" not in {entry["alias"] for entry in result["citation_projection"]["catalog"]["entries"]}


@pytest.fixture(scope="module", params=("normal", "fallback", "unknown", "malformed", "unterminated", "empty", "required-empty"))
def citation_mainline(tmp_path_factory, request):
    return _citation_run(tmp_path_factory, request.param)


def _citation_run(tmp_path_factory, scenario):
    root = tmp_path_factory.mktemp("citation344-" + scenario)
    task_id, run_id = "task-citation344-" + scenario, "run-citation344-" + scenario
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    if scenario == "required-empty":
        raw = project.model_dump(mode="json")
        raw["generation_requirements"]["need_citation"] = True
        raw["generation_requirements"]["citation_required_sections"] = raw["generation_requirements"]["required_sections"]
        project = type(project).model_validate(raw)
    token = "[C99]" if scenario == "unknown" else "[C 1]" if scenario == "malformed" else "[C1\n" if scenario == "unterminated" else "" if scenario in {"empty", "required-empty"} else "[C1]"
    body = (TEXT + token) * 5
    calls, checks = [], []
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": body}, deep=True)
        if scenario == "fallback" and req.extra.get("response_contract") == "criterion_support_evaluator_v1":
            value = json.loads(response.content)
            value.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(value)}, deep=True)
        return response

    def complete(composition, **kwargs):
        before = len(calls)
        if scenario in {"commit-window", "intermediate-window"} and kwargs["scope"].value == "SECTION":
            import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citation
            original_finalize = composition.validation_store.finalize_validator_execution
            def interrupted(event, record, **options):
                if record.validator_id != citation.CitationIntegrityValidator.VALIDATOR_ID:
                    return original_finalize(event, record, **options)
                if scenario == "commit-window":
                    original_finalize(event, record, **options)
                raise RuntimeError("injected Citation commit-window interruption")
            with patch.object(composition.validation_store, "finalize_validator_execution", interrupted):
                with pytest.raises(RuntimeError, match="commit-window"):
                    original_complete(composition, **kwargs)
            before_recovery = _durable_state_from_composition(composition)
            with patch.object(citation, "scan_citation_markers", _forbidden), patch.object(citation.TargetPostWriteCitationIntegrity, "facts", _forbidden):
                if scenario == "intermediate-window":
                    with pytest.raises(citation.CitationProjectionIntegrityError, match="incomplete committed Citation"):
                        original_complete(composition, **kwargs)
                    assert before_recovery == _durable_state_from_composition(composition)
                    raise RuntimeError("incomplete Citation safely refused")
                outcome = original_complete(composition, **kwargs)
        else:
            outcome = original_complete(composition, **kwargs)
        # 345/346 add independent semantic consumers; 344's scanner/Facts remain deterministic.
        # Only these new semantic contracts may add calls around this composed boundary.
        added=calls[before:]
        contracts=[req.extra.get("response_contract") for req in added]
        assert all(value in {"citation_claim_association_v1", "requirement_adherence_batch_v1"} for value in contracts), contracts
        assert contracts.count("requirement_adherence_batch_v1")==1
        assert contracts.count("citation_claim_association_v1")==(
            1 if scenario in {"normal","fallback","commit-window"} else 0
        )
        before=len(calls)
        completion = composition.validation_store.resolve_completion(outcome[0])
        items = [item for item in completion.items if item.expected_validation_item_ref.startswith("citation:integrity:")]
        assert len(items) == 1, "real citation Integrity input/execution is not composed"
        facts = tuple(composition.validation_store.resolve_fact(ref) for ref in items[0].fact_refs)
        checks.append((kwargs["scope"].value, kwargs["artifact_ref"], completion, items[0], facts))
        composition._citation_test_arguments = getattr(composition, "_citation_test_arguments", []) + [dict(kwargs)]
        import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citation
        with patch.object(citation, "scan_citation_markers", _forbidden), patch.object(citation.TargetPostWriteCitationIntegrity, "facts", _forbidden):
            assert original_complete(composition, **kwargs) == outcome
        assert len(calls) == before
        return outcome

    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True, gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    factory = workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,{"GROUNDED_GENERATION_POLICY_FILE":str(workflow_tests._w15_parallel_budget_policy_file(root))}))
        if scenario == "required-empty":
            stack.enter_context(patch.object(workflow_tests, "_w13_single_section_project_input", return_value=project))
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default", "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", complete))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], task_id=task_id, run_id=run_id, user_input=project.user_query,
            output_root=root, clean_existing=True, settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False)
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    if scenario == "intermediate-window":
        assert not checks and top.terminal_error is not None
        return scenario, body, calls, checks, summary, engine, top
    if len(checks) != 2:
        raise AssertionError(top.terminal_error.model_dump(mode="json") if top.terminal_error else summary)
    return scenario, body, calls, checks, summary, engine, top


def test_real_section_and_document_integrity_are_independent_of_grounding(citation_mainline):
    scenario, body, calls, checks, summary, engine, top = citation_mainline
    expected = "PASS" if scenario in {"normal", "fallback"} else "NOT_APPLICABLE" if scenario in {"empty", "required-empty"} else "FAIL"
    assert {scope for scope, *_ in checks} == {"SECTION", "DOCUMENT"}
    for scope, artifact, completion, item, facts in checks:
        assert item.state.value == expected
        assert all(fact.family.value == "CITATION_INTEGRITY" for fact in facts)
        assert all(fact.subject_hash == completion.subject_hash for fact in facts)
        original_item = [row for row in completion.items if row.expected_validation_item_ref.startswith("citation:item:")]
        assert len(original_item) == 1 and original_item[0].state.value == ("INDETERMINATE" if scenario in {"normal","fallback"} else "NOT_APPLICABLE")
        if scenario in {"normal", "fallback"}:
            assert {fact.payload["rule_id"] for fact in facts} == {"PW-R-CIT-I-01", "PW-R-CIT-I-02", "PW-R-CIT-I-03"}
            assert len(facts) == 15
        if scenario == "unknown":
            assert {fact.semantic_state.value for fact in facts if fact.payload["rule_id"] == "PW-R-CIT-I-01"} == {"PASS"}
            assert {fact.semantic_state.value for fact in facts if fact.payload["rule_id"] == "PW-R-CIT-I-02"} == {"FAIL"}
    assert len({completion.subject_hash for _, _, completion, *_ in checks}) == 2
    # Existing canonical Writer boundary strips outer whitespace before commit;
    # Citation validation must preserve that committed body, including bad markers.
    assert top.section_execution_results[0].section.content == body.strip()
    services = engine.stage_service.section_execution_coordinator.require_pre_write_services()
    writer = services.writer_invocation.execution_store.resolve_writer_execution(
        top.section_execution_results[0].writer_execution_ref
    )
    assert writer.artifact.content == top.section_execution_results[0].section.content
    if scenario == "required-empty":
        assert top.project_input.generation_requirements.need_citation
        assert all(item.state.value == "NOT_APPLICABLE" and not facts for _, _, _, item, facts in checks)
    assert summary["status"] == "partial_success"
    assert summary["target_release_publication"]["manifest"]["release_mode"] == "BEST_EFFORT"
    final = engine.stage_service.target_post_write_composition.artifact_store.resolve_exact(top.target_document_artifact_ref)
    assert "citation_projection" in final.canonical_payload_ref
    assert not any(req.extra.get("call_purpose") in {"citation_integrity", "citation_grounding"} for req in calls)


def _rehash_projection(payload):
    projection = payload["citation_projection"]
    catalog = projection["catalog"]
    catalog["catalog_hash"] = canonical_sha256({key: value for key, value in catalog.items() if key != "catalog_hash"})
    projection["projection_hash"] = canonical_sha256({key: value for key, value in projection.items() if key != "projection_hash"})


@pytest.mark.parametrize("damage", ("foreign_evidence", "wrong_scope", "foreign_stable_source", "lost_origin", "extra_invalid", "missing_invalid", "duplicate_source", "wrong_mapping"))
def test_self_consistent_hash_does_not_authorize_foreign_document_catalog(damage):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
        assemble_document_citation_projection, validate_document_citation_projection,
        CitationAliasCatalog, CitationAliasEntry,
    )
    payload = {"included_sections": [
        {"section_key": name, "visible_payload": {"section_title": name, "content": "source[C1]."}}
        for name in ("a", "b", "c")
    ]}
    catalogs = [_catalog("a", "source-a"), _catalog("b", "source-b"), _catalog("c", "source-a")]
    invalid = CitationAliasEntry(alias="BAD", citation_ref="bad-source", exact_evidence_ref="bad-evidence",
        source_identity_consistent=False, origin_refs=({"section_id": "a"},))
    catalogs[0] = CitationAliasCatalog.build(scope="SECTION", source_refs=catalogs[0].source_refs,
        entries=(*catalogs[0].entries, invalid))
    result = assemble_document_citation_projection(payload, catalogs)
    projection = result["citation_projection"]
    entries = projection["catalog"]["entries"]
    entry = next(row for row in entries if row["alias"] == "C1")
    if damage == "foreign_evidence":
        entry["exact_evidence_ref"] = "evidence:outside-the-frozen-sources"
    elif damage == "wrong_scope":
        projection["catalog"]["scope"] = "SECTION"
    elif damage == "foreign_stable_source":
        entry["citation_ref"] = "foreign-source"
    elif damage == "lost_origin":
        entry["origin_refs"] = entry["origin_refs"][:1]
    elif damage == "extra_invalid":
        entries.append(invalid.model_copy(update={"alias": "EXTRA"}).model_dump(mode="json"))
    elif damage == "missing_invalid":
        entries.remove(next(row for row in entries if row["alias"] == "BAD"))
    elif damage == "duplicate_source":
        entries.append({**entry, "alias": "C9"})
    else:
        projection["section_alias_maps"][1]["mapping"]["C1"] = "C1"
    _rehash_projection(result)
    with pytest.raises(ValueError):
        validate_document_citation_projection(result, payload, catalogs)


def _forbidden(*args, **kwargs):
    raise AssertionError("committed Citation replay must not scan, allocate, project rules or call a model")


def _reopen(engine):
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import TargetPostWriteClaimProjection
    coordinator = engine.stage_service.section_execution_coordinator
    services = coordinator.require_pre_write_services()
    reopened = TargetPostWriteComposition.create(
        runs_dir=Path(engine.checkpointer.path).parent, research_runtime=engine.research_runtime,
        writer_execution_store=services.writer_invocation.execution_store,
        budget_manager_provider=engine.budget_runtime,
        lifecycle_service=RunAttemptLifecycleService(engine.lifecycle_service.path))
    original = engine.stage_service.target_post_write_composition
    if original.requirement_adherence is not None:
        reopened.attach_requirement_adherence(
            model_gateway=original.requirement_adherence.gateway
        )
    if original.coverage_composition is not None:
        reopened.attach_coverage(
            model_gateway=original.coverage_composition.gateway
        )
    if original.consistency_composition is not None:
        reopened.attach_consistency(
            model_gateway=original.consistency_composition.gateway
        )
    reopened.attach_claim_quality_projection(TargetPostWriteClaimProjection(
        claim_runtime=coordinator.claim_verification_runtime,
        verifier_composition=coordinator.claim_verifier_composition))
    return reopened


def _durable_state(engine):
    composition = engine.stage_service.target_post_write_composition
    with composition.database.read_transaction() as connection:
        database = tuple(connection.iterdump())
    root = Path(engine.checkpointer.path).parent
    files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
             if p.is_file() and p.suffix not in {".db", ".sqlite", ".sqlite3"}
             and not str(p).endswith(("-wal", "-shm"))}
    return database, files


def test_completed_and_terminal_reopen_restore_committed_citation_without_semantics(citation_mainline):
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citation
    scenario, body, calls, checks, summary, engine, top = citation_mainline
    original = engine.stage_service.target_post_write_composition
    reopened = _reopen(engine)
    before = _durable_state(engine)
    budget = engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    source_bytes = tuple(row.model_dump_json() for row in top.section_execution_results)
    with ExitStack() as stack:
        for name in ("scan_citation_markers", "assemble_document_citation_projection"):
            stack.enter_context(patch.object(citation, name, _forbidden))
        stack.enter_context(patch.object(citation.TargetPostWriteCitationIntegrity, "facts", _forbidden))
        stack.enter_context(patch.object(citation.CitationIntegrityValidator, "project_rule", _forbidden))
        stack.enter_context(patch.object(FakeLLMClient, "generate", _forbidden))
        for arguments, check in zip(original._citation_test_arguments, checks):
            scope, artifact, completion, expected, _ = check
            actual = reopened.citation_integrity.complete_item(
                validation_run_id=completion.validation_run_id, subject_hash=completion.subject_hash,
                **{key: arguments[key] for key in ("artifact_ref", "scope", "source_sections", "evidence_snapshot_ref", "assembly_input_manifest_ref") if key in arguments})
            assert actual == expected
            assert reopened.validation_store.resolve_completion(reopened._complete_validation(**arguments)[0]) == completion
        replay = reopened.complete_target_post_write(task_id=top.task_id, run_id=top.run_id,
            document_id=top.document_id, project_input=top.project_input,
            section_execution_results=tuple(top.section_execution_results))
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert replay.published_release.manifest == top.target_release_publication.manifest
    assert replay.run_completion_assessment_ref == top.target_run_completion_assessment_ref
    assert before == _durable_state(engine)
    assert budget == engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    assert source_bytes == tuple(row.model_dump_json() for row in top.section_execution_results)


@pytest.fixture(scope="module")
def normal_citation_run(tmp_path_factory):
    return _citation_run(tmp_path_factory, "normal")


@pytest.mark.parametrize("scope", ("SECTION", "DOCUMENT"))
@pytest.mark.parametrize("damage", ("missing_execution", "corrupt_execution", "missing_intermediate",
    "corrupt_intermediate", "missing_fact", "corrupt_fact", "validator_version", "parser_version", "stale_source", "changed_text", "duplicate_section"))
def test_committed_citation_damage_is_rejected_without_reexecution(normal_citation_run, scope, damage):
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citation
    _, _, _, checks, _, engine, top = normal_citation_run
    composition = engine.stage_service.target_post_write_composition
    index = 0 if scope == "SECTION" else 1
    arguments = composition._citation_test_arguments[index]
    _, artifact, completion, item, _ = checks[index]
    kwargs = {key: arguments[key] for key in ("artifact_ref", "scope", "source_sections", "evidence_snapshot_ref", "assembly_input_manifest_ref") if key in arguments}
    kwargs.update(validation_run_id=completion.validation_run_id, subject_hash=completion.subject_hash)
    store = composition.validation_store
    execution = store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
        validator_id=citation.CitationIntegrityValidator.VALIDATOR_ID, expected_item_ref=item.expected_validation_item_ref)
    before = _durable_state(engine)
    original_execution = store.resolve_validator_execution_record
    original_intermediate = store.resolve_intermediate
    with ExitStack() as stack:
        stack.enter_context(patch.object(citation, "scan_citation_markers", _forbidden))
        stack.enter_context(patch.object(citation, "assemble_document_citation_projection", _forbidden))
        stack.enter_context(patch.object(citation.CitationIntegrityValidator, "project_rule", _forbidden))
        stack.enter_context(patch.object(FakeLLMClient, "generate", _forbidden))
        stack.enter_context(patch.object(store, "put_fact_if_absent_or_same", _forbidden))
        stack.enter_context(patch.object(store, "finalize_validator_execution", _forbidden))
        if damage == "missing_execution":
            stack.enter_context(patch.object(store, "find_validator_execution_for_item", return_value=None))
        elif damage == "corrupt_execution":
            stack.enter_context(patch.object(store, "resolve_validator_execution_record",
                side_effect=lambda ref, *, connection=None: (_ for _ in ()).throw(PostWritePersistenceIntegrityError("ValidatorExecutionRecord row/hash mismatch"))
                if ref == execution.validator_execution_ref else original_execution(ref, connection=connection)))
        elif damage in {"missing_intermediate", "corrupt_intermediate"}:
            stack.enter_context(patch.object(store, "resolve_intermediate",
                side_effect=lambda ref, *, connection=None: (_ for _ in ()).throw(PostWritePersistenceIntegrityError(
                    "required ValidationIntermediate event is missing" if damage.startswith("missing") else "ValidationIntermediate canonical hash mismatch"))
                if ref in execution.intermediate_refs else original_intermediate(ref, connection=connection)))
        elif damage in {"missing_fact", "corrupt_fact"}:
            stack.enter_context(patch.object(store, "resolve_fact",
                side_effect=PostWritePersistenceIntegrityError("required ValidationFact is missing" if damage.startswith("missing") else "ValidationFact hash mismatch")))
        elif damage == "validator_version":
            stack.enter_context(patch.object(store, "find_validator_execution_for_item",
                return_value=execution.model_copy(update={"validator_version": "foreign-version"})))
        elif damage == "parser_version":
            snapshot = store.resolve_intermediate(execution.intermediate_refs[0])
            snapshot["parser_version"] = "future-parser"
            stack.enter_context(patch.object(store, "resolve_intermediate", return_value=snapshot))
        elif damage == "stale_source":
            stack.enter_context(patch.object(composition.research_runtime, "validate_evidence_package_binding_current",
                side_effect=ValueError("stale Evidence source")))
        else:
            source = kwargs["source_sections"][0]
            if damage == "changed_text":
                sources = (source.model_copy(update={"section": source.section.model_copy(update={"content": "tampered body"})}),)
            else:
                sources = (source, source)
            kwargs["source_sections"] = sources
            arguments = {**arguments, "source_sections": sources}
        with pytest.raises((ValueError, PostWritePersistenceIntegrityError)):
            composition.citation_integrity.complete_item(**kwargs)
        with pytest.raises((ValueError, PostWritePersistenceIntegrityError)):
            composition._complete_validation(**arguments)
    assert before == _durable_state(engine)


def test_actual_store_rejects_corrupt_execution_and_intermediate_rows(normal_citation_run):
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citation
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    _, _, _, checks, _, engine, _ = normal_citation_run
    composition = engine.stage_service.target_post_write_composition
    store = composition.validation_store
    for _, _, completion, item, _ in checks:
        record = store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
            validator_id=citation.CitationIntegrityValidator.VALIDATOR_ID, expected_item_ref=item.expected_validation_item_ref)
        for table, key, value, column in (
            ("validator_execution_records", "validator_execution_ref", record.validator_execution_ref, "record_json"),
            ("validation_execution_events", "event_id", record.intermediate_refs[0].event_id, "event_json"),
        ):
            with composition.database.read_transaction() as connection:
                raw = connection.execute(f"SELECT {column} FROM {table} WHERE {key}=?", (value,)).fetchone()[0]
            try:
                with composition.database.write_transaction(immediate=True) as connection:
                    connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", ("{}", value))
                with pytest.raises(PostWritePersistenceIntegrityError):
                    store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
                        validator_id=citation.CitationIntegrityValidator.VALIDATOR_ID, expected_item_ref=item.expected_validation_item_ref)
            finally:
                with composition.database.write_transaction(immediate=True) as connection:
                    connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (raw, value))


def test_real_factory_multisection_sources_origins_reservation_and_output(tmp_path, monkeypatch):
    from test_phase7_document_closure_mainline import _InspectingFactory
    from tools.fake_rag_tool import FakeRAGTool
    from test_multi_agent_v3_workflow_recovery import ProjectInputSchema
    names = ["安全设计", "技术方案", "建设内容"]
    project = ProjectInputSchema(schema_version="project_input_v3", task_id="task-cit344-multi", task_type="scheme_generation",
        user_query="生成安全设计、技术方案及建设内容", generation_requirements={
            "required_sections": names, "need_citation": True, "citation_required_sections": names,
            "extra": {"enable_section_aware_retrieval": True, "enable_corrective_section_retrieval": False},
            "need_human_review": False, "min_section_chars": 80},
        output_schema={"document_title": "多章节引用", "required_sections": names})
    calls, captures, sources = [], [], []
    original_generate, original_rag = FakeLLMClient.generate, FakeRAGTool.run
    original_complete = TargetPostWriteComposition._complete_validation
    def rag_provider(tool, call):
        # Controlled provider content: chapters 1/3 cite the same stable source;
        # chapter 2 cites another source, all with provider-local alias C1.
        title = (call.tool_input.get("evidence_requirements") or {}).get("section_title", "")
        source_title = "安全设计" if title == "建设内容" else title
        tool_input = {**call.tool_input, "evidence_requirements": {
            **(call.tool_input.get("evidence_requirements") or {}), "section_title": source_title}}
        response = original_rag(tool, call.model_copy(update={"tool_call_id": "fixture-source-" + source_title,
                                                             "tool_input": tool_input}))
        sources.append((title, response.model_dump(mode="json")))
        return response.model_copy(update={"tool_call_id": call.tool_call_id})
    def provider(client, request):
        calls.append(request)
        response = original_generate(client, request)
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 5 + "依据[C1]，待核对[C2]。"})
        return response
    def complete(composition, **arguments):
        before = len(calls)
        source_bytes = tuple(row.model_dump_json() for row in arguments["source_sections"])
        result = original_complete(composition, **arguments)
        added = calls[before:]
        assert [request.extra.get("response_contract") for request in added] == [
            "requirement_adherence_batch_v1",
            "citation_claim_association_v1",
        ]
        assert source_bytes == tuple(row.model_dump_json() for row in arguments["source_sections"])
        captures.append((arguments, composition.validation_store.resolve_completion(result[0])))
        return result
    for name, value in {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "citation_collision",
        "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(FakeRAGTool, "run", rag_provider)
    monkeypatch.setattr(FakeLLMClient, "generate", provider)
    monkeypatch.setattr(TargetPostWriteComposition, "_complete_validation", complete)
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE",str(workflow_tests._w15_parallel_budget_policy_file(tmp_path)))
    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True)
    factory = _InspectingFactory()
    summary = MainlineApplicationService(supervisor_factory=factory).run(project_root=Path(__file__).resolve().parents[2],
        task_id=project.task_id, run_id="run-cit344-multi", user_input=project.user_query, output_root=tmp_path,
        clean_existing=True, settings=settings, project_input=project.model_dump(mode="json"),
        allow_demo_defaults=False, enable_agent_self_rag=False)
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    assert top.terminal_error is None, top.terminal_error or summary
    assert len(top.section_execution_results) == 3
    assert {title for title, _ in sources} >= set(names)
    composition = engine.stage_service.target_post_write_composition
    final = composition.artifact_store.resolve_exact(top.target_document_artifact_ref)
    projection = final.canonical_payload_ref["citation_projection"]
    catalogs = projection["source_catalogs"]
    assert [catalog["entries"][0]["alias"] for catalog in catalogs] == ["C1"] * 3
    stable = [catalog["entries"][0]["citation_ref"] for catalog in catalogs]
    assert stable[0] == stable[2] and stable[0] != stable[1]
    entries = projection["catalog"]["entries"]
    assert len(entries) == 2
    assert {entry["alias"] for entry in entries} == {"C1", "C3"}
    assert sorted(len(entry["origin_refs"]) for entry in entries) == [1, 2]
    assert [item["mapping"] for item in projection["section_alias_maps"]] == [{"C1": "C1"}, {"C1": "C3"}, {"C1": "C1"}]
    visible = final.canonical_payload_ref["included_sections"]
    assert [item["visible_payload"]["content"] for item in visible] == [
        TEXT * 5 + "依据[C1]，待核对[C2]。", TEXT * 5 + "依据[C3]，待核对[C2]。", TEXT * 5 + "依据[C1]，待核对[C2]。"]
    assert len({completion.subject_hash for _, completion in captures}) == 4
    for arguments, completion in captures:
        item = next(row for row in completion.items if row.expected_validation_item_ref.startswith("citation:integrity:"))
        facts = [composition.validation_store.resolve_fact(ref) for ref in item.fact_refs]
        assert item.state.value == "FAIL"  # unknown C2 is preserved, never rescued by remapping
        assert len(facts) == (15 if arguments["scope"].value == "DOCUMENT" else 5)
        assert all(fact.subject_hash == completion.subject_hash for fact in facts)
    for section in top.section_execution_results:
        writer = composition.writer_execution_store.resolve_writer_execution(section.writer_execution_ref)
        assert writer.artifact.content == section.section.content == TEXT * 5 + "依据[C1]，待核对[C2]。"
    assert summary["target_release_publication"]["manifest"]["release_mode"] == "BEST_EFFORT"
    # Verify output through the real rendering/extraction contract as well.
    binding = final.validated_output_bindings[0]
    stored = composition.output_store.get_bytes(binding.output_artifact_ref.artifact_ref, task_id=top.task_id, run_id=top.run_id)
    actual_output = json.loads(stored) if binding.output_format == "json" else composition._extract_markdown_visible_payload(stored)
    assert actual_output == final.visible_projection.visible_payload
    assert actual_output["included_sections"] == visible
    rendered = composition._render_markdown_visible_payload(final.visible_projection.visible_payload)
    assert composition._extract_markdown_visible_payload(rendered) == final.visible_projection.visible_payload


def _durable_state_from_composition(composition):
    with composition.database.read_transaction() as connection:
        return tuple(connection.iterdump())


@pytest.mark.parametrize("scenario", ("commit-window", "intermediate-window"))
def test_citation_commit_windows_restore_committed_or_refuse_incomplete_without_reparse(tmp_path_factory, scenario):
    _, _, calls, checks, summary, engine, top = _citation_run(tmp_path_factory, scenario)
    if scenario == "commit-window":
        assert len(checks) == 2 and top.terminal_error is None
        assert summary["target_release_publication"]["manifest"]["release_mode"] == "BEST_EFFORT"
    else:
        assert "incomplete Citation safely refused" in top.terminal_error.message
        assert not top.target_release_publication
        composition = engine.stage_service.target_post_write_composition
        with composition.database.read_transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM validation_completions").fetchone()[0] == 0
    assert not any(request.extra.get("call_purpose") in {"citation_integrity", "citation_grounding"} for request in calls)


def test_ambiguous_alias_and_source_mismatch_remain_business_facts(normal_citation_run):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
        CitationAliasCatalog, CitationIntegritySnapshot, TargetPostWriteCitationIntegrity)
    _, _, _, checks, _, engine, _ = normal_citation_run
    composition = engine.stage_service.target_post_write_composition
    _, artifact, completion, item, _ = checks[0]
    execution = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=completion.validation_run_id, validator_id="post_write_citation_integrity",
        expected_item_ref=item.expected_validation_item_ref)
    snapshot = CitationIntegritySnapshot.model_validate(composition.validation_store.resolve_intermediate(execution.intermediate_refs[0]))
    entry = snapshot.catalog.entries[0]
    for entries, expected in (
        ((entry, entry.model_copy(update={"citation_ref": "another-stable-source"})),
         {"PW-R-CIT-I-01": "PASS", "PW-R-CIT-I-02": "FAIL"}),
        ((entry.model_copy(update={"source_identity_consistent": False}),),
         {"PW-R-CIT-I-01": "PASS", "PW-R-CIT-I-02": "PASS", "PW-R-CIT-I-03": "FAIL"}),
    ):
        catalog = CitationAliasCatalog.build(scope="SECTION", source_refs=snapshot.catalog.source_refs, entries=entries)
        facts = TargetPostWriteCitationIntegrity.facts(snapshot.model_copy(update={"catalog": catalog}),
            validation_run_id=completion.validation_run_id, subject_hash=completion.subject_hash)
        assert len(facts) == len(snapshot.occurrences) * len(expected)
        assert {fact.payload["rule_id"]: fact.semantic_state.value for fact in facts} == expected
