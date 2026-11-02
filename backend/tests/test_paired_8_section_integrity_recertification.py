"""Synthetic-only evidence tests. No completed acceptance root is accessed."""
import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

TOOL = Path(__file__).resolve().parents[2] / "scripts/experiments/paired_8_section_integrity_recertification.py"
spec = importlib.util.spec_from_file_location("paired_integrity", TOOL)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

from schemas.rag import EvidenceBundleSchema, RAGContextSchema


def fixture():
    subject = {"run_id": "synthetic-run", "document_id": "synthetic-document", "section_id": "synthetic-section"}
    route = audit.typed(audit.PreWriteRouteDecisionSchema, {
        "route_decision_id": "synthetic-route", "writer_subject_ref": subject,
        "write_instance_id": "attempt-1", "selected_route": "normal",
        "decision_ref": {"decision_id": "decision-1"}, "binding_ref": {"binding_id": "binding-1"},
        "enforcement_ref": {"mode": "strict"}, "recovery_control_ref": {"recovery_state_revision": 0},
    })
    key = audit.typed(audit.WriterLogicalExecutionKey, {
        "writer_subject_ref": subject, "write_instance_id": "attempt-1",
        "route_decision_ref": {"route_decision_id": "synthetic-route"},
    })
    bundle = EvidenceBundleSchema(query="synthetic evidence", context=RAGContextSchema(context_text="", used_context_chars=0)).model_dump(mode="json")
    lock = {"context_package_id": "context-1", "context_sha256": "a" * 64}
    invocation = audit.typed(audit.WriterGenerationInvocationSchema, {
        **key, "generation_class": "normal_write",
        "canonical_evidence_input": {**bundle, "evidence_bundle_sha256": audit.canonical_sha256(bundle)},
        "unified_writer_input_snapshot": {"target_prompt_lock": lock},
    })
    result = audit.typed(audit.WriterExecutionResultSchema, {
        "logical_execution_key": key, "status": "success",
        "artifact": {"section_id": subject["section_id"], "section_title": "Synthetic", "section_order": 1,
                     "input": {"writer_generation_invocation": invocation}, "extra": {"generation_class": "normal_write"}},
        "generation_provenance": {**key, **lock, "generation_class": "normal_write", "evidence_bundle_sha256": audit.canonical_sha256(bundle)},
    })
    state = {**subject, "pending_writer_generation_invocation": invocation,
             "current_pre_write_route_decision": route, "active_evidence_bundle": bundle,
             "active_execution_lineage": {"run_id": subject["run_id"], "attempt_id": "attempt-1", "attempt_scope_ref": "section:" + subject["section_id"]}}
    rows = {"writer_executions": [{"logical_execution_key": json.dumps(key), "semantic_fingerprint": audit.canonical_sha256(invocation), "state": "SUCCEEDED", "result_json": json.dumps(result), "created_at_epoch": 1, "updated_at_epoch": 1}],
            "pre_write_route_decisions": [{"route_decision_id": route["route_decision_id"], "schema_version": route["schema_version"], "contract_version": route["contract_version"], "decision_json": json.dumps(route), "decision_sha256": audit.canonical_sha256(route), "authorization_snapshot_sha256": "b" * 64, "created_at_epoch": 1}],
            "recovery_dispatches": []}
    return state, rows


def database(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE pre_write_execution_metadata (key TEXT, value TEXT)")
        conn.execute("INSERT INTO pre_write_execution_metadata VALUES ('schema_version','gate_v2_pre_write_execution_store_v2')")
        for table, columns in audit.TABLE_COLUMNS.items():
            conn.execute(f"CREATE TABLE {table} (" + ",".join(name + (" REAL" if name.endswith("epoch") else " TEXT") for name in columns) + ")")
            for row in rows[table]:
                conn.execute(f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in columns) + ")", [row.get(name) for name in columns])


def test_exact_writer_sqlite_without_any_observer(tmp_path):
    state, rows = fixture()
    db = tmp_path / "synthetic.sqlite3"
    database(db, rows)
    before = audit.file_hash(db)
    loaded = audit.read_database(db, tmp_path / "copy" / "store.sqlite3", execution=True)
    assert audit.audit_writer(state, loaded)["pass"]
    assert audit.file_hash(db) == before


@pytest.mark.parametrize("telemetry", [[], [{"event": "call_return", "method": "unrelated"}], [{"event": "call_return", "method": "SectionExecutionCoordinator.invoke_pre_write_writer"}] * 3])
def test_observer_is_not_authority(tmp_path, telemetry):
    state, rows = fixture()
    audit.write(tmp_path / "observer.json", telemetry)
    assert audit.audit_writer(state, rows)["pass"]


@pytest.mark.parametrize("case", [
    "missing", "duplicate", "RESERVED", "DISPATCHED", "CONFIRMED_FAILED",
    "technical_failure", "malformed_key", "malformed_result", "wrong_subject", "wrong_attempt",
    "wrong_route", "wrong_durable_route", "missing_durable_route", "duplicate_durable_route",
    "generation_class", "evidence_hash", "evidence_material", "context_package", "context_hash",
    "artifact_provenance", "fingerprint", "recovery_writer", "lineage_attempt", "result_key",
])
def test_writer_rejects_corruption(tmp_path, case):
    state, rows = fixture()
    row = rows["writer_executions"][0]
    result = json.loads(row["result_json"])
    if case == "missing": rows["writer_executions"].clear()
    elif case == "duplicate": rows["writer_executions"].append(deepcopy(row))
    elif case in ("RESERVED", "DISPATCHED", "CONFIRMED_FAILED"): row["state"] = case
    elif case == "malformed_key": row["logical_execution_key"] = "{"
    elif case == "malformed_result": row["result_json"] = "{"
    elif case == "wrong_subject":
        key = json.loads(row["logical_execution_key"]); key["writer_subject_ref"]["run_id"] = "other"; row["logical_execution_key"] = json.dumps(key)
    elif case in ("wrong_attempt", "wrong_route"):
        key = json.loads(row["logical_execution_key"])
        if case == "wrong_attempt": key["write_instance_id"] = "other"
        else: key["route_decision_ref"]["route_decision_id"] = "other"
        row["logical_execution_key"] = json.dumps(key)
    elif case == "wrong_durable_route":
        route = json.loads(rows["pre_write_route_decisions"][0]["decision_json"]); route["write_instance_id"] = "other"; rows["pre_write_route_decisions"][0]["decision_json"] = json.dumps(route)
    elif case == "missing_durable_route": rows["pre_write_route_decisions"].clear()
    elif case == "duplicate_durable_route": rows["pre_write_route_decisions"] *= 2
    elif case == "technical_failure":
        result.update(status="technical_failure", artifact=None, failure_kind="model_timeout",
                      error={"error_code": "synthetic", "error_type": "Timeout", "message": "synthetic timeout"})
        audit.typed(audit.WriterExecutionResultSchema, result)
    elif case == "generation_class": result["generation_provenance"]["generation_class"] = "fallback_write"
    elif case == "evidence_hash": result["generation_provenance"]["evidence_bundle_sha256"] = "b" * 64
    elif case == "evidence_material": state["active_evidence_bundle"]["query"] = "different material"
    elif case == "context_package": result["generation_provenance"]["context_package_id"] = "other"
    elif case == "context_hash": result["generation_provenance"]["context_sha256"] = "b" * 64
    elif case == "artifact_provenance": result["artifact"]["extra"]["generation_class"] = "fallback_write"
    elif case == "fingerprint": row["semantic_fingerprint"] = "b" * 64
    elif case == "recovery_writer": state["current_pre_write_route_decision"]["selected_route"] = "recovery"
    elif case == "lineage_attempt": state["active_execution_lineage"]["attempt_id"] = "other"
    elif case == "result_key": result["logical_execution_key"]["write_instance_id"] = "other"
    if case != "malformed_result": row["result_json"] = json.dumps(result)
    db = tmp_path / "synthetic.sqlite3"
    database(db, rows)
    loaded = audit.read_database(db, tmp_path / "copy.sqlite3", execution=True)
    with pytest.raises(audit.EvidenceFailure):
        audit.audit_writer(state, loaded)


def physical():
    return {"request": {"model_call_id": "call-1", "extra": {"selected_profile": "deepseek"}}, "model_name": "deepseek_api", "provider_model": "deepseek-v4-flash", "response": {"model_call_id": "call-1", "success": True}}


def test_provider_duplicate_rejected():
    assert all(audit.audit_provider([physical()]).values())
    with pytest.raises(audit.EvidenceFailure, match="no_duplicate_provider_dispatch"):
        audit.audit_provider([physical(), physical()])


@pytest.mark.parametrize("case", ["exception", "failure", "profile", "response_identity", "empty"])
def test_provider_other_failures(case):
    row = physical()
    if case == "exception": row["exception"] = "synthetic"
    elif case == "failure": row["response"]["success"] = False
    elif case == "profile": row["request"]["extra"]["selected_profile"] = "other"
    elif case == "response_identity": row["response"]["model_call_id"] = "other"
    with pytest.raises(audit.EvidenceFailure): audit.audit_provider([] if case == "empty" else [row])


@pytest.mark.parametrize("relation", ["SUPPORTED", "PARTIALLY_SUPPORTED"])
def test_supported_requires_refs(relation):
    with pytest.raises(audit.EvidenceFailure, match="canonical_supported_without_refs"):
        audit.audit_support([{"relation": relation, "supporting_refs": []}])
    assert audit.audit_support([{"relation": relation, "supporting_refs": ["synthetic-ref"]}])


@pytest.mark.parametrize("damage", ["schema", "columns"])
def test_unsupported_database_fails_closed(tmp_path, damage):
    _, rows = fixture()
    path = tmp_path / "source.sqlite3"
    database(path, rows)
    with sqlite3.connect(path) as conn:
        if damage == "schema": conn.execute("UPDATE pre_write_execution_metadata SET value='unknown'")
        else: conn.execute("ALTER TABLE writer_executions ADD COLUMN unexpected TEXT")
    with pytest.raises(audit.EvidenceFailure): audit.read_database(path, tmp_path / "copy.sqlite3", execution=True)


def test_roots_and_immutable_manifest(tmp_path):
    source = tmp_path / "source"; source.mkdir(); (source / "raw.json").write_text("{}")
    before = audit.source_hashes(source)
    for output in (source, source / "nested", tmp_path):
        with pytest.raises(audit.EvidenceFailure): audit.recertify(source, output)
    assert audit.source_hashes(source) == before
    (source / "raw.json").write_text('{"changed":true}')
    assert audit.source_hashes(source) != before


def test_failed_recertification_receipt_and_source_fence(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    audit.write(source / "HASH_MANIFEST.json", {"files": {"missing": "a" * 64}})
    output = tmp_path / "derived"
    assert audit.recertify(source, output) == 2
    receipt = audit.read(output / "FINAL_CLASSIFICATION.json")
    assert receipt["status"] == "BLOCKED"
    assert receipt["SOURCE_IMMUTABILITY"] == "PASS"
    assert receipt["provider_calls_during_recertification"] == 0


def test_tool_imports_are_offline_and_schema_only():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom): imports.append(node.module)
    allowed_stdlib = {"__future__", "argparse", "hashlib", "json", "pathlib", "shutil", "sqlite3", "sys"}
    assert all(name in allowed_stdlib or name == "core.hashing" or name.startswith("apps.enterprise_document.schemas.") for name in imports)
    assert not any("gateway" in name.lower() or "provider" in name.lower() for name in imports)


@pytest.mark.parametrize("damage", [None, "round_chain", "round_duplicate", "package", "occurrence", "raw_response"])
def test_non_writer_raw_recomputation(tmp_path, damage):
    states, execution = {}, {}
    for variant in ("normal", "missing"):
        base = tmp_path / "STORAGE" / variant; base.mkdir(parents=True)
        rr = {"run_id": variant, "section_id": variant, "attempt_id": "attempt-" + variant}
        package = {"package_id": variant}
        state = {"run_id": variant, "section_id": variant, "active_execution_lineage": {"attempt_id": rr["attempt_id"]},
                 "current_research_round_binding": {"round_ref": rr}, "active_evidence_package_binding": {"version_ref": package}}
        round_record = {"round_ref": rr, "prior_committed_round_ref": None, "package_progress": {"resulting_evidence_package_version_ref": package}}
        if damage == "round_chain": round_record["prior_committed_round_ref"] = {"wrong": True}
        if damage == "package": state["active_evidence_package_binding"] = {"version_ref": {"wrong": True}}
        entries = [{"record": round_record}] * (2 if damage == "round_duplicate" else 1)
        (base / "research_runtime.jsonl").write_text("\n".join(json.dumps(row) for row in entries), encoding="utf-8")
        (base / "claim_support.jsonl").write_text(json.dumps({"record": {"relation": "SUPPORTED", "supporting_refs": [variant]}}), encoding="utf-8")
        states[variant] = [state]
        execution[variant] = {"recovery_dispatches": [{"occurrence_id": "same" if damage == "occurrence" else variant}]}
    p = {**physical(), "ordinal": 1}
    audit.write(tmp_path / "PHYSICAL_ATTEMPTS.json", [p])
    (tmp_path / "RAW_RESPONSES").mkdir()
    audit.write(tmp_path / "RAW_RESPONSES/physical_001.json", {} if damage == "raw_response" else p)
    if damage:
        with pytest.raises(audit.EvidenceFailure): audit.audit_non_writer(tmp_path, states, execution)
    else:
        result = audit.audit_non_writer(tmp_path, states, execution)
        assert result["pass"] and all(result["checks"].values())


@pytest.mark.parametrize("damage", [None, "truncated", "invalid_json", "missing_commit", "wrong_raw"])
def test_regression_requires_raw_call_and_canonical_commit(tmp_path, damage):
    for folder in ("RAW_RESPONSES", "NORMAL/SECTION_04", "STORAGE/normal"):
        (tmp_path / folder).mkdir(parents=True)
    row = {"ordinal": 1, "variant": "normal", "section_order": 4,
           "request": {"model_call_id": "synthetic-call", "max_tokens": 4096, "extra": {"call_purpose": "claim_materialization"}},
           "response": {"success": True, "content": "{}", "finish_reason": "stop", "token_usage": {"completion_tokens": 100}}}
    if damage == "truncated": row["response"]["finish_reason"] = "length"
    if damage == "invalid_json": row["response"]["content"] = "{"
    audit.write(tmp_path / "REQUEST_MANIFEST.json", [row])
    audit.write(tmp_path / "RAW_RESPONSES/logical_001.json", {} if damage == "wrong_raw" else row)
    ref = {"coverage_scope_ref": "section:synthetic-section", "coverage_proof_id": "synthetic-proof"}
    audit.write(tmp_path / "NORMAL/SECTION_04/RESULT.json", {"canonical_claim_coverage_proof_refs": [ref]})
    commit = {"event_type": "claim_records_committed", "receipt": {"coverage_proof_ref": ref}, "coverage_accounting": {"coverage_scope_ref": ref["coverage_scope_ref"]}, "claim_records": ["synthetic-claim"]}
    (tmp_path / "STORAGE/normal/claim_verification.jsonl").write_text("" if damage == "missing_commit" else json.dumps(commit), encoding="utf-8")
    if damage:
        with pytest.raises(audit.EvidenceFailure): audit.audit_regression(tmp_path)
    else: assert audit.audit_regression(tmp_path)["status"] == "PASS"


@pytest.mark.parametrize("path", [r"STORAGE\normal\db.sqlite3", r"STORAGE\normal/db.sqlite3", "./STORAGE/./normal//db.sqlite3"])
def test_manifest_portability_match(path):
    historical, receipt = audit.normalize_manifest({path: "a" * 64})
    assert historical == {"STORAGE/normal/db.sqlite3": "a" * 64}
    reconciled = audit.reconcile_historical_manifest(historical, {"STORAGE/normal/db.sqlite3": "a" * 64}, {})
    assert reconciled["durable"]["status"] == receipt["status"] == "PASS"


@pytest.mark.parametrize("path", ["../escape", "a/../escape", r"a\..\escape", r"C:\absolute", "C:relative", "/absolute", r"\\server\share\file", "", ".", "././", "a/file:stream", "a/.. ", "a\x00b"])
def test_manifest_portability_rejects_unsafe_path(path):
    with pytest.raises(audit.EvidenceFailure): audit.normalize_manifest({path: "a" * 64})


@pytest.mark.parametrize("alias", [r"a\b", "a/./b", "A/B"])
def test_manifest_portability_collision(alias):
    with pytest.raises(audit.EvidenceFailure, match="collision"):
        audit.normalize_manifest({"a/b": "a" * 64, alias: "a" * 64})


@pytest.mark.parametrize("case,expected", [
    ("missing_shm", True), ("missing_empty_wal", True), ("missing_nonempty_wal", False),
    ("missing_main", False), ("changed_main", False), ("empty_wal_changed_main", False),
    ("missing_normal", False), ("unknown_extra", False), ("inspection_extra", True),
    ("nonempty_inspection", False), ("other_empty_extra", False), ("manifest_self", True),
    ("changed_present_shm", False), ("changed_present_wal", False), ("present_nonempty_wal", True),
    ("sidecar_unlisted_main", False), ("inspection_wrong_size", False),
])
def test_sqlite_sidecar_and_extra_policy(case, expected):
    main = "STORAGE/normal/execution_control.sqlite3"
    historical = {main: "a" * 64}
    current = dict(historical)
    sizes = {main: 4096}
    if case in ("missing_shm", "missing_main", "changed_main", "changed_present_shm", "sidecar_unlisted_main"):
        historical[main + "-shm"] = "b" * 64
    if case in ("missing_empty_wal", "empty_wal_changed_main", "changed_present_wal"):
        historical[main + "-wal"] = audit.EMPTY_SHA256
    if case in ("missing_nonempty_wal", "present_nonempty_wal"):
        historical[main + "-wal"] = "c" * 64
    if case == "missing_main": current.pop(main)
    if case in ("changed_main", "empty_wal_changed_main"): current[main] = "d" * 64
    if case == "sidecar_unlisted_main": historical.pop(main)
    if case == "changed_present_shm": current[main + "-shm"] = "d" * 64
    if case == "changed_present_wal": current[main + "-wal"] = "d" * 64
    if case == "present_nonempty_wal": current[main + "-wal"] = "c" * 64
    if case == "missing_normal": historical["required.json"] = "a" * 64
    if case == "unknown_extra": current["unexpected.json"] = "a" * 64
    if case == "other_empty_extra": current["other.sqlite3"] = audit.EMPTY_SHA256; sizes["other.sqlite3"] = 0
    if case == "manifest_self": current["HASH_MANIFEST.json"] = "b" * 64
    if case in ("inspection_extra", "nonempty_inspection", "inspection_wrong_size"):
        current[audit.INSPECTION_EXTRA] = "c" * 64 if case == "nonempty_inspection" else audit.EMPTY_SHA256
        sizes[audit.INSPECTION_EXTRA] = 3 if case in ("nonempty_inspection", "inspection_wrong_size") else 0
    result = audit.reconcile_historical_manifest(historical, current, sizes)
    assert (result["durable"]["status"] == "PASS") is expected
    for row in result["sidecars"]["entries"]:
        assert {"path", "sidecar_type", "historical_hash", "historical_wal_empty", "main_db_path", "main_db_hash_match", "current_presence", "classification", "accepted", "blocked"} <= row.keys()
    if case == "missing_empty_wal":
        assert result["sidecars"]["entries"][0]["disposition"] == "EPHEMERAL_EMPTY_WAL_ABSENCE"
    if case == "inspection_extra":
        assert result["extras"]["entries"][0]["classification"] == "KNOWN_NONAUTHORITATIVE_INSPECTION_EXTRA"


def test_inventory_fence_blocks_before_writer_and_preserves_tree(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    (source / "unknown").write_bytes(b"")
    audit.write(source / "HASH_MANIFEST.json", {"files": {"required": "a" * 64}})
    def forbidden(*args, **kwargs): raise AssertionError("Writer stage reached before fence")
    monkeypatch.setattr(audit, "audit_writer", forbidden)
    before = audit.source_hashes(source)
    output = tmp_path / "derived"
    assert audit.recertify(source, output) == 2
    assert audit.source_hashes(source) == before
    assert audit.read(output / "WRITER_DURABLE_AUTHORITY_AUDIT.json")["status"] == "NOT_RUN"
    assert audit.read(output / "SOURCE_EXTRA_FILES_RECONCILIATION.json")["unknown_extra_count"] == 1
