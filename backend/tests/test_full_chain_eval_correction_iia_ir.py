"""FCE-IIA-001/002: original capture -> safe snapshot -> OBSERVE -> report.

Only fixed clients and the existing production RAG/Fake resource fixture run.
Fault captures are independent new fixture bytes; no original audit is edited.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from document_ingestion.document_router import DocumentRouter
from eval.full_chain.contracts import EvaluationRunIdentity, canonical_hash
from eval.full_chain.domains.runtime_reliability import read_diagnostic_prefix
from eval.full_chain.report import emit_report
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import (DiagnosticEvent, SnapshotView, diagnostic_capture_complete,
    diagnostic_frame_valid, load_source_snapshot, original_diagnostic_frame)
from test_full_chain_eval_capture import FixedClient, gateway, request
from test_full_chain_eval_retrieval import capture_fixed_retrieval_snapshot, _real_pipeline


def _manifest(path, snapshot_id="correction-ir"):
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    blobs = sorted({row["payload_ref"]["relative_path"] for row in rows if row.get("payload_ref")})
    return {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": snapshot_id,
        "sources": [{"name": "diagnostics", "family": "diagnostics", "format": "diagnostic_jsonl",
            "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "representation_files": blobs}]}


def _safe_report(path, tmp_path):
    before = sha256(path.read_bytes()).hexdigest()
    original_files = {str(file): sha256(file.read_bytes()).hexdigest() for file in path.parent.rglob("*") if file.is_file()}
    snapshot = load_source_snapshot(_manifest(path), [path.parent])
    report = observe(snapshot, EvaluationRunIdentity("correction-ir", "fixed-rag"))
    output = tmp_path / "report"
    json_path, _ = emit_report(report, output, source_roots=[path.parent])
    machine = json.loads(json_path.read_text(encoding="utf-8"))
    assert sha256(path.read_bytes()).hexdigest() == before
    assert {str(file): sha256(file.read_bytes()).hexdigest() for file in path.parent.rglob("*") if file.is_file()} == original_files
    assert len(machine["edge_fidelity"]) == len(report.edge_fidelity)
    assert not any(f.code == "DOMAIN_READ_FAILURE" for f in report.findings)
    return snapshot, report, machine


def _mixed_capture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    session = DiagnosticSession(DiagnosticConfig(source / "capture", source, "correction-mixed", flush_records=1))
    item = {"child_chunk_id": "child-A", "chunk_id": "child-A", "parent_chunk_id": "parent-A",
        "doc_id": "doc-A", "tenant_id": "tenant-A", "index_version": "index-A"}
    client = FixedClient()
    with session:
        for key in ("R-DENSE", "R-BM25", "R-SOURCE-FUSION"):
            session.emit_pool({"evidence_key": key, "operation_ref": "rag-1", "run_id": "run-fixed",
                "query": "q", "round": 0, "declared_count": 1, "phase": "RESULT"}, [item])
        session.emit({"event_type": "I-INPUT-REQUEST", "operation_ref": "ingest-1", "payload": {
            "root_token": "root-A", "include_mode": "EXPLICIT", "requested_names": ["a.pdf"], "complete": True}})
        session.emit({"event_type": "I-PREFILTER", "operation_ref": "ingest-1", "payload": {"entries": [{
            "name": "a.pdf", "exists": True, "existence_reliable": True, "regular_file": True,
            "accessible": True, "selected": True, "supported": True}], "phase": "END", "complete": True}})
        session.emit({"event_type": "I-SOURCE", "operation_ref": "ingest-1", "payload": {
            "documents": [{"source_name": "a.pdf", "status": "success"}]}})
        assert gateway(client).generate(request()).success
    assert len(client.calls) == 1 and session.health()["lost_count"] == 0
    return session.path


def _rewrite(path, rows, *, resequence=True):
    events = [row for row in rows if row.get("event_type") != "SESSION_SEAL"]
    seals = [row for row in rows if row.get("event_type") == "SESSION_SEAL"]
    if resequence:
        for sequence, row in enumerate(events, 1):
            row["sequence"] = sequence
        for seal in seals:
            seal["last_sequence"] = seal["flushed_sequence"] = len(events)
    for row in events + seals:
        row["frame_sha256"] = canonical_hash({key: value for key, value in row.items() if key != "frame_sha256"})
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in events + seals))


def test_hydrated_material_preserves_formal_ingestion_and_retrieval_report(tmp_path):
    path = _mixed_capture(tmp_path)
    prefix = read_diagnostic_prefix(path, [path.parent])
    assert prefix["completeness"] == "COMPLETE"
    hydrated = next(row for row in prefix["events"] if row.get("material"))
    assert isinstance(hydrated, DiagnosticEvent) and diagnostic_frame_valid(hydrated) and hydrated.material_valid
    assert "material" not in original_diagnostic_frame(hydrated)
    original = hydrated.original_frame
    original["sequence"] = -1
    assert diagnostic_frame_valid(hydrated), "projection property must not expose mutable original bytes"
    direct = SnapshotView.from_records({"diagnostics": prefix["events"] + [prefix["seal"]]})
    assert diagnostic_capture_complete(direct)
    direct_report = observe(direct, EvaluationRunIdentity("direct-prefix", "fixed-rag"))
    assert "CAPTURE_INCOMPLETE" not in direct_report.limitations
    snapshot, report, machine = _safe_report(path, tmp_path)
    assert not snapshot.limitations and diagnostic_capture_complete(snapshot)
    assert "CAPTURE_INCOMPLETE" not in report.limitations
    coverage = next(metric for metric in report.metrics if metric.metric_id == "input_disposition_coverage")
    assert coverage.measurement_state == "MEASURED" and coverage.value == 1
    assert all(edge.assertion_result is True for edge in report.edge_fidelity if edge.edge_id == "X04")
    assert all(stage.measurement_state == "MEASURED" for stage in report.stage_quality
        if stage.payload.get("evidence_key") == "R-DENSE" and stage.evidence_refs)
    assert any(row.get("material") for row in snapshot.records("diagnostics"))
    assert machine["diagnostic_readiness"] == "NOT_ESTABLISHED"
    for metric in report.metrics:
        if metric.metric_id == "rank_quality":
            assert metric.measurement_state == "UNMEASURED"


def test_hydrated_material_tampering_cannot_reuse_verified_raw_frame(tmp_path):
    path = _mixed_capture(tmp_path)
    prefix = read_diagnostic_prefix(path, [path.parent])
    original_files = {str(file): sha256(file.read_bytes()).hexdigest() for file in path.parent.rglob("*") if file.is_file()}
    hydrated = next(row for row in prefix["events"] if row.get("material"))
    hydrated["material"]["prompt"] = "different derived model material"
    hydrated["source_frame_verified"] = True
    assert diagnostic_frame_valid(hydrated), "original encoded frame has not changed"
    assert not hydrated.material_valid
    snapshot = SnapshotView.from_records({"diagnostics": prefix["events"] + [prefix["seal"]]})
    report = observe(snapshot, EvaluationRunIdentity("mutated-hydrated-view", "fixed-rag"))
    assert not diagnostic_capture_complete(snapshot) and "CAPTURE_INCOMPLETE" in report.limitations
    assert all(edge.assertion_result is None for edge in report.edge_fidelity if edge.edge_id == "X04")
    assert {str(file): sha256(file.read_bytes()).hexdigest() for file in path.parent.rglob("*") if file.is_file()} == original_files


def test_raw_material_reference_with_caller_boolean_does_not_prove_blob_read(tmp_path):
    path = _mixed_capture(tmp_path)
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    # The raw frame hashes are valid, but no safe reader has dereferenced blobs.
    # Attaching an arbitrary envelope flag cannot fill in that missing proof.
    rows_with_refs = [row for row in rows if row.get("payload_ref")]
    for row in rows_with_refs:
        row["source_frame_verified"] = True
        row["frame_sha256"] = canonical_hash({key: value for key, value in row.items() if key != "frame_sha256"})
    assert rows_with_refs and all(diagnostic_frame_valid(row) for row in rows_with_refs)
    snapshot = SnapshotView.from_records({"diagnostics": rows})
    assert not diagnostic_capture_complete(snapshot)
    report = observe(snapshot, EvaluationRunIdentity("raw-material-refs", "fixed-rag"))
    assert "CAPTURE_INCOMPLETE" in report.limitations
    assert all(edge.assertion_result is None for edge in report.edge_fidelity if edge.edge_id == "X04")


@pytest.mark.parametrize("fault,reason", [("frame", "FRAME_HASH_MISMATCH"), ("blob", "REPRESENTATION_HASH_MISMATCH"),
    ("sequence", "SEQUENCE_GAP"), ("seal_hash", "SEAL_HASH_MISMATCH"), ("seal_missing", "NO_SEAL"),
    ("lost", "SEAL_INCOMPLETE_OR_MISMATCH"), ("pool_digest", None)])
def test_actual_integrity_faults_remain_incomplete_through_formal_report(fault, reason, tmp_path):
    path = _mixed_capture(tmp_path)
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if fault == "frame":
        rows[0]["operation_ref"] = "tampered"
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    elif fault == "blob":
        ref = next(row["payload_ref"] for row in rows if row.get("payload_ref"))
        (path.parent / ref["relative_path"]).write_bytes(b'{"tampered":true}')
    elif fault == "sequence":
        rows[0]["sequence"] += 1
        _rewrite(path, rows, resequence=False)
    elif fault == "seal_hash":
        rows[-1]["frame_sha256"] = "0" * 64
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    elif fault == "seal_missing":
        _rewrite(path, rows[:-1])
    elif fault == "lost":
        rows[-1]["lost_count"] = 1
        _rewrite(path, rows)
    else:
        next(row for row in rows if row.get("event_type") == "POOL_END")["end_digest"] = "0" * 64
        _rewrite(path, rows)
    snapshot, report, _ = _safe_report(path, tmp_path)
    if reason:
        assert any(reason in limitation for limitation in snapshot.limitations)
        assert not diagnostic_capture_complete(snapshot)
        assert "CAPTURE_INCOMPLETE" in report.limitations
    else:
        assert diagnostic_capture_complete(snapshot), "valid frame is separate from invalid finite-pool digest"
    assert all(edge.assertion_result is None for edge in report.edge_fidelity if edge.edge_id == "X04")
    assert all(stage.measurement_state != "MEASURED" for stage in report.stage_quality
        if stage.unit_id == "C04" and stage.payload.get("evidence_key") == "R-DENSE")


def _native_capture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _, output = capture_fixed_retrieval_snapshot(source, label="native-fixed")
    paths = list(source.rglob("*.jsonl"))
    assert len(paths) == 1 and output["context_pack"]["selected_results"]
    return paths[0]


def _pool_change(rows, key, operation=None, *, remove=False, mutation=None):
    begin = next(row for row in rows if row.get("event_type") == "POOL_BEGIN" and row.get("evidence_key") == key
        and row.get("phase", "RESULT") == "RESULT" and (operation is None or row.get("operation_ref") == operation))
    pool_id = begin["pool_id"]
    if remove:
        return [row for row in rows if row.get("pool_id") != pool_id]
    items = [row for row in rows if row.get("pool_id") == pool_id and row.get("event_type") == "POOL_ITEM"]
    if mutation == "empty":
        rows = [row for row in rows if row not in items]
        items = []
    else:
        mutation(items[0]["item"])
    begin["declared_count"] = len(items)
    end = next(row for row in rows if row.get("pool_id") == pool_id and row.get("event_type") == "POOL_END")
    end["actual_count"] = end["declared_count"] = len(items)
    end["end_digest"] = sha256(b"".join(json.dumps(row["item"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode() + b"\n" for row in items)).hexdigest()
    return rows


@pytest.mark.parametrize("edge_id", ["X03", "X04", "X05", "X06", "X07"])
@pytest.mark.parametrize("case", ["normal", "one_fault", "missing"])
def test_native_retrieval_edges_safe_snapshot_observe_report(edge_id, case, tmp_path):
    original_path = _native_capture(tmp_path)
    original_bytes = original_path.read_bytes()
    rows = [json.loads(line) for line in original_bytes.splitlines()]
    if case != "normal":
        missing = case == "missing"
        if edge_id == "X03":
            rows = _pool_change(rows, "R-PARENT", "parent_enriched", remove=missing,
                mutation=lambda item: item["metadata"].update(matched_child_chunk_ids=[]))
        elif edge_id == "X04":
            rows = _pool_change(rows, "R-SOURCE-FUSION", remove=missing, mutation="empty")
        elif edge_id == "X05":
            if missing:
                ids = {row["pool_id"] for row in rows if row.get("event_type") == "POOL_BEGIN"
                    and row.get("evidence_key") == "R-RERANK" and row.get("phase") == "RESULT"}
                rows = [row for row in rows if row.get("pool_id") not in ids]
            else:
                rows = _pool_change(rows, "R-RERANK", "initial_rerank_output", mutation=lambda item: item.update(parent_chunk_id="wrong-parent"))
        elif edge_id == "X06":
            rows = _pool_change(rows, "R-GOV", "governance_output", remove=missing,
                mutation=lambda item: item.update(child_chunk_id="wrong-child"))
        else:
            if missing:
                rows = [row for row in rows if row.get("event_type") != "R-PACK"]
            else:
                next(row for row in rows if row.get("event_type") == "R-PACK")["payload"]["context_pack"]["context"] = "context without selected evidence"
    case_path = original_path.parent / "case_capture.jsonl"
    _rewrite(case_path, deepcopy(rows))
    snapshot, report, machine = _safe_report(case_path, tmp_path)
    assert diagnostic_capture_complete(snapshot) and not snapshot.limitations
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == edge_id]
    assert edges
    expected = True if case == "normal" else False if case == "one_fault" else None
    assert expected in [edge.assertion_result for edge in edges], [(edge.reason, edge.payload) for edge in edges]
    if case == "normal":
        assert all(edge.assertion_result is True and edge.measurement_state == "MEASURED" for edge in edges)
    elif case == "missing":
        assert any(edge.assertion_result is None and edge.measurement_state == "UNMEASURED" for edge in edges)
    assert any(edge.source_refs for edge in edges)
    assert all(edge.payload["reference_quality_state"] == "UNMEASURED" for edge in edges)
    assert any(row["assertion_result"] is expected for row in machine["edge_fidelity"] if row["edge_id"] == edge_id)
    assert original_path.read_bytes() == original_bytes


def test_repeated_native_query_invocations_do_not_mix_operation_instances(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    session = DiagnosticSession(DiagnosticConfig(source / "capture", source, "repeated-native",
        flush_records=1, max_queue_events=1024))
    pipeline = _real_pipeline()
    with session:
        pipeline.run("same query", filter_expr=None, keyword_doc_ids=None)
        pipeline.run("same query", filter_expr=None, keyword_doc_ids=None)
    _, report, _ = _safe_report(session.path, tmp_path)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X04"]
    assert len(edges) == 4 and all(edge.assertion_result is True for edge in edges)
    assert len({edge.payload["invocation_identity"] for edge in edges}) == 2
    assert len({edge.source_key for edge in edges}) == 4


@pytest.mark.parametrize("mismatch", ["query", "round", "operation", "ambiguous"])
def test_explicit_endpoint_identity_mismatches_never_pair_by_last_record(mismatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    session = DiagnosticSession(DiagnosticConfig(source / "capture", source, "identity"))
    candidate = {"child_chunk_id": "one", "doc_id": "doc", "tenant_id": "tenant", "index_version": "v1"}
    with session:
        for repetition in range(2 if mismatch == "ambiguous" else 1):
            for key in ("R-DENSE", "R-BM25", "R-SOURCE-FUSION"):
                header = {"evidence_key": key, "operation_ref": "operation", "run_id": "run", "query": "q", "round": 0, "declared_count": 1}
                if key == "R-SOURCE-FUSION" and mismatch != "ambiguous":
                    header[{"query": "query", "round": "round", "operation": "operation_ref"}[mismatch]] = 1 if mismatch == "round" else "other"
                session.emit_pool(header, [candidate])
    _, report, _ = _safe_report(session.path, tmp_path)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X04"]
    assert edges and all(edge.assertion_result is None and edge.measurement_state == "UNMEASURED" for edge in edges)


def test_original_literal_material_and_caller_verification_flag_are_not_special_proof(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    session = DiagnosticSession(DiagnosticConfig(source / "capture", source, "literal"))
    with session:
        session.emit({"event_type": "literal", "material": {"literal_original_field": True}})
    raw = [json.loads(line) for line in session.path.read_bytes().splitlines()]
    literal = next(row for row in raw if row.get("event_type") == "literal")
    assert diagnostic_frame_valid(literal)
    assert original_diagnostic_frame(literal)["material"] == {"literal_original_field": True}
    literal["source_frame_verified"] = True
    assert not diagnostic_frame_valid(literal)
    report = observe(SnapshotView.from_records({"diagnostics": raw}), EvaluationRunIdentity("forged", "subject"))
    assert "CAPTURE_INCOMPLETE" in report.limitations
    # Standard dataclass report projection reconstructs dict subclasses safely.
    assert json.loads(json.dumps(asdict(DiagnosticEventContract(DiagnosticEvent({"x": 1})))))["row"] == {"x": 1}


from dataclasses import dataclass


@dataclass
class DiagnosticEventContract:
    row: dict


@pytest.mark.parametrize("case", ["normal", "one_fault", "missing"])
def test_native_X01_router_capture_safe_snapshot_observe_report(case, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    input_dir = source / "input"
    input_dir.mkdir()
    (input_dir / "A.pdf").write_bytes(b"finite fixed source; no parser is dispatched")
    session = DiagnosticSession(DiagnosticConfig(source / "capture", source, "native-X01"))
    with session:
        result = DocumentRouter().run(input_dir=input_dir, output_dir=source / "unused-output", dry_run=True,
            diagnostic_callback=session.emit)
    assert result.documents[0]["status"] == "planned" and not (source / "unused-output").exists()
    original = session.path.read_bytes()
    path = session.path
    if case != "normal":
        rows = [json.loads(line) for line in original.splitlines()]
        if case == "one_fault":
            for row in rows:
                if row.get("evidence_key") == "I-SOURCE":
                    payload = row.get("payload", {})
                    if "status" in payload:
                        payload["status"] = "unproven_outcome"
                    for document in payload.get("documents", []):
                        document["status"] = "unproven_outcome"
        else:
            rows = [row for row in rows if not (row.get("evidence_key") == "I-PREFILTER" and row.get("payload", {}).get("phase") == "END")]
        path = source / "case_capture.jsonl"
        _rewrite(path, rows)
    snapshot, report, machine = _safe_report(path, tmp_path)
    assert diagnostic_capture_complete(snapshot)
    edge = next(row for row in report.edge_fidelity if row.edge_id == "X01")
    expected = True if case == "normal" else False if case == "one_fault" else None
    assert edge.assertion_result is expected
    assert edge.measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert edge.source_refs and edge.target_refs
    assert next(row for row in machine["edge_fidelity"] if row["edge_id"] == "X01")["assertion_result"] is expected
    assert session.path.read_bytes() == original


def build_native_lineage_sources(source):
    """Read finite fixed unit/row fixtures and execute the original char chunker.

    Row assets are declared fixture inputs. No index builder, embedder, parser,
    OCR, vector database, or activation API is called.
    """
    from rag.plugins.chunkers.plugin import FixedParentChildChunkerPlugin
    from rag.store.parent_chunk_store import load_jsonl_dicts
    from rag.retriever.bm25_child_retriever import load_jsonl_dicts as load_bm25_rows
    from test_stage_c_structured_chunkers import _unit
    source.mkdir()
    input_dir = source / "input"
    input_dir.mkdir()
    raw_source = input_dir / "A.pdf"
    raw_source.write_bytes(b"finite source fixture used only for identity")
    report = DocumentRouter().run(input_dir=input_dir, output_dir=source / "unused-output", dry_run=True)
    document = report.documents[0]
    unit = _unit(document_id=document["doc_id"], strategy="fixed_parent_child", order=1,
        text="First fixed paragraph. Second fixed paragraph. Third fixed paragraph.")
    unit["extra"]["document_profile"]["source_sha256"] = document["source_sha256"]
    unit["extra"]["document_profile"]["classification"]["reviewed"] = False
    def write_rows(path, rows):
        path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in rows))
        return path
    normalized = write_rows(source / "normalized.jsonl", [unit])
    chunked = FixedParentChildChunkerPlugin(parent_chunk_size=100, parent_chunk_overlap=0,
        child_chunk_size=18, child_chunk_overlap=0, unit="char", deterministic_created_at="2026-10-05T00:00:00Z").chunk_records(load_jsonl_dicts(normalized))
    assert len(chunked.children) > 1
    chunks = write_rows(source / "children.jsonl", chunked.children)
    # Existing finite row assets are read by the original pure JSONL loaders.
    vector_rows = write_rows(source / "vector_rows.jsonl", chunked.children)
    bm25_rows = write_rows(source / "bm25_rows.jsonl", chunked.children)
    assert load_jsonl_dicts(vector_rows) == chunked.children
    assert load_bm25_rows(bm25_rows) == chunked.children
    router_report = source / "router_report.json"
    router_report.write_text(json.dumps(report.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    return {"router": ("document_router_report_v1", router_report, "json"),
        "normalized": ("normalized", normalized, "jsonl"), "children": ("chunks", chunks, "jsonl"),
        "vector_rows": ("vector_rows", vector_rows, "jsonl"), "bm25_rows": ("bm25_rows", bm25_rows, "jsonl")}


@pytest.mark.parametrize("case", ["normal", "one_fault", "missing"])
def test_native_X02_finite_loader_chunk_row_assets_safe_snapshot_observe_report(case, tmp_path):
    source = tmp_path / "source"
    files = build_native_lineage_sources(source)
    original = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    if case == "missing":
        del files["vector_rows"]
    elif case == "one_fault":
        rows = [json.loads(line) for line in files["vector_rows"][1].read_bytes().splitlines()]
        path = source / "fault_vector_rows.jsonl"
        path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in reversed(rows)))
        files["vector_rows"] = ("vector_rows", path, "jsonl")
    frozen = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    manifest = {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "native-X02-" + case,
        "sources": [{"name": name, "family": family, "format": fmt, "path": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest()} for name, (family, path, fmt) in files.items()]}
    snapshot = load_source_snapshot(manifest, [source])
    assert not snapshot.limitations
    report = observe(snapshot, EvaluationRunIdentity("native-X02-" + case, "fixed-lineage"))
    assert not any(f.code == "DOMAIN_READ_FAILURE" for f in report.findings)
    edge = next(row for row in report.edge_fidelity if row.edge_id == "X02")
    expected = True if case == "normal" else False if case == "one_fault" else None
    assert edge.assertion_result is expected, edge
    assert edge.measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert edge.source_refs and edge.target_refs
    assert edge.payload["build_activation_state"] == "UNMEASURED" and edge.payload["reference_quality_state"] == "UNMEASURED"
    json_path, _ = emit_report(report, tmp_path / "report", source_roots=[source])
    machine = json.loads(json_path.read_text(encoding="utf-8"))
    assert next(row for row in machine["edge_fidelity"] if row["edge_id"] == "X02")["assertion_result"] is expected
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in original.items())
    assert {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()} == frozen
