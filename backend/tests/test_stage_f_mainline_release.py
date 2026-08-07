from __future__ import annotations

from pathlib import Path
import os
from unittest.mock import patch

from tools.fake_rag_tool import FakeRAGTool
from test_phase4_section_agents_mainline import _run_fake as _run_phase4
from test_phase7_document_closure_mainline import (
    _latest_top_payload,
    _run_fake as _run_phase7,
)


def test_success_mainline_publishes_verified_release_manifest(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release-store"
    with patch.dict(
        os.environ,
        {"RAG_AGENT_RELEASE_ROOT": str(release_root)},
    ):
        summary, factory = _run_phase7(
            tmp_path / "run",
            run_id="stage_f_success_publication",
        )

    assert summary["status"] == "success"
    publication = summary["release_publication"]
    assert publication is not None
    assert publication["verified_after_write"] is True
    assert publication["idempotent_replay"] is False
    manifest = publication["manifest"]
    assert manifest["release_status"] == "published"
    assert manifest["immutable"] is True
    assert manifest["document_version"] == "v1.0"
    assert manifest["release_payload_sha256"] in manifest[
        "document_artifact"
    ]["content_hash"]
    assert manifest["eligibility_proof_sha256"] in manifest[
        "eligibility_artifact"
    ]["content_hash"]
    assert summary["deliverable_final_result"]["release_publication"] == publication
    assert len(list(release_root.rglob("*.json"))) == 3

    payload = _latest_top_payload(Path(factory.engines[0].checkpointer.path))
    assert payload["artifact_route"] == "deliverable"
    assert payload["release_publication"]["manifest"]["release_id"] == (
        manifest["release_id"]
    )
    assert payload["terminal_agent_result"]["metadata"][
        "release_verified_after_write"
    ] is True
    assert payload["terminal_agent_result"]["need_human_review"] is False


def test_rejected_mainline_does_not_publish_release_artifacts(
    tmp_path: Path,
) -> None:
    original = FakeRAGTool.run

    def blocked_gate(self, tool_call):
        result = original(self, tool_call)
        evidence = result.result["evidence"]
        extra = evidence["extra"]
        gate = extra["gate_decision"]
        gate.update(
            {
                "evidence_status": "absent",
                "delivery_mode": "blocked",
                "allowed_claim_types": [],
                "forbidden_claim_types": ["current_project_fact"],
                "covered_topics": [],
                "missing_topics": ["stage_f_forced_gap"],
                "human_review_required": False,
                "reason_codes": ["stage_f_forced_blocked_gate"],
            }
        )
        extra["generation_allowed"] = False
        return result

    release_root = tmp_path / "release-store"
    with patch.dict(
        os.environ,
        {"RAG_AGENT_RELEASE_ROOT": str(release_root)},
    ):
        summary, factory = _run_phase4(
            tmp_path / "run",
            run_id="stage_f_rejected_no_publication",
            patchers=(patch.object(FakeRAGTool, "run", blocked_gate),),
        )

    assert summary["status"] == "failed"
    assert summary["deliverable_final_result"] is None
    assert summary["release_publication"] is None
    assert summary["rejected_artifact"] is not None
    assert list(release_root.rglob("*.json")) == []

    payload = _latest_top_payload(Path(factory.engines[0].checkpointer.path))
    assert payload["artifact_route"] == "rejected"
    assert payload.get("release_publication") is None
    assert payload.get("deliverable_final_result") is None
