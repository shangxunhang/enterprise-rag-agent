"""Closed DOCUMENT wire controls only; no native worker/admission proof claim."""
from copy import deepcopy

import pytest

from apps.enterprise_document.schemas import scheme_generation_graph as existing_graph_schema
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimSupportSubjectRefSchema, EvidenceTargetSetRefSchema,
)
from core.hashing import canonical_sha256


def _schema():
    # Root exports after Step4 GREEN. Before that, meaningful call-time RED
    # cannot be mistaken for a module-import collection error.
    return getattr(existing_graph_schema, "BudgetDocumentRefusalSchema")


def _association_wire():
    key = canonical_sha256("TEST_OWNED_DOCUMENT_ASSOCIATION_INPUT")
    base = "artifact://citation-association/" + key
    return {
        "schema_version": "citation_budget_refusal_v1",
        "task_id": "TEST_OWNED_TASK", "run_id": "TEST_OWNED_RUN", "section_id": "TEST_OWNED_SECTION_A",
        "input_ref": base + "/input", "input_sha256": canonical_sha256("TEST_OWNED_INPUT"),
        "native_denial_ref": base + "/result", "native_denial_sha256": canonical_sha256("TEST_OWNED_RESULT"),
        "phase_proof_ref": base + "/result", "phase_proof_sha256": canonical_sha256("TEST_OWNED_NO_PROOF"),
        "semantic_input_sha256": key, "semantic_operation_ref": "citation-association-call://sha256/" + key,
        "artifact_state_ref": {
            "schema_version": "artifact_state_ref_v1", "artifact_id": "TEST_OWNED_DOCUMENT",
            "artifact_scope": "DOCUMENT", "artifact_version": 2, "artifact_hash": canonical_sha256("TEST_OWNED_BODY"),
        }, "artifact_version": 2,
    }


def _support_wire():
    raw = _association_wire()
    subject = ClaimSupportSubjectRefSchema(authority_kind="SIF07_CLAIM", subject_ref="TEST_OWNED_CLAIM")
    target = EvidenceTargetSetRefSchema(evidence_target_set_id="TEST_OWNED_TARGET_SET",
        claim_support_subject_ref=subject, evidence_member_refs=("TEST_OWNED_EVIDENCE",),
        target_set_contract_version="claim-authorized-evidence-v1", target_set_semantic_hash=canonical_sha256("TEST_OWNED_TARGET"))
    pending = {"subject": subject.model_dump(mode="json"), "target": target.model_dump(mode="json"),
               "contract": "claim-evidence-support-v1"}
    operation_key = canonical_sha256(pending)
    support_base = "artifact://claim-support/" + operation_key
    raw.update({
        "operation_kind": "DOCUMENT_SUPPORT", "section_id": "TEST_OWNED_SECTION_B",
        "document_input_ref": raw["input_ref"], "document_input_sha256": raw["input_sha256"],
        "document_result_ref": raw["native_denial_ref"], "document_result_sha256": raw["native_denial_sha256"],
        "document_identity": {"task_id": raw["task_id"], "run_id": raw["run_id"], "section_id": "TEST_OWNED_SECTION_A"},
        "association_budget_continuation": None, "pending_support": pending,
        "input_ref": support_base + "/input", "input_sha256": canonical_sha256("TEST_OWNED_SUPPORT_RECEIPT"),
        "native_denial_ref": support_base + "/candidate", "native_denial_sha256": canonical_sha256("TEST_OWNED_SUPPORT_FAILURE"),
        "phase_proof_ref": support_base + "/candidate", "phase_proof_sha256": canonical_sha256("TEST_OWNED_NO_SUPPORT_PROOF"),
        "semantic_input_sha256": canonical_sha256("TEST_OWNED_SUPPORT_MATERIAL"),
        "semantic_operation_ref": "claim-support-evaluation://sha256/" + operation_key,
    })
    return raw


def test_original_association_wire_normalizes_only_defaults_without_rewriting():
    raw = _association_wire()
    before = deepcopy(raw)
    explicit = deepcopy(raw)
    explicit.update(operation_kind="CITATION_ASSOCIATION", document_input_ref=None,
        document_input_sha256=None, document_result_ref=None, document_result_sha256=None,
        document_identity=None, association_budget_continuation=None, pending_support=None)
    assert _schema().model_validate(raw) == _schema().model_validate(explicit)
    assert raw == before and "operation_kind" not in raw


def test_support_wire_preserves_native_fingerprint_distinct_from_operation_key():
    raw = _support_wire()
    typed = _schema().model_validate(raw)
    assert typed.operation_kind == "DOCUMENT_SUPPORT"
    assert typed.semantic_input_sha256 != typed.semantic_operation_ref.rsplit("/", 1)[1]
    assert typed.section_id != typed.document_identity.section_id
    assert typed.native_denial_ref.endswith("/candidate")


@pytest.mark.parametrize("damage", (
    "unknown-owner", "native-suffix", "doc-pair", "missing-doc-hash", "doc-cross-run",
    "target-subject", "foreign-subject", "support-contract", "semantic-operation", "unknown-field", "cross-kind",
))
def test_support_wire_rejects_other_owner_or_incomplete_native_binding(damage):
    raw = _support_wire()
    if damage == "unknown-owner": raw["operation_kind"] = "ARBITRARY_TOOL"
    elif damage == "native-suffix": raw["native_denial_ref"] = raw["input_ref"].removesuffix("/input") + "/result"
    elif damage == "doc-pair": raw["document_result_ref"] += "/altered"
    elif damage == "missing-doc-hash": raw["document_result_sha256"] = None
    elif damage == "doc-cross-run": raw["document_identity"]["run_id"] += "_OTHER"
    elif damage == "target-subject": raw["pending_support"]["target"]["claim_support_subject_ref"]["subject_ref"] += "_OTHER"
    elif damage == "foreign-subject": raw["pending_support"]["subject"]["authority_kind"] = "POST_WRITE_FOREIGN_LOCAL"
    elif damage == "support-contract": raw["pending_support"]["contract"] = "UNAUTHORIZED_CONTRACT"
    elif damage == "semantic-operation": raw["semantic_operation_ref"] = "citation-association-call://sha256/" + raw["semantic_input_sha256"]
    elif damage == "unknown-field": raw["new_authority_owner"] = "TEST_OWNED_NO_AUTHORITY"
    elif damage == "cross-kind":
        raw["operation_kind"] = "CITATION_ASSOCIATION"
        raw["native_denial_ref"] = raw["input_ref"].removesuffix("/input") + "/result"
        raw["phase_proof_ref"] = raw["native_denial_ref"]
        raw["semantic_operation_ref"] = "citation-association-call://sha256/" + raw["semantic_input_sha256"]
    with pytest.raises(ValueError):
        _schema().model_validate(raw)
