"""Shared live-entry source checks; this module never starts a Graph itself.

The parent integration test supplies one fresh protected Native Fake Run after
the formal C3 gate. Real source owners remain authoritative throughout.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from unittest.mock import patch

from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import (
    SectionDependencyIntegrityError,
)
from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.status import ExecutionStatus


def _exception_evidence(error):
    rows = []
    while error is not None and len(rows) < 5:
        rows.append({"type": type(error).__name__, "message": str(error)[:600]})
        error = error.__cause__
    return rows


def _source_fingerprint(engine, owner, graph, results):
    """Read only this supplied Run's existing owner state, retaining missingness."""
    artifact_hashes = {str(path.relative_to(owner.store.root)):
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in owner.store.root.rglob("*.json") if path.is_file()}
    ledger_hashes = {}
    for label, path in (("lifecycle", owner.lifecycle.path),
            ("claim", owner.claim_runtime.ledger_path),
            ("paragraph_manifest", owner.paragraph_runtime.manifest_ledger_path)):
        path = Path(path)
        ledger_hashes[label] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "ABSENT"
    writer_results = [owner.writer_execution_store.resolve_writer_execution(
        result.writer_execution_ref).model_dump(mode="json") for result in results]
    preimage = owner.lifecycle.budget_preimage(graph.run_id)
    return {"artifacts_sha256": wc._sha(artifact_hashes), "artifact_count": len(artifact_hashes),
        "ledger_sha256": ledger_hashes, "writer_results_sha256": wc._sha(writer_results),
        "run_sha256": wc._sha(preimage.run.model_dump(mode="json")),
        "children_sha256": preimage.child_preimage_sha256,
        "budget_sha256": wc._sha(engine.budget_runtime(graph.task_id, graph.run_id, require_existing_account=True)
            .canonical_state().model_dump(mode="json"))}


def _project_section(payload, index, section):
    payload.sections[index] = section
    payload.section_execution_results[index] = payload.section_execution_results[index].model_copy(
        update={"section": section}, deep=True)
    payload.assembly.draft.sections[index] = section
    payload.generated_draft.sections[index] = section


def _render(payload):
    value = "\n\n".join(f"{index}、{section.section_title}\n{section.content}"
        for index, section in enumerate(payload.sections, start=1))
    payload.assembly.draft.full_text = value
    payload.generated_draft.full_text = value


def run_live_source_checks(engine, workflow, graph, payload):
    """Run once inside the parent's final-review entry, with a live real Owner.

    No source/store state is mutated. Negative transport rows and document views
    are detached copies; they never become accepted evidence or sealed files.
    On failure the original exception carries ``writer_copy_source_evidence``.
    """
    owner = engine.stage_service.target_workflow_runtime.section_dependencies
    report = {"scope": "SHARED_LIVE_NATIVE_RUN_REAL_SOURCE_OWNER",
        "task_id": graph.task_id, "run_id": graph.run_id, "status": "RUNNING",
        "checks": [], "rejections": [], "not_run": [],
        "negative_mutations": "DETACHED_MEMORY_ONLY_NO_OWNER_OR_CHECKPOINT_WRITES",
        "same_bytes_new_paragraph_version": "NOT_RUN_NO_CANONICAL_HEAD_MUTATION_IN_SHARED_RUN"}

    def reject(case, operation, expected=(ValueError,)):
        try:
            operation()
        except Exception as error:
            if not isinstance(error, expected):
                raise AssertionError(f"{case} raised an unexpected {type(error).__name__}") from error
            report["rejections"].append({"case": case, "status": "REJECTED",
                "exceptions": _exception_evidence(error)})
        else:
            raise AssertionError(case + " unexpectedly passed")

    def resolve(current_workflow=workflow, current_graph=graph, current_payload=payload):
        return wc.resolve_committed_predecessor(engine, workflow=current_workflow,
            before_graph=current_graph, before_payload=current_payload)

    try:
        assert owner is not None and len(payload.sections) >= 2, "shared fixture requires real multiple sections"
        with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("copy dispatched a model")) as model_guard, \
                patch.object(owner, "complete_source", side_effect=AssertionError("copy rewrote source")) as write_guard, \
                patch.object(owner, "validate_source", wraps=owner.validate_source) as source_reads:
            before = _source_fingerprint(engine, owner, graph, payload.section_execution_results)
            snapshot = resolve()
            source = wc.validate_exact_writer_document(snapshot, section_dependencies=owner)
            receipt = source["receipt"]
            section_count = len(payload.sections)
            assert source_reads.call_count == section_count
            assert all(call.kwargs == {"require_accepted": False} for call in source_reads.call_args_list)
            assert len(receipt["required_sections"]) == section_count
            assert source["markdown"] == payload.assembly.draft.full_text.encode("utf-8")
            assert receipt["markdown_sha256"] == hashlib.sha256(source["markdown"]).hexdigest()
            assert receipt["checkpoint_id"] == snapshot["checkpoint_id"]
            assert receipt["formal_release_authorized"] is False
            assert receipt["post_write_quality"] == "POSTWRITE_NOT_ASSESSED"
            assert receipt["validation_scope"] == "SOURCE_AUTHENTICATED_NOT_POSTWRITE_VERIFIED"
            assert "VERIFYING" in receipt["source_binding_states"], "use the existing genuine VERIFYING fixture"
            assert receipt["source_review_status"] == "SOURCE_VERIFYING_NOT_ACCEPTED"
            for result, row in zip(payload.section_execution_results, receipt["required_sections"]):
                binding = result.canonical_section_source_binding
                writer = owner.writer_execution_store.resolve_writer_execution(result.writer_execution_ref)
                assert writer.status == "success" and writer.artifact is not None
                assert writer.artifact.content.encode("utf-8") == result.section.content.encode("utf-8")
                assert binding.paragraph_manifest.generation_membership_state == "SEALED"
                assert row["source_ref"] == binding.source_ref and row["source_binding_sha256"] == binding.source_sha256
                assert row["writer_execution_ref"] == result.writer_execution_ref.model_dump(mode="json")
                if binding.workflow_status == "VERIFYING":
                    reject("WC-N08_existing_acceptance_still_rejects_VERIFYING:" + result.section.section_id,
                        lambda binding=binding: owner.validate_source(binding, require_accepted=True),
                        (SectionDependencyIntegrityError,))
            report.update({"section_count": section_count, "source_binding_states": receipt["source_binding_states"],
                "source_review_status": receipt["source_review_status"], "checkpoint_id": snapshot["checkpoint_id"],
                "markdown_sha256": receipt["markdown_sha256"], "source_receipt": receipt})
            report["checks"].append({"case": "WC-N05/N07/N08/N09_real_owner_complete_multisection_VERIFYING", "status": "PASS"})

            # Exercise real public Owner rejection, not an alternative validator.
            first, second = payload.section_execution_results[:2]
            owner_defects = {
                "wrong_run": lambda row: row["plan_binding"]["plan_version_ref"]["logical_ref"].update(run_id="wrong-run"),
                "wrong_plan_version": lambda row: row["plan_binding"]["plan_version_ref"].update(artifact_version_id="wrong-plan"),
                "wrong_writer": lambda row: row.update(writer_execution_ref=second.writer_execution_ref.model_dump(mode="json")),
                "wrong_invocation": lambda row: row.update(writer_invocation_ref=second.writer_generation_invocation_ref.model_dump(mode="json")),
                "wrong_attempt": lambda row: row.update(producer_attempt_id="wrong-attempt"),
                "wrong_paragraph_manifest": lambda row: row.update(paragraph_manifest=second.section_paragraph_manifest.model_dump(mode="json")),
                "stale_input": lambda row: row.update(input_snapshot_version="stale-input"),
                "missing_coverage": lambda row: row.update(coverage_proof_refs=[]),
                "open_manifest": lambda row: row["paragraph_manifest"].update(generation_membership_state="OPEN"),
            }
            if first.canonical_claim_verification_refs:
                owner_defects["missing_verification"] = lambda row: row.update(verification_refs=[])
            else:
                report["not_run"].append({"case": "nonempty_verification_ref_omission", "reason": "this genuine VERIFYING fixture has no verification refs"})
            for name, edit in owner_defects.items():
                altered = first.canonical_section_source_binding.model_dump(mode="json")
                edit(altered)
                altered["source_sha256"] = canonical_sha256({key: value for key, value in altered.items()
                    if key not in {"source_ref", "source_sha256"}})
                reject("WC-N06_real_owner_" + name,
                    lambda altered=altered: owner.validate_source(altered, require_accepted=False),
                    (SectionDependencyIntegrityError,))

            config = phase2_checkpoint_identity(workflow, run_id=graph.run_id)
            config["configurable"]["checkpoint_id"] = snapshot["checkpoint_id"]
            checkpoint = engine.checkpointer.get_tuple(config)  # Exact-ID source for negative clones.
            assert checkpoint is not None
            reject("WC-N02_wrong_run", lambda: resolve(current_graph=graph.model_copy(update={"run_id": "wrong-run"}, deep=True)))
            reject("WC-N02_wrong_task", lambda: resolve(current_graph=graph.model_copy(update={"task_id": "wrong-task"}, deep=True)))
            reject("WC-N02_wrong_namespace", lambda: resolve(current_workflow=workflow.model_copy(update={"workflow_id": "wrong-workflow"}, deep=True)))
            reject("WC-N02_wrong_version", lambda: resolve(current_workflow=workflow.model_copy(update={"workflow_version": "wrong-version"}, deep=True)))
            reject("WC-N03_incoming_revision", lambda: resolve(current_payload=payload.model_copy(update={"business_revision": payload.business_revision + 1}, deep=True)))
            reject("WC-N03_incoming_document", lambda: resolve(current_payload=payload.model_copy(update={"document_id": "wrong-document"}, deep=True)))

            def bad_checkpoint(case, edit=None, identity=None, metadata=None, pending=None):
                value = copy.deepcopy(checkpoint.checkpoint)
                if edit is not None:
                    edit(value["channel_values"])
                bad_config = copy.deepcopy(checkpoint.config)
                bad_config["configurable"].update(identity or {})
                row = checkpoint._replace(checkpoint=value, config=bad_config,
                    metadata={**checkpoint.metadata, **(metadata or {})},
                    pending_writes=checkpoint.pending_writes if pending is None else pending)
                with patch.object(engine.checkpointer, "get_tuple", return_value=row):
                    reject(case, resolve)

            bad_checkpoint("WC-N02_returned_thread", identity={"thread_id": "wrong-run"})
            bad_checkpoint("WC-N02_returned_namespace", identity={"checkpoint_ns": "wrong:namespace"})
            bad_checkpoint("WC-N02_returned_carrier", identity={"workflow_namespace": "wrong:namespace"})
            bad_checkpoint("WC-N02_missing_id", identity={"checkpoint_id": ""})
            bad_checkpoint("WC-N02_metadata_source", metadata={"source": "update"})
            bad_checkpoint("WC-N02_metadata_namespace", metadata={"workflow_namespace": "wrong:namespace"})
            bad_checkpoint("WC-N02_failed_node_result", edit=lambda value: value["node_outputs"][-1]["result"].update(status="failed"))
            bad_checkpoint("WC-N02_wrong_route", edit=lambda value: value.update(business_route="technical_failure"))
            bad_checkpoint("WC-N02_failed_assembly", edit=lambda value: value["scheme_generation_payload"]["assembly_validation"].update(passed=False))
            bad_checkpoint("WC-N03_successor_is_not_predecessor", edit=lambda value: value.update(current_business_node="final_artifact_review"))
            bad_checkpoint("WC-N02_pending_writes_cannot_supply_commit",
                edit=lambda value: value.update(current_business_node="final_artifact_review"),
                pending=[("pending", "scheme_generation_payload", checkpoint.checkpoint["channel_values"]["scheme_generation_payload"])])
            for name, pinned in (("identity", checkpoint._replace(config={"configurable": {
                    **checkpoint.config["configurable"], "thread_id": "wrong-run"}})),
                    ("snapshot", checkpoint._replace(checkpoint=copy.deepcopy(checkpoint.checkpoint)))):
                if name == "snapshot":
                    pinned.checkpoint["channel_values"]["business_route"] = "technical_failure"
                with patch.object(engine.checkpointer, "get_tuple", side_effect=[checkpoint, pinned]):
                    reject("WC-N02_exact_pin_" + name, resolve)

            def bad_document(case, edit):
                changed = payload.model_copy(deep=True)
                edit(changed)
                reject(case, lambda: wc.validate_exact_writer_document({**snapshot, "payload": changed}, section_dependencies=owner))

            def change_result(field, value):
                return lambda changed: changed.section_execution_results.__setitem__(0,
                    changed.section_execution_results[0].model_copy(update={field: value}, deep=True))

            for name, field, value in (("binding_missing", "canonical_section_source_binding", None),
                    ("writer", "writer_execution_ref", second.writer_execution_ref),
                    ("invocation", "writer_generation_invocation_ref", second.writer_generation_invocation_ref),
                    ("plan", "canonical_plan_binding", second.canonical_plan_binding),
                    ("paragraph_manifest", "section_paragraph_manifest", second.section_paragraph_manifest),
                    ("coverage", "canonical_claim_coverage_proof_refs", []),
                    ("attempt", "active_execution_lineage", first.active_execution_lineage.model_copy(update={"attempt_id": "wrong-attempt"}, deep=True)),
                    ("forged_hash", "canonical_section_source_binding", first.canonical_section_source_binding.model_copy(update={"source_sha256": "0" * 64}, deep=True))):
                bad_document("WC-N06_projection_" + name, change_result(field, value))

            def coherent(changed, **updates):
                _project_section(changed, 0, changed.sections[0].model_copy(update=updates, deep=True))
                _render(changed)

            bad_document("WC-N07_one_projection_byte", lambda changed: setattr(changed.sections[0], "content", changed.sections[0].content + "!"))
            bad_document("WC-N07_coherent_body_differs_from_Writer", lambda changed: coherent(changed, content=changed.sections[0].content + "!"))
            bad_document("WC-N09_missing_section", lambda changed: changed.sections.pop())
            bad_document("WC-N09_duplicate_section", lambda changed: changed.sections.__setitem__(1, changed.sections[0].model_copy(deep=True)))
            bad_document("WC-N09_wrong_order", lambda changed: changed.sections.reverse())
            bad_document("WC-N09_wrong_order_number", lambda changed: coherent(changed, section_order=999))
            bad_document("WC-N09_empty_body", lambda changed: coherent(changed, content=""))
            bad_document("WC-N09_placeholder", lambda changed: coherent(changed, content="待生成"))
            bad_document("WC-N09_title_only", lambda changed: coherent(changed, content=changed.sections[0].section_title))
            bad_document("WC-N09_partial", lambda changed: coherent(changed, status=ExecutionStatus.PARTIAL_SUCCESS))
            bad_document("WC-N09_truncated", lambda changed: coherent(changed, truncation=changed.sections[0].truncation.model_copy(update={"truncated": True}, deep=True)))
            bad_document("WC-N09_generation_blocked", lambda changed: coherent(changed, extra={**changed.sections[0].extra, "generation_blocked": True}))
            bad_document("WC-N09_normal_generation_blocked", lambda changed: coherent(changed, eval_result=changed.sections[0].eval_result.model_copy(update={"checks": {**changed.sections[0].eval_result.checks, "normal_generation_blocked": True}}, deep=True)))
            bad_document("WC-N09_fulltext_byte", lambda changed: setattr(changed.assembly.draft, "full_text", changed.assembly.draft.full_text + "!"))
            bad_document("WC-N09_duplicate_required_title", lambda changed: changed.required_sections.append(changed.required_sections[0]))
            bad_document("WC-N09_unplanned_extra", lambda changed: changed.sections.append(changed.sections[0].model_copy(update={"section_id": "unplanned"}, deep=True)))

            after = _source_fingerprint(engine, owner, graph, payload.section_execution_results)
            assert before == after, "derivation changed canonical source or budget state"
            assert model_guard.call_count == write_guard.call_count == 0
            report.update({"source_fingerprint_before": before, "source_fingerprint_after": after,
                "model_calls": model_guard.call_count, "complete_source_calls": write_guard.call_count,
                "real_source_owner_read_calls": source_reads.call_count, "status": "PASS"})
            report["checks"].append({"case": "canonical_source_writer_lifecycle_and_budget_unchanged", "status": "PASS"})
        return report
    except BaseException as error:
        report.update({"status": "FAIL", "exceptions": _exception_evidence(error)})
        error.writer_copy_source_evidence = report
        raise
