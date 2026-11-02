"""W15-S11 current Target publication evidence; not a legacy Stage F quality waiver."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.post_write.release import PostWritePublishedReleaseSchema, PostWriteReleaseInputProofSchema
from schemas.agent import AgentResultSchema
from task.task_manager import JsonlTaskManager
from test_phase7_document_closure_mainline import _latest_top_payload, _run_fake


@pytest.fixture(scope="module")
def target_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("w15-target-release-summary")
    # This suite tests exact publication projection, not default token capacity.
    # Configure fake-only startup headroom before the new isolated Run; retain
    # the complete eight-section fixture and the default 44 logical-call limit.
    default_path = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"
    default_bytes = default_path.read_bytes()
    policy = yaml.safe_load(default_bytes)
    assert policy["canonical_budget"]["logical_model_calls"] == 44
    assert policy["canonical_budget"]["model_tokens_total"] == 24000
    policy["canonical_budget"]["model_tokens_total"] = 64000
    policy_path = root / "explicit_fake_release_summary_policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    summary, factory = _run_fake(root, run_id="w15_target_release_summary",
        patchers=(patch.dict(os.environ, {"GROUNDED_GENERATION_POLICY_FILE": str(policy_path)}),))
    engine = factory.engines[0]
    source = next(
        item for item in summary["supervisor_result"]["result"]["sub_agent_results"]
        if item["agent_name"] == "SchemeWriterAgent"
    )
    native = source["result"]["target_release_publication"]
    assert native is not None, "precondition: actual Target publication must exist, not be invented by summary"
    publication = PostWritePublishedReleaseSchema.model_validate(native)
    payload = _latest_top_payload(Path(engine.checkpointer.path))
    assert payload["terminal_error"] is None
    budget = engine.budget_runtime(publication.manifest.task_id, publication.manifest.run_id).canonical_state()
    assert budget.limits.logical_model_calls == 44
    assert budget.limits.model_tokens_total == 64000
    assert budget.used.logical_model_calls == 43
    assert all(value == 0 for value in budget.active_reserved.model_dump().values())
    assert default_path.read_bytes() == default_bytes
    return root, summary, engine, source, publication, payload


def test_initial_summary_retains_exact_target_publication_without_legacy_alias(target_run):
    _, summary, _, source, publication, payload = target_run
    assert summary.get("target_release_publication") == source["result"]["target_release_publication"]
    assert summary["target_release_publication"] == payload["target_release_publication"]
    assert summary["release_publication"] is None
    assert summary["status"] == "partial_success"
    assert publication.verified_after_write is True
    assert publication.reuse_state.value == "NEW_COMMIT"
    assert publication.manifest.immutable is True
    assert publication.manifest.release_mode.value == "BEST_EFFORT"
    assert publication.manifest.quality_state_passthrough.value == "UNRESOLVED"
    assert publication.manifest.enforcement_action_passthrough.value == "OUTPUT_BEST_EFFORT"
    assert publication.manifest.unresolved_reason_refs
    assert len(payload["section_execution_results"]) == 8
    assert payload["artifact_route"] == "deliverable"
    assert payload["terminal_agent_result"]["metadata"]["release_verified_after_write"] is True


def test_actual_target_release_files_hashes_and_final_lineage(target_run):
    _, summary, engine, _, publication, payload = target_run
    composition = engine.stage_service.target_post_write_composition
    manifest = publication.manifest
    identity = {"task_id": manifest.task_id, "run_id": manifest.run_id}
    artifacts = (
        publication.manifest_artifact,
        manifest.release_payload_artifact,
        manifest.release_input_proof_artifact,
    )
    before = {}
    for reference in artifacts:
        stored_ref, content = composition.release_store.get_bytes_with_reference(reference.artifact_ref, **identity)
        assert stored_ref == reference
        assert stored_ref.content_hash == "sha256:" + hashlib.sha256(content).hexdigest()
        before[reference.artifact_ref] = content
    assert len(list(composition.release_store.root.rglob("*.json"))) == 3
    assert composition.release_store.get_json(publication.manifest_artifact.artifact_ref, **identity) == manifest.model_dump(mode="json")
    assert hashlib.sha256(before[manifest.release_payload_artifact.artifact_ref]).hexdigest() == manifest.release_payload_sha256
    # The envelope's content_hash covers bytes; the manifest proof hash covers
    # semantic proof fields excluding its own hash (checked by typed validation).
    proof = PostWriteReleaseInputProofSchema.model_validate(
        composition.release_store.get_json(manifest.release_input_proof_artifact.artifact_ref, **identity)
    )
    assert proof.proof_sha256 == manifest.release_input_proof_sha256
    assert proof.document_artifact_ref == manifest.document_artifact_ref
    assert manifest.document_artifact_ref.model_dump(mode="json") == payload["target_document_artifact_ref"]
    assert manifest.post_write_control_decision_ref.model_dump(mode="json") == payload["target_post_write_control_decision_ref"]
    assert manifest.run_completion_assessment_ref.model_dump(mode="json") == payload["target_run_completion_assessment_ref"]
    control = composition.control_store.resolve_exact(manifest.post_write_control_decision_ref)
    assert control.enforcement_action == manifest.enforcement_action_passthrough
    terminal = engine.lifecycle_service.load_run_lifecycle(manifest.run_id)
    assert terminal.status.value == "success"
    assert terminal.lifecycle_revision == manifest.run_lifecycle_revision
    assert terminal.run_completion_assessment_ref == manifest.run_completion_assessment_ref
    assert summary["status"] == "partial_success", "Run success does not upgrade semantic quality"
    for reference in artifacts:
        assert composition.release_store.get_bytes(reference.artifact_ref, **identity) == before[reference.artifact_ref]


def test_continuation_summary_retains_same_target_publication(target_run):
    root, summary, _, source, _, _ = target_run
    actual = MainlineApplicationService()._continuation_summary(
        workspace=SimpleNamespace(paths=lambda: summary["paths"]),
        task_manager=JsonlTaskManager(root / "tasks"),
        result=AgentResultSchema.model_validate(source),
    )
    assert actual.get("target_release_publication") == source["result"]["target_release_publication"]
    assert actual["release_publication"] is None
    assert actual["status"] == "partial_success"


def test_continuation_does_not_upgrade_legacy_publication_into_target(target_run):
    root, summary, _, source, _, _ = target_run
    legacy = {"legacy_marker": "not-canonical-target-authority"}
    result = AgentResultSchema.model_validate(source)
    result = result.model_copy(update={"result": {**result.result, "target_release_publication": None, "release_publication": legacy}})
    actual = MainlineApplicationService()._continuation_summary(
        workspace=SimpleNamespace(paths=lambda: summary["paths"]),
        task_manager=JsonlTaskManager(root / "tasks"), result=result,
    )
    assert "target_release_publication" in actual
    assert actual["target_release_publication"] is None
    assert actual["release_publication"] == legacy
