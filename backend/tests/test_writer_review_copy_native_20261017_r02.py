"""Native Writer Copy checks against production code, with live Saver ownership."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver

from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity
from test_phase2_runtime_safety_closure import _run_production_fake, _execution


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _run_native_at_live_entry(output_root, *, run_id, on_entry=None, evidence_path=None):
    """Keep exact-ID and current-source checks within the real resource lifetime."""
    evidence = {"run_id": run_id, "output_root": str(output_root),
        "durability_injection": "NONE; execute uses the current production Graph",
        "saver_put_calls": [], "entries": [], "result": "RUNNING"}
    guard = threading.Lock()

    def persist():
        if evidence_path is not None:
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def configure(engine):
        saver = engine.checkpointer
        assert isinstance(saver, SqliteSaver)
        original_put = saver.put

        def timed_put(config, checkpoint, metadata, new_versions):
            values = checkpoint.get("channel_values", {})
            call = {"checkpoint_id": checkpoint.get("id"),
                "checkpoint_namespace": config.get("configurable", {}).get("checkpoint_ns"),
                "node": values.get("current_business_node"), "started_ns": time.perf_counter_ns()}
            try:
                return original_put(config, checkpoint, metadata, new_versions)
            finally:
                call["returned_ns"] = time.perf_counter_ns()
                with guard:
                    evidence["saver_put_calls"].append(call)

        saver.put = timed_put
        original_run_stage = engine._run_stage

        def observe(runtime_state, *, workflow, node_name, stage):
            if node_name == "final_artifact_review":
                entry_ns = time.perf_counter_ns()
                graph, payload = engine._restore(runtime_state, workflow)
                # First and only latest read at this live Stage entry.
                row = saver.get_tuple(phase2_checkpoint_identity(workflow, run_id=graph.run_id))
                entry = {"entry_ns": entry_ns, "latest_read_count": 1,
                    "latest_read_returned_ns": time.perf_counter_ns(),
                    "incoming_task_id": graph.task_id, "incoming_run_id": graph.run_id,
                    "incoming_business_revision": payload.business_revision,
                    "incoming_graph_sha256": _digest(graph.model_dump(mode="json")),
                    "incoming_payload_sha256": _digest(payload.model_dump(mode="json"))}
                evidence["entries"].append(entry)
                assert row is not None
                values = row.checkpoint["channel_values"]
                raw_payload = values.get("scheme_generation_payload", {})
                last = (values.get("node_outputs") or [{}])[-1]
                entry.update({"checkpoint_config": row.config,
                    "checkpoint_id": row.config["configurable"].get("checkpoint_id"),
                    "task_id": values.get("task_id"), "run_id": values.get("run_id"),
                    "workflow_namespace": values.get("workflow_namespace"),
                    "node": values.get("current_business_node"),
                    "transition_count": values.get("transition_count"),
                    "business_revision": raw_payload.get("business_revision"),
                    "last_output_node": last.get("node_id"), "last_output_status": last.get("status"),
                    "last_result_status": (last.get("result") or {}).get("status"),
                    "route": values.get("business_route"),
                    "assembly_pass": (raw_payload.get("assembly_validation") or {}).get("passed"),
                    "committed_channel_values_sha256": _digest(values), "metadata": row.metadata})
                persist()
                assert entry["node"] == entry["last_output_node"] == "validate_document_assembly", entry
                assert entry["last_output_status"] == entry["last_result_status"] == "success"
                assert entry["route"] == "continue" and entry["assembly_pass"] is True
                assert entry["task_id"] == graph.task_id and entry["run_id"] == graph.run_id
                namespace = engine.runtime_state_codec.workflow_namespace(workflow)
                assert entry["workflow_namespace"] == namespace
                assert row.config["configurable"]["thread_id"] == graph.run_id
                assert row.config["configurable"]["checkpoint_ns"] == namespace
                assert row.metadata["source"] == "loop" and entry["checkpoint_id"]
                # Exact-ID reread verifies one snapshot; it is not a latest retry.
                pinned = saver.get_tuple(row.config)
                assert pinned is not None and pinned.checkpoint == row.checkpoint and pinned.config == row.config
                restored_values, restored_payload, count, _ = engine._recovery_checkpoint_values(
                    workflow, pinned, task_id=graph.task_id, run_id=graph.run_id)
                restored_graph = engine.runtime_state_codec.restore_graph_state(restored_values, workflow=workflow)
                assert restored_payload.model_dump(mode="json") == payload.model_dump(mode="json")
                assert restored_graph.model_dump(mode="json") == graph.model_dump(mode="json")
                assert count == payload.business_revision == entry["transition_count"]
                database = output_root / "runs" / "langgraph_checkpoints.sqlite3"
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as independent:
                    independent.execute("PRAGMA query_only=ON")
                    raw = independent.execute("SELECT type,checkpoint,parent_checkpoint_id FROM checkpoints "
                        "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                        (graph.run_id, namespace, entry["checkpoint_id"])).fetchone()
                    assert raw is not None and saver.serde.loads_typed((raw[0], raw[1])) == row.checkpoint
                committed_calls = [call for call in evidence["saver_put_calls"]
                    if call["checkpoint_id"] == entry["checkpoint_id"]]
                assert len(committed_calls) == 1 and committed_calls[0]["returned_ns"] < entry_ns
                entry.update({"exact_id_pinned_same_snapshot": True,
                    "independent_read_only_connection_same_snapshot": True,
                    "codec_graph_and_payload_equal_incoming": True,
                    "validate_put_returned_ns": committed_calls[0]["returned_ns"],
                    "validate_put_return_before_entry_ns": entry_ns - committed_calls[0]["returned_ns"],
                    "checkpoint_parent_id": raw[2], "result": "PASS"})
                persist()
                if on_entry is not None:
                    on_entry(engine=engine, workflow=workflow, graph=graph, payload=payload,
                        checkpoint=row, values=values, entry=entry)
            return original_run_stage(runtime_state, workflow=workflow, node_name=node_name, stage=stage)

        engine._run_stage = observe

    started = time.perf_counter_ns()
    try:
        summary, factory = _run_production_fake(output_root, run_id=run_id, mutate_engine=configure)
        assert len(evidence["entries"]) == 1 and evidence["entries"][0]["result"] == "PASS"
        execution = _execution(summary)
        metadata = execution.get("metadata") or {}
        evidence["original_business_result"] = {"status": execution.get("status"),
            "terminal_reason": metadata.get("terminal_reason"), "pause_purpose": metadata.get("pause_purpose"),
            "budget_wait_ref": metadata.get("budget_wait_ref")}
        evidence["result"] = "FORMAL_PRODUCTION_C1_NATIVE_PASS"
        return evidence, summary, factory
    except BaseException as exc:
        evidence.update({"result": "FORMAL_PRODUCTION_C1_NATIVE_FAILURE", "exception_type": type(exc).__name__})
        raise
    finally:
        evidence["elapsed_ns"] = time.perf_counter_ns() - started
        persist()


def test_wc_n01_r02_committed_predecessor_at_live_entry(tmp_path):
    evidence_dir = os.environ.get("WC_NATIVE_R02_EVIDENCE_DIR")
    evidence_path = Path(evidence_dir) / "11_C1_FIXED_LIVE_OBSERVATIONS.json" if evidence_dir else None
    _run_native_at_live_entry(tmp_path / "native-runtime", run_id="writer-copy-r02-c1-" + str(uuid.uuid4()),
        evidence_path=evidence_path)


# NATIVE_SOURCE_GRAPH_TESTS_BEGIN
import copy
from unittest.mock import patch

import pytest


def _native_source_owner_fingerprint(engine, owner, graph):
    artifacts = {str(path.relative_to(owner.store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in owner.store.root.rglob("*.json")}
    preimage = owner.lifecycle.budget_preimage(graph.run_id)
    return {"artifacts_sha256": _digest(artifacts),
        "run_sha256": _digest(preimage.run.model_dump(mode="json")),
        "children_sha256": preimage.child_preimage_sha256,
        "budget_sha256": _digest(engine.budget_runtime(graph.task_id, graph.run_id).canonical_state().model_dump(mode="json"))}


def _project_section(payload, index, section):
    """Change every document projection together, keeping the Writer authoritative."""
    payload.sections[index] = section
    payload.section_execution_results[index] = payload.section_execution_results[index].model_copy(
        update={"section": section}, deep=True)
    payload.assembly.draft.sections[index] = section
    payload.generated_draft.sections[index] = section


def _render_all_projections(payload):
    text = "\n\n".join(f"{i}、{section.section_title}\n{section.content}"
        for i, section in enumerate(payload.sections, 1))
    payload.assembly.draft.full_text = text
    payload.generated_draft.full_text = text


def test_wc_n02_n03_n05_to_n09_native_checkpoint_source_and_complete_bytes(tmp_path):
    from model_gateway.fake_llm_client import FakeLLMClient
    from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
    from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyIntegrityError
    from schemas.status import ExecutionStatus

    evidence_dir = os.environ.get("WC_NATIVE_R02_EVIDENCE_DIR")
    path = Path(evidence_dir) / "12_NATIVE_SOURCE_CHECKPOINT_OBSERVATIONS.json" if evidence_dir else None
    checks = []

    def inspect(*, engine, workflow, graph, payload, checkpoint, values, entry):
        owner = engine.stage_service.target_workflow_runtime.section_dependencies
        entry["source_checkpoint_checks"] = checks
        entry["source_checkpoint_group_result"] = "RUNNING"

        def resolve(*, current_workflow=workflow, current_graph=graph, current_payload=payload):
            return wc.resolve_committed_predecessor(engine, workflow=current_workflow,
                before_graph=current_graph, before_payload=current_payload)

        def reject(label, operation):
            with pytest.raises((ValueError, RuntimeError)) as caught:
                operation()
            checks.append({"case": label, "result": "REJECTED", "exception_type": type(caught.value).__name__,
                "diagnostic": str(caught.value)[:100]})

        before = _native_source_owner_fingerprint(engine, owner, graph)
        # The legitimate source owner is real. No model may run inside derivation.
        with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("copy dispatched a model")), \
                patch.object(owner, "complete_source", side_effect=AssertionError("copy rewrote canonical source")):
            snapshot = resolve()
            source = wc.validate_exact_writer_document(snapshot, section_dependencies=owner)
            receipt = source["receipt"]
            assert len(payload.sections) == len(receipt["required_sections"]) == 8
            assert source["markdown"] == payload.assembly.draft.full_text.encode("utf-8")
            assert receipt["markdown_sha256"] == hashlib.sha256(source["markdown"]).hexdigest()
            assert "VERIFYING" in receipt["source_binding_states"]
            assert receipt["source_review_status"] == "SOURCE_VERIFYING_NOT_ACCEPTED"
            assert receipt["post_write_quality"] == "POSTWRITE_NOT_ASSESSED"
            assert receipt["formal_release_authorized"] is False
            assert receipt["checkpoint_id"] == entry["checkpoint_id"]
            checks.append({"case": "WC-N05/N08/N09_real_owner_complete_eight_sections_VERIFYING",
                "result": "PASS", "source_binding_states": receipt["source_binding_states"],
                "markdown_sha256": receipt["markdown_sha256"]})
            # VERIFYING remains insufficient for existing canonical acceptance.
            for result in payload.section_execution_results:
                binding = result.canonical_section_source_binding
                if binding.workflow_status == "VERIFYING":
                    with pytest.raises(SectionDependencyIntegrityError):
                        owner.validate_source(binding, require_accepted=True)
            checks.append({"case": "WC-N08_existing_require_accepted_still_rejects_VERIFYING", "result": "PASS"})

            reject("WC-N02_wrong_run", lambda: resolve(current_graph=graph.model_copy(update={"run_id": "wrong-run"}, deep=True)))
            reject("WC-N02_wrong_task", lambda: resolve(current_graph=graph.model_copy(update={"task_id": "wrong-task"}, deep=True)))
            reject("WC-N02_wrong_workflow_namespace", lambda: resolve(current_workflow=workflow.model_copy(update={"workflow_id": "wrong-workflow"}, deep=True)))
            reject("WC-N02_wrong_workflow_version", lambda: resolve(current_workflow=workflow.model_copy(update={"workflow_version": "wrong-version"}, deep=True)))
            reject("WC-N03_incoming_revision", lambda: resolve(current_payload=payload.model_copy(update={"business_revision": payload.business_revision + 1}, deep=True)))
            reject("WC-N03_incoming_document", lambda: resolve(current_payload=payload.model_copy(update={"document_id": "other-document"}, deep=True)))

            def bad_checkpoint(label, edit=None, identity=None, metadata=None, pending=None):
                altered = copy.deepcopy(checkpoint.checkpoint)
                if edit is not None:
                    edit(altered["channel_values"])
                config = copy.deepcopy(checkpoint.config)
                if identity:
                    config["configurable"].update(identity)
                row = checkpoint._replace(checkpoint=altered, config=config,
                    metadata={**checkpoint.metadata, **(metadata or {})},
                    pending_writes=checkpoint.pending_writes if pending is None else pending)
                with patch.object(engine.checkpointer, "get_tuple", return_value=row):
                    reject(label, resolve)

            bad_checkpoint("WC-N02_wrong_returned_thread", identity={"thread_id": "wrong-run"})
            bad_checkpoint("WC-N02_wrong_returned_namespace", identity={"checkpoint_ns": "wrong:namespace"})
            bad_checkpoint("WC-N02_missing_checkpoint_id", identity={"checkpoint_id": ""})
            bad_checkpoint("WC-N02_metadata_source_not_loop", metadata={"source": "update"})
            bad_checkpoint("WC-N02_metadata_workflow_namespace", metadata={"workflow_namespace": "wrong:namespace"})
            bad_checkpoint("WC-N02_failed_node_result", edit=lambda v: v["node_outputs"][-1]["result"].update(status="failed"))
            bad_checkpoint("WC-N02_wrong_route", edit=lambda v: v.update(business_route="technical_failure"))
            bad_checkpoint("WC-N02_failed_assembly", edit=lambda v: v["scheme_generation_payload"]["assembly_validation"].update(passed=False))
            bad_checkpoint("WC-N03_successor_or_terminal_is_not_validate", edit=lambda v: v.update(current_business_node="final_artifact_review"))
            bad_checkpoint("WC-N02_pending_writes_cannot_substitute_committed_channels",
                edit=lambda v: v.update(current_business_node="final_artifact_review"),
                pending=[("uncommitted", "scheme_generation_payload", values["scheme_generation_payload"])])
            wrong_pinned = checkpoint._replace(config={"configurable": {
                **checkpoint.config["configurable"], "thread_id": "wrong-run"}})
            with patch.object(engine.checkpointer, "get_tuple", side_effect=[checkpoint, wrong_pinned]):
                reject("WC-N02_exact_pin_wrong_returned_identity", resolve)
            changed_pinned = copy.deepcopy(checkpoint.checkpoint)
            changed_pinned["channel_values"]["business_route"] = "technical_failure"
            with patch.object(engine.checkpointer, "get_tuple", side_effect=[checkpoint,
                    checkpoint._replace(checkpoint=changed_pinned)]):
                reject("WC-N02_exact_pin_snapshot_changed", resolve)

            def bad_document(label, edit):
                changed = payload.model_copy(deep=True)
                edit(changed)
                reject(label, lambda: wc.validate_exact_writer_document({**snapshot, "payload": changed}, section_dependencies=owner))

            def change_result(field, value):
                return lambda p: p.section_execution_results.__setitem__(0,
                    p.section_execution_results[0].model_copy(update={field: value}, deep=True))

            first, second = payload.section_execution_results[:2]
            bad_document("WC-N06_missing_source_binding", change_result("canonical_section_source_binding", None))
            bad_document("WC-N06_wrong_writer_execution", change_result("writer_execution_ref", second.writer_execution_ref))
            bad_document("WC-N06_wrong_writer_invocation", change_result("writer_generation_invocation_ref", second.writer_generation_invocation_ref))
            bad_document("WC-N06_wrong_plan_binding", change_result("canonical_plan_binding", second.canonical_plan_binding))
            bad_document("WC-N06_wrong_manifest", change_result("section_paragraph_manifest", second.section_paragraph_manifest))
            bad_document("WC-N06_missing_coverage_closure", change_result("canonical_claim_coverage_proof_refs", []))
            bad_document("WC-N06_wrong_producer_attempt", change_result("active_execution_lineage",
                first.active_execution_lineage.model_copy(update={"attempt_id": "not-current-attempt"}, deep=True)))
            bad_document("WC-N06_forged_source_binding_hash_owner_rejects", change_result("canonical_section_source_binding",
                first.canonical_section_source_binding.model_copy(update={"source_sha256": "0" * 64}, deep=True)))
            if first.canonical_claim_verification_refs:
                bad_document("WC-N06_missing_verification_closure", change_result("canonical_claim_verification_refs", []))

            def coherent_section(p, **updates):
                _project_section(p, 0, p.sections[0].model_copy(update=updates, deep=True))
                _render_all_projections(p)

            bad_document("WC-N07_payload_only_one_utf8_byte", lambda p: setattr(p.sections[0], "content", p.sections[0].content + "!"))
            bad_document("WC-N07_coherent_document_one_byte_differs_from_durable_Writer", lambda p: coherent_section(p, content=p.sections[0].content + "!"))
            bad_document("WC-N09_missing_required_section", lambda p: p.sections.pop())
            bad_document("WC-N09_duplicate_section", lambda p: p.sections.__setitem__(1, p.sections[0].model_copy(deep=True)))
            bad_document("WC-N09_reverse_section_order", lambda p: p.sections.reverse())
            bad_document("WC-N09_wrong_section_order_number", lambda p: coherent_section(p, section_order=999))
            bad_document("WC-N09_empty_body", lambda p: coherent_section(p, content=""))
            bad_document("WC-N09_placeholder_body", lambda p: coherent_section(p, content="待生成"))
            bad_document("WC-N09_title_only_body", lambda p: coherent_section(p, content=p.sections[0].section_title))
            bad_document("WC-N09_partial_success_cannot_prove_complete", lambda p: coherent_section(p, status=ExecutionStatus.PARTIAL_SUCCESS))
            bad_document("WC-N09_truncated", lambda p: coherent_section(p, truncation=p.sections[0].truncation.model_copy(update={"truncated": True}, deep=True)))
            bad_document("WC-N09_generation_blocked", lambda p: coherent_section(p, extra={**p.sections[0].extra, "generation_blocked": True}))
            bad_document("WC-N09_normal_generation_blocked", lambda p: coherent_section(p, eval_result=p.sections[0].eval_result.model_copy(update={"checks": {**p.sections[0].eval_result.checks, "normal_generation_blocked": True}}, deep=True)))
            bad_document("WC-N09_fulltext_one_byte_changed", lambda p: setattr(p.assembly.draft, "full_text", p.assembly.draft.full_text + "!"))
            bad_document("WC-N09_duplicate_required_title", lambda p: p.required_sections.append(p.required_sections[0]))
            bad_document("WC-N09_extra_unplanned_section", lambda p: p.sections.append(p.sections[0].model_copy(update={"section_id": "unplanned"}, deep=True)))
            after = _native_source_owner_fingerprint(engine, owner, graph)
            assert after == before
            entry["source_owner_read_only_fingerprint"] = before
            checks.append({"case": "WC-N05_to_N09_source_artifacts_lifecycle_children_and_budget_unchanged", "result": "PASS"})
            entry["source_checkpoint_group_result"] = "PASS"

    evidence, _, _ = _run_native_at_live_entry(tmp_path / "native-runtime", run_id="writer-copy-r02-sources-" + str(uuid.uuid4()),
        on_entry=inspect, evidence_path=path)
    assert evidence["entries"][0]["source_checkpoint_group_result"] == "PASS"
# NATIVE_SOURCE_GRAPH_TESTS_END


# FILE_PROTOCOL_TESTS_BEGIN
import concurrent.futures
import copy
import ctypes
import errno
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as _file_copy


def _file_source(*, task_id="task-file", run_id="run-file", markdown=None):
    """File-only fixture; this does not assert canonical Source Owner acceptance."""
    markdown = markdown if markdown is not None else "第一章\r\n完整原文\n\n附录\n合法非必需章节".encode("utf-8")
    core = {"schema_version": "writer_review_copy_receipt_v1",
        "receipt_kind": "WRITER_REVIEW_COPY_NOT_FORMAL_RELEASE", "task_id": task_id, "run_id": run_id,
        "document_id": "document-file", "workflow_namespace": "scheme_generation_v2:v2.0",
        "checkpoint_id": "native-file-only-checkpoint", "committed_checkpoint_source_sha256": "1" * 64,
        "business_revision": 10, "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
        "required_sections": [{"section_id": "section-one", "section_title": "第一章", "required": True,
            "source_binding_state": "VERIFYING", "writer_execution_ref": "writer://file-fixture-one"},
            {"section_id": "section-extra", "section_title": "附录", "required": False,
            "source_binding_state": "VERIFYING", "writer_execution_ref": "writer://file-fixture-extra"}],
        "required_section_titles": ["第一章"], "source_binding_states": ["VERIFYING"],
        "source_review_status": "SOURCE_VERIFYING_NOT_ACCEPTED",
        "validation_scope": "SOURCE_AUTHENTICATED_NOT_POSTWRITE_VERIFIED", "post_write_quality": "POSTWRITE_NOT_ASSESSED",
        "human_approval": "NOT_ESTABLISHED", "formal_release_authorized": False}
    return {"markdown": markdown, "receipt": core}


def _file_verify(runs_dir, sealed, **overrides):
    receipt = sealed["receipt"]
    return _file_copy.verify_sealed_pair(runs_dir=runs_dir, **{"task_id": receipt["task_id"],
        "run_id": receipt["run_id"], "source_snapshot_sha256": receipt["source_snapshot_sha256"], **overrides})


def test_file_protocol_atomic_multiprocess_idempotency(tmp_path):
    assert os.name == "nt", "Native Windows/NTFS is required for this acceptance"
    filesystem = ctypes.create_unicode_buffer(32)
    assert ctypes.windll.kernel32.GetVolumeInformationW(tmp_path.drive + "\\", None, 0, None, None, None, filesystem, 32)
    assert filesystem.value == "NTFS"
    runs_dir, source = tmp_path / "runs", _file_source()
    encoded = json.dumps({"markdown_hex": source["markdown"].hex(), "receipt": source["receipt"]}, ensure_ascii=False)
    script = """import json,sys
from apps.enterprise_document.services.agent_rag_v1.writer_review_copy import seal_local_writer_copy
value=json.loads(sys.argv[2]); value['markdown']=bytes.fromhex(value.pop('markdown_hex'))
sealed=seal_local_writer_copy(runs_dir=sys.argv[1],source=value)
print(json.dumps({'ready':sealed['ready'],'source':sealed['receipt']['source_snapshot_sha256']}))
"""
    child_env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONIOENCODING": "utf-8"}
    def publish(_):
        return subprocess.run([sys.executable, "-c", script, str(runs_dir), encoded], env=child_env,
            capture_output=True, text=True, encoding="utf-8", timeout=60)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        children = list(pool.map(publish, range(4)))
    evidence_dir = os.environ.get("WC_FILE_EVIDENCE_DIR")
    if evidence_dir:
        Path(evidence_dir, "13_FILE_MULTIPROCESS_RESULTS.json").write_text(json.dumps([
            {"exit_code": child.returncode, "stdout": child.stdout, "stderr": child.stderr}
            for child in children], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert all(child.returncode == 0 for child in children), [(child.returncode, child.stderr) for child in children]
    assert len({json.loads(child.stdout)["source"] for child in children}) == 1
    first = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    md, record = Path(first["markdown_path"]), Path(first["receipt_path"])
    before = (md.read_bytes(), record.read_bytes(), md.stat().st_ino, record.stat().st_ino)
    assert _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source) == first
    assert (md.read_bytes(), record.read_bytes(), md.stat().st_ino, record.stat().st_ino) == before
    assert _file_verify(runs_dir, first, expected_receipt=source["receipt"])["verification"] == "HISTORICAL_VERIFIED"
    assert md.read_bytes() == source["markdown"] and list(md.parent.glob("*.tmp")) == []
    changed = {**source, "markdown": source["markdown"] + b"changed"}
    with pytest.raises(_file_copy.WriterReviewCopyUnavailable):
        _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=changed)
    other = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=_file_source(markdown=b"different source\n"))
    assert other["markdown_path"] != first["markdown_path"]


def test_file_protocol_edited_orphan_and_cross_run_rejected(tmp_path):
    runs_dir = tmp_path / "runs"
    source = _file_source(run_id="orphan-md")
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    md, record = Path(sealed["markdown_path"]), Path(sealed["receipt_path"])
    original_md, original_receipt, inode = md.read_bytes(), record.read_bytes(), md.stat().st_ino
    record.unlink()
    repaired = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    assert repaired == sealed and record.read_bytes() == original_receipt and md.stat().st_ino == inode
    md.unlink()
    with pytest.raises(_file_copy.WriterReviewCopyUnavailable):
        _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    assert not md.exists() and record.read_bytes() == original_receipt
    source = _file_source(run_id="edited")
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    md, record = Path(sealed["markdown_path"]), Path(sealed["receipt_path"])
    edited = md.read_bytes() + b"human edit"
    md.write_bytes(edited)
    for operation in (lambda: _file_verify(runs_dir, sealed),
            lambda: _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)):
        with pytest.raises(_file_copy.WriterReviewCopyUnavailable): operation()
    assert md.read_bytes() == edited
    source = _file_source(run_id="receipt-edit")
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    record = Path(sealed["receipt_path"])
    invalid = copy.deepcopy(sealed["receipt"]); invalid["formal_release_authorized"] = 0
    invalid["receipt_sha256"] = _file_copy._sha({k: v for k, v in invalid.items() if k != "receipt_sha256"})
    record.write_bytes(_file_copy._json_bytes(invalid))
    with pytest.raises(_file_copy.WriterReviewCopyUnavailable): _file_verify(runs_dir, sealed)
    source = _file_source(run_id="original-run")
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    foreign = runs_dir / "review_drafts" / source["receipt"]["task_id"] / "foreign-run"
    foreign.mkdir()
    for key in ("markdown_path", "receipt_path"):
        shutil.copyfile(sealed[key], foreign / Path(sealed[key]).name)
    with pytest.raises(_file_copy.WriterReviewCopyUnavailable): _file_verify(runs_dir, sealed, run_id="foreign-run")
    assert original_md == _file_source()["markdown"]


def test_file_protocol_ids_reparse_and_windows_handles(tmp_path):
    for bad in ("../escape", "%252e%252e", "run:stream", "CON"):
        with pytest.raises(ValueError):
            _file_copy.seal_local_writer_copy(runs_dir=tmp_path / "bad-ids", source=_file_source(run_id=bad))
    outside = tmp_path / "outside"; outside.mkdir()
    junction_root = tmp_path / "junction-runs"; junction_root.mkdir()
    junction = junction_root / "review_drafts"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    assert created.returncode == 0, created.stderr
    assert not junction.is_symlink() and junction.lstat().st_file_attributes & 0x400
    with pytest.raises((ValueError, OSError)):
        _file_copy.seal_local_writer_copy(runs_dir=junction_root, source=_file_source())
    assert list(outside.iterdir()) == []
    runs_dir, source = tmp_path / "safe-runs", _file_source()
    source_hash = _file_copy._sha(source["receipt"])
    with _file_copy._wc_locked_paths(runs_dir, "task-file", "run-file", source_hash, create=True) as (md, _):
        with pytest.raises(PermissionError): md.parent.rename(md.parent.with_name("moved-run"))
        kernel = _file_copy._wc_kernel()
        handle = kernel.CreateFileW(_file_copy._wc_extended(md.parent), 0x40000000, 7, None, 3, 0x02200000, None)
        assert handle == ctypes.c_void_p(-1).value and ctypes.get_last_error() == 32
    outside_file = outside / "kept.md"; outside_file.write_bytes(source["markdown"])
    os.symlink(outside_file, md)
    with pytest.raises((ValueError, OSError)):
        _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    assert outside_file.read_bytes() == source["markdown"] and md.is_symlink()
    md.unlink()
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    with _file_copy._wc_read_file(Path(sealed["markdown_path"])) as stream:
        assert stream.read() == source["markdown"]
        with pytest.raises(PermissionError): Path(sealed["markdown_path"]).write_bytes(b"must not edit")


def test_file_protocol_fsync_link_and_receipt_failure_is_closed(tmp_path):
    for operation in ("fsync", "link"):
        runs_dir = tmp_path / operation
        with patch.object(_file_copy.os, operation, side_effect=OSError(errno.ENOSPC, "injected isolated fault")):
            with pytest.raises(OSError):
                _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=_file_source())
        assert list(runs_dir.rglob("*.receipt.json")) == [] and list(runs_dir.rglob("*.tmp")) == []
    runs_dir, source = tmp_path / "receipt-fault", _file_source()
    original_link, calls = _file_copy.os.link, []
    def fail_receipt(left, right):
        calls.append(str(right))
        if len(calls) == 2: raise PermissionError(errno.EACCES, "injected receipt permission fault")
        return original_link(left, right)
    with patch.object(_file_copy.os, "link", side_effect=fail_receipt):
        with pytest.raises(PermissionError): _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    assert len(list(runs_dir.rglob("*.md"))) == 1 and list(runs_dir.rglob("*.receipt.json")) == []
    sealed = _file_copy.seal_local_writer_copy(runs_dir=runs_dir, source=source)
    assert _file_verify(runs_dir, sealed)["ready"]
# FILE_PROTOCOL_TESTS_END
