"""PAIRED8-008: offline consumer of frozen evidence; never a runtime authority.

Only schema modules and hashing are imported from production. SQLite is copied
with its sidecars before mode=ro SELECTs. No observer event is an input to Writer
certification. Failure receipts contain check names/hashes, never raw payloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from apps.enterprise_document.schemas.pre_write_writer import (
    WriterLogicalExecutionKey, WriterExecutionResultSchema,
    WriterGenerationInvocationSchema,
)
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAssessmentRecordSchema, RunLifecycleRecordSchema,
)
from apps.enterprise_document.schemas.post_write.control import PostWriteControlDecisionSchema
from apps.enterprise_document.schemas.post_write.document import AssemblyInputManifestSchema
from apps.enterprise_document.schemas.post_write.entry import SectionPostWriteEntrySchema
from core.hashing import canonical_sha256


class EvidenceFailure(ValueError):
    """Fail closed, with a payload-free diagnostic code."""


def require(condition, code):
    if not condition:
        raise EvidenceFailure(code)


def typed(schema, value):
    try:
        return schema.model_validate(value).model_dump(mode="json")
    except (ValueError, TypeError) as exc:
        raise EvidenceFailure("typed_parse:" + schema.__name__) from exc


def parse(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError) as exc:
        raise EvidenceFailure("malformed_json") from exc


def read(path):
    return parse(path.read_text(encoding="utf-8-sig"))


def jsonl(path):
    return [parse(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes(root):
    files = {}
    sizes = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "source_symlink")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_hash(path)
            sizes[path.relative_to(root).as_posix()] = path.stat().st_size
    require(bool(files), "empty_source")
    return {"file_count": len(files), "files": files, "sizes": sizes,
            "manifest_sha256": canonical_sha256(files)}


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
INSPECTION_EXTRA = "STORAGE/normal/pre_write.sqlite3"


def normalize_manifest_path(value):
    """Portable relative key, with no Windows drive/ADS/parent escape aliases."""
    require(isinstance(value, str) and bool(value), "manifest_empty_path")
    path = value.replace("\\", "/")
    require(not PureWindowsPath(path).drive and not path.startswith("/"), "manifest_absolute_path")
    parts = [part for part in path.split("/") if part not in ("", ".")]
    require(bool(parts), "manifest_empty_path")
    require(all(part != ".." for part in parts), "manifest_parent_traversal")
    require(all(":" not in part and "\x00" not in part and part == part.rstrip(" .") for part in parts), "manifest_unsafe_path")
    return "/".join(parts)


def normalize_manifest(entries):
    require(isinstance(entries, dict) and bool(entries), "manifest_empty_inventory")
    normalized, mapping, aliases = {}, [], set()
    for original, digest in entries.items():
        path = normalize_manifest_path(original)
        require(path.casefold() not in aliases, "manifest_normalized_collision:" + path)
        require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "manifest_invalid_hash")
        aliases.add(path.casefold())
        normalized[path] = digest
        mapping.append({"original_path": original, "normalized_path": path})
    return normalized, {"status": "PASS", "entry_count": len(normalized),
                        "changed_key_count": sum(row["original_path"] != row["normalized_path"] for row in mapping),
                        "collision_count": 0, "paths": mapping}


def classify_sqlite_sidecar(path, historical, current):
    suffix = "-shm" if path.endswith(".sqlite3-shm") else "-wal"
    main = path[:-len(suffix)]
    main_match = main in historical and main in current and historical[main] == current[main]
    present = path in current
    wal_empty = historical[path] == EMPTY_SHA256 if suffix == "-wal" else None
    category = "EPHEMERAL_SQLITE_SHM" if suffix == "-shm" else (
        "EPHEMERAL_EMPTY_SQLITE_WAL" if wal_empty else "DURABLE_REQUIRED")
    accepted = main_match and (current[path] == historical[path] if present else suffix == "-shm" or wal_empty)
    return {"path": path, "sidecar_type": suffix[1:], "historical_hash": historical[path],
            "historical_wal_empty": wal_empty, "main_db_path": main,
            "main_db_hash_match": main_match, "current_presence": present,
            "current_hash": current.get(path), "classification": category,
            "disposition": ("EXACT_MATCH" if present else "EPHEMERAL_ABSENCE" if suffix == "-shm" else "EPHEMERAL_EMPTY_WAL_ABSENCE") if accepted else "BLOCKED",
            "accepted": bool(accepted), "blocked": not accepted}


def reconcile_historical_manifest(historical, current, sizes):
    """Inputs already normalized; classified exceptions never bypass whole-tree fence."""
    retained, durable, sidecars, extras = [], [], [], []
    for path, digest in historical.items():
        if path.endswith((".sqlite3-shm", ".sqlite3-wal")):
            sidecars.append(classify_sqlite_sidecar(path, historical, current))
        else:
            durable.append({"path": path, "classification": "DURABLE_REQUIRED",
                            "historical_hash": digest, "current_hash": current.get(path),
                            "accepted": path in current and current[path] == digest})
        if current.get(path) == digest:
            retained.append(path)
    for path in sorted(set(current) - set(historical)):
        category, accepted = "UNKNOWN_EXTRA", False
        if path == "HASH_MANIFEST.json":
            category, accepted = "EXPECTED_MANIFEST_SELF", True
        elif path == INSPECTION_EXTRA:
            category = "KNOWN_NONAUTHORITATIVE_INSPECTION_EXTRA"
            accepted = sizes.get(path) == 0 and current[path] == EMPTY_SHA256
        extras.append({"path": path, "classification": category, "size": sizes.get(path),
                       "sha256": current[path], "accepted": accepted,
                       "reason": "self-excluded manifest" if path == "HASH_MANIFEST.json" else
                       "prior DevSpace wrong-path sqlite3.connect inspection; no business authority" if path == INSPECTION_EXTRA else "not allowlisted"})
    passed = all(row["accepted"] for row in durable + sidecars + extras)
    return {
        "durable": {"status": "PASS" if passed else "BLOCKED", "historical_entry_count": len(historical),
                    "retained_historical_exact_count": len(retained), "durable_required_count": len(durable),
                    "durable_required_exact_count": sum(row["accepted"] for row in durable),
                    "accepted_absent_sidecar_count": sum(row["accepted"] and not row["current_presence"] for row in sidecars),
                    "entries": durable, "requires_current_source_immutability": True},
        "sidecars": {"status": "PASS" if all(row["accepted"] for row in sidecars) else "BLOCKED", "entries": sidecars},
        "extras": {"status": "PASS" if all(row["accepted"] for row in extras) else "BLOCKED",
                   "unknown_extra_count": sum(row["classification"] == "UNKNOWN_EXTRA" for row in extras), "entries": extras},
    }


TABLE_COLUMNS = {
    "writer_executions": "logical_execution_key semantic_fingerprint state result_json created_at_epoch updated_at_epoch".split(),
    "pre_write_route_decisions": "route_decision_id schema_version contract_version decision_json decision_sha256 authorization_snapshot_sha256 created_at_epoch".split(),
    "recovery_dispatches": "occurrence_id semantic_fingerprint status owner_id claim_token lease_expires_at result_ref release_reason created_at_epoch updated_at_epoch".split(),
}
POST_TABLES = (
    "post_write_control_decisions", "post_write_entries", "document_binding_values",
)


def read_database(source, destination, *, execution):
    """Only copy bytes from source; SQLite never opens a historical file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists(), "database_copy_exists")
    shutil.copyfile(source, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(str(destination) + suffix))
    connection = sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        if execution:
            metadata = [dict(row) for row in connection.execute("SELECT key,value FROM pre_write_execution_metadata")]
            require(metadata == [{"key": "schema_version", "value": "gate_v2_pre_write_execution_store_v2"}], "execution_schema_version")
            for table, columns in TABLE_COLUMNS.items():
                actual = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
                require(actual == columns, "table_columns:" + table)
        tables = TABLE_COLUMNS if execution else POST_TABLES
        return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
    finally:
        connection.close()


def one(rows, code):
    require(len(rows) == 1, code + ":count=" + str(len(rows)))
    return rows[0]


def audit_writer(state, tables):
    invocation = typed(WriterGenerationInvocationSchema, state["pending_writer_generation_invocation"])
    route = typed(PreWriteRouteDecisionSchema, state["current_pre_write_route_decision"])
    subject = {name: state[name] for name in ("run_id", "document_id", "section_id")}
    keys = [(row, typed(WriterLogicalExecutionKey, parse(row["logical_execution_key"]))) for row in tables["writer_executions"]]
    row, key = one([(row, key) for row, key in keys if key["writer_subject_ref"] == subject], "current_writer")
    result = typed(WriterExecutionResultSchema, parse(row["result_json"]))
    route_row = one([row for row in tables["pre_write_route_decisions"] if row["route_decision_id"] == route["route_decision_id"]], "durable_route")
    durable_route = typed(PreWriteRouteDecisionSchema, parse(route_row["decision_json"]))
    expected_ref = {"route_decision_id": route["route_decision_id"], "route_decision_schema_version": route["schema_version"], "route_decision_contract_version": route["contract_version"]}
    bundle = dict(invocation["canonical_evidence_input"])
    bundle_hash = bundle.pop("evidence_bundle_sha256")
    provenance = result["generation_provenance"]
    artifact = result.get("artifact") or {}
    lock = invocation["unified_writer_input_snapshot"]["target_prompt_lock"]
    expected_class = {"normal": "normal_write", "fallback": "fallback_write"}.get(route["selected_route"])
    checks = {
        "count_one": True,
        "state_succeeded": row["state"] == "SUCCEEDED",
        "result_success": result["status"] == "success",
        "result_key": result["logical_execution_key"] == key,
        "writer_subject": invocation["writer_subject_ref"] == key["writer_subject_ref"] == route["writer_subject_ref"] == subject,
        "attempt_exact": invocation["write_instance_id"] == key["write_instance_id"] == route["write_instance_id"] == state["active_execution_lineage"]["attempt_id"],
        "lineage_scope": state["active_execution_lineage"]["run_id"] == subject["run_id"] and state["active_execution_lineage"]["attempt_scope_ref"] == "section:" + subject["section_id"],
        "route_exact": invocation["route_decision_ref"] == key["route_decision_ref"] == expected_ref,
        "durable_route_exact": durable_route == route,
        "durable_route_hash": route_row["decision_sha256"] == canonical_sha256(durable_route),
        "durable_route_versions": route_row["schema_version"] == route["schema_version"] and route_row["contract_version"] == route["contract_version"],
        "invocation_fingerprint": row["semantic_fingerprint"] == canonical_sha256(invocation),
        "generation_class": expected_class is not None and invocation["generation_class"] == provenance.get("generation_class") == expected_class,
        "evidence_payload": bundle == state["active_evidence_bundle"],
        "evidence_hash": bundle_hash == provenance.get("evidence_bundle_sha256") == canonical_sha256(bundle),
        "context_package": lock["context_package_id"] == provenance.get("context_package_id"),
        "context_hash": lock["context_sha256"] == provenance.get("context_sha256"),
        "artifact_provenance": artifact.get("extra", {}).get("generation_class") == invocation["generation_class"],
        "artifact_invocation": artifact.get("input", {}).get("writer_generation_invocation") == invocation,
        "provenance_subject": provenance.get("writer_subject_ref") == subject,
        "provenance_attempt": provenance.get("write_instance_id") == key["write_instance_id"],
        "provenance_route": provenance.get("route_decision_ref") == expected_ref,
    }
    if route["selected_route"] == "fallback":
        checks.update(deny_preserved=state["active_writer_admission_decision"]["admission_outcome"] == "deny",
                      block_preserved=state["current_enforcement_result"]["action"] == "block",
                      exhaustion_preserved=state["current_recovery_control_facts"]["recovery_exhausted"] is True)
    require(all(checks.values()), "writer:" + ",".join(k for k, v in checks.items() if not v))
    return {"section_id": subject["section_id"], "checks": checks, "pass": True,
            "logical_execution_key": key, "result_sha256": canonical_sha256(result),
            "invocation_sha256": canonical_sha256(invocation), "generation_class": expected_class,
            "evidence_sha256": bundle_hash, "context_sha256": lock["context_sha256"],
            "context_package_id": lock["context_package_id"]}


def audit_provider(physical):
    require(bool(physical), "no_physical_evidence")
    ids = [row["request"]["model_call_id"] for row in physical]
    checks = {
        "no_duplicate_provider_dispatch": len(ids) == len(set(ids)),
        "all_provider_deepseek": all(row["request"]["extra"].get("selected_profile") == "deepseek" and row["model_name"] == "deepseek_api" and row["provider_model"] == "deepseek-v4-flash" for row in physical),
        "provider_no_exception": all(not row.get("exception") and row.get("response", {}).get("success") is True and not row["response"].get("error") and not row["response"].get("error_message") for row in physical),
        "provider_response_identity": all(row["response"]["model_call_id"] == row["request"]["model_call_id"] for row in physical),
    }
    require(all(checks.values()), "provider:" + ",".join(k for k, v in checks.items() if not v))
    return checks


def audit_support(records):
    require(bool(records), "no_claim_support_evidence")
    require(all(row["supporting_refs"] for row in records if row["relation"] in ("SUPPORTED", "PARTIALLY_SUPPORTED")), "canonical_supported_without_refs")
    return True


def audit_non_writer(source, states, execution):
    rounds_all, packages, occurrences, sections = [], [], [], []
    supports = []
    for variant in ("normal", "missing"):
        rounds = [item["record"] for item in jsonl(source / "STORAGE" / variant / "research_runtime.jsonl")]
        matched = []
        for state in states[variant]:
            sid = state["section_id"]
            rr = [row for row in rounds if row["round_ref"]["section_id"] == sid]
            require(bool(rr), "missing_round:" + sid)
            checks = {
                "round_scope": all(row["round_ref"]["attempt_id"] == state["active_execution_lineage"]["attempt_id"] and row["round_ref"]["run_id"] == state["run_id"] for row in rr),
                "round_chain": all(row["prior_committed_round_ref"] == (rr[i-1]["round_ref"] if i else None) for i, row in enumerate(rr)),
                "current_round": state["current_research_round_binding"]["round_ref"] == rr[-1]["round_ref"],
                "current_package": state["active_evidence_package_binding"]["version_ref"] == rr[-1]["package_progress"]["resulting_evidence_package_version_ref"],
            }
            require(all(checks.values()), "research_lineage:" + sid)
            matched.extend(rr)
            packages.append(canonical_sha256(state["active_evidence_package_binding"]["version_ref"]))
            sections.append({"section_id": sid, "checks": checks})
        require(len(matched) == len(rounds), "unscoped_research_round")
        rounds_all.extend(canonical_sha256(row["round_ref"]) for row in rounds)
        occurrences.extend(row["occurrence_id"] for row in execution[variant]["recovery_dispatches"])
        supports.extend(item["record"] for item in jsonl(source / "STORAGE" / variant / "claim_support.jsonl"))
    physical = read(source / "PHYSICAL_ATTEMPTS.json")
    checks = audit_provider(physical)
    checks.update(no_duplicate_round=len(rounds_all) == len(set(rounds_all)),
                  no_reused_package=len(packages) == len(set(packages)),
                  no_reused_recovery_occurrence=len(occurrences) == len(set(occurrences)),
                  no_canonical_supported_without_refs=audit_support(supports))
    # Cross-check raw per-dispatch receipts, not an inherited reporter boolean.
    for row in physical:
        raw = read(source / "RAW_RESPONSES" / f"physical_{row['ordinal']:03d}.json")
        require(raw == row, "physical_raw_receipt_mismatch")
    require(all(checks.values()), "non_writer_integrity")
    return {"pass": True, "checks": checks, "sections": sections,
            "physical_calls": len(physical), "rounds": len(rounds_all),
            "packages": len(packages), "recovery_occurrences": len(occurrences), "claim_support_records": len(supports)}


def resolve_control(ref, rows):
    row = one([row for row in rows if row["control_decision_id"] == ref["control_decision_id"]], "control")
    model = PostWriteControlDecisionSchema.model_validate(parse(row["decision_json"]))
    require(model.as_ref().model_dump(mode="json") == ref, "control_ref")
    require(row["control_decision_hash"] == ref["control_decision_hash"], "control_row_hash")
    return model.model_dump(mode="json")


def audit_terminal(source, variant, states, post, writers):
    base = source / variant.upper()
    payload = read(base / "DOCUMENT_GATE_PAYLOAD.json")
    certificate = read(base / "TERMINAL_CERTIFICATION.json")
    expected_ids = [state["section_id"] for state in states]
    run_id = states[0]["run_id"]
    require([row["section"]["section_id"] for row in payload["section_execution_results"]] == expected_ids, "payload_section_set")
    ref = payload["target_run_completion_assessment_ref"]
    assessments = [typed(RunCompletionAssessmentRecordSchema, row) for row in jsonl(source / "STORAGE" / variant / "run_completion_assessments.jsonl")]
    assessment = one([row for row in assessments if row["assessment_id"] == ref["assessment_id"]], "assessment")
    require(RunCompletionAssessmentRecordSchema.model_validate(assessment).as_ref().model_dump(mode="json") == ref, "assessment_ref")
    require(assessment["run_id"] == run_id and assessment["aggregate_result"] == "SUCCESS", "assessment_success")
    lifecycle = [typed(RunLifecycleRecordSchema, row["record"]) for row in jsonl(source / "STORAGE" / variant / "lifecycle.jsonl") if row["entity_type"] == "run" and row["record"]["identity"]["run_id"] == run_id]
    require(bool(lifecycle) and [row["lifecycle_revision"] for row in lifecycle] == list(range(len(lifecycle))), "lifecycle_revisions")
    require(lifecycle[-1]["status"] == "success" and lifecycle[-1]["run_completion_assessment_ref"] == ref, "lifecycle_terminal")
    envelopes = assessment["section_completion_refs"]
    require([row["source_scope_ref"] for row in envelopes] == ["section:" + sid for sid in expected_ids] and len(expected_ids) == 8 and len(set(expected_ids)) == 8, "exact_section_set")
    require(all(row["source_kind"] == "SECTION_CONTROL_DECISION" for row in envelopes), "section_ref_kind")
    document_control = resolve_control(assessment["post_write_control_decision_ref"]["exact_ref_payload"], post["post_write_control_decisions"])
    require(payload["target_post_write_control_decision_ref"] == assessment["post_write_control_decision_ref"]["exact_ref_payload"], "document_control_projection")
    manifest_ref = document_control["input_manifest"]["assembly_input_manifest_ref"]
    binding = one([row for row in post["document_binding_values"] if row["binding_kind"] == "ASSEMBLY_INPUT_MANIFEST" and row["binding_id"] == manifest_ref["manifest_id"]], "assembly_manifest")
    manifest_model = AssemblyInputManifestSchema.model_validate(parse(binding["payload_json"]))
    require(manifest_model.as_ref().model_dump(mode="json") == manifest_ref, "assembly_ref")
    manifest = manifest_model.model_dump(mode="json")
    require(manifest["required_section_keys"] == expected_ids, "assembly_sections")
    sections = []
    for sid, envelope, assembly_entry, writer in zip(expected_ids, envelopes, manifest["ordered_section_entries"], writers, strict=True):
        control_ref = envelope["exact_ref_payload"]
        control = resolve_control(control_ref, post["post_write_control_decisions"])
        control_input = control["input_manifest"]
        entry_ref = control_input["post_write_entry_ref"]
        entry_row = one([row for row in post["post_write_entries"] if row["entry_id"] == entry_ref["entry_id"]], "section_entry")
        entry = typed(SectionPostWriteEntrySchema, parse(entry_row["entry_json"]))
        identity = entry["identity"]
        require(identity["entry_sha256"] == entry_ref["entry_sha256"] == entry_row["entry_sha256"], "entry_hash")
        require(identity["section_id"] == sid and identity["run_id"] == run_id and identity["document_id"] == states[0]["document_id"], "entry_subject")
        require(control_input["artifact_scope"] == identity["entry_scope"] == "SECTION" and identity["artifact_state_ref"] == control_input["artifact_state_ref"], "entry_artifact")
        evidence_ref = identity["evidence_binding"]["section_writer_snapshot_ref"]
        require(bool(evidence_ref), "entry_evidence")
        require(identity["writer_execution_ref"]["logical_execution_key"] == writer["logical_execution_key"] and identity["writer_execution_ref"]["result_sha256"] == writer["result_sha256"], "entry_writer_result")
        require(identity["writer_invocation_ref"]["invocation_sha256"] == writer["invocation_sha256"] and evidence_ref["evidence_bundle_sha256"] == writer["evidence_sha256"], "entry_writer_input")
        require(control["fresh_currentness_proof"]["currentness_state"] == "CURRENT" and control["artifact_usability_integrity_proof"]["state"] == "TRUSTED_USABLE", "section_eligibility")
        require(control["enforcement_action"] == "OUTPUT_BEST_EFFORT" and control["quality_state_passthrough"] == "FAIL" and control["availability_projection"]["state"] == "BEST_EFFORT", "section_quality_preserved")
        candidate = assembly_entry["section_assembly_candidate"]
        expected_candidate = {
            "section_key": sid, "section_artifact_ref": control_input["artifact_state_ref"],
            "section_post_write_entry_ref": entry_ref, "section_evidence_snapshot_ref": evidence_ref,
            "section_gate_decision_ref": control["delivery_gate_decision_ref"],
            "section_control_decision_ref": control_ref, "generation_class": control_input["generation_class"],
            "quality_state": "FAIL", "availability_state": "BEST_EFFORT", "enforcement_action": "OUTPUT_BEST_EFFORT",
        }
        require(candidate is not None and all(candidate[k] == v for k, v in expected_candidate.items()), "assembly_candidate_exact")
        require(assembly_entry["selection_state"] == "INCLUDED" and assembly_entry["inclusion_reason_code"] == "SECTION_OUTPUT_BEST_EFFORT", "assembly_eligibility_projection")
        sections.append({"section_id": sid, "control_decision_ref": control_ref, "enforcement_action": "OUTPUT_BEST_EFFORT", "quality_state": "FAIL", "availability_state": "BEST_EFFORT", "eligible_candidate": True, "selection_state": "INCLUDED", "reason_code": "SECTION_OUTPUT_BEST_EFFORT"})
    terminal = payload["terminal_outcome"]
    require(terminal["run_id"] == run_id and terminal["execution_status"] == "partial_success" and terminal["run_status"] == "success", "terminal_status")
    require(terminal["control_action"] == document_control["enforcement_action"] == "OUTPUT_BEST_EFFORT" and terminal["quality_state"] == document_control["quality_state_passthrough"] == "FAIL" and terminal["availability_state"] == document_control["availability_projection"]["state"] == "BEST_EFFORT", "terminal_quality_projection")
    expected_certificate = {"authority": "target_post_write_run_completion", "run_id": run_id, "document_id": states[0]["document_id"], "run_lifecycle_status": "success", "run_completion_assessment_ref": ref, "run_completion_aggregate_result": "SUCCESS", "terminal_outcome_status": "partial_success", "terminal_control_action": "OUTPUT_BEST_EFFORT", "artifact_route": payload["artifact_route"], "section_completion_count": 8, "sections": sections, "source_workflow_status_is_terminal_authority": False}
    require(certificate == expected_certificate, "historical_certificate_projection")
    return {"pass": True, "terminal_count": 8, "certificate": expected_certificate,
            "canonical_assessment_sha256": assessment["assessment_sha256"], "assembly_manifest_ref": manifest_ref}


def audit_regression(source):
    requests = read(source / "REQUEST_MANIFEST.json")
    calls = [row for row in requests if row["request"]["extra"].get("call_purpose") == "claim_materialization" and row["variant"] == "normal" and row["section_order"] == 4]
    require(bool(calls), "regression_no_section4_claim_call")
    receipts = []
    for row in calls:
        response = row["response"]
        require(read(source / "RAW_RESPONSES" / f"logical_{row['ordinal']:03d}.json") == row, "regression_raw_receipt")
        require(row["request"]["max_tokens"] == 4096 and response["success"] is True and response["finish_reason"] != "length" and 0 < response["token_usage"]["completion_tokens"] < 4096, "regression_truncation")
        parse(response["content"])
        receipts.append({"model_call_id": row["request"]["model_call_id"], "max_tokens": 4096, "completion_tokens": response["token_usage"]["completion_tokens"], "finish_reason": response["finish_reason"], "valid_json": True})
    result = read(source / "NORMAL/SECTION_04/RESULT.json")
    refs = result["canonical_claim_coverage_proof_refs"]
    require(bool(refs), "regression_claim_commit_missing")
    commits = [row for row in jsonl(source / "STORAGE/normal/claim_verification.jsonl") if row["event_type"] == "claim_records_committed"]
    for ref in refs:
        commit = one([row for row in commits if row["receipt"]["coverage_proof_ref"] == ref], "regression_claim_commit")
        require(commit["coverage_accounting"]["coverage_scope_ref"] == ref["coverage_scope_ref"] and bool(commit["claim_records"]), "regression_claim_scope")
    return {"status": "PASS", "calls": receipts, "exact_canonical_claim_coverage_proof_refs": refs}


def recertify(source, output):
    source, output = source.resolve(), output.resolve()
    require(source.is_dir(), "source_missing")
    require(source != output and source not in output.parents and output not in source.parents, "overlapping_roots")
    require(not output.exists(), "output_must_be_new")
    before = source_hashes(source)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "SOURCE_HASH_BEFORE.json", before)
    write(output / "SOURCE_EVIDENCE_REFERENCE.json", {"source_root": str(source), "tool_sha256": file_hash(Path(__file__)), "authority": "durable_writer_executions", "observer": "diagnostic_only", "historical_reports_preserved": True})
    result = {"status": "BLOCKED", "provider_calls_during_recertification": 0, "credential_required": False, "canonical_runs_created": 0, "checkpoints_resumed": 0, "source_runtime_mutations": 0,
              "BUSINESS_CONTENT_AUDIT": "NOT_STARTED", "PAIRED_BUSINESS_ACCEPTANCE_PASS": "NOT_YET_CLAIMED", "FULL_E2E_READY": False, "FULL_E2E_NOT_STARTED": True, "PERFORMANCE_OPTIMIZATION_NOT_STARTED": True}
    exit_code = 2
    for name in ("WRITER_DURABLE_AUTHORITY_AUDIT", "NON_WRITER_INTEGRITY_AUDIT", "TERMINAL_CERTIFICATION_AUDIT", "PAIRED8_005_REGRESSION_RECHECK",
                 "HISTORICAL_MANIFEST_NORMALIZATION", "HISTORICAL_DURABLE_EVIDENCE_RECONCILIATION", "SQLITE_SIDECAR_RECONCILIATION", "SOURCE_EXTRA_FILES_RECONCILIATION"):
        write(output / (name + ".json"), {"status": "NOT_RUN", "pass": False})
    try:
        frozen, normalization = normalize_manifest(read(source / "HASH_MANIFEST.json")["files"])
        write(output / "HISTORICAL_MANIFEST_NORMALIZATION.json", normalization)
        current, _ = normalize_manifest(before["files"])
        reconciliation = reconcile_historical_manifest(frozen, current, before["sizes"])
        write(output / "HISTORICAL_DURABLE_EVIDENCE_RECONCILIATION.json", reconciliation["durable"])
        write(output / "SQLITE_SIDECAR_RECONCILIATION.json", reconciliation["sidecars"])
        write(output / "SOURCE_EXTRA_FILES_RECONCILIATION.json", reconciliation["extras"])
        require(reconciliation["durable"]["status"] == "PASS", "historical_durable_evidence_mismatch")
        result.update(HISTORICAL_DURABLE_EVIDENCE_CONTINUITY="PASS", SQLITE_SIDECAR_RECONCILIATION="PASS", UNKNOWN_EXTRA_FILES=0)
        states, execution, post, writers = {}, {}, {}, {}
        for variant in ("normal", "missing"):
            states[variant] = [read(source / variant.upper() / f"SECTION_{order:02d}" / "WRITER_INPUT_STATE.json") for order in range(1, 9)]
            execution[variant] = read_database(source / "STORAGE" / variant / "execution_control.sqlite3", output / "SQLITE_COPIES" / variant / "execution_control.sqlite3", execution=True)
            post[variant] = read_database(source / "STORAGE" / variant / "post_write.sqlite3", output / "SQLITE_COPIES" / variant / "post_write.sqlite3", execution=False)
            writers[variant] = [audit_writer(state, execution[variant]) for state in states[variant]]
            require(len(execution[variant]["writer_executions"]) == 8, "unexpected_writer_rows")
        write(output / "WRITER_DURABLE_AUTHORITY_AUDIT.json", {"pass": True, "count": 16, "variants": writers})
        non_writer = audit_non_writer(source, states, execution)
        write(output / "NON_WRITER_INTEGRITY_AUDIT.json", non_writer)
        terminal = {variant: audit_terminal(source, variant, states[variant], post[variant], writers[variant]) for variant in ("normal", "missing")}
        write(output / "TERMINAL_CERTIFICATION_AUDIT.json", terminal)
        regression = audit_regression(source)
        write(output / "PAIRED8_005_REGRESSION_RECHECK.json", regression)
        result.update(status="PAIRED8_008_RECERTIFICATION_PASS", paired_status="PAIRED_TERMINAL_INTEGRITY_PASS", NORMAL_TERMINAL=8, MISSING_TERMINAL=8, TOTAL_TERMINAL=16, WRITER_DURABLE_LINEAGE=16, NON_WRITER_INTEGRITY="PASS", PAIRED8_005="PASS")
        exit_code = 0
    except Exception as exc:
        # Pydantic errors may contain raw source payloads: retain only type/code.
        result["blocking_issue"] = str(exc) if isinstance(exc, EvidenceFailure) else type(exc).__name__
    finally:
        after = source_hashes(source)
        write(output / "SOURCE_HASH_AFTER.json", after)
        result["SOURCE_IMMUTABILITY"] = "PASS" if before == after else "FAIL"
        result["CURRENT_SOURCE_IMMUTABILITY"] = result["SOURCE_IMMUTABILITY"]
        if before != after:
            result.update(status="BLOCKED", paired_status="BLOCKED", blocking_issue="source_mutation", source_runtime_mutations=None)
            exit_code = 2
        write(output / "PAIRED8_008_RECERTIFICATION.json", result)
        write(output / "FINAL_CLASSIFICATION.json", result)
        write(output / "PROCESS_EXIT.json", {"exit_code": exit_code, "source_unchanged": before == after})
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    return recertify(args.source_root, args.output_root)


if __name__ == "__main__":
    raise SystemExit(main())
