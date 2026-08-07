"""Fail-closed recomputation of Stage E writer artifact integrity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.schemas.writer_governance import (
    ClaimAuditResultSchema,
    WriterGateBindingSchema,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    WriterClaimAuditor,
    blocked_claim_audit,
    classifier_policy_is_supported,
    clean_claim_span_text,
    content_sha256,
    normalized_content_sha256,
    writer_gate_binding_sha256,
)
from schemas.status import ExecutionStatus


@dataclass
class WriterArtifactIntegrityResult:
    checks: dict[str, bool]
    failure_codes: list[str]
    binding: WriterGateBindingSchema | None = None
    audit: ClaimAuditResultSchema | None = None
    recomputed_formal_deliverable_eligible: bool = False
    upstream_formal_deliverable_eligible: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def _claim_audit_projection(audit: ClaimAuditResultSchema) -> dict[str, Any]:
    """Fields that a deterministic re-audit must reproduce exactly."""

    return {
        "section_id": audit.section_id,
        "execution_status": audit.execution_status.value,
        "passed": audit.passed,
        "human_review_required": audit.human_review_required,
        "generated_claims": [
            item.model_dump(mode="json", exclude_none=True)
            for item in audit.generated_claims
        ],
        "violations": [
            item.model_dump(mode="json", exclude_none=True)
            for item in audit.violations
        ],
        "allowed_claim_types": [item.value for item in audit.allowed_claim_types],
        "forbidden_claim_types": [
            item.value for item in audit.forbidden_claim_types
        ],
        "required_qualifiers": list(audit.required_qualifiers),
        "applied_qualifiers": list(audit.applied_qualifiers),
        "checks": dict(audit.checks),
    }


def validate_writer_section_artifact(
    section: Any,
    *,
    document_id: str | None = None,
    run_id: str | None = None,
) -> WriterArtifactIntegrityResult:
    """Recompute formal eligibility from the current section artifact.

    No upstream boolean or historical ``claim_audit.passed`` value is accepted
    without validating current content, identity, Gate lineage, classifier policy,
    claim boundaries, spans, and deterministic re-audit equivalence.
    """

    extra = dict(getattr(section, "extra", None) or {})
    content = str(getattr(section, "content", "") or "")
    section_id = str(getattr(section, "section_id", "") or "")
    section_title = str(getattr(section, "section_title", "") or "")
    section_order = getattr(section, "section_order", None)
    upstream_eligible = bool(extra.get("formal_deliverable_eligible"))
    checks = {
        "writer_gate_bound": False,
        "writer_binding_identity_fields_present": False,
        "writer_binding_section_identity_valid": False,
        "writer_execution_status_consistent": False,
        "writer_generation_allowed": False,
        "writer_human_review_clear": False,
        "writer_claim_audit_present": False,
        "writer_claim_audit_schema_valid": False,
        "writer_audit_section_identity_valid": False,
        "writer_audit_content_hash_valid": False,
        "writer_audit_binding_hash_valid": False,
        "writer_evidence_lineage_fields_present": False,
        "writer_audit_evidence_lineage_valid": False,
        "writer_audit_claim_boundary_valid": False,
        "writer_audit_claim_spans_valid": False,
        "writer_classifier_policy_valid": False,
        "writer_claim_audit_recomputed_valid": False,
        "writer_claim_audits_passed": False,
        "writer_required_qualifiers_applied": False,
        "writer_section_status_deliverable": False,
        "writer_formal_eligibility_recomputed": False,
    }
    failure_codes: list[str] = []
    metadata: dict[str, Any] = {
        "section_id": section_id,
        "section_title": section_title,
        "current_content_sha256": content_sha256(content),
        "current_normalized_content_sha256": normalized_content_sha256(content),
    }

    binding_payload = extra.get("writer_gate_binding")
    if not isinstance(binding_payload, dict):
        failure_codes.append("writer_gate_missing")
        return WriterArtifactIntegrityResult(
            checks=checks,
            failure_codes=failure_codes,
            upstream_formal_deliverable_eligible=upstream_eligible,
            metadata=metadata,
        )
    try:
        binding = WriterGateBindingSchema.model_validate(binding_payload)
    except Exception as exc:
        failure_codes.append("writer_gate_invalid")
        metadata["writer_gate_validation_error"] = str(exc)
        return WriterArtifactIntegrityResult(
            checks=checks,
            failure_codes=failure_codes,
            upstream_formal_deliverable_eligible=upstream_eligible,
            metadata=metadata,
        )
    checks["writer_gate_bound"] = True
    metadata["current_writer_gate_binding_sha256"] = writer_gate_binding_sha256(
        binding
    )

    missing_identity_fields: list[str] = []
    if binding.section_order is None:
        missing_identity_fields.append("section_order")
        failure_codes.append("writer_binding_section_order_missing")
    if binding.document_id is None:
        missing_identity_fields.append("document_id")
        failure_codes.append("writer_binding_document_id_missing")
    if binding.run_id is None:
        missing_identity_fields.append("run_id")
        failure_codes.append("writer_binding_run_id_missing")
    identity_fields_present = not missing_identity_fields
    checks["writer_binding_identity_fields_present"] = identity_fields_present
    if not identity_fields_present:
        failure_codes.extend(
            [
                "writer_binding_identity_fields_missing",
                "legacy_writer_artifact_incomplete",
            ]
        )
        metadata["legacy_writer_artifact_route"] = "human_review"
        metadata["missing_writer_binding_identity_fields"] = missing_identity_fields

    binding_identity_valid = bool(
        identity_fields_present
        and binding.section_id == section_id
        and binding.section_title == section_title
        and binding.section_order == section_order
        and binding.document_id == document_id
        and binding.run_id == run_id
    )
    checks["writer_binding_section_identity_valid"] = binding_identity_valid
    if identity_fields_present and not binding_identity_valid:
        failure_codes.append("writer_binding_identity_mismatch")

    expected_status = (
        "not_assessed"
        if binding.legacy_artifact_incomplete
        else binding.execution_status.value
    )
    actual_status = str(extra.get("writer_execution_status") or "").strip()
    checks["writer_execution_status_consistent"] = actual_status == expected_status
    if actual_status != expected_status:
        failure_codes.append("writer_execution_status_mismatch")

    checks["writer_generation_allowed"] = bool(
        binding.ordinary_generation_permitted
        and binding.gate_decision.delivery_mode
        not in {DeliveryMode.BLOCKED, DeliveryMode.HUMAN_REVIEW}
    )
    if not checks["writer_generation_allowed"]:
        failure_codes.append("writer_generation_not_allowed")
    checks["writer_human_review_clear"] = not bool(
        binding.gate_decision.human_review_required
        or extra.get("need_human_review")
    )
    if not checks["writer_human_review_clear"]:
        failure_codes.append("writer_human_review_required")

    audit_payload = extra.get("claim_audit")
    checks["writer_claim_audit_present"] = isinstance(audit_payload, dict)
    if not checks["writer_claim_audit_present"]:
        failure_codes.append("writer_claim_audit_missing")
        return WriterArtifactIntegrityResult(
            checks=checks,
            failure_codes=list(dict.fromkeys(failure_codes)),
            binding=binding,
            upstream_formal_deliverable_eligible=upstream_eligible,
            metadata=metadata,
        )

    # Preserve specific cross-section diagnostics even if strict schema
    # validation rejects the payload before a typed audit can be created.
    raw_integrity = audit_payload.get("integrity")
    raw_integrity = raw_integrity if isinstance(raw_integrity, dict) else {}
    raw_claims = audit_payload.get("generated_claims")
    raw_claims = raw_claims if isinstance(raw_claims, list) else []
    raw_identity_valid = (
        str(audit_payload.get("section_id") or "") == section_id
        and str(raw_integrity.get("section_id") or "") == section_id
        and str(raw_integrity.get("section_title") or "") == section_title
        and all(
            isinstance(item, dict)
            and str(item.get("section_id") or "") == section_id
            for item in raw_claims
        )
    )
    if not raw_identity_valid:
        failure_codes.append("writer_audit_identity_mismatch")

    try:
        audit = ClaimAuditResultSchema.model_validate(audit_payload)
    except Exception as exc:
        failure_codes.append("writer_claim_audit_invalid")
        metadata["writer_claim_audit_validation_error"] = str(exc)
        return WriterArtifactIntegrityResult(
            checks=checks,
            failure_codes=list(dict.fromkeys(failure_codes)),
            binding=binding,
            upstream_formal_deliverable_eligible=upstream_eligible,
            metadata=metadata,
        )
    checks["writer_claim_audit_schema_valid"] = True

    audit_identity_valid = raw_identity_valid and (
        audit.section_id == section_id
        and audit.integrity.section_id == section_id
        and audit.integrity.section_title == section_title
        and all(item.section_id == section_id for item in audit.generated_claims)
    )
    checks["writer_audit_section_identity_valid"] = audit_identity_valid
    if not audit_identity_valid:
        failure_codes.append("writer_audit_identity_mismatch")

    expected_binding_hash = writer_gate_binding_sha256(binding)
    content_hash_valid = (
        audit.integrity.content_sha256 == content_sha256(content)
        and audit.integrity.normalized_content_sha256
        == normalized_content_sha256(content)
    )
    checks["writer_audit_content_hash_valid"] = content_hash_valid
    if not content_hash_valid:
        failure_codes.extend(
            ["writer_claim_audit_stale", "writer_audit_content_hash_mismatch"]
        )

    binding_hash_valid = (
        audit.integrity.writer_gate_binding_sha256 == expected_binding_hash
    )
    checks["writer_audit_binding_hash_valid"] = binding_hash_valid
    if not binding_hash_valid:
        failure_codes.append("writer_gate_binding_hash_mismatch")

    missing_lineage_fields: list[str] = []
    if binding.evidence_contract_sha256 is None:
        missing_lineage_fields.append("binding.evidence_contract_sha256")
        failure_codes.append("writer_evidence_contract_hash_missing")
    if binding.prompt_evidence_contract_sha256 is None:
        missing_lineage_fields.append("binding.prompt_evidence_contract_sha256")
        failure_codes.append("writer_prompt_evidence_contract_hash_missing")
    if audit.integrity.evidence_contract_sha256 is None:
        missing_lineage_fields.append("audit.evidence_contract_sha256")
        failure_codes.append("writer_audit_evidence_contract_hash_missing")
    if audit.integrity.prompt_evidence_contract_sha256 is None:
        missing_lineage_fields.append("audit.prompt_evidence_contract_sha256")
        failure_codes.append("writer_audit_prompt_evidence_contract_hash_missing")
    lineage_fields_present = not missing_lineage_fields
    checks["writer_evidence_lineage_fields_present"] = lineage_fields_present
    if not lineage_fields_present:
        failure_codes.extend(
            [
                "writer_evidence_lineage_fields_missing",
                "legacy_writer_artifact_incomplete",
            ]
        )
        metadata["legacy_writer_artifact_route"] = "human_review"
        metadata["missing_writer_evidence_lineage_fields"] = missing_lineage_fields

    evidence_lineage_valid = bool(
        lineage_fields_present
        and audit.integrity.evidence_contract_sha256
        == binding.evidence_contract_sha256
        and audit.integrity.prompt_evidence_contract_sha256
        == binding.prompt_evidence_contract_sha256
    )
    checks["writer_audit_evidence_lineage_valid"] = evidence_lineage_valid
    if lineage_fields_present and not evidence_lineage_valid:
        failure_codes.append("writer_audit_evidence_lineage_mismatch")

    claim_boundary_valid = (
        audit.execution_status == binding.execution_status
        and list(audit.allowed_claim_types)
        == list(binding.gate_decision.allowed_claim_types)
        and list(audit.forbidden_claim_types)
        == list(binding.gate_decision.forbidden_claim_types)
        and list(audit.required_qualifiers)
        == list(binding.gate_decision.required_qualifiers)
    )
    checks["writer_audit_claim_boundary_valid"] = claim_boundary_valid
    if not claim_boundary_valid:
        failure_codes.append("writer_audit_claim_boundary_mismatch")

    spans_valid = True
    invalid_claim_ids: list[str] = []
    for claim in audit.generated_claims:
        valid = (
            claim.section_id == section_id
            and 0 <= claim.span_start < claim.span_end <= len(content)
            and clean_claim_span_text(content[claim.span_start : claim.span_end])
            == claim.text
        )
        if not valid:
            spans_valid = False
            invalid_claim_ids.append(claim.claim_id)
    checks["writer_audit_claim_spans_valid"] = spans_valid
    if not spans_valid:
        failure_codes.append("writer_generated_claim_span_mismatch")
    metadata["invalid_generated_claim_ids"] = invalid_claim_ids

    classifier_valid = (
        classifier_policy_is_supported(
            classifier_id=audit.integrity.classifier_id,
            classifier_version=audit.integrity.classifier_version,
            policy_sha256=audit.integrity.classifier_policy_sha256,
        )
        and all(
            item.classifier_id == audit.integrity.classifier_id
            and item.classifier_version == audit.integrity.classifier_version
            for item in audit.generated_claims
        )
    )
    checks["writer_classifier_policy_valid"] = classifier_valid
    if not classifier_valid:
        failure_codes.append("writer_classifier_policy_mismatch")

    expected_qualifiers = list(binding.gate_decision.required_qualifiers)
    qualifier_valid = all(item in content for item in expected_qualifiers) and set(
        expected_qualifiers
    ).issubset(set(audit.applied_qualifiers))
    checks["writer_required_qualifiers_applied"] = qualifier_valid
    if not qualifier_valid:
        failure_codes.append("writer_required_qualifier_missing")

    if binding.legacy_artifact_incomplete:
        recomputed_audit = blocked_claim_audit(
            content,
            binding,
            audit_created_at=audit.audit_created_at,
            legacy_incomplete=True,
        )
    elif binding.generation_allowed:
        recomputed_audit = WriterClaimAuditor().audit(
            content,
            binding=binding,
            audit_created_at=audit.audit_created_at,
        )
    else:
        recomputed_audit = blocked_claim_audit(
            content,
            binding,
            audit_created_at=audit.audit_created_at,
        )
    recomputed_valid = (
        _claim_audit_projection(recomputed_audit) == _claim_audit_projection(audit)
    )
    checks["writer_claim_audit_recomputed_valid"] = recomputed_valid
    if not recomputed_valid:
        failure_codes.append("writer_claim_audit_recomputation_mismatch")

    checks["writer_claim_audits_passed"] = bool(audit.passed)
    if not audit.passed:
        failure_codes.append("writer_claim_audit_failed")

    section_status = getattr(section, "status", None)
    checks["writer_section_status_deliverable"] = section_status in {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.PARTIAL_SUCCESS,
    }
    if not checks["writer_section_status_deliverable"]:
        failure_codes.append("writer_section_status_not_deliverable")

    integrity_checks_for_delivery = (
        "writer_gate_bound",
        "writer_binding_identity_fields_present",
        "writer_binding_section_identity_valid",
        "writer_execution_status_consistent",
        "writer_generation_allowed",
        "writer_human_review_clear",
        "writer_claim_audit_present",
        "writer_claim_audit_schema_valid",
        "writer_audit_section_identity_valid",
        "writer_audit_content_hash_valid",
        "writer_audit_binding_hash_valid",
        "writer_evidence_lineage_fields_present",
        "writer_audit_evidence_lineage_valid",
        "writer_audit_claim_boundary_valid",
        "writer_audit_claim_spans_valid",
        "writer_classifier_policy_valid",
        "writer_claim_audit_recomputed_valid",
        "writer_claim_audits_passed",
        "writer_required_qualifiers_applied",
        "writer_section_status_deliverable",
    )
    recomputed_eligible = all(checks[item] for item in integrity_checks_for_delivery)
    checks["writer_formal_eligibility_recomputed"] = (
        upstream_eligible == recomputed_eligible
    )
    if upstream_eligible != recomputed_eligible:
        failure_codes.append("writer_formal_eligibility_mismatch")

    metadata.update(
        {
            "audit_content_sha256": audit.integrity.content_sha256,
            "audit_normalized_content_sha256": (
                audit.integrity.normalized_content_sha256
            ),
            "audit_writer_gate_binding_sha256": (
                audit.integrity.writer_gate_binding_sha256
            ),
            "expected_writer_gate_binding_sha256": expected_binding_hash,
            "upstream_formal_deliverable_eligible": upstream_eligible,
            "recomputed_formal_deliverable_eligible": recomputed_eligible,
            "claim_count": len(audit.generated_claims),
            "audit_violation_codes": [
                item.violation_code for item in audit.violations
            ],
        }
    )
    return WriterArtifactIntegrityResult(
        checks=checks,
        failure_codes=list(dict.fromkeys(failure_codes)),
        binding=binding,
        audit=audit,
        recomputed_formal_deliverable_eligible=recomputed_eligible,
        upstream_formal_deliverable_eligible=upstream_eligible,
        metadata=metadata,
    )


__all__ = [
    "WriterArtifactIntegrityResult",
    "validate_writer_section_artifact",
]
