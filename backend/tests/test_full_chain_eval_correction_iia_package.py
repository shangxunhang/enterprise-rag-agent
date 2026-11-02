"""Native durable EvidenceBundle -> committed EvidencePackage formal X08."""
from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.research import CanonicalEvidenceBundleBindingSchema
from apps.enterprise_document.services.agent_rag_v1 import EvidencePackageCommitOperations
from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.report import emit_report
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import load_source_snapshot
from rag.store.evidence_bundle_store import EvidenceBundleSnapshotStore
from test_multi_agent_v3_research_runtime import _setup, _op, RUN_ID, SECTION_ID


def build_native_package_sources(source):
    """Run original immutable owners in a fresh E fixture, with fixed resolver."""
    source.mkdir(parents=True, exist_ok=False)
    env = _setup(source / "owners")
    store = EvidenceBundleSnapshotStore(source / "bundles")
    bundle_ref = store.persist(env["bundle"])
    # The existing test port is a finite handle resolver. Its sole new result is
    # verified by the actual immutable store, not a reconstructed Bundle.
    env["bundles"][bundle_ref.handle] = store.resolve(bundle_ref.handle)
    content = env["package_content"].model_copy(update={"evidence_bundle_binding": CanonicalEvidenceBundleBindingSchema(
        evidence_bundle_handle=bundle_ref.handle, evidence_bundle_sha256=bundle_ref.evidence_bundle_sha256)}, deep=True)
    lineage = env["lineage"]
    version = env["research"].commit_evidence_package(candidate_content=content, logical_ref=env["package_logical"],
        plan_binding=env["plan_binding"], expected_head_revision=env["package_binding"].resolved_head_revision,
        expected_active_version_id=env["package_version"].artifact_version_id,
        operations=EvidencePackageCommitOperations(
            version_commit=_op("correction-package-commit", "ARTIFACT_VERSION_COMMIT", attempt_scope_ref=f"section:{SECTION_ID}", attempt_id=lineage.attempt_id),
            active_promotion=_op("correction-package-promote", "ARTIFACT_ACTIVE_PROMOTION", attempt_scope_ref=f"section:{SECTION_ID}", attempt_id=lineage.attempt_id)),
        commit_guard_context=lineage)
    current = env["research"].resolve_current_evidence_package_binding(run_id=RUN_ID, section_id=SECTION_ID)
    assert current.version_ref == version and current.resolved_head_revision == 2
    bundle_path = store._path_for_handle(bundle_ref.handle)
    package_path = env["artifact"].artifact_store._path_for_ref(version.content_ref)
    assert bundle_path.is_file() and package_path.is_file()
    return {"bundle_path": bundle_path, "package_path": package_path, "version_ref": version.model_dump(mode="json"),
        "bundle_handle": bundle_ref.handle, "bundle_sha256": bundle_ref.evidence_bundle_sha256,
        "run_id": RUN_ID, "section_id": SECTION_ID, "selected_evidence_ids": env["bundle"].selected_evidence_ids}


@pytest.mark.parametrize("case", ["normal", "one_fault_selection", "one_fault_bundle_hash", "one_fault_run", "missing"])
def test_native_X08_store_envelopes_safe_snapshot_observe_report(case, tmp_path):
    source = tmp_path / "source"
    original = build_native_package_sources(source)
    original_hashes = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    package_path = original["package_path"]
    if case.startswith("one_fault"):
        envelope = json.loads(package_path.read_bytes())
        content = json.loads(base64.b64decode(envelope["content_base64"], validate=True))
        if case == "one_fault_selection":
            content["selections"][0]["evidence_id"] = "E999"
        elif case == "one_fault_bundle_hash":
            content["evidence_bundle_binding"]["evidence_bundle_sha256"] = "sha256:" + "0" * 64
        else:
            content["section_plan_source"]["plan_version_ref"]["logical_ref"]["run_id"] = "different-run"
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        envelope["content_base64"] = base64.b64encode(raw).decode("ascii")
        envelope["reference"]["content_hash"] = "sha256:" + sha256(raw).hexdigest()
        envelope["reference"]["size_bytes"] = len(raw)
        package_path = source / "fault_package.json"
        package_path.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    files = [("package", "evidence_package", package_path)]
    if case != "missing":
        files.append(("bundle", "evidence_bundle", original["bundle_path"]))
    frozen_source_files = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    manifest = {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "native-X08",
        "sources": [{"name": name, "family": family, "format": "json", "path": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest()} for name, family, path in files]}
    snapshot = load_source_snapshot(manifest, [source])
    assert not snapshot.limitations
    report = observe(snapshot, EvaluationRunIdentity("native-X08-" + case, RUN_ID))
    assert not any(f.code == "DOMAIN_READ_FAILURE" for f in report.findings)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X08"]
    expected = True if case == "normal" else None if case == "missing" else False
    assert len(edges) == 1 and edges[0].assertion_result is expected, edges
    assert edges[0].measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert edges[0].target_refs
    if expected is not None:
        assert edges[0].source_refs
    json_path, _ = emit_report(report, tmp_path / "report", source_roots=[source])
    machine = json.loads(json_path.read_text(encoding="utf-8"))
    assert next(edge for edge in machine["edge_fidelity"] if edge["edge_id"] == "X08")["assertion_result"] is expected
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in original_hashes.items())
    assert {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()} == frozen_source_files
