from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import pytest

from document_ingestion.document_router import DocumentRouter
from document_ingestion.exceptions import DocIdCollisionError, SourceIdentityError
from eval.full_chain.domains.ingestion_index import (check_source_asset_edges, classify_input_disposition,
    evaluate_ingestion_index, extract_ingestion, stable_span_comparability, vector_integrity)
from eval.full_chain.snapshot import SnapshotView
from core.diagnostics import DiagnosticConfig, DiagnosticSession


DESIGN_CASES = tuple(f"I{number:02d}" for number in range(1, 17))


def _capture(root, output, include=None, **kwargs):
    events = []
    result, error = None, None
    try:
        result = DocumentRouter().run(input_dir=root, output_dir=output, include=include,
            dry_run=True, diagnostic_callback=events.append, **kwargs)
    except Exception as caught:
        error = caught
    return result, error, extract_ingestion(SnapshotView.from_records({"diagnostic_events": events}))


def _subjects(batch):
    return next(stage.payload["subjects"] for stage in batch.stages if stage.operation_id == "U0_U4_POPULATION")


def _metric(batch, name):
    return next(item for item in batch.metrics if item.metric_id == name)


@pytest.mark.parametrize("case", DESIGN_CASES, ids=DESIGN_CASES)
def test_ingestion_design_cases(case, tmp_path):
    root = tmp_path / "input"; root.mkdir()
    (root / "A.pdf").write_bytes(b"fixed-pdf")
    if case in {"I01", "I11", "I12", "I13", "I14", "I15", "I16"}:
        if case == "I01":
            (root / "B.pdf").write_bytes(b"fixed-pdf")
            result, error, batch = _capture(root, tmp_path / "output")
            assert error is None and len(result.documents) == 2
            assert len({item["subject_id"] for item in _subjects(batch)}) == 2
            assert len({item["source_sha256"] for item in result.documents}) == 1
            collision = {"error": {"type": "DocIdCollisionError"}}
            entry = {"exists": True, "regular_file": True, "supported": True}
            assert classify_input_disposition(entry, collision) == "IDENTITY_COLLISION"
        elif case == "I11":
            (root / "notes.xyz").write_text("unsupported")
            result, error, batch = _capture(root, tmp_path / "output")
            assert error is None and len(result.documents) == 1
            assert {item["name"]: item["disposition"] for item in _subjects(batch)} == {"A.pdf": "PLANNED_DRY_RUN", "notes.xyz": "UNSUPPORTED_FORMAT"}
            assert _metric(batch, "input_disposition_coverage").denominator == 2
        elif case == "I12":
            (root / "notes.xyz").write_text("unsupported")
            _, error, batch = _capture(root, tmp_path / "output", ["notes.xyz"])
            assert type(error) is FileNotFoundError
            assert next(item for item in _subjects(batch) if item["name"] == "notes.xyz")["disposition"] == "UNSUPPORTED_FORMAT"
        elif case == "I13":
            _, error, batch = _capture(root, tmp_path / "output", ["missing.pdf"])
            assert type(error) is FileNotFoundError
            assert next(item for item in _subjects(batch) if item["name"] == "missing.pdf")["disposition"] == "MISSING_REQUESTED"
        elif case == "I14":
            (root / "B.pdf").write_bytes(b"other")
            result, error, batch = _capture(root, tmp_path / "output", ["A.pdf"])
            assert error is None and len(result.documents) == 1
            assert next(item for item in _subjects(batch) if item["name"] == "B.pdf")["disposition"] == "SKIPPED_NOT_SELECTED"
            assert _metric(batch, "input_disposition_coverage").denominator == 1
        elif case == "I15":
            batch = extract_ingestion(SnapshotView.from_records({"ingestion": {"documents": [{"source_name": "A.pdf", "status": "success"}]}}))
            assert _metric(batch, "input_disposition_coverage").measurement_state == "UNMEASURED"
            assert _metric(batch, "input_disposition_coverage").denominator is None
            assert _metric(batch, "routed_outcome_counts").value == {"success": 1}
        else:
            entry = {"exists": True, "regular_file": True, "supported": True}
            assert classify_input_disposition(entry, {"status": "failed", "error": {"type": "SourceIdentityError"}}) == "SOURCE_IDENTITY_MISMATCH"
            assert classify_input_disposition(entry, {"status": "failed", "error": {"type": "DocIdCollisionError"}}) == "IDENTITY_COLLISION"
        return
    fact = {"subject": "equipment", "value": 12, "unit": "台", "negated": True, "relation": "upper_bound"}
    if case == "I02":
        second = {**fact, "subject": "table_row_2"}
        checks = check_source_asset_edges(source_facts=[fact, second], candidate_facts=[fact], synthetic=True)
        assert checks["source_fact_retention"]["value"] == .5
        assert checks["source_fact_retention"]["missing"]
    elif case == "I03":
        altered = {**fact, "unit": None, "negated": False}
        checks = check_source_asset_edges(source_facts=[fact], candidate_facts=[altered], synthetic=True)
        assert checks["source_fact_retention"]["value"] == 0
        assert checks["source_fact_retention"]["added"]
    elif case == "I04":
        assert check_source_asset_edges()["span_coverage"]["state"] == "UNMEASURED"
    elif case == "I05":
        actual_token_ids = list(range(512))
        assert len(actual_token_ids) == 512
        checks = check_source_asset_edges(tokenizer_evidence={"trusted_asset_sha256": "synthetic-fixed-tokenizer",
            "actual_input_digest_verified": True, "visible_token_range": [0, len(actual_token_ids)], "required_token_ranges": [[510, 520]]})
        assert checks["evidence_truncation"]["value"] == 1
        assert check_source_asset_edges(tokenizer_evidence={"chars": 10000})["evidence_truncation"]["state"] == "UNMEASURED"
    elif case == "I06":
        checks = check_source_asset_edges(expected_rows=["child-A", "child-B"], actual_rows=["child-B", "child-A"])
        assert checks["row_identity"] is False
        assert check_source_asset_edges(expected_rows=["child-A"], actual_rows=["child-A"])["row_identity"] is True
        actual = vector_integrity([[1, 0], [0, 1]], ["child-A", "child-B"], ["child-B", "child-A"], expected_dim=2, normalized=True)
        assert actual["assertion"] is False and actual["checks"]["finite"] is True
    elif case == "I07":
        assert check_source_asset_edges(dense_scope={"tenant": "one"}, bm25_scope={"tenant": "two"})["scope_equality"] is False
    elif case == "I08":
        span = {"doc_id": "doc", "source_sha256": "abc", "unit_id": "unit", "start": 1, "end": 9}
        assert stable_span_comparability({**span, "chunk_id": "old"}, {**span, "chunk_id": "new"}) == "COMPARABLE"
        assert stable_span_comparability(span, {"chunk_id": "new"}) == "NOT_COMPARABLE"
    elif case == "I09":
        assert check_source_asset_edges(profiles=[{"document_id": "gold", "is_gold_reference": True, "should_index": True}])["gold_leakage"] == ["gold"]
    elif case == "I10":
        batch = evaluate_ingestion_index(SnapshotView.from_records({"index_manifest": {"child_count": 12}}))
        assert any(stage.unit_id == "A07" and stage.measurement_state == "MEASURED" for stage in batch.stages)
        assert check_source_asset_edges()["build_process_state"] == "UNMEASURED"


@pytest.mark.parametrize("selection", [None, [], "generator"], ids=["include_none", "include_empty", "include_one_shot"])
def test_router_optional_capture_preserves_selection_and_single_scan(selection, tmp_path, monkeypatch):
    root = tmp_path / "input"; root.mkdir()
    (root / "A.pdf").write_bytes(b"A"); (root / "B.pdf").write_bytes(b"B")
    consumed = []
    def names():
        for name in ["A.pdf"]:
            consumed.append(name); yield name
    include = names() if selection == "generator" else selection
    original = Path.iterdir; scans = []
    def counted(path):
        if path == root:
            scans.append(path)
        return original(path)
    monkeypatch.setattr(Path, "iterdir", counted)
    result, error, batch = _capture(root, tmp_path / "output", include)
    assert error is None and len(scans) == 1
    assert len(result.documents) == {None: 2, "generator": 1}.get(selection if isinstance(selection, str) or selection is None else "empty", 0)
    if selection == "generator":
        assert consumed == ["A.pdf"]
    if selection == []:
        metric = _metric(batch, "input_disposition_coverage")
        assert metric.measurement_state == "N/A" and metric.value is None and metric.denominator == 0


def test_enumeration_interruption_and_nonregular_never_become_missing(tmp_path, monkeypatch):
    root = tmp_path / "input"; root.mkdir(); (root / "folder.pdf").mkdir()
    _, error, batch = _capture(root, tmp_path / "output", ["folder.pdf"])
    assert type(error) is FileNotFoundError
    subject = next(item for item in _subjects(batch) if item["name"] == "folder.pdf")
    assert subject["disposition"] == "UNKNOWN_EVIDENCE_GAP" and subject["reason"] == "NOT_REGULAR_FILE"
    original = Path.iterdir
    def denied(path):
        if path == root:
            raise PermissionError("fixed denial")
        return original(path)
    monkeypatch.setattr(Path, "iterdir", denied)
    _, error, batch = _capture(root, tmp_path / "output", ["missing.pdf"])
    assert type(error) is PermissionError
    assert _metric(batch, "input_disposition_coverage").measurement_state == "UNMEASURED"
    assert next(item for item in _subjects(batch) if item["name"] == "missing.pdf")["disposition"] == "UNKNOWN_EVIDENCE_GAP"


@pytest.mark.parametrize("error_type", [DocIdCollisionError, SourceIdentityError], ids=["collision", "source_mismatch"])
def test_real_router_identity_failure_preserves_subject_and_original_result(error_type, tmp_path):
    root = tmp_path / "input"; root.mkdir(); (root / "A.docx").write_bytes(b"A")
    events = []
    class RejectIdentity:
        def __init__(self, output): pass
        def claim(self, **kwargs): raise error_type("fixed identity failure")
    router = DocumentRouter(identity_store_factory=RejectIdentity)
    result = router.run(input_dir=root, output_dir=tmp_path / "output", diagnostic_callback=events.append)
    assert result.failure_count == 1 and result.documents[0]["error"]["type"] == error_type.__name__
    batch = extract_ingestion(SnapshotView.from_records({"diagnostic_events": events}))
    assert len(_subjects(batch)) == 1
    assert _subjects(batch)[0]["disposition"] == ("IDENTITY_COLLISION" if error_type is DocIdCollisionError else "SOURCE_IDENTITY_MISMATCH")


def test_diagnostic_callback_failure_does_not_change_router(tmp_path):
    root = tmp_path / "input"; root.mkdir(); (root / "A.pdf").write_bytes(b"A")
    def broken(_): raise RuntimeError("capture failed")
    result = DocumentRouter().run(input_dir=root, output_dir=tmp_path / "output", dry_run=True, diagnostic_callback=broken)
    assert result.documents[0]["status"] == "planned" and not (tmp_path / "output").exists()


@pytest.mark.parametrize("case", ["normal", "unsupported", "missing", "identity_collision"], ids=["normal", "unsupported", "missing", "identity_collision"])
def test_real_router_capture_on_off_preserves_results_errors_and_manifest_bytes(case, tmp_path, monkeypatch):
    import document_ingestion.document_router as router_module
    from test_document_ingestion_router import FakeWordPipeline
    monkeypatch.setattr(router_module, "datetime", SimpleNamespace(now=lambda _zone: datetime(2026, 10, 5, tzinfo=timezone.utc)))
    root = tmp_path / "input"; root.mkdir(); (root / "A.docx").write_bytes(b"source")
    (root / "notes.xyz").write_text("unsupported")
    include = ["notes.xyz"] if case == "unsupported" else (["missing.docx"] if case == "missing" else ["A.docx"])
    class Reject:
        def __init__(self, output): pass
        def claim(self, **kwargs): raise DocIdCollisionError("fixed collision")
    def build_router():
        options = {"word_pipeline_factory": FakeWordPipeline}
        if case == "identity_collision": options["identity_store_factory"] = Reject
        return DocumentRouter(**options)
    def execute(output, callback=None):
        try: return build_router().run(input_dir=root, output_dir=output, include=include, diagnostic_callback=callback), None
        except Exception as exc: return None, (type(exc), str(exc))
    expected, expected_error = execute(tmp_path / "disabled")
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "router_equivalence"))
    with session:
        actual, actual_error = execute(tmp_path / "enabled", session.emit)
    assert actual_error == expected_error
    if actual is not None:
        assert asdict(actual) == asdict(expected)
        for relative in ("reports/document_ingestion_report.json", "document_manifest.jsonl"):
            assert (tmp_path / "disabled" / relative).read_bytes() == (tmp_path / "enabled" / relative).read_bytes()
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    snapshot = SnapshotView.from_records({"diagnostic_events": rows})
    assert _metric(extract_ingestion(snapshot), "input_disposition_coverage").measurement_state == "MEASURED"
    unsealed = SnapshotView.from_records({"diagnostic_events": [row for row in rows if row.get("event_type") != "SESSION_SEAL"]})
    assert _metric(extract_ingestion(unsealed), "input_disposition_coverage").measurement_state == "UNMEASURED"


def test_encoding_service_fixed_model_boundary_is_single_call_and_does_not_copy_vectors(tmp_path, monkeypatch):
    import numpy as np
    from rag.embed.embedding_service import encode_texts_with_model
    class FixedEmbedder:
        calls = 0
        def __init__(self, **kwargs): self.model = SimpleNamespace(max_seq_length=8)
        def encode_texts(self, texts):
            type(self).calls += 1
            return np.asarray([[1., 0.] for _ in texts])
    monkeypatch.setitem(sys.modules, "rag.embed.embedder", SimpleNamespace(TextEmbedder=FixedEmbedder))
    expected = encode_texts_with_model(["one", "two"], "fixed_fake", "cpu", 2)
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "encoding", tmp_path, "encoding"))
    with session:
        actual = encode_texts_with_model(["one", "two"], "fixed_fake", "cpu", 2)
    assert FixedEmbedder.calls == 2 and np.array_equal(expected[0], actual[0]) and expected[1:] == actual[1:]
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert not any("values" in row.get("payload", {}) or "vectors" in row.get("payload", {}) for row in rows)
    assert any(row.get("payload", {}).get("tokenizer_visible_input") == "NOT_RECORDED" for row in rows)


def test_unreviewed_fact_candidate_does_not_become_independent_reference():
    fact = {"subject": "plant", "value": 12, "unit": "台", "negated": False, "relation": "upper_bound"}
    assert check_source_asset_edges(source_facts=[fact], candidate_facts=[fact])["source_fact_retention"]["state"] == "UNMEASURED"


def ingestion_lineage_snapshot(*, mutation=None):
    source = {"source_sha256": "a" * 64, "scope": {"tenant_id": "tenant-1", "kb_id": "kb-1"},
        "child_ids": ["child-1", "child-2"], "parent_child_map": {"child-1": "parent-1", "child-2": "parent-1"}}
    target = {"parsed_source_sha256": "a" * 64, "scope": {"tenant_id": "tenant-1", "kb_id": "kb-1"},
        "vector_row_child_ids": ["child-1", "child-2"], "bm25_child_ids": ["child-1", "child-2"],
        "parent_child_map": {"child-1": "parent-1", "child-2": "parent-1"}}
    if mutation == "parser": target["parsed_source_sha256"] = "b" * 64
    elif mutation == "chunk": target["parent_child_map"]["child-2"] = "wrong-parent"
    elif mutation == "row": target["vector_row_child_ids"] = ["child-2", "child-1"]
    elif mutation == "scope": target["scope"]["tenant_id"] = "other-tenant"
    elif mutation == "missing": del target["vector_row_child_ids"]
    return SnapshotView.from_records({"ingestion_lineage": {"schema_version": "ingestion_lineage_v1",
        "source": source, "target": target, "assertion_result": True}})


@pytest.mark.parametrize("mutation", [None, "parser", "chunk", "row", "scope", "missing"], ids=["normal", "parser_mismatch", "chunk_mismatch", "row_swap", "scope_mismatch", "missing_endpoint_field"])
def test_x02_extracts_actual_source_target_and_computes_oracle(mutation):
    batch = extract_ingestion(ingestion_lineage_snapshot(mutation=mutation))
    edge = next(edge for edge in batch.edges if edge.edge_id == "X02")
    assert edge.source_refs and edge.target_refs
    if mutation == "missing":
        assert edge.measurement_state == "UNMEASURED" and edge.assertion_result is None
    else:
        assert edge.measurement_state == "MEASURED" and edge.assertion_result is (mutation is None)


@pytest.mark.parametrize("mutation", [None, "unknown_routing", "missing_prefilter"], ids=["normal", "unknown_routing_endpoint", "missing_prefilter_endpoint"])
def test_x01_reads_actual_router_endpoints_and_computes_coverage(mutation, tmp_path):
    root = tmp_path / "input"; root.mkdir(); (root / "A.pdf").write_bytes(b"fixed-source")
    events = []
    DocumentRouter().run(input_dir=root, output_dir=tmp_path / "output", dry_run=True, diagnostic_callback=events.append)
    if mutation == "unknown_routing":
        for event in events:
            if event.get("evidence_key") == "I-SOURCE":
                if "status" in event["payload"]:
                    event["payload"]["status"] = "unproven_outcome"
                for row in event["payload"].get("documents", []):
                    row["status"] = "unproven_outcome"
    elif mutation == "missing_prefilter":
        events = [event for event in events if not (event.get("evidence_key") == "I-PREFILTER" and event["payload"].get("phase") == "END")]
    batch = extract_ingestion(SnapshotView.from_records({"diagnostic_events": events}))
    edge = next(edge for edge in batch.edges if edge.edge_id == "X01")
    assert edge.source_refs and edge.target_refs
    if mutation == "missing_prefilter":
        assert edge.measurement_state == "UNMEASURED" and edge.assertion_result is None
    else:
        assert edge.measurement_state == "MEASURED" and edge.assertion_result is (mutation is None)
