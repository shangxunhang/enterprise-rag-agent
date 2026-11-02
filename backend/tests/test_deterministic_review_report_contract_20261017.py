"""Saved Canonical fact replay and pure Report contracts, without a Mainline Run.

The source receipt below is explicitly an isolated fixture. The historical Run
predates Writer Review Copy; these tests neither create its missing receipt nor
claim that the old Run was rerun. Original historical files are never opened.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from apps.enterprise_document.schemas.post_write.control import PostWriteControlDecisionSchema
from apps.enterprise_document.services.agent_rag_v1 import deterministic_review_report as report
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.persistence import (
    ArtifactStateStore, ControlDecisionStore, DocumentBindingStore,
    GateDecisionStore, PostWriteEntryStore, ValidationStore,
    PostWritePersistenceIntegrityError,
)
from test_writer_review_copy_c3_production_20261017_r03 import protected_directory


PACKAGE = Path(__file__).resolve().parents[2] / "docs" / "sdc_stage2_deterministic_review_report_implementation_20261017_r01"
CAPTURE = PACKAGE / "10_HISTORICAL_CANONICAL_FACTS.json"
CAPTURE_SHA256 = "418cf22966df11d0c414acddf525826ce9a1e878298c248f36323007adfda7f9"


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class _ReadOnlyDatabase:
    """Borrow exact-read transactions; deliberately has no write/init methods."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _exact_read_connection(self, connection=None):
        if connection is not None:
            assert connection.in_transaction
            yield connection
            return
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.close()

    read_transaction = _exact_read_connection


def _quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


@pytest.fixture(scope="module")
def historical_fixture():
    raw = CAPTURE.read_bytes()
    assert _sha(raw) == CAPTURE_SHA256
    capture = json.loads(raw)
    configured = os.environ.get("RPT_TEST_ROOT")
    if configured:
        parent = Path(configured)
    elif (PACKAGE / "14_TEST_ROOT.json").exists():
        parent = Path(json.loads((PACKAGE / "14_TEST_ROOT.json").read_text(encoding="utf-8"))["test_root"])
    else:
        parent = Path(r"E:\rag-agent-writer-copy-native")
    root = protected_directory(parent / ("contract-" + uuid.uuid4().hex))
    database_path = root / "isolated-saved-facts.sqlite3"
    with sqlite3.connect(database_path) as connection:
        # Rebuild only an isolated test database from captured physical columns.
        # No PostWriteSqliteDatabase constructor or old Owner is ever called.
        for name, table in capture["sqlite_tables"].items():
            columns = table["columns"]
            parts = []
            for column in columns:
                declaration = _quoted(column["name"]) + " " + column["type"]
                if column["notnull"]:
                    declaration += " NOT NULL"
                if column["dflt_value"] is not None:
                    declaration += " DEFAULT " + column["dflt_value"]
                parts.append(declaration)
            primary = sorted((column for column in columns if column["pk"]), key=lambda column: column["pk"])
            if primary:
                parts.append("PRIMARY KEY (" + ",".join(_quoted(column["name"]) for column in primary) + ")")
            connection.execute("CREATE TABLE " + _quoted(name) + " (" + ",".join(parts) + ")")
            names = [column["name"] for column in columns]
            connection.executemany("INSERT INTO " + _quoted(name) + " (" + ",".join(map(_quoted, names))
                + ") VALUES (" + ",".join("?" for _ in names) + ")",
                [tuple(row[name] for name in names) for row in table["rows"]])

    database = _ReadOnlyDatabase(database_path)
    post = SimpleNamespace(database=database, artifact_store=ArtifactStateStore(database),
        entry_store=PostWriteEntryStore(database), gate_store=GateDecisionStore(database),
        control_store=ControlDecisionStore(database), validation_store=ValidationStore(database),
        binding_store=DocumentBindingStore(database))
    canonical = capture["canonical"]
    section_entry = next(entry for entry in canonical["entries"] if entry["identity"]["entry_scope"] == "SECTION")
    identity = section_entry["identity"]
    artifact = next(artifact for artifact in canonical["artifacts"] if artifact["artifact_ref"] == identity["artifact_state_ref"])
    body = ArtifactCanonicalPayloadMaterializer.business_payload(artifact["canonical_payload_ref"])
    source_bytes = body["content"].encode("utf-8")
    source = {"task_id": capture["source_task_id"], "run_id": capture["source_run_id"],
        "document_id": capture["source_document_id"], "source_snapshot_sha256": _sha(b"isolated-historical-source-fixture"),
        "markdown_sha256": _sha(source_bytes), "evidence_level": "ISOLATED_SOURCE_FIXTURE_NOT_HISTORICAL_WRITER_COPY",
        "required_sections": [{"section_id": identity["section_id"], "section_title": body["section_title"],
            "section_sha256": _sha(source_bytes), "writer_execution_ref": identity["writer_execution_ref"],
            "writer_invocation_ref": identity["writer_invocation_ref"]}]}
    document_control = next(control for control in canonical["controls"] if control["input_manifest"]["artifact_scope"] == "DOCUMENT")
    document_ref = PostWriteControlDecisionSchema.model_validate(document_control).as_ref().model_dump(mode="json")
    result = SimpleNamespace(post=post, source=source, source_bytes=source_bytes, document_ref=document_ref,
        canonical=canonical, database_path=database_path, database_sha256=_sha(database_path.read_bytes()), capture=capture)
    yield result
    assert _sha(database_path.read_bytes()) == result.database_sha256
    assert _sha(CAPTURE.read_bytes()) == CAPTURE_SHA256


def _collect(fixture, *, source=None, document_ref=True):
    return report.collect_review_results(fixture.post, source_receipt=source or copy.deepcopy(fixture.source),
        document_control_ref=fixture.document_ref if document_ref is True else document_ref)


def _render_bundle(fixture, collected=None):
    bundle = collected if collected is not None else _collect(fixture)
    manifest = next(artifact for artifact in fixture.capture["release_artifacts"]
        if artifact["decoded_json"] and artifact["decoded_json"].get("schema_version") == "post_write_release_manifest_v1")
    return {**bundle, "source_receipt": copy.deepcopy(fixture.source),
        "terminal": {"execution_status": "success", "evidence_level": "SAVED_EXECUTION_RECEIPT"},
        "publication": {"state": "SAVED_COMMITTED_MANIFEST", "release_mode": manifest["decoded_json"]["release_mode"],
            "release_id": manifest["decoded_json"]["release_id"], "manifest_sha256": manifest["decoded_content_sha256"]}}


def test_saved_two_scope_truth_and_condition_only_fact(historical_fixture):
    """RPT-01/02: exact saved chains; SECTION failure survives DOCUMENT PASS."""
    result = _collect(historical_fixture)
    section, document = result["sections"][0], result["document"]
    assert result["completeness"] == "COMPLETE"
    assert section["gate"]["overall_quality_state"] == "FAIL"
    assert document["gate"]["overall_quality_state"] == "PASS"
    assert section["control"]["enforcement_action"] == "OUTPUT_BEST_EFFORT"
    assert section["gate"]["blocking_fact_refs"] == []
    condition = next(item for item in section["gate"]["quality_policy_projection"]["condition_results"]
        if item["dimension_id"] == "consistency")
    assert condition["state"] == "VIOLATED"
    fail = next(fact for fact in section["facts"] if fact["semantic_state"] == "FAIL")
    assert fail["fact_id"] == "validation-fact:artifact-consistency:3ab71739d0eebda8ed04e45f1d17796680ffc6bcac6afae5c275514bdf20f839"
    assert fail["fact_sha256"] == "3221f4e1c6ba3c114e8addb6f6d206fdff51f00974c243ab184f13b503fb8cfd"
    assert any(ref["fact_id"] == fail["fact_id"] for ref in condition["consumed_fact_refs"])
    text = report.render_review_report(_render_bundle(historical_fixture, result)).decode("utf-8")
    assert "Overall Quality: FAIL" in text and "Overall Quality: PASS" in text
    assert "VIOLATED" in text and fail["fact_id"] in text and "STANDARD" in text


def test_partial_preserves_committed_fail_and_unknown_scope(historical_fixture):
    """RPT-04: no document proof and an absent Section never imply PASS/NOT_RUN."""
    source = copy.deepcopy(historical_fixture.source)
    absent = copy.deepcopy(source["required_sections"][0])
    absent.update(section_id="uncommitted-fixture-section", section_title="Uncommitted fixture Section")
    source["required_sections"].append(absent)
    result = _collect(historical_fixture, source=source, document_ref=None)
    assert result["completeness"] == "PARTIAL" and result["document"] is None
    assert result["sections"][0]["gate"]["overall_quality_state"] == "FAIL"
    assert result["sections"][1]["status"] == "COMPLETION_NOT_ESTABLISHED"
    assert "gate" not in result["sections"][1]
    bundle = _render_bundle(historical_fixture, result)
    bundle.update(source_receipt=source, terminal={"execution_status": "failed", "technical_error": "test-only timeout"})
    text = report.render_review_report(bundle).decode("utf-8")
    assert "PARTIAL" in text and "failed" in text and "test-only timeout" in text
    assert "COMPLETION_NOT_ESTABLISHED / UNVERIFIABLE" in text
    assert "NOT_RUN" not in json.dumps(result)


def test_foreign_run_artifact_scope_hash_and_writer_refs_refuse(historical_fixture):
    """RPT-07: all adversarial cases use the isolated exact persisted set."""
    for field, value in (("run_id", "foreign-run"), ("document_id", "foreign-document")):
        source = copy.deepcopy(historical_fixture.source)
        source[field] = value
        with pytest.raises(report.ReviewReportUnavailable):
            _collect(historical_fixture, source=source)
    for field in ("section_sha256", "writer_execution_ref", "writer_invocation_ref"):
        source = copy.deepcopy(historical_fixture.source)
        row = source["required_sections"][0]
        row[field] = "0" * 64 if field == "section_sha256" else {"foreign": "wrong-artifact-binding"}
        with pytest.raises(report.ReviewReportUnavailable):
            _collect(historical_fixture, source=source)
    wrong_hash = {**historical_fixture.document_ref, "control_decision_hash": "0" * 64}
    with pytest.raises(report.ReviewReportUnavailable):
        _collect(historical_fixture, document_ref=wrong_hash)
    section = next(control for control in historical_fixture.canonical["controls"] if control["input_manifest"]["artifact_scope"] == "SECTION")
    wrong_scope = PostWriteControlDecisionSchema.model_validate(section).as_ref().model_dump(mode="json")
    with pytest.raises(report.ReviewReportUnavailable):
        _collect(historical_fixture, document_ref=wrong_scope)


def test_pure_renderer_order_templates_and_unverified_locator(historical_fixture):
    """RPT-03/05/06: renderer-only states are not new persisted owner verdicts."""
    bundle = _render_bundle(historical_fixture)
    original = bundle["sections"][0]
    sections = []
    for index, state in enumerate(("FAIL", "UNRESOLVED", "WARNING")):
        section = copy.deepcopy(original)
        section.update(section_id="renderer-only-" + str(index), title="Renderer fixture " + str(index))
        section["gate"]["overall_quality_state"] = state
        section["gate"]["dimension_result_projections"][0]["state"] = state
        section["gate"]["dimension_result_projections"][0]["warning_fact_refs"] = ["renderer-only-warning"]
        section["gate"]["dimension_result_projections"][0]["unresolved_reason_refs"] = ["renderer-only-unresolved"]
        sections.append(section)
    bundle["sections"] = sections
    first = report.render_review_report(bundle)
    reordered = copy.deepcopy(bundle)
    for section in reordered["sections"]:
        section["facts"].reverse()
        section["completion"]["items"].reverse()
        section["gate"]["dimension_result_projections"].reverse()
    # Results hashes preserve the exact input set, including input list order;
    # compare presentation independently after its source-hash metadata line.
    second = report.render_review_report(reordered)
    def without_input_hashes(body):
        return [line for line in body.decode("utf-8").splitlines()
            if not line.startswith("- Results SHA256:") and not line.startswith("- 来源集合 SHA256:")]
    assert without_input_hashes(first) == without_input_hashes(second)
    text = first.decode("utf-8")
    assert text.index("Renderer fixture 0") < text.index("Renderer fixture 1") < text.index("Renderer fixture 2")
    assert all(advice in text for advice in report._ADVICE.values())
    assert "LOCATOR_NOT_PROVEN" in text and "MODEL_DERIVED" in text
    assert "WARNING FactRefs" in text and "UNRESOLVED" in text
    assert "original_quote" not in text and "paragraph_offset" not in text


def test_byte_idempotency_version_binding_and_zero_model(historical_fixture, monkeypatch):
    """Pure renderer coverage for RPT-08/10; file publication is Native-only."""
    from model_gateway.fake_llm_client import FakeLLMClient
    def forbidden(*args, **kwargs):
        raise AssertionError("Report attempted a model call or Writer Copy write")
    monkeypatch.setattr(FakeLLMClient, "generate", forbidden)
    monkeypatch.setattr(report.wc, "seal_local_writer_copy", forbidden)
    bundle = _render_bundle(historical_fixture)
    before = _sha(historical_fixture.database_path.read_bytes())
    first = report.render_review_report(bundle)
    assert first == report.render_review_report(copy.deepcopy(bundle))
    changed = copy.deepcopy(bundle)
    changed["terminal"]["technical_error"] = "renderer-only additional saved result version"
    assert report.render_review_report(changed) != first
    receipt = {"writer_markdown_sha256": historical_fixture.source["markdown_sha256"]}
    assert report.report_applies_to(receipt, historical_fixture.source_bytes)
    assert not report.report_applies_to(receipt, historical_fixture.source_bytes + b"\nengineer edit")
    assert _sha(historical_fixture.database_path.read_bytes()) == before


def test_persisted_foreign_fact_and_condition_rows_rejected_at_store_integrity(historical_fixture):
    """RPT-07: corrupt only fresh copies; disclose the actual early refusal layer.

    These cases retain the original hashes. They prove the existing Store's
    persisted-row/hash boundary rejects tampering, and do not claim to test a
    hash-consistent foreign semantic verdict or recompute any quality result.
    """
    fixture = historical_fixture
    original_sha = _sha(fixture.database_path.read_bytes())
    fail = next(fact for fact in fixture.canonical["facts"] if fact["semantic_state"] == "FAIL")
    document_fact = next(fact for fact in fixture.canonical["facts"]
        if fact["validation_run_id"] != fail["validation_run_id"])
    section_gate = next(gate for gate in fixture.canonical["gates"]
        if gate["subject_key"]["validation_scope"] == "SECTION")
    document_ref = next(ref for ref in next(gate for gate in fixture.canonical["gates"]
        if gate["subject_key"]["validation_scope"] == "DOCUMENT")["gate_input_manifest"]["validation_fact_refs"]
        if ref["fact_id"] == document_fact["fact_id"])
    observed = []
    for case in ("fact_foreign_validation_run", "fact_foreign_subject", "fact_wrong_artifact_target", "condition_consumes_document_scope_fact"):
        case_root = protected_directory(fixture.database_path.parent.parent / ("contract-corrupt-" + uuid.uuid4().hex))
        case_path = case_root / "tampered-isolated-facts.sqlite3"
        with case_path.open("xb") as stream:
            stream.write(fixture.database_path.read_bytes())
        with sqlite3.connect(case_path) as connection:
            if case.startswith("fact_"):
                raw = json.loads(connection.execute("SELECT fact_json FROM validation_facts WHERE fact_id=?", (fail["fact_id"],)).fetchone()[0])
                if case == "fact_foreign_validation_run":
                    raw["validation_run_id"] = document_fact["validation_run_id"]
                elif case == "fact_foreign_subject":
                    raw["subject_hash"] = document_fact["subject_hash"]
                else:
                    raw["target_ref"] = "artifact://wrong-artifact-target-for-test"
                connection.execute("UPDATE validation_facts SET fact_json=? WHERE fact_id=?", (json.dumps(raw, ensure_ascii=False), fail["fact_id"]))
            else:
                raw = json.loads(connection.execute("SELECT decision_json FROM delivery_gate_decisions WHERE delivery_gate_decision_id=?",
                    (section_gate["delivery_gate_decision_id"],)).fetchone()[0])
                condition = next(condition for condition in raw["quality_policy_projection"]["condition_results"]
                    if condition["dimension_id"] == "consistency")
                condition["consumed_fact_refs"] = [document_ref]
                connection.execute("UPDATE delivery_gate_decisions SET decision_json=? WHERE delivery_gate_decision_id=?",
                    (json.dumps(raw, ensure_ascii=False), section_gate["delivery_gate_decision_id"]))
        database = _ReadOnlyDatabase(case_path)
        post = SimpleNamespace(database=database, artifact_store=ArtifactStateStore(database),
            entry_store=PostWriteEntryStore(database), gate_store=GateDecisionStore(database),
            control_store=ControlDecisionStore(database), validation_store=ValidationStore(database),
            binding_store=DocumentBindingStore(database))
        before_read = _sha(case_path.read_bytes())
        with pytest.raises(report.ReviewReportUnavailable) as refusal:
            report.collect_review_results(post, source_receipt=copy.deepcopy(fixture.source), document_control_ref=fixture.document_ref)
        cause = refusal.value.__cause__
        assert isinstance(cause, PostWritePersistenceIntegrityError)
        if case.startswith("fact_"):
            assert str(cause) == "ValidationFact row/hash mismatch"
        else:
            assert str(cause) == "corrupt DeliveryGateDecision"
        observed.append({"case": case, "actual_refusal_layer": type(cause).__name__, "actual_reason": str(cause),
            "deeper_hash_consistent_semantic_case_tested": False})
        assert _sha(case_path.read_bytes()) == before_read
    assert _sha(fixture.database_path.read_bytes()) == original_sha
    print("PERSISTED_TAMPER_REFUSALS " + json.dumps(observed, sort_keys=True))


def test_actual_public_terminal_result_excludes_internal_report(historical_fixture):
    """The actual public allowlist ignores the new internal report attachment."""
    from application.terminal_outcome_projector import project_terminal, public_terminal_result
    from schemas.status import ExecutionStatus
    fixture = historical_fixture
    outcome = project_terminal(task_id=fixture.source["task_id"], run_id=fixture.source["run_id"],
        execution_status=ExecutionStatus.SUCCESS, phase="finalize_workflow_result").model_dump(mode="json")
    summary = {"terminal_outcome": outcome}
    expected = public_terminal_result(summary)
    secrets = {"markdown": "INTERNAL_REPORT_BODY_SENTINEL", "fact_ref": "validation-fact:INTERNAL_REPORT_REF_SENTINEL",
        "report_path": str(fixture.database_path.parent / "INTERNAL_REPORT_PATH_SENTINEL.md"),
        "receipt_path": str(fixture.database_path.parent / "INTERNAL_RECEIPT_PATH_SENTINEL.json")}
    summary.update(target_review_report=secrets,
        supervisor_result={"result": {"target_review_report": secrets}})
    public = public_terminal_result(summary)
    assert public == expected
    assert set(public) == {"task_id", "run_id", "status", "terminal_outcome"}
    rendered = json.dumps(public)
    assert "target_review_report" not in rendered
    assert all(secret not in rendered for secret in secrets.values())
    assert "INTERNAL_REPORT" not in rendered and "INTERNAL_RECEIPT" not in rendered
