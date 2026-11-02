"""Isolated first-chapter review-draft boundary tests; no Provider or Run.

The native eight-section fake-run fixture is separately blocked by local disk
pressure; these tests do NOT claim native Target E2E acceptance.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.enterprise_document.schemas.post_write.control import (
    DocumentPostWriteControlInputManifestSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    DeliveryGateDecisionRefSchema,
    EnforcementAction,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import CurrentnessState
from apps.enterprise_document.schemas.post_write.document import AssemblySelectionState
from apps.enterprise_document.services.agent_rag_v1.post_write_review_draft import (
    ReviewDraftUnavailable,
    TargetReviewDraftReader,
)
from apps.enterprise_document.services.post_write.artifact_state import (
    ArtifactStateIntegrityError,
)
from apps.enterprise_document.services.post_write.control import (
    ArtifactUsabilityIntegrityState,
)


def _fixture(*, quality=OverallQualityState.FAIL, action=EnforcementAction.OUTPUT_BEST_EFFORT):
    document_ref = ArtifactStateRefSchema(
        artifact_id="document-artifact:test:ch1",
        artifact_version=3,
        artifact_hash="a" * 64,
        artifact_scope=ArtifactScope.DOCUMENT,
    )
    control_ref = PostWriteControlDecisionRefSchema(
        control_decision_id="control-1",
        schema_version="post_write_control_decision_v1",
        control_contract_version="v1",
        control_decision_hash="b" * 64,
    )
    gate_ref = DeliveryGateDecisionRefSchema(
        delivery_gate_decision_id="gate-1",
        schema_version="delivery_gate_decision_v1",
        gate_contract_version="v1",
        decision_hash="c" * 64,
    )
    entry_ref = SimpleNamespace(id="entry-1")
    assembly_ref = SimpleNamespace(manifest_id="manifest-ch1")
    active = SimpleNamespace(
        artifact_state_ref=document_ref, assembly_input_manifest_ref=assembly_ref
    )
    existing_currentness = SimpleNamespace(
        currentness_state=CurrentnessState.CURRENT,
        active_projection=active,
        expected_active_entry_identity="current-entry",
    )
    manifest = DocumentPostWriteControlInputManifestSchema.model_construct(
        artifact_state_ref=document_ref,
        post_write_entry_ref=entry_ref,
        assembly_input_manifest_ref=assembly_ref,
    )
    assembly_manifest = SimpleNamespace(
        document_logical_id="document:test:ch1",
        required_section_keys=("ch1",),
        ordered_section_entries=(
            SimpleNamespace(
                section_key="ch1",
                selection_state=AssemblySelectionState.INCLUDED,
                section_assembly_candidate=SimpleNamespace(section_key="ch1"),
            ),
        ),
    )
    control = SimpleNamespace(
        input_manifest=manifest,
        enforcement_action=action,
        fresh_currentness_proof=existing_currentness,
        delivery_gate_decision_ref=gate_ref,
        quality_state_passthrough=quality,
        as_ref=lambda: control_ref,
    )
    sections = [
        {
            "section_key": "ch1",
            "visible_payload": {
                "payload_kind": "section",
                "section_title": "项目背景",
                "section_id": "ch1",
                "content": "本项目计划新建服务器12台，采购安装状态未确认。",
            },
        }
    ]
    payload = {
        "payload_kind": "document",
        "document_logical_id": "document:test:ch1",
        "included_sections": sections,
        "missing_section_keys": [],
        "withheld_section_keys": [],
        "partial": False,
    }
    raw = b"# First chapter validated output\n"
    output_ref = SimpleNamespace(
        artifact_ref="target-validated-output:test:ch1",
        task_id="task-ch1",
        run_id="run-ch1",
        input_snapshot_version="3",
        model_dump=lambda mode="json": {
            "artifact_ref": "target-validated-output:test:ch1",
            "task_id": "task-ch1",
            "run_id": "run-ch1",
        },
    )
    binding = SimpleNamespace(
        output_format="markdown",
        output_artifact_ref=output_ref,
        output_payload_sha256=sha256(raw).hexdigest(),
    )
    record = SimpleNamespace(
        artifact_ref=document_ref,
        validated_output_bindings=(binding,),
        canonical_payload_ref=payload,
    )
    gate = SimpleNamespace(
        gate_input_manifest=SimpleNamespace(artifact_state_ref=document_ref),
        overall_quality_state=quality,
        dimension_result_projections=(
            SimpleNamespace(dimension_id="faithfulness", state=quality),
        ),
        blocking_fact_refs=(SimpleNamespace(fact_id="fact-001"),) if quality == OverallQualityState.FAIL else (),
        warning_fact_refs=(),
        unresolved_reason_refs=("CONSISTENCY_PENDING",) if quality == OverallQualityState.UNRESOLVED else (),
        as_ref=lambda: gate_ref,
    )
    post_write = SimpleNamespace(
        binding_store=SimpleNamespace(
            resolve_assembly_manifest=Mock(return_value=assembly_manifest)
        ),
        control_store=SimpleNamespace(resolve_exact=Mock(return_value=control)),
        artifact_store=SimpleNamespace(
            resolve_exact=Mock(return_value=record),
            resolve_current=Mock(return_value=record),
        ),
        currentness_resolver=SimpleNamespace(
            prove=Mock(return_value=SimpleNamespace(
                currentness_state=CurrentnessState.CURRENT,
                expected_active_entry_identity="current-entry",
            ))
        ),
        usability_resolver=SimpleNamespace(
            resolve=Mock(return_value=SimpleNamespace(
                state=ArtifactUsabilityIntegrityState.TRUSTED_USABLE
            ))
        ),
        gate_store=SimpleNamespace(resolve_exact=Mock(return_value=gate)),
        output_conformance_verifier=SimpleNamespace(
            resolve_bound_conforming=Mock(return_value=True)
        ),
        output_store=SimpleNamespace(get_bytes=Mock(return_value=raw)),
    )
    arguments = {
        "document_artifact_ref": document_ref,
        "control_decision_ref": control_ref,
        "task_id": "task-ch1",
        "run_id": "run-ch1",
    }
    return post_write, arguments, gate


def test_nonpass_quality_does_not_withhold_trusted_review_draft():
    service, request, _gate = _fixture()
    draft = TargetReviewDraftReader(service).read(**request)
    assert "新建服务器12台" in draft.editable_review_text
    assert draft.validated_output_bytes == b"# First chapter validated output\n"
    assert draft.quality_state is OverallQualityState.FAIL
    assert draft.formal_quality_eligible is False
    assert draft.blocking_fact_ids == ("fact-001",)
    receipt = draft.as_result_reference()
    assert receipt["quality_state"] == "FAIL"
    assert receipt["validated_output_ref"]["task_id"] == "task-ch1"
    assert receipt["formal_release_authorized"] is False
    assert receipt["requires_human_confirmation"] is True
    assert service.artifact_store.resolve_exact.call_count == 1
    assert service.output_store.get_bytes.call_count == 1


def test_quality_pass_only_qualifies_for_request_not_human_authorization():
    service, request, _gate = _fixture(
        quality=OverallQualityState.PASS, action=EnforcementAction.OUTPUT
    )
    draft = TargetReviewDraftReader(service).read(**request)
    assert draft.formal_quality_eligible is True
    assert draft.as_result_reference()["formal_release_authorized"] is False
    unresolved = replace(
        draft, quality_state=OverallQualityState.UNRESOLVED,
        unresolved_reason_refs=("CHECK_PENDING",),
    )
    assert unresolved.formal_quality_eligible is False


def test_stale_artifact_head_does_not_pass_as_current():
    service, request, _ = _fixture()
    old = service.artifact_store.resolve_current()
    service.artifact_store.resolve_current.return_value = SimpleNamespace(
        artifact_ref=old.artifact_ref.model_copy(update={"artifact_version": 4})
    )
    with pytest.raises(ReviewDraftUnavailable, match="not the current"):
        TargetReviewDraftReader(service).read(**request)


def test_required_currentness_must_be_reproven():
    service, request, _ = _fixture()
    service.currentness_resolver.prove.return_value = SimpleNamespace(
        currentness_state=CurrentnessState.STALE,
        expected_active_entry_identity="old-entry",
    )
    with pytest.raises(ReviewDraftUnavailable, match="stale"):
        TargetReviewDraftReader(service).read(**request)


def test_technical_block_is_not_mislabeled_as_review_approval():
    service, request, _ = _fixture(action=EnforcementAction.BLOCK)
    with pytest.raises(ReviewDraftUnavailable, match="withheld"):
        TargetReviewDraftReader(service).read(**request)


def test_incomplete_document_cannot_masquerade_as_complete_review_draft():
    service, request, _ = _fixture()
    record = service.artifact_store.resolve_exact()
    record.canonical_payload_ref["partial"] = True
    with pytest.raises(ReviewDraftUnavailable, match="not a complete"):
        TargetReviewDraftReader(service).read(**request)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "foreign", "wrong_order"])
def test_review_draft_manifest_membership_rejects_bad_sections(mutation):
    service, request, _ = _fixture()
    record = service.artifact_store.resolve_exact()
    source = record.canonical_payload_ref["included_sections"][0]
    if mutation == "duplicate":
        record.canonical_payload_ref["included_sections"] = [source, dict(source)]
    elif mutation == "missing":
        record.canonical_payload_ref["included_sections"] = []
    elif mutation == "foreign":
        record.canonical_payload_ref["included_sections"] = [{
            "section_key": "other",
            "visible_payload": {**source["visible_payload"], "section_id": "other"},
        }]
    else:
        record.canonical_payload_ref["included_sections"] = [{
            "section_key": "ch1",
            "visible_payload": {**source["visible_payload"], "section_id": "other"},
        }]
    with pytest.raises(ReviewDraftUnavailable, match="Sections|membership"):
        TargetReviewDraftReader(service).read(**request)


def test_review_draft_rejects_incomplete_assembly_membership():
    service, request, _ = _fixture()
    manifest = service.binding_store.resolve_assembly_manifest()
    manifest.required_section_keys = ("ch1", "ch2")
    with pytest.raises(ReviewDraftUnavailable, match="Assembly Manifest"):
        TargetReviewDraftReader(service).read(**request)


def test_corrupt_output_and_cross_run_binding_are_rejected():
    service, request, _ = _fixture()
    service.output_conformance_verifier.resolve_bound_conforming.side_effect = (
        ArtifactStateIntegrityError("bad output")
    )
    with pytest.raises(ReviewDraftUnavailable, match="not conforming"):
        TargetReviewDraftReader(service).read(**request)
    service, request, _ = _fixture()
    with pytest.raises(ReviewDraftUnavailable, match="another Run"):
        TargetReviewDraftReader(service).read(**{**request, "run_id": "other-run"})


def test_invalid_gate_quality_authority_cannot_be_ignored():
    service, request, gate = _fixture()
    gate.overall_quality_state = OverallQualityState.PASS
    with pytest.raises(ReviewDraftUnavailable, match="authority mismatch"):
        TargetReviewDraftReader(service).read(**request)


def test_binding_bytes_check_after_conformance_resolution():
    service, request, _ = _fixture()
    service.output_store.get_bytes.return_value = b"tampered output"
    with pytest.raises(ReviewDraftUnavailable, match="checksum"):
        TargetReviewDraftReader(service).read(**request)
