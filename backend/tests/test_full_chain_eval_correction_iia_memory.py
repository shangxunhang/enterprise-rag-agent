"""Original durable owner writes and independent reads through formal X29."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pytest

from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.report import emit_report
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import load_source_snapshot
from test_step7_durable_memory_persistence import _setup, _commit


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def build_native_memory_sources(source):
    """Original owners plus DB-API harness; every export comes from their output."""
    source.mkdir(parents=True, exist_ok=False)
    fact, db, composition = _setup(source / "owners")
    source_ref = _commit(fact)
    memory_refs = composition.project_artifact(source_ref)
    assert len(memory_refs) == 1
    memory_ref = memory_refs[0]
    # Capture the actual committed DB row before performing a separate store read.
    committed = deepcopy(db.memory[memory_ref.memory_id])
    readback = composition.runtime.store.get_memory_record(memory_ref)
    assert readback is not None
    record = composition.runtime.resolve_record(memory_ref)
    freshness = composition.runtime.assess_freshness(record)
    resolution = composition._resolve_artifact(record.source_binding.primary_source)
    source_path = fact.artifact_runtime.artifact_store._path_for_ref(source_ref.content_ref)
    files = {
        "source_fact": ("source_fact", source_path, "json"),
        "source_heads": ("artifact_runtime", source / "owners" / "artifact-runtime.jsonl", "jsonl"),
        "memory_record": ("memory_record", _write(source / "memory_record.json", committed), "json"),
        "memory_readback": ("memory_readback", _write(source / "memory_readback.json", readback.model_dump(mode="json")), "json"),
        "freshness": ("memory_freshness", _write(source / "freshness.json", freshness.model_dump(mode="json")), "json"),
        "source_resolution": ("memory_source_resolution", _write(source / "source_resolution.json", asdict(resolution)), "json"),
        "capability": ("memory_capability", _write(source / "capability.json", {
            "required": composition.required,
            "store_class": type(composition.runtime.store).__name__,
            "source_resolver_keys": [list(key) for key in sorted(composition.runtime._source_resolvers)],
        }), "json"),
    }
    return {"files": files, "source_ref": source_ref.model_dump(mode="json"),
        "memory_ref": memory_ref.model_dump(mode="json"), "commits": db.commits,
        "committed_record": committed, "readback_record": readback.model_dump(mode="json")}


@pytest.mark.parametrize("case", ["normal", "one_fault_readback", "missing"])
def test_native_X29_durable_write_readback_safe_snapshot_observe_report(case, tmp_path):
    source = tmp_path / "source"
    original = build_native_memory_sources(source)
    original_hashes = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    files = dict(original["files"])
    if case == "missing":
        del files["source_fact"]
    elif case == "one_fault_readback":
        readback = deepcopy(original["readback_record"])
        readback["projection"]["projection_payload"]["source_ref"]["artifact_version_id"] = "wrong-version"
        files["memory_readback"] = ("memory_readback", _write(source / "fault_readback.json", readback), "json")
    frozen = {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    manifest = {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "native-X29-" + case,
        "sources": [{"name": name, "family": family, "format": format_, "path": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest()} for name, (family, path, format_) in files.items()]}
    snapshot = load_source_snapshot(manifest, [source])
    assert not snapshot.limitations
    report = observe(snapshot, EvaluationRunIdentity("native-X29-" + case, "run-fact-1"))
    assert not any(f.code == "DOMAIN_READ_FAILURE" for f in report.findings)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X29"]
    expected = True if case == "normal" else False if case == "one_fault_readback" else None
    assert len(edges) == 1 and edges[0].assertion_result is expected, edges
    assert edges[0].measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert edges[0].source_refs and edges[0].target_refs
    json_path, _ = emit_report(report, tmp_path / "report", source_roots=[source])
    machine = json.loads(json_path.read_text(encoding="utf-8"))
    assert next(row for row in machine["edge_fidelity"] if row["edge_id"] == "X29")["assertion_result"] is expected
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in original_hashes.items())
    assert {str(path): sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()} == frozen
