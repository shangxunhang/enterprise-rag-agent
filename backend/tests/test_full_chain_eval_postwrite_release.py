"""S01-S16 fixed local PostWrite diagnostics and safe owner-snapshot fixtures."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import sqlite3

import pytest

from eval.full_chain.snapshot import SnapshotView, load_source_snapshot
from eval.full_chain.domains.postwrite_release import (CURRENT_POSTWRITE_SCHEMA, classify_publication,
    enumerate_required_items, evaluate_postwrite, evaluate_quality_dimension, read_postwrite_records,
    check_control_and_assembly, check_postwrite_edge)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition, declare_required_validation_items)


def _evaluate(records, references=None):
    return evaluate_postwrite(SnapshotView.from_records(records), references=references)


def _codes(batch):
    return {f.code for f in batch.findings}


def _declaration(shared=True, dimensions=None):
    declarations = declare_required_validation_items(required_dimensions=dimensions or TargetPostWriteComposition.REQUIRED_DIMENSIONS,
        artifact_hash="a" * 64, has_claim_projection=shared)
    return {"items": [{"expected_validation_item_ref": d.expected_validation_item_ref, "required": d.required,
        "dimension": d.dimension, "producer": d.producer} for d in declarations]}


def _gate_fixture(tmp_path):
    import test_post_write_delivery_gate as fixtures
    from apps.enterprise_document.schemas.post_write import ValidationFactFamily, ValidationSemanticState, ValidationCompletionItemState
    return fixtures._gate_fixture(tmp_path,
        fact_states=(("fact-pass", ValidationFactFamily.FAITHFULNESS, ValidationSemanticState.PASS, "claim-1"),),
        completion_states=(("faithfulness:item-1", True, ValidationCompletionItemState.PASS, "fact-pass"),))


def _snapshot_database(path, tmp_path, *, frozen=True, tables=None):
    tables = tables or ["post_write_metadata", "artifact_states", "post_write_entries", "validation_completions",
        "validation_facts", "requirement_snapshots", "quality_delivery_contracts", "delivery_gate_decisions"]
    manifest = {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "postwrite-fixed",
        "sources": [{"name": "postwrite", "path": str(path), "family": "postwrite", "format": "sqlite", "frozen": frozen,
            "schema_version": CURRENT_POSTWRITE_SCHEMA, "tables": tables, "sha256": sha256(path.read_bytes()).hexdigest()}]}
    return load_source_snapshot(manifest, [tmp_path], output_root=tmp_path.parent / (tmp_path.name + "_snapshot"))


def test_S01_complete_titles_still_missing_independent_requirement():
    record = {"run_id": "r1", "titles": ["section" + str(i) for i in range(8)],
        "covered_requirements": ["budget"], "checked_sections": ["s1"], "changed_sections": []}
    assert "DOCUMENT_REQUIREMENT_MISSING" in _codes(_evaluate({"S-PRECHECK": record}, {"document_requirements": ["budget", "capacity"]}))


def test_S02_incremental_noop_is_zero_checked_and_zero_repair():
    batch = _evaluate({"S-PRECHECK": {"run_id": "r1", "passed": True, "checked_sections": [], "changed_sections": []}})
    assert next(m for m in batch.metrics if m.metric_id == "actual_document_checked_count").value == 0
    assert next(m for m in batch.metrics if m.metric_id == "actual_repair_count").value == 0


def test_S03_dimensions_present_missing_required_extra_detected():
    declared = _declaration()
    completion = {"items": [dict(item, state="PASS") for item in declared["items"] if item["producer"] != "claim_quality_projection"]}
    row = {**declared, "run_id": "r1", "completion": completion}
    assert "REQUIRED_DECLARATION_COMPLETION_MISMATCH" in _codes(_evaluate({"S-PLAN": row}))
    assert enumerate_required_items(completion)["items"] == tuple(completion["items"])


def test_S04_shared_and_fth03_are_distinct_actual_declarations():
    declaration = _declaration()
    faithfulness = [i for i in declaration["items"] if i["dimension"] == "faithfulness"]
    assert {i["producer"] for i in faithfulness} == {"faithfulness_fth03", "claim_quality_projection"}
    assert len({i["expected_validation_item_ref"] for i in faithfulness}) == len(faithfulness)
    custom = _declaration(False, ("consistency", "coverage"))
    assert [i["dimension"] for i in custom["items"]] == ["consistency", "coverage", "citation"]
    assert not any(i["producer"] == "claim_quality_projection" for i in custom["items"])


def test_S05_citation_integrity_pass_does_not_hide_association_error():
    observed = {"integrity": "PASS", "units": [{"id": "claim1:occurrence0", "verdict": "CORRECT_BINDING"}]}
    reference = {"scope": "SECTION", "reference_id": "independent-fixed", "expected_units": [{"id": "claim1:occurrence0", "verdict": "WRONG_SOURCE"}]}
    metric = evaluate_quality_dimension(observed, reference, dimension="citation", scope="SECTION")
    assert metric.assertion_result is False and metric.value == 0


def test_S06_document_conflict_uses_document_denominator():
    reference = {"scope": "DOCUMENT", "expected_units": [{"id": "capacity-cross-section", "verdict": "CONTRADICTION"}]}
    observed = {"units": [{"id": "capacity-cross-section", "verdict": "CONSISTENT"}]}
    assert evaluate_quality_dimension(observed, reference, dimension="consistency", scope="DOCUMENT").assertion_result is False
    assert evaluate_quality_dimension(observed, reference, dimension="consistency", scope="SECTION").measurement_state == "UNMEASURED"


def test_S07_bad_role_structure_is_technical_not_semantic_conflict():
    batch = _evaluate({"S-ITEM": {"run_id": "r1", "scope": "DOCUMENT", "dimension": "consistency", "parse_error": "role_bindings_missing"}})
    assert "VALIDATOR_SCHEMA_TECHNICAL_FAILURE" in _codes(batch)
    metric = next(m for m in batch.metrics if m.metric_id == "consistency_accuracy")
    assert metric.measurement_state == "TECHNICAL_FAILURE" and metric.assertion_result is None
    assert not any(f.code == "SEMANTIC_CONTRADICTION" for f in batch.findings)


@pytest.mark.parametrize("action", ["OUTPUT_BEST_EFFORT", "BLOCK"])
def test_S08_best_effort_and_block_are_legal_actions_with_negative_quality(action):
    batch = _evaluate({"S-CONTROL": {"run_id": "r1", "quality_state": "FAIL", "action": action, "allowed_actions": [action],
        "gate_ref": "gate:1", "gate": {"gate_ref": "gate:1", "quality_state": "FAIL"}}})
    assert next(m for m in batch.metrics if m.metric_id == "control_policy_conformance").assertion_result is True
    assert next(s for s in batch.stages if s.operation_id == "S-CONTROL").observed_quality_status == "FAIL"


def test_S09_publication_absence_requires_complete_eligibility_path():
    assert classify_publication({"manifest_present": False}) == ("PUBLICATION_OR_CLOSE_UNKNOWN",)
    assert classify_publication({"eligible": True, "path_complete": True, "publication_started": False}) == ("RELEASE_ELIGIBLE_NOT_STARTED",)
    assert classify_publication({"control_action": "BLOCK", "policy_requires_publication": False}) == ("RELEASE_NOT_EXPECTED_POLICY_BLOCK",)
    assert classify_publication({"path_complete": True, "upstream_failure": True, "publication_started": False}) == ("RELEASE_NOT_REACHED_UPSTREAM_FAILURE",)


def test_S10_terminalized_publication_failure_distinct_from_missing_evidence():
    assert classify_publication({"terminal_valid": True, "assessment_valid": True, "publication_started": True,
        "publication_error": "OSError"}) == ("TERMINALIZED_PUBLICATION_FAILED",)
    assert classify_publication({"terminal_valid": True}) == ("PUBLICATION_OR_CLOSE_UNKNOWN",)
    assert classify_publication({"manifest_present": True, "artifact_integrity_valid": False}) == ("PUBLICATION_ARTIFACT_INTEGRITY_FAILURE",)


def test_S11_legal_release_wrong_public_alias_is_projection_failure():
    batch = _evaluate({"S-PUBLIC": {"run_id": "r1", "release_valid": True, "public_projection_valid": False,
        "public_content": "wrong[C1]", "release_content": "correct[C2]"}})
    assert "PUBLIC_PROJECTION_MISMATCH" in _codes(batch)


def test_S12_os_exit_does_not_prove_resource_usage_closed():
    assert classify_publication({"terminal_valid": True, "os_exit_code": 0, "resource_closed": False, "usage_finalized": False}) == ("RESOURCE_OR_USAGE_FINALIZATION_INCOMPLETE",)


def test_S13_actual_isolated_publication_replay_zero_new_then_legal_obligation(tmp_path):
    # Fresh synthetic owner records, never a historical campaign or business publication.
    import test_post_write_release_publication as fixtures
    fx = fixtures._release_fixture(tmp_path)
    first = fixtures._publish(fx)
    files_before = {str(p.relative_to(tmp_path)): sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    second = fixtures._publish(fx)
    files_after = {str(p.relative_to(tmp_path)): sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    assert first.manifest == second.manifest
    assert files_before == files_after
    healthy = _evaluate({"S-REPLAY": {"run_id": "r1", "healthy_exact_read": True, "new_model_calls": 0,
        "new_business_writes": 0, "new_publications": 0}})
    assert next(m for m in healthy.metrics if m.metric_id == "replay_side_effects").assertion_result is True
    legal = _evaluate({"S-REPLAY": {"run_id": "r1", "authorized_missing_obligation": True,
        "failed_run_revived": False, "new_model_calls": 0, "new_business_writes": 1, "new_publications": 1}})
    assert next(m for m in legal.metrics if m.metric_id == "replay_side_effects").reason == "LEGAL_MISSING_OBLIGATION"


def test_S14_markdown_and_unproven_wal_never_construct_postwrite_owner(tmp_path, monkeypatch):
    from apps.enterprise_document.services.post_write.persistence import PostWriteSqliteDatabase
    def forbidden(*args, **kwargs):
        raise AssertionError("OBSERVE constructed a migrating owner")
    monkeypatch.setattr(PostWriteSqliteDatabase, "__init__", forbidden)
    assert read_postwrite_records(SnapshotView.from_records({"markdown": {"content": "# Draft"}}))["errors"] == ["POSTWRITE_SOURCE_UNAVAILABLE"]
    path = tmp_path / "unproven.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE post_write_metadata (key TEXT, value TEXT)")
    wal = tmp_path / "unproven.sqlite3-wal"
    wal.write_bytes(b"torn-wal")
    before = (path.read_bytes(), wal.read_bytes())
    snapshot = _snapshot_database(path, tmp_path)
    assert any("WAL_CONSISTENT_SNAPSHOT_NOT_PROVEN" in l for l in snapshot.limitations)
    assert read_postwrite_records(snapshot)["errors"] == ["POSTWRITE_SOURCE_UNAVAILABLE"]
    assert (path.read_bytes(), wal.read_bytes()) == before


def _minimal_composition(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import TargetPostWriteCitationIntegrity
    from apps.enterprise_document.services.post_write.execution import ValidationRunLifecycleService
    fx = _gate_fixture(tmp_path)
    composition = object.__new__(TargetPostWriteComposition)
    composition.database = fx["database"]
    composition.validation_store = fx["validation_store"]
    from types import SimpleNamespace
    composition.validation_lifecycle = ValidationRunLifecycleService(store=fx["validation_store"], clock=SimpleNamespace(now_epoch=lambda: 2000.))
    composition.claim_quality_projection = None
    composition.requirement_adherence = None
    composition.coverage_composition = None
    composition.consistency_composition = None
    composition.citation_integrity = TargetPostWriteCitationIntegrity(composition)
    from apps.enterprise_document.services.post_write import GateInputIntegrityGuard
    resolved = fx["guard"].resolve(validation_run_id=fx["run_id"], validation_completion_ref=fx["completion_ref"],
        quality_delivery_contract_ref=fx["contract_ref"], currentness_proof=fx["currentness"])
    from apps.enterprise_document.schemas.post_write import ValidationScope
    kwargs = {"run_id": "run-fce-isolated", "entry_ref": fx["entry_ref"], "artifact_ref": fx["artifact_ref"],
        "scope": ValidationScope.SECTION, "target_ref": "section-gate-1", "quality_contract_ref": fx["contract_ref"],
        "currentness_proof": fx["currentness"], "requirement_ref": resolved.manifest.requirement_ref}
    return composition, kwargs


def test_S15_real_composition_timeout_keeps_required_plan_without_completion(tmp_path, monkeypatch):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics
    composition, kwargs = _minimal_composition(tmp_path)
    calls = []
    def timeout(**arguments):
        calls.append("integrity")
        raise TimeoutError("controlled isolated producer deadline")
    monkeypatch.setattr(composition.citation_integrity, "complete_item", timeout)
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "postwrite-timeout"))
    with activate_diagnostics(session), pytest.raises(TimeoutError, match="controlled isolated"):
        composition._complete_validation(**kwargs)
    session.close()
    events = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    plan = next(e for e in events if e.get("event_type") == "S-PLAN")
    assert enumerate_required_items(declaration=plan)["provenance"] == "DERIVED_EXPECTED"
    assert len(plan["items"]) == len(declare_required_validation_items(required_dimensions=composition.REQUIRED_DIMENSIONS,
        artifact_hash=kwargs["artifact_ref"].artifact_hash, has_claim_projection=False))
    assert calls == ["integrity"]
    assert any(e.get("event_type") == "S-PRODUCER-ERROR" for e in events)
    with composition.database.read_transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM validation_completions WHERE validation_run_id=?", (plan["validation_run_id"],)).fetchone()[0]
    assert count == 0
    assert session.lost_count == 0


def test_S16_safe_actual_owner_reader_rejects_metadata_version_mix(tmp_path, monkeypatch):
    from apps.enterprise_document.services.post_write.persistence import PostWriteSqliteDatabase
    fx = _gate_fixture(tmp_path)
    path = fx["database"].path
    before = path.read_bytes()
    snapshot = _snapshot_database(path, tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("reader attempted owner initialization")
    monkeypatch.setattr(PostWriteSqliteDatabase, "__init__", forbidden)
    decoded = read_postwrite_records(snapshot)
    assert decoded["errors"] == []
    assert decoded["records"]["validation_completions"][0]["record"] == fx["completion"].model_dump(mode="json")
    assert "#validation_completions/row/0" in decoded["records"]["validation_completions"][0]["location"]
    assert path.read_bytes() == before
    changed = deepcopy(snapshot.get("postwrite"))
    changed["validation_completions"][0]["completion_schema_version"] = "validation_completion_v0"
    from dataclasses import replace
    mixed = replace(snapshot, sources={"postwrite": replace(snapshot.sources["postwrite"], data=changed)})
    assert any("EXACT_ROW_METADATA_MISMATCH" in error for error in read_postwrite_records(mixed)["errors"])


def test_required_extraction_preserves_original_requirement_error_and_no_producer(tmp_path, monkeypatch):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteCompositionError
    composition, kwargs = _minimal_composition(tmp_path)
    def forbidden(**kwargs):
        raise AssertionError("producer was executed before canonical Requirement error")
    monkeypatch.setattr(composition.citation_integrity, "complete_item", forbidden)
    kwargs["requirement_ref"] = None
    with pytest.raises(TargetPostWriteCompositionError) as disabled:
        composition._complete_validation(**kwargs)
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "postwrite-equivalence"))
    with activate_diagnostics(session), pytest.raises(TargetPostWriteCompositionError) as enabled:
        composition._complete_validation(**kwargs)
    session.close()
    assert str(disabled.value) == str(enabled.value) == "Validation requires exact canonical RequirementRef"
    events = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event_type") == "S-PLAN" for e in events)
    assert not any(e.get("event_type") == "S-PRODUCER-BEGIN" for e in events)


def test_gate_completion_manifest_mismatch_retains_both_originals():
    gate = {"gate_ref": "g1", "gate_input_manifest": {"validation_completion_ref": {
        "completion_id": "c1", "validation_run_id": "r1", "subject_hash": "a", "completion_sha256": "h1"}}}
    control = {"gate_ref": "g1", "action": "OUTPUT", "allowed_actions": ["OUTPUT"]}
    completion = {"completion_id": "c2", "validation_run_id": "r1", "subject_hash": "a", "completion_sha256": "h2"}
    before = deepcopy((gate, completion))
    edge = check_control_and_assembly(gate, control, completion=completion)
    assert edge.assertion_result is False and edge.reason == "GATE_COMPLETION_MANIFEST_MISMATCH"
    assert (gate, completion) == before


def postwrite_edge_fixture(edge_id, *, negative=False):
    artifact = {"artifact_id": "document:r1", "artifact_version": 1, "artifact_hash": "a" * 64, "artifact_scope": "DOCUMENT"}
    completion_ref = {"completion_id": "c1", "completion_sha256": "b" * 64, "validation_run_id": "v1", "subject_hash": "c" * 64}
    if edge_id == "X16":
        source = {"section_refs": ["section:s1:v1", "section:s2:v1"], "section_ids": ["s1", "s2"]}
        target = {"input_section_refs": list(source["section_refs"]), "changed_sections": ["s1"], "checked_sections": ["s1"]}
        if negative:
            target["input_section_refs"][0] = "section:s1:v2"
    elif edge_id == "X17":
        declaration = _declaration()
        source = {"declaration": declaration, "completion": {**completion_ref, "items": [dict(item, state="PASS") for item in sorted(declaration["items"], key=lambda i: i["expected_validation_item_ref"])]}}
        target = {"gate_input_manifest": {"validation_completion_ref": dict(completion_ref)}}
        if negative:
            source["completion"]["items"].pop()
    elif edge_id == "X18":
        source = {"gate_ref": "gate:v1", "quality_state": "FAIL"}
        target = {"gate_ref": "gate:v1", "action": "OUTPUT_BEST_EFFORT", "allowed_actions": ["OUTPUT_BEST_EFFORT"]}
        if negative:
            target["gate_ref"] = "gate:v2"
    elif edge_id == "X19":
        source = {"allowed_entries": ["entry:s1:v1", "entry:s2:v1"], "required_entries": ["entry:s1:v1"], "document_artifact_ref": artifact}
        target = {"selected_entries": ["entry:s1:v1", "entry:s2:v1"], "document_artifact_ref": deepcopy(artifact)}
        if negative:
            target["document_artifact_ref"]["artifact_version"] = 2
    elif edge_id == "X20":
        source = {"document_artifact_ref": artifact}
        target = {"document_checks": {"scope": "DOCUMENT", "artifact_ref": deepcopy(artifact), "completion_ref": completion_ref},
            "assessment": {"document_artifact_ref": deepcopy(artifact), "document_completion_ref": dict(completion_ref)}}
        if negative:
            target["document_checks"]["scope"] = "SECTION"
    elif edge_id == "X21":
        source = {"terminal_ref": {"run_id": "r1", "lifecycle_revision": 12}, "assessment_ref": "assessment:r1:h1",
            "document_artifact_ref": artifact, "terminal_valid": True, "assessment_valid": True}
        target = {"publication_started": True, "request": {k: deepcopy(source[k]) for k in ("terminal_ref", "assessment_ref", "document_artifact_ref")}}
        if negative:
            target["request"]["assessment_ref"] = "assessment:other-run"
    elif edge_id == "X27":
        source = {"artifact_ref": artifact, "content": "Final[C2]", "status": "OUTPUT_BEST_EFFORT", "human_review_required": True}
        target = deepcopy(source)
        if negative:
            target["human_review_required"] = False
    else:
        raise ValueError(edge_id)
    return source, target


@pytest.mark.parametrize("edge_id", ["X16", "X17", "X18", "X19", "X20", "X21", "X27"])
def test_postwrite_edges_have_normal_negative_and_missing_endpoints(edge_id):
    check = (lambda source, target: check_control_and_assembly(source, target)) if edge_id == "X18" else (lambda source, target: check_postwrite_edge(edge_id, source, target))
    assert check(*postwrite_edge_fixture(edge_id)).assertion_result is True
    assert check(*postwrite_edge_fixture(edge_id, negative=True)).assertion_result is False
    assert check(None, None).measurement_state == "UNMEASURED"


def postwrite_edge_snapshot(edge_id, *, negative=False):
    source, target = postwrite_edge_fixture(edge_id, negative=negative)
    families = {"X16": ("S-SECTIONS", "S-PRECHECK"), "X17": ("S-REQUIRED", "S-GATE"), "X18": ("S-GATE", "S-CONTROL"),
        "X19": ("S-CONTROL", "S-ASSEMBLY"), "X20": ("S-ASSEMBLY", "S-DOCUMENT-ASSESSMENT"),
        "X21": ("S-TERMINAL", "S-PUBLISH"), "X27": ("S-RELEASE", "S-PUBLIC")}
    left, right = families[edge_id]
    return SnapshotView.from_records({left: source, right: target})


_ENDPOINT_FAMILIES = {"X16": ("S-SECTIONS", "S-PRECHECK"), "X17": ("S-REQUIRED", "S-GATE"),
    "X18": ("S-GATE", "S-CONTROL"), "X19": ("S-CONTROL", "S-ASSEMBLY"),
    "X20": ("S-ASSEMBLY", "S-DOCUMENT-ASSESSMENT"), "X21": ("S-TERMINAL", "S-PUBLISH"), "X27": ("S-RELEASE", "S-PUBLIC")}


def _original_endpoint_lineage(source, target, lineage):
    if isinstance(source.get("terminal_ref"), dict):
        source["terminal_ref"]["run_id"] = lineage["run_id"]
    if isinstance(target.get("request", {}).get("terminal_ref"), dict):
        target["request"]["terminal_ref"]["run_id"] = lineage["run_id"]
    return {**source, **lineage}, {**target, **lineage}


@pytest.mark.parametrize("edge_id", list(_ENDPOINT_FAMILIES))
@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_sealed_diagnostic_envelopes_connect_postwrite_endpoints(tmp_path, edge_id, mutation):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession
    from eval.full_chain.domains.runtime_reliability import read_diagnostic_prefix
    from eval.full_chain.domains.planning_prewrite import capture_planning_postwrite_snapshot
    from eval.full_chain.domains.postwrite_release import extract_postwrite
    source, target = postwrite_edge_fixture(edge_id, negative=mutation == "negative")
    lineage = {"run_id": "isolated-r1", "scope": "DOCUMENT", "attempt_id": "isolated-a1"}
    source, target = _original_endpoint_lineage(source, target, lineage)
    left, right = _ENDPOINT_FAMILIES[edge_id]
    observer = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "postwrite-envelopes"))
    with observer:
        observer.emit({"event_type": left, "payload": {**source, **lineage}})
        if mutation != "missing":
            observer.emit({"event_type": right, "payload": {**target, **lineage}})
    before = observer.path.read_bytes()
    prefix = read_diagnostic_prefix(observer.path, [tmp_path])
    assert prefix["completeness"] == "COMPLETE"
    batch = extract_postwrite(capture_planning_postwrite_snapshot(prefix["events"] + [prefix["seal"]]))
    edges = [edge for edge in batch.edges if edge.edge_id == edge_id]
    assert len(edges) == 1
    assert edges[0].assertion_result is {"normal": True, "negative": False, "missing": None}[mutation]
    assert edges[0].source_refs
    if mutation != "missing":
        assert edges[0].target_refs
        assert edges[0].payload["source_observation"]["run_id"] == "isolated-r1"
    assert observer.path.read_bytes() == before


@pytest.mark.parametrize("edge_id", list(_ENDPOINT_FAMILIES))
def test_multiple_original_postwrite_instances_do_not_drop_later_bad_edge(edge_id):
    from eval.full_chain.domains.postwrite_release import extract_postwrite
    left, right = _ENDPOINT_FAMILIES[edge_id]
    rows = []
    for index in (1, 2):
        source, target = postwrite_edge_fixture(edge_id, negative=index == 2)
        original = {"run_id": "original-r" + str(index), "scope": "DOCUMENT", "attempt_id": "original-a" + str(index)}
        source, target = _original_endpoint_lineage(source, target, original)
        rows.extend([{"event_type": left, "payload": {**source, **original}},
            {"event_type": right, "payload": {**target, **original}}])
    # Reverse consumer order: the join must use original lineage, not row order.
    rows = rows[::2] + list(reversed(rows[1::2]))
    batch = extract_postwrite(SnapshotView.from_records({"diagnostics": rows}))
    edges = [edge for edge in batch.edges if edge.edge_id == edge_id]
    assert [edge.assertion_result for edge in edges] == [True, False]
    assert [edge.subject_id for edge in edges] == ["original-r1", "original-r2"]
    assert all(edge.source_refs and edge.target_refs for edge in edges)
    assert any(finding.edge_id == edge_id for finding in batch.findings)


@pytest.mark.parametrize("edge_id", list(_ENDPOINT_FAMILIES))
def test_ambiguous_postwrite_instances_preserve_all_endpoint_evidence_as_unknown(edge_id):
    from eval.full_chain.domains.postwrite_release import extract_postwrite
    left, right = _ENDPOINT_FAMILIES[edge_id]
    source, target = postwrite_edge_fixture(edge_id)
    lineage = {"run_id": "same-r", "scope": "DOCUMENT", "attempt_id": "same-a"}
    source, target = _original_endpoint_lineage(source, target, lineage)
    rows = [{"event_type": left, "payload": {**source, **lineage}} for _ in range(2)]
    rows += [{"event_type": right, "payload": {**target, **lineage}} for _ in range(2)]
    batch = extract_postwrite(SnapshotView.from_records({"diagnostics": rows}))
    edges = [edge for edge in batch.edges if edge.edge_id == edge_id]
    assert len(edges) == 4 and all(edge.assertion_result is None for edge in edges)
    assert all(edge.measurement_state == "UNMEASURED" for edge in edges)
    pointers = [ref.pointer for edge in edges for ref in edge.source_refs + edge.target_refs]
    assert sorted(pointers) == ["/0", "/1", "/2", "/3"]


def test_postwrite_dimensions_have_distinct_stage_units_and_missing_markers():
    full = _evaluate({"S-ITEM": [{"run_id": "r1", "dimension": d, "scope": "SECTION", "units": []}
        for d in ("faithfulness", "citation", "relevancy", "coverage", "consistency")]})
    assert {s.unit_id for s in full.stages if s.operation_id == "S-ITEM"} == {"E04", "E05", "E06", "E07", "E08"}
    assert all(s.measurement_state == "MEASURED" for s in full.stages if s.operation_id == "S-ITEM")
    absent = _evaluate({})
    assert {s.unit_id for s in absent.stages if s.measurement_state == "UNMEASURED"} >= {"E04", "E05", "E06", "E07", "E08"}


def test_postwrite_actual_sqlite_records_become_exact_stages(tmp_path):
    import test_post_write_delivery_gate as fixtures
    fx = _gate_fixture(tmp_path)
    fixtures._evaluate(fx)
    snapshot = _snapshot_database(fx["database"].path, tmp_path)
    batch = evaluate_postwrite(snapshot)
    persisted = [s for s in batch.stages if s.identity_quality == "ORIGINAL_OWNER_REF"]
    assert {s.unit_id for s in persisted} >= {"E02", "E03", "E04", "E09"}
    assert all(s.evidence_refs and s.evidence_refs[0].pointer for s in persisted)
    completion = next(s for s in persisted if s.payload["table"] == "validation_completions")
    assert completion.payload["record"] == fx["completion"].model_dump(mode="json")
    assert "GATE_COMPLETION_MANIFEST_MISMATCH" not in _codes(batch)


def test_completion_gate_capture_preserves_order_exact_refs_and_original_accounting(tmp_path, monkeypatch):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics
    from apps.enterprise_document.schemas.post_write import ValidationCompletionItemSchema, ValidationCompletionItemState
    composition, kwargs = _minimal_composition(tmp_path)
    # Fixed producer boundary, original composition/lifecycle/store/Gate owners.
    # Remaining required items retain their original NOT_RUN accounting.
    calls = []
    def fixed_integrity(**arguments):
        calls.append(("integrity", arguments["validation_run_id"], arguments["subject_hash"]))
        return ValidationCompletionItemSchema(expected_validation_item_ref="citation:integrity:" + arguments["artifact_ref"].artifact_hash,
            required=True, state=ValidationCompletionItemState.NOT_APPLICABLE)
    monkeypatch.setattr(composition.citation_integrity, "complete_item", fixed_integrity)
    import test_post_write_delivery_gate as fixtures
    # Gate already owns the same database/Entry/quality contract through fixture.
    from apps.enterprise_document.services.post_write import DeliveryGateService, GateDecisionStore, GateInputIntegrityGuard, PostWriteEntryStore, QualityDeliveryContractStore
    composition.gate_service = DeliveryGateService(input_guard=GateInputIntegrityGuard(validation_store=composition.validation_store,
        entry_store=PostWriteEntryStore(composition.database), contract_store=QualityDeliveryContractStore(composition.database)),
        decision_store=GateDecisionStore(composition.database), clock=lambda: 2000.)
    baseline = composition._complete_validation(**kwargs)
    baseline_completion = composition.validation_store.resolve_completion(baseline[0])
    baseline_calls = list(calls)
    calls.clear()
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "postwrite-accounting-equivalence"))
    with activate_diagnostics(session):
        observed = composition._complete_validation(**kwargs)
    session.close()
    assert observed == baseline
    assert composition.validation_store.resolve_completion(observed[0]) == baseline_completion
    assert calls == baseline_calls
    declarations = declare_required_validation_items(required_dimensions=composition.REQUIRED_DIMENSIONS,
        artifact_hash=kwargs["artifact_ref"].artifact_hash, has_claim_projection=False)
    assert [i.expected_validation_item_ref for i in baseline_completion.items] == sorted(d.expected_validation_item_ref for d in declarations)
    dimension_refs = {d.expected_validation_item_ref for d in declarations if d.channel == "dimension"}
    assert all(item.state is ValidationCompletionItemState.NOT_RUN for item in baseline_completion.items if item.expected_validation_item_ref in dimension_refs)
    events = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event_type") == "S-GATE-RESULT" for e in events)
    assert session.lost_count == 0
