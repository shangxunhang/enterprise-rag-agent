"""Canonical M6 Claim binding / verification / issue contracts.

Binding facts are deterministic source-relation truth. Shared Claim/Evidence
support is owned by claim_support.py. Verifier output is candidate-only.
VerificationResult + revision/head/issue state become canonical only through the
ClaimVerificationRuntime commit boundary.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ._base import ContractSchema
from .artifacts import ArtifactVersionRefSchema
from .claim_support import ClaimEvidenceSupportRefSchema
from .claims import ClaimOccurrenceRefSchema, ClaimRefSchema
from .enums import ClaimLifecycleStatus
from .lifecycle import ActiveExecutionLineageSchema
from ..pre_write_writer import WriterGenerationInvocationRefSchema
from core.hashing import canonical_sha256


VERIFIER_POLICY_ID = "canonical-initial-claim-verifier"
VERIFIER_POLICY_VERSION = "v2"
VERIFIER_RESPONSE_CONTRACT = "canonical_claim_verifier_v2"
AUTHORITY_PROJECTION_CONTRACT = "verifier-authority-projection-v1"


def project_input_basis_ref(invocation_ref, project_input_sha256):
    return "writer-project-input-basis://sha256/" + canonical_sha256({
        "basis_contract_version": "verifier-project-input-basis-v1",
        "writer_generation_invocation_ref": invocation_ref.model_dump(mode="json"),
        "project_input_sha256": project_input_sha256,
    })


def authority_universe_sha256(binding_ref, basis_ref):
    return canonical_sha256({
        "contract": "verifier-authority-universe-v1",
        "binding_facts_ref": binding_ref.model_dump(mode="json"),
        "project_input_basis_ref": basis_ref,
        "authority_projection_contract_version": AUTHORITY_PROJECTION_CONTRACT,
    })


def build_verifier_semantic_input_sha256(material):
    """Single identity definition shared by provider, candidate, commit and reads.

Lifecycle status/revision is a currentness observation, not cognition input;
the exact Run/Attempt/snapshot/scope identities remain semantic inputs.
"""
    data = material.model_dump(mode="json") if hasattr(material, "model_dump") else material
    keys = ("claim_ref", "claim_occurrence_ref", "paragraph_version_ref", "binding_facts_ref",
            "claim_evidence_support_ref", "verifier_project_input_basis", "authority_universe_sha256",
            "verification_authorization_ref", "verifier_policy_id", "verifier_policy_version",
            "response_contract", "deterministic_validation_facts")
    lineage = data["active_lineage"]
    return canonical_sha256({"contract": "verifier-semantic-input-v2",
        **{key: data[key] for key in keys},
        "execution_scope": {key: lineage[key] for key in (
            "run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version")}})


def _canonical_refs(refs):
    if tuple(sorted(set(refs))) != tuple(refs):
        raise ValueError("authority refs must be sorted and unique")


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenVerificationContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ClaimOperationContextSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_operation_context_v1"] = "claim_operation_context_v1"
    active_lineage: ActiveExecutionLineageSchema
    authorization_ref: NonEmptyStr
    operation_purpose: NonEmptyStr


class ClaimBindingRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    NO_MATERIAL_SUPPORT = "NO_MATERIAL_SUPPORT"


class ClaimBindingSupportKind(str, Enum):
    AUTHORITATIVE = "AUTHORITATIVE"
    DIRECT_SUPPORT = "DIRECT_SUPPORT"
    CONTEXTUAL_SUPPORT = "CONTEXTUAL_SUPPORT"


class ClaimAuthorizedSourceRefSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_authorized_source_ref_v1"] = (
        "claim_authorized_source_ref_v1"
    )
    source_kind: NonEmptyStr
    exact_source_ref: NonEmptyStr
    artifact_version_ref: ArtifactVersionRefSchema | None = None
    source_scope_ref: NonEmptyStr | None = None


class ClaimBindingFactSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_binding_fact_v1"] = "claim_binding_fact_v1"
    source_kind: NonEmptyStr
    exact_source_ref: NonEmptyStr
    support_kind: ClaimBindingSupportKind
    relation: ClaimBindingRelation
    observed_value_hash: Sha256Hex | None = None
    authoritative_value_hash: Sha256Hex | None = None
    unit_relation: NonEmptyStr | None = None
    source_currentness_proof_ref: NonEmptyStr
    source_scope_ref: NonEmptyStr | None = None


class ClaimBindingFactsRefSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_binding_facts_ref_v1"] = "claim_binding_facts_ref_v1"
    binding_facts_id: NonEmptyStr
    claim_ref: ClaimRefSchema
    source_set_hash: Sha256Hex
    binding_semantic_fingerprint: Sha256Hex


class ClaimBindingFactsCandidateSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_binding_facts_candidate_v1"] = (
        "claim_binding_facts_candidate_v1"
    )
    claim_ref: ClaimRefSchema
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    extraction_occurrence_ref: NonEmptyStr
    exact_authorized_source_refs: tuple[ClaimAuthorizedSourceRefSchema, ...] = Field(
        min_length=1
    )
    facts: tuple[ClaimBindingFactSchema, ...] = Field(min_length=1)
    source_set_hash: Sha256Hex
    resolver_policy_id: NonEmptyStr
    resolver_policy_version: NonEmptyStr
    binding_purpose: NonEmptyStr
    candidate_semantic_fingerprint: Sha256Hex

    @model_validator(mode="after")
    def _candidate_is_exact(self) -> "ClaimBindingFactsCandidateSchema":
        if self.claim_ref.claim_occurrence_ref != self.claim_occurrence_ref:
            raise ValueError("BindingFacts Claim ref/occurrence mismatch")
        if self.paragraph_version_ref != self.claim_occurrence_ref.paragraph_version_ref:
            raise ValueError("BindingFacts Paragraph ref mismatch")
        keys = [
            (
                item.source_kind,
                item.exact_source_ref,
                item.artifact_version_ref.content_hash
                if item.artifact_version_ref is not None
                else "",
                item.source_scope_ref or "",
            )
            for item in self.exact_authorized_source_refs
        ]
        if keys != sorted(keys):
            raise ValueError("authorized source refs must be canonically sorted")
        if len(keys) != len(set(keys)):
            raise ValueError("authorized source refs must be unique")
        fact_keys = [
            (
                item.source_kind,
                item.exact_source_ref,
                item.relation.value,
                item.support_kind.value,
                item.source_scope_ref or "",
            )
            for item in self.facts
        ]
        if fact_keys != sorted(fact_keys):
            raise ValueError("BindingFacts facts must be canonically sorted")
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("BindingFacts facts must be unique")
        authorized = {
            (item.source_kind, item.exact_source_ref)
            for item in self.exact_authorized_source_refs
        }
        if any(
            (fact.source_kind, fact.exact_source_ref) not in authorized
            for fact in self.facts
        ):
            raise ValueError("BindingFact references unauthorized source")
        return self


class ClaimBindingFactsRecordSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_binding_facts_record_v1"] = (
        "claim_binding_facts_record_v1"
    )
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_ref: ClaimRefSchema
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    extraction_occurrence_ref: NonEmptyStr
    exact_authorized_source_refs: tuple[ClaimAuthorizedSourceRefSchema, ...]
    facts: tuple[ClaimBindingFactSchema, ...]
    source_set_hash: Sha256Hex
    resolver_policy_id: NonEmptyStr
    resolver_policy_version: NonEmptyStr
    binding_purpose: NonEmptyStr
    binding_semantic_fingerprint: Sha256Hex
    committed_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _record_matches_ref(self) -> "ClaimBindingFactsRecordSchema":
        ref = self.binding_facts_ref
        if ref.claim_ref != self.claim_ref:
            raise ValueError("BindingFacts record Claim ref mismatch")
        if ref.source_set_hash != self.source_set_hash:
            raise ValueError("BindingFacts record source_set_hash mismatch")
        if ref.binding_semantic_fingerprint != self.binding_semantic_fingerprint:
            raise ValueError("BindingFacts record semantic fingerprint mismatch")
        return self


class VerifierProjectInputBasisV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_project_input_basis_v1"] = "verifier_project_input_basis_v1"
    writer_generation_invocation_ref: WriterGenerationInvocationRefSchema
    project_input_sha256: Sha256Hex
    project_input_basis_ref: NonEmptyStr

    @model_validator(mode="after")
    def _exact_basis(self):
        if self.project_input_basis_ref != project_input_basis_ref(
            self.writer_generation_invocation_ref, self.project_input_sha256):
            raise ValueError("ProjectInput basis identity mismatch")
        return self


class VerifierClaimInputSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_claim_input_v2"] = "verifier_claim_input_v2"
    claim_ref: ClaimRefSchema
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    exact_relevant_source_refs: tuple[ClaimAuthorizedSourceRefSchema, ...]
    active_lineage: ActiveExecutionLineageSchema
    verification_authorization_ref: NonEmptyStr
    deterministic_validation_facts: dict[str, Any] = Field(default_factory=dict)
    verifier_project_input_basis: VerifierProjectInputBasisV1
    authority_universe_sha256: Sha256Hex
    verifier_semantic_input_sha256: Sha256Hex
    allowed_authority_refs: tuple[NonEmptyStr, ...]
    verifier_policy_id: NonEmptyStr
    verifier_policy_version: NonEmptyStr
    response_contract: NonEmptyStr

    @model_validator(mode="after")
    def _exact_input(self):
        if self.claim_ref.claim_occurrence_ref != self.claim_occurrence_ref:
            raise ValueError("Verifier Claim occurrence mismatch")
        if self.paragraph_version_ref != self.claim_occurrence_ref.paragraph_version_ref:
            raise ValueError("Verifier Paragraph mismatch")
        if any(i.source_kind not in {"FACT_SNAPSHOT", "FACT", "EVIDENCE_PACKAGE", "EVIDENCE"} for i in self.exact_relevant_source_refs):
            raise ValueError("Verifier Binding source kind is inadmissible")
        allowed = tuple(sorted({item.exact_source_ref for item in self.exact_relevant_source_refs}
                               | {self.verifier_project_input_basis.project_input_basis_ref}))
        if self.allowed_authority_refs != allowed:
            raise ValueError("Verifier derived authority projection mismatch")
        if self.authority_universe_sha256 != authority_universe_sha256(
                self.binding_facts_ref, self.verifier_project_input_basis.project_input_basis_ref):
            raise ValueError("Verifier authority hash mismatch")
        if self.verifier_semantic_input_sha256 != build_verifier_semantic_input_sha256(self):
            raise ValueError("Verifier semantic input hash mismatch")
        validate_verifier_local_recovery_facts(self)
        return self


class VerificationVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    UNSUPPORTED = "UNSUPPORTED"
    REJECTED = "REJECTED"


class VerifierSemanticIssueCandidateSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_semantic_issue_candidate_v1"] = (
        "verifier_semantic_issue_candidate_v1"
    )
    issue_slot: NonEmptyStr
    issue_code: NonEmptyStr
    severity: NonEmptyStr
    semantic_category: NonEmptyStr
    message_hash: Sha256Hex
    rationale_ref: NonEmptyStr | None = None
    source_refs: tuple[NonEmptyStr, ...] = ()
    material_source_scope_key: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _canonical_source_refs(self) -> "VerifierSemanticIssueCandidateSchema":
        if tuple(sorted(self.source_refs)) != self.source_refs:
            raise ValueError("Verifier issue source_refs must be sorted")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("Verifier issue source_refs must be unique")
        return self


class VerifierSemanticResultCandidateSchema(VerifierClaimInputSchema):
    schema_version: Literal["verifier_semantic_result_candidate_v2"] = (
        "verifier_semantic_result_candidate_v2"
    )
    claim_ref: ClaimRefSchema
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    recommended_verdict: VerificationVerdict
    semantic_rationale_hash: Sha256Hex
    semantic_rationale_ref: NonEmptyStr | None = None
    issue_candidates: tuple[VerifierSemanticIssueCandidateSchema, ...] = ()
    verifier_policy_id: NonEmptyStr
    verifier_policy_version: NonEmptyStr
    verification_occurrence_ref: NonEmptyStr
    diagnostic_metadata: dict[str, Any] = Field(default_factory=dict)
    positive_basis_refs: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _issue_slots_unique(self) -> "VerifierSemanticResultCandidateSchema":
        slots = [item.issue_slot for item in self.issue_candidates]
        if len(slots) != len(set(slots)):
            raise ValueError("Verifier semantic issue slots must be unique")
        _canonical_refs(self.positive_basis_refs)
        if self.recommended_verdict is VerificationVerdict.VERIFIED and not self.positive_basis_refs:
            raise ValueError("VERIFIED requires non-empty positive basis")
        if not set(self.positive_basis_refs) <= set(self.allowed_authority_refs):
            raise ValueError("positive basis escapes authority universe")
        if any(not set(issue.source_refs) <= set(self.allowed_authority_refs) for issue in self.issue_candidates):
            raise ValueError("issue provenance escapes authority universe")
        return self


class VerificationResultRefSchema(_FrozenVerificationContract):
    schema_version: Literal["verification_result_ref_v1"] = "verification_result_ref_v1"
    verification_result_id: NonEmptyStr
    claim_ref: ClaimRefSchema
    verification_semantic_fingerprint: Sha256Hex


class VerifierIssueRefSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_issue_ref_v1"] = "verifier_issue_ref_v1"
    issue_id: NonEmptyStr
    issue_logical_id: NonEmptyStr
    issue_slot: NonEmptyStr
    verification_result_ref: VerificationResultRefSchema


class VerificationResultRecordSchema(_FrozenVerificationContract):
    schema_version: Literal["verification_result_record_v2"] = (
        "verification_result_record_v2"
    )
    verification_result_ref: VerificationResultRefSchema
    claim_ref: ClaimRefSchema
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    verifier_semantic_result_fingerprint: Sha256Hex
    verdict: VerificationVerdict
    issue_refs: tuple[VerifierIssueRefSchema, ...] = ()
    semantic_rationale_hash: Sha256Hex
    semantic_rationale_ref: NonEmptyStr | None = None
    verifier_policy_id: NonEmptyStr
    verifier_policy_version: NonEmptyStr
    binding_source_set_hash: Sha256Hex
    writer_generation_invocation_ref: WriterGenerationInvocationRefSchema
    project_input_sha256: Sha256Hex
    project_input_basis_ref: NonEmptyStr
    authority_universe_sha256: Sha256Hex
    verifier_semantic_input_sha256: Sha256Hex
    positive_basis_refs: tuple[NonEmptyStr, ...]
    active_lineage: ActiveExecutionLineageSchema
    verification_authorization_ref: NonEmptyStr
    response_contract: NonEmptyStr
    deterministic_validation_facts: dict[str, Any] = Field(default_factory=dict)
    verification_occurrence_ref: NonEmptyStr
    committed_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _exact_result(self):
        basis = VerifierProjectInputBasisV1(writer_generation_invocation_ref=self.writer_generation_invocation_ref,
            project_input_sha256=self.project_input_sha256, project_input_basis_ref=self.project_input_basis_ref)
        if self.verification_result_ref.claim_ref != self.claim_ref or self.claim_ref.claim_occurrence_ref != self.claim_occurrence_ref:
            raise ValueError("Result Claim identity mismatch")
        if self.paragraph_version_ref != self.claim_occurrence_ref.paragraph_version_ref:
            raise ValueError("Result Paragraph identity mismatch")
        if self.authority_universe_sha256 != authority_universe_sha256(self.binding_facts_ref, self.project_input_basis_ref):
            raise ValueError("Result authority identity mismatch")
        material = self.model_dump(mode="json")
        material["verifier_project_input_basis"] = basis.model_dump(mode="json")
        if self.verifier_semantic_input_sha256 != build_verifier_semantic_input_sha256(material):
            raise ValueError("Result semantic input identity mismatch")
        _canonical_refs(self.positive_basis_refs)
        if self.verdict is VerificationVerdict.VERIFIED and not self.positive_basis_refs:
            raise ValueError("VERIFIED requires positive basis")
        validate_verifier_local_recovery_facts(self)
        return self


class ClaimVerificationRevisionRefSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_verification_revision_ref_v1"] = (
        "claim_verification_revision_ref_v1"
    )
    claim_ref: ClaimRefSchema
    verification_revision: int = Field(ge=1)


class ClaimVerificationRevisionRecordSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_verification_revision_record_v1"] = (
        "claim_verification_revision_record_v1"
    )
    revision_ref: ClaimVerificationRevisionRefSchema
    claim_ref: ClaimRefSchema
    verification_revision: int = Field(ge=1)
    verification_result_ref: VerificationResultRefSchema
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    previous_verification_revision_ref: ClaimVerificationRevisionRefSchema | None = None
    source_set_hash: Sha256Hex
    committed_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _revision_is_consistent(self) -> "ClaimVerificationRevisionRecordSchema":
        if (
            self.revision_ref.claim_ref != self.claim_ref
            or self.revision_ref.verification_revision != self.verification_revision
        ):
            raise ValueError("Claim verification revision ref mismatch")
        if self.verification_revision == 1 and self.previous_verification_revision_ref is not None:
            raise ValueError("first Claim verification revision cannot have predecessor")
        if self.verification_revision > 1:
            previous = self.previous_verification_revision_ref
            if previous is None:
                raise ValueError("reverification requires previous revision ref")
            if (
                previous.claim_ref != self.claim_ref
                or previous.verification_revision != self.verification_revision - 1
            ):
                raise ValueError("previous Claim verification revision is not contiguous")
        return self


class ClaimCurrentHeadSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_current_head_v1"] = "claim_current_head_v1"
    claim_ref: ClaimRefSchema
    head_revision: int = Field(ge=1)
    active_verification_revision_ref: ClaimVerificationRevisionRefSchema
    lifecycle_status: ClaimLifecycleStatus
    expected_previous_head_revision: int | None = Field(default=None, ge=1)
    head_updated_by_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _head_revision_is_coherent(self) -> "ClaimCurrentHeadSchema":
        if self.active_verification_revision_ref.claim_ref != self.claim_ref:
            raise ValueError("Claim head active revision belongs to another Claim")
        if self.head_revision == 1 and self.expected_previous_head_revision is not None:
            raise ValueError("initial Claim head must expect no prior head")
        if self.head_revision > 1:
            if self.expected_previous_head_revision != self.head_revision - 1:
                raise ValueError("Claim head expected previous revision is not contiguous")
        return self


class VerifierIssueLifecycleStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class VerifierIssueRecordSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_issue_record_v1"] = "verifier_issue_record_v1"
    issue_ref: VerifierIssueRefSchema
    issue_logical_id: NonEmptyStr
    issue_slot: NonEmptyStr
    claim_ref: ClaimRefSchema | None = None
    paragraph_version_ref: ArtifactVersionRefSchema | None = None
    verification_result_ref: VerificationResultRefSchema
    issue_code: NonEmptyStr
    severity: NonEmptyStr
    semantic_category: NonEmptyStr
    message_hash: Sha256Hex
    rationale_ref: NonEmptyStr | None = None
    source_refs: tuple[NonEmptyStr, ...] = ()
    created_by_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _issue_identity_matches(self) -> "VerifierIssueRecordSchema":
        if (
            self.issue_ref.issue_logical_id != self.issue_logical_id
            or self.issue_ref.issue_slot != self.issue_slot
            or self.issue_ref.verification_result_ref != self.verification_result_ref
        ):
            raise ValueError("VerifierIssue record/ref identity mismatch")
        return self


class VerifierIssueHeadSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_issue_head_v1"] = "verifier_issue_head_v1"
    issue_ref: VerifierIssueRefSchema
    issue_revision: int = Field(ge=1)
    lifecycle_status: VerifierIssueLifecycleStatus
    resolution_ref: NonEmptyStr | None = None
    superseded_by_issue_ref: VerifierIssueRefSchema | None = None
    head_updated_by_operation_id: NonEmptyStr


class ClaimVerificationCommitReceiptSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_verification_commit_receipt_v1"] = (
        "claim_verification_commit_receipt_v1"
    )
    committed_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    verification_result_ref: VerificationResultRefSchema
    verification_revision_ref: ClaimVerificationRevisionRefSchema
    resulting_claim_head: ClaimCurrentHeadSchema
    issue_refs: tuple[VerifierIssueRefSchema, ...] = ()


class ClaimLifecycleTransitionReceiptSchema(_FrozenVerificationContract):
    schema_version: Literal["claim_lifecycle_transition_receipt_v1"] = (
        "claim_lifecycle_transition_receipt_v1"
    )
    committed_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    prior_head: ClaimCurrentHeadSchema
    resulting_head: ClaimCurrentHeadSchema
    reason_ref: NonEmptyStr
    successor_claim_refs: tuple[ClaimRefSchema, ...] = ()


class VerifierIssueLifecycleTransitionReceiptSchema(_FrozenVerificationContract):
    schema_version: Literal["verifier_issue_lifecycle_transition_receipt_v1"] = (
        "verifier_issue_lifecycle_transition_receipt_v1"
    )
    committed_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    prior_head: VerifierIssueHeadSchema
    resulting_head: VerifierIssueHeadSchema
    reason_ref: NonEmptyStr


class ActiveVerifiedClaimViewSchema(_FrozenVerificationContract):
    schema_version: Literal["active_verified_claim_view_v1"] = (
        "active_verified_claim_view_v1"
    )
    claim_ref: ClaimRefSchema
    claim_head_current: bool
    paragraph_self_current: bool
    paragraph_membership_current: bool
    binding_source_current: bool
    verification_current: bool
    lifecycle_active: bool
    verdict_verified: bool
    active_verified_claim: bool

    @model_validator(mode="after")
    def _derived_value_is_exact(self) -> "ActiveVerifiedClaimViewSchema":
        expected = all(
            (
                self.claim_head_current,
                self.paragraph_self_current,
                self.paragraph_membership_current,
                self.binding_source_current,
                self.verification_current,
                self.lifecycle_active,
                self.verdict_verified,
            )
        )
        if self.active_verified_claim != expected:
            raise ValueError("active_verified_claim must equal intrinsic SIF-07 dimensions")
        return self


# Local recovery is a prospective, non-owning binding inside the existing V2
# semantic input. These control records never constitute a VerificationResult.
VERIFIER_LOCAL_REGISTRATION_KEY = "verifier_local_correction_registration_v1"
VERIFIER_LOCAL_CORRECTION_KEY = "verifier_local_correction_v1"
VERIFIER_RECOVERY_CONTROL_VERSION = "claim_verifier_recovery_control_event_v1"
VERIFIER_LOCAL_CORRECTION_POLICY_ID = "verifier_local_correction_policy_v1"
EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED = "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"
VerifierRecoveryEventType = Literal[
    "verifier_candidate_rejected",
    "verifier_correction_authorized",
    "verifier_correction_terminated",
]


def verifier_feedback_sha256(feedback: str) -> str:
    """Hash the actual UTF-8 feedback bytes, without trimming or normalization."""
    return hashlib.sha256(feedback.encode("utf-8")).hexdigest()


def _recovery_material(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _recovery_content_sha256(value, *excluded):
    material = dict(_recovery_material(value))
    for key in excluded:
        material.pop(key, None)
    return canonical_sha256(material)


def build_verifier_registration_scope_sha256(material):
    """The initial registration mutex excludes policy/auth/feedback/profile.

    Exact source lineage is additionally bound by the selected original input;
    changing that lineage does not permit replacing a previously selected root.
    Lifecycle observation revisions are intentionally not cognition identity.
    """
    data = _recovery_material(material)
    lineage = data["active_lineage"]
    return canonical_sha256({
        "contract": "verifier-original-registration-scope-v1",
        "claim_ref": data["claim_ref"],
        "claim_occurrence_ref": data["claim_occurrence_ref"],
        "paragraph_version_ref": data["paragraph_version_ref"],
        "execution_scope": {key: lineage[key] for key in (
            "run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version")},
    })


class VerifierLocalCorrectionPolicyV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_local_correction_policy_v1"] = "verifier_local_correction_policy_v1"
    policy_id: Literal["verifier_local_correction_policy_v1"] = VERIFIER_LOCAL_CORRECTION_POLICY_ID
    eligible_rule: Literal["EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"] = EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED
    max_additional_calls: Literal[1] = 1
    policy_sha256: Sha256Hex

    @model_validator(mode="after")
    def _fixed_policy(self):
        if self.policy_sha256 != _recovery_content_sha256(self, "policy_sha256"):
            raise ValueError("Verifier local correction policy hash mismatch")
        return self


def verifier_local_correction_policy():
    material = {"schema_version": "verifier_local_correction_policy_v1",
        "policy_id": VERIFIER_LOCAL_CORRECTION_POLICY_ID,
        "eligible_rule": EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED, "max_additional_calls": 1}
    return VerifierLocalCorrectionPolicyV1(**material, policy_sha256=canonical_sha256(material))


class VerifierLocalCorrectionRegistrationV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_local_correction_registration_v1"] = "verifier_local_correction_registration_v1"
    policy: VerifierLocalCorrectionPolicyV1
    registration_scope_sha256: Sha256Hex


def build_verifier_local_correction_registration(material):
    return VerifierLocalCorrectionRegistrationV1(policy=verifier_local_correction_policy(),
        registration_scope_sha256=build_verifier_registration_scope_sha256(material))


def _verifier_physical_occurrence(material):
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": _recovery_material(material)["verifier_semantic_input_sha256"]})
    return "claim-verification-v2://sha256/" + key


class VerifierOriginalRootV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_original_root_v1"] = "verifier_original_root_v1"
    original_input: VerifierClaimInputSchema
    original_input_ref: NonEmptyStr
    original_input_sha256: Sha256Hex
    original_physical_occurrence: NonEmptyStr
    original_root_ref: NonEmptyStr
    original_root_sha256: Sha256Hex

    @model_validator(mode="after")
    def _registered_original(self):
        facts = self.original_input.deterministic_validation_facts
        if VERIFIER_LOCAL_REGISTRATION_KEY not in facts or VERIFIER_LOCAL_CORRECTION_KEY in facts:
            raise ValueError("Verifier root must be a prospectively registered initial input")
        registration = VerifierLocalCorrectionRegistrationV1.model_validate(facts[VERIFIER_LOCAL_REGISTRATION_KEY])
        if registration.registration_scope_sha256 != build_verifier_registration_scope_sha256(self.original_input):
            raise ValueError("Verifier root registration scope mismatch")
        if self.original_input_sha256 != canonical_sha256(self.original_input.model_dump(mode="json")):
            raise ValueError("Verifier root original input hash mismatch")
        if self.original_physical_occurrence != _verifier_physical_occurrence(self.original_input):
            raise ValueError("Verifier root physical occurrence mismatch")
        key = self.original_physical_occurrence.rsplit("/", 1)[-1]
        if self.original_input_ref != f"artifact://canonical-verifier-v2/{key}/input":
            raise ValueError("Verifier root original input ref mismatch")
        support_subject = self.original_input.claim_evidence_support_ref.claim_support_subject_ref
        if (self.original_input.binding_facts_ref.claim_ref != self.original_input.claim_ref
                or support_subject.authority_kind.value != "SIF07_CLAIM"
                or support_subject.subject_ref != self.original_input.claim_ref.claim_id):
            raise ValueError("Verifier root Binding/Support Claim mismatch")
        expected = _recovery_content_sha256(self, "original_root_ref", "original_root_sha256")
        if self.original_root_sha256 != expected or self.original_root_ref != "verifier-original-root://sha256/" + expected:
            raise ValueError("Verifier original root identity mismatch")
        return self


def build_verifier_original_root(original_input, input_ref=None, input_sha256=None, physical_occurrence=None):
    original_input = VerifierClaimInputSchema.model_validate(original_input)
    occurrence = physical_occurrence or _verifier_physical_occurrence(original_input)
    key = occurrence.rsplit("/", 1)[-1]
    material = {"schema_version": "verifier_original_root_v1",
        "original_input": original_input.model_dump(mode="json"),
        "original_input_ref": input_ref or f"artifact://canonical-verifier-v2/{key}/input",
        "original_input_sha256": input_sha256 or canonical_sha256(original_input.model_dump(mode="json")),
        "original_physical_occurrence": occurrence}
    digest = canonical_sha256(material)
    return VerifierOriginalRootV1(**material, original_root_ref="verifier-original-root://sha256/" + digest,
        original_root_sha256=digest)


def verifier_recovery_operation_id(event_type, root, *, ordinal=1, candidate_ref=None):
    """Permanent local namespace, independent of auth/feedback/random IDs."""
    if event_type not in {"verifier_candidate_rejected", "verifier_correction_authorized", "verifier_correction_terminated"}:
        raise ValueError("unknown Verifier recovery event type")
    if ordinal not in {0, 1} or (event_type != "verifier_candidate_rejected" and ordinal != 1):
        raise ValueError("invalid Verifier recovery ordinal")
    root_ref = root.original_root_ref if hasattr(root, "original_root_ref") else str(root)
    material = {"contract": VERIFIER_RECOVERY_CONTROL_VERSION, "event_type": event_type,
        "original_root_ref": root_ref, "correction_ordinal": ordinal}
    if event_type == "verifier_candidate_rejected":
        if not candidate_ref:
            raise ValueError("Verifier rejection operation requires exact candidate ref")
        material["candidate_ref"] = candidate_ref
    elif candidate_ref is not None:
        raise ValueError("Verifier slot/termination identity cannot depend on candidate")
    return canonical_sha256(material)


class VerifierCandidateContractViolationV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_candidate_contract_violation_v1"] = "verifier_candidate_contract_violation_v1"
    rule_code: Literal["EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"] = EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED
    original_input_ref: NonEmptyStr
    original_input_sha256: Sha256Hex
    candidate_ref: NonEmptyStr
    candidate_sha256: Sha256Hex
    violating_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    allowed_authority_refs: tuple[NonEmptyStr, ...]
    allowed_authority_sha256: Sha256Hex
    admissible_positive_basis_refs: tuple[NonEmptyStr, ...]
    admissible_positive_basis_sha256: Sha256Hex
    binding_facts_ref: ClaimBindingFactsRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    detection_policy_version: Literal["verifier-positive-basis-admission-v1"] = "verifier-positive-basis-admission-v1"
    rejection_stage: Literal["M6_PROVENANCE_VALIDATION"] = "M6_PROVENANCE_VALIDATION"
    violation_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_failure(self):
        for refs in (self.violating_refs, self.allowed_authority_refs, self.admissible_positive_basis_refs):
            _canonical_refs(refs)
        if self.allowed_authority_sha256 != canonical_sha256(self.allowed_authority_refs):
            raise ValueError("Verifier violation allowed authority hash mismatch")
        if self.admissible_positive_basis_sha256 != canonical_sha256(self.admissible_positive_basis_refs):
            raise ValueError("Verifier violation admissible basis hash mismatch")
        if (not set(self.violating_refs) <= set(self.allowed_authority_refs)
                or set(self.violating_refs) & set(self.admissible_positive_basis_refs)):
            raise ValueError("Verifier violation is not an allowed but unadmitted exact ref")
        if self.violation_sha256 != _recovery_content_sha256(self, "violation_sha256"):
            raise ValueError("Verifier violation evidence hash mismatch")
        return self


def build_verifier_candidate_contract_violation(**material):
    material = {"schema_version": "verifier_candidate_contract_violation_v1",
        "rule_code": EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED,
        "detection_policy_version": "verifier-positive-basis-admission-v1",
        "rejection_stage": "M6_PROVENANCE_VALIDATION", **material}
    material = {key: _recovery_material(value) for key, value in material.items()}
    material["violation_sha256"] = _recovery_content_sha256(material, "violation_sha256")
    return VerifierCandidateContractViolationV1.model_validate(material)


class VerifierRecoveryArtifactChainV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_recovery_artifact_chain_v1"] = "verifier_recovery_artifact_chain_v1"
    input_ref: NonEmptyStr
    input_sha256: Sha256Hex
    request_ref: NonEmptyStr
    request_sha256: Sha256Hex
    response_ref: NonEmptyStr
    response_sha256: Sha256Hex
    candidate_ref: NonEmptyStr
    candidate_sha256: Sha256Hex
    physical_occurrence: NonEmptyStr


class VerifierCandidateRejectedV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_candidate_rejected_v1"] = "verifier_candidate_rejected_v1"
    control_type: Literal["verifier_candidate_rejected"] = "verifier_candidate_rejected"
    root: VerifierOriginalRootV1
    correction_ordinal: Literal[0, 1]
    rejected_input: VerifierClaimInputSchema
    candidate: VerifierSemanticResultCandidateSchema
    request: dict[str, Any]
    response: dict[str, Any]
    artifact_chain: VerifierRecoveryArtifactChainV1
    violation: VerifierCandidateContractViolationV1
    rejection_ref: NonEmptyStr
    rejection_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_rejection(self):
        chain, violation = self.artifact_chain, self.violation
        input_data = self.rejected_input.model_dump(mode="json")
        candidate_data = self.candidate.model_dump(mode="json")
        if any(candidate_data[key] != value for key, value in input_data.items() if key != "schema_version"):
            raise ValueError("Verifier rejected candidate/input mismatch")
        if (chain.input_sha256 != canonical_sha256(input_data)
                or chain.candidate_sha256 != canonical_sha256(candidate_data)
                or chain.request_sha256 != canonical_sha256(self.request)
                or chain.response_sha256 != canonical_sha256(self.response)):
            raise ValueError("Verifier rejection artifact content hash mismatch")
        if chain.physical_occurrence != _verifier_physical_occurrence(self.rejected_input):
            raise ValueError("Verifier rejection physical occurrence mismatch")
        key = chain.physical_occurrence.rsplit("/", 1)[-1]
        prefix = f"artifact://canonical-verifier-v2/{key}"
        if (chain.input_ref, chain.request_ref, chain.response_ref, chain.candidate_ref) != tuple(
                prefix + suffix for suffix in ("/input", "/request", "/response", "/candidate")):
            raise ValueError("Verifier rejection artifact ref mismatch")
        if (violation.original_input_ref, violation.original_input_sha256, violation.candidate_ref,
                violation.candidate_sha256) != (chain.input_ref, chain.input_sha256, chain.candidate_ref, chain.candidate_sha256):
            raise ValueError("Verifier rejection violation parent mismatch")
        if (violation.binding_facts_ref != self.candidate.binding_facts_ref
                or violation.claim_evidence_support_ref != self.candidate.claim_evidence_support_ref
                or violation.allowed_authority_refs != self.candidate.allowed_authority_refs
                or not set(violation.violating_refs) <= set(self.candidate.positive_basis_refs)):
            raise ValueError("Verifier rejection typed evidence mismatch")
        if self.candidate.verification_occurrence_ref != chain.physical_occurrence:
            raise ValueError("Verifier rejection candidate occurrence mismatch")
        if self.response.get("success") is not True:
            raise ValueError("Verifier rejection requires a complete successful Provider response")
        for field in ("model_call_id", "task_id", "run_id"):
            if not self.request.get(field) or self.request.get(field) != self.response.get(field):
                raise ValueError("Verifier rejection request/response identity mismatch")
        if (self.request["model_call_id"] != "model_call_verifier_" + key
                or self.request["run_id"] != self.rejected_input.active_lineage.run_id):
            raise ValueError("Verifier rejection Provider occurrence mismatch")
        if self.correction_ordinal == 0:
            if self.rejected_input != self.root.original_input or chain.input_ref != self.root.original_input_ref:
                raise ValueError("Verifier original rejection root mismatch")
        else:
            facts = self.rejected_input.deterministic_validation_facts
            if VERIFIER_LOCAL_CORRECTION_KEY not in facts:
                raise ValueError("Verifier second rejection lacks correction binding")
            binding = VerifierLocalCorrectionBindingV1.model_validate(facts[VERIFIER_LOCAL_CORRECTION_KEY])
            if binding.authorization.root != self.root:
                raise ValueError("Verifier second rejection targets another root")
        expected = _recovery_content_sha256(self, "rejection_ref", "rejection_sha256")
        if self.rejection_sha256 != expected or self.rejection_ref != "verifier-candidate-rejected://sha256/" + expected:
            raise ValueError("Verifier rejection identity mismatch")
        return self


def build_verifier_candidate_rejection(**material):
    material = {"schema_version": "verifier_candidate_rejected_v1", "control_type": "verifier_candidate_rejected", **material}
    material = {key: _recovery_material(value) for key, value in material.items()}
    digest = _recovery_content_sha256(material, "rejection_ref", "rejection_sha256")
    material.update(rejection_ref="verifier-candidate-rejected://sha256/" + digest, rejection_sha256=digest)
    return VerifierCandidateRejectedV1.model_validate(material)


class VerifierCorrectionAuthorizedV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_correction_authorized_v1"] = "verifier_correction_authorized_v1"
    control_type: Literal["verifier_correction_authorized"] = "verifier_correction_authorized"
    root: VerifierOriginalRootV1
    correction_ordinal: Literal[1] = 1
    rejection_ref: NonEmptyStr
    rejection_sha256: Sha256Hex
    policy: VerifierLocalCorrectionPolicyV1
    authorization_ref: NonEmptyStr
    authorization_sha256: Sha256Hex
    feedback: Annotated[str, StringConstraints(min_length=1)]
    feedback_sha256: Sha256Hex
    expected_head_revision: None = None
    authorization_event_ref: NonEmptyStr
    grant_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_grant(self):
        registration = VerifierLocalCorrectionRegistrationV1.model_validate(
            self.root.original_input.deterministic_validation_facts[VERIFIER_LOCAL_REGISTRATION_KEY])
        if self.policy != registration.policy:
            raise ValueError("Verifier correction policy differs from original registration")
        operation_id = verifier_recovery_operation_id("verifier_correction_authorized", self.root)
        if self.authorization_ref != "verifier-correction-authorization://sha256/" + operation_id:
            raise ValueError("Verifier correction authorization does not name fixed slot1")
        if self.authorization_sha256 != verifier_feedback_sha256(self.authorization_ref):
            raise ValueError("Verifier correction authorization hash mismatch")
        if self.feedback_sha256 != verifier_feedback_sha256(self.feedback):
            raise ValueError("Verifier correction actual feedback hash mismatch")
        if self.rejection_ref != "verifier-candidate-rejected://sha256/" + self.rejection_sha256:
            raise ValueError("Verifier correction rejection ref/hash mismatch")
        expected = _recovery_content_sha256(self, "authorization_event_ref", "grant_sha256")
        if self.grant_sha256 != expected or self.authorization_event_ref != "verifier-correction-authorized://sha256/" + expected:
            raise ValueError("Verifier correction grant identity mismatch")
        return self


def build_verifier_correction_authorization(**material):
    root = VerifierOriginalRootV1.model_validate(material["root"])
    ref = "verifier-correction-authorization://sha256/" + verifier_recovery_operation_id("verifier_correction_authorized", root)
    material = {"schema_version": "verifier_correction_authorized_v1", "control_type": "verifier_correction_authorized",
        "correction_ordinal": 1, "expected_head_revision": None, "policy": verifier_local_correction_policy(),
        **material, "authorization_ref": ref, "authorization_sha256": verifier_feedback_sha256(ref)}
    material["feedback_sha256"] = verifier_feedback_sha256(material["feedback"])
    material = {key: _recovery_material(value) for key, value in material.items()}
    digest = _recovery_content_sha256(material, "authorization_event_ref", "grant_sha256")
    material.update(authorization_event_ref="verifier-correction-authorized://sha256/" + digest, grant_sha256=digest)
    return VerifierCorrectionAuthorizedV1.model_validate(material)


class VerifierLocalCorrectionBindingV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_local_correction_v1"] = "verifier_local_correction_v1"
    authorization: VerifierCorrectionAuthorizedV1
    binding_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_binding(self):
        if self.binding_sha256 != _recovery_content_sha256(self, "binding_sha256"):
            raise ValueError("Verifier correction binding hash mismatch")
        return self


def build_verifier_correction_binding(authorization):
    authorization = VerifierCorrectionAuthorizedV1.model_validate(authorization)
    material = {"schema_version": "verifier_local_correction_v1", "authorization": authorization.model_dump(mode="json")}
    return VerifierLocalCorrectionBindingV1(**material, binding_sha256=canonical_sha256(material))


class VerifierCorrectionTerminatedV1(_FrozenVerificationContract):
    schema_version: Literal["verifier_correction_terminated_v1"] = "verifier_correction_terminated_v1"
    control_type: Literal["verifier_correction_terminated"] = "verifier_correction_terminated"
    root: VerifierOriginalRootV1
    correction_ordinal: Literal[1] = 1
    authorization_event_ref: NonEmptyStr | None = None
    grant_sha256: Sha256Hex | None = None
    reason_code: Literal["SECOND_CONTRACT_VIOLATION", "PROVIDER_OUTCOME_UNKNOWN", "STALE", "CANCELLED", "PROVIDER_FAILURE", "ORPHAN_RESERVATION", "INVALID_RESPONSE"]
    reason_ref: NonEmptyStr
    second_rejection_ref: NonEmptyStr | None = None
    second_rejection_sha256: Sha256Hex | None = None
    termination_ref: NonEmptyStr
    termination_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_termination(self):
        if (self.authorization_event_ref is None) != (self.grant_sha256 is None):
            raise ValueError("Verifier termination grant linkage is incomplete")
        if self.authorization_event_ref is not None and self.authorization_event_ref != "verifier-correction-authorized://sha256/" + self.grant_sha256:
            raise ValueError("Verifier termination grant ref/hash mismatch")
        if (self.second_rejection_ref is None) != (self.second_rejection_sha256 is None):
            raise ValueError("Verifier termination second rejection linkage is incomplete")
        if self.second_rejection_ref is not None and self.second_rejection_ref != "verifier-candidate-rejected://sha256/" + self.second_rejection_sha256:
            raise ValueError("Verifier termination second rejection ref/hash mismatch")
        if self.reason_code == "SECOND_CONTRACT_VIOLATION" and (self.second_rejection_ref is None or self.authorization_event_ref is None):
            raise ValueError("Verifier second violation termination requires grant and rejection")
        expected = _recovery_content_sha256(self, "termination_ref", "termination_sha256")
        if self.termination_sha256 != expected or self.termination_ref != "verifier-correction-terminated://sha256/" + expected:
            raise ValueError("Verifier termination identity mismatch")
        return self


def build_verifier_correction_termination(**material):
    material = {"schema_version": "verifier_correction_terminated_v1", "control_type": "verifier_correction_terminated",
        "correction_ordinal": 1, "authorization_event_ref": None, "grant_sha256": None,
        "second_rejection_ref": None, "second_rejection_sha256": None, **material}
    material = {key: _recovery_material(value) for key, value in material.items()}
    digest = _recovery_content_sha256(material, "termination_ref", "termination_sha256")
    material.update(termination_ref="verifier-correction-terminated://sha256/" + digest, termination_sha256=digest)
    return VerifierCorrectionTerminatedV1.model_validate(material)


class ClaimVerifierRecoveryControlEventV1(_FrozenVerificationContract):
    schema_version: Literal["claim_verifier_recovery_control_event_v1"] = VERIFIER_RECOVERY_CONTROL_VERSION
    event_type: VerifierRecoveryEventType
    operation_id: Sha256Hex
    semantic_fingerprint: Sha256Hex
    result_ref: NonEmptyStr
    payload: Annotated[VerifierCandidateRejectedV1 | VerifierCorrectionAuthorizedV1 | VerifierCorrectionTerminatedV1,
        Field(discriminator="control_type")]

    @model_validator(mode="after")
    def _exact_control(self):
        payload = self.payload
        if self.event_type != payload.control_type:
            raise ValueError("Verifier control event type/payload mismatch")
        if isinstance(payload, VerifierCandidateRejectedV1):
            operation_id = verifier_recovery_operation_id(self.event_type, payload.root,
                ordinal=payload.correction_ordinal, candidate_ref=payload.artifact_chain.candidate_ref)
            fingerprint, result_ref = payload.rejection_sha256, payload.rejection_ref
        elif isinstance(payload, VerifierCorrectionAuthorizedV1):
            operation_id = verifier_recovery_operation_id(self.event_type, payload.root)
            fingerprint, result_ref = payload.grant_sha256, payload.authorization_event_ref
        else:
            operation_id = verifier_recovery_operation_id(self.event_type, payload.root)
            fingerprint, result_ref = payload.termination_sha256, payload.termination_ref
        if (self.operation_id, self.semantic_fingerprint, self.result_ref) != (operation_id, fingerprint, result_ref):
            raise ValueError("Verifier control operation/ref/fingerprint mismatch")
        return self


def build_verifier_recovery_control_event(payload):
    if isinstance(payload, VerifierCandidateRejectedV1):
        operation_id = verifier_recovery_operation_id(payload.control_type, payload.root,
            ordinal=payload.correction_ordinal, candidate_ref=payload.artifact_chain.candidate_ref)
        fingerprint, result_ref = payload.rejection_sha256, payload.rejection_ref
    elif isinstance(payload, VerifierCorrectionAuthorizedV1):
        operation_id = verifier_recovery_operation_id(payload.control_type, payload.root)
        fingerprint, result_ref = payload.grant_sha256, payload.authorization_event_ref
    elif isinstance(payload, VerifierCorrectionTerminatedV1):
        operation_id = verifier_recovery_operation_id(payload.control_type, payload.root)
        fingerprint, result_ref = payload.termination_sha256, payload.termination_ref
    else:
        raise ValueError("unknown Verifier recovery control payload")
    return ClaimVerifierRecoveryControlEventV1(event_type=payload.control_type, operation_id=operation_id,
        semantic_fingerprint=fingerprint, result_ref=result_ref, payload=payload)


def validate_verifier_local_recovery_facts(material):
    """Validate new nested semantics while leaving absent historical facts absent."""
    data = _recovery_material(material)
    facts = data.get("deterministic_validation_facts", {})
    registration_data = facts.get(VERIFIER_LOCAL_REGISTRATION_KEY)
    binding_data = facts.get(VERIFIER_LOCAL_CORRECTION_KEY)
    registration = None
    if VERIFIER_LOCAL_REGISTRATION_KEY in facts:
        registration = VerifierLocalCorrectionRegistrationV1.model_validate(registration_data)
        if registration.registration_scope_sha256 != build_verifier_registration_scope_sha256(data):
            raise ValueError("Verifier local registration scope mismatch")
    if VERIFIER_LOCAL_CORRECTION_KEY in facts:
        if registration is not None:
            raise ValueError("Verifier correction child cannot also register an original root")
        binding = VerifierLocalCorrectionBindingV1.model_validate(binding_data)
        original = binding.authorization.root.original_input.model_dump(mode="json")
        for field in ("claim_ref", "claim_occurrence_ref", "paragraph_version_ref", "binding_facts_ref",
                "claim_evidence_support_ref", "authority_universe_sha256", "verifier_policy_id",
                "verifier_policy_version", "response_contract"):
            if data[field] != original[field]:
                raise ValueError("Verifier correction child changes original exact source lineage")
        for field in ("exact_relevant_source_refs", "allowed_authority_refs"):
            if field in data and data[field] != original[field]:
                raise ValueError("Verifier correction child changes original source projection")
        child_facts = {key: value for key, value in facts.items() if key != VERIFIER_LOCAL_CORRECTION_KEY}
        original_facts = {key: value for key, value in original["deterministic_validation_facts"].items()
            if key != VERIFIER_LOCAL_REGISTRATION_KEY}
        if child_facts != original_facts:
            raise ValueError("Verifier correction child changes original deterministic input")
        basis = data.get("verifier_project_input_basis")
        if basis is None:  # Result V2 records the same exact basis flattened.
            basis = {"schema_version": "verifier_project_input_basis_v1",
                **{key: data[key] for key in ("writer_generation_invocation_ref", "project_input_sha256", "project_input_basis_ref")}}
        if basis != original["verifier_project_input_basis"]:
            raise ValueError("Verifier correction child changes exact Writer ProjectInput")
        if build_verifier_registration_scope_sha256(data) != build_verifier_registration_scope_sha256(original):
            raise ValueError("Verifier correction child changes original execution scope")
        if data["verification_authorization_ref"] != original["verification_authorization_ref"]:
            raise ValueError("Verifier correction child authorization mismatch")
        return None, binding
    return registration, None


def build_verifier_correction_feedback(violation, original_material, original_constraints=None):
    violation = VerifierCandidateContractViolationV1.model_validate(violation)
    evidence = json.dumps(violation.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    full_material = json.dumps({"original_material": _recovery_material(original_material),
        "original_constraints": _recovery_material(original_constraints)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "The previous complete candidate violates EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED. "
        "Each exact ref in violating_refs is allowed authority but is not admitted by the intersection of "
        "shared Support supporting_refs and admissible Binding observations. This rejection does not establish "
        "that the Evidence is false in reality. Perform one new complete judgment using all original material "
        "and all original verifier constraints. Any existing legal verdict (VERIFIED, UNSUPPORTED, CONFLICTED, "
        "REJECTED) is allowed. Do not repair, delete references from, or resubmit the old candidate.\n"
        "Typed guard evidence:\n" + evidence + "\nComplete original material and verifier constraints:\n" + full_material
    )


build_verifier_recovery_feedback = build_verifier_correction_feedback


__all__ = [
    "VERIFIER_LOCAL_REGISTRATION_KEY", "VERIFIER_LOCAL_CORRECTION_KEY", "VERIFIER_RECOVERY_CONTROL_VERSION",
    "VERIFIER_LOCAL_CORRECTION_POLICY_ID", "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED",
    "VerifierLocalCorrectionPolicyV1", "VerifierLocalCorrectionRegistrationV1", "VerifierOriginalRootV1",
    "VerifierCandidateContractViolationV1", "VerifierRecoveryArtifactChainV1", "VerifierCandidateRejectedV1",
    "VerifierCorrectionAuthorizedV1", "VerifierCorrectionTerminatedV1", "VerifierLocalCorrectionBindingV1",
    "ClaimVerifierRecoveryControlEventV1", "verifier_local_correction_policy", "verifier_feedback_sha256",
    "build_verifier_registration_scope_sha256", "build_verifier_local_correction_registration", "build_verifier_original_root",
    "verifier_recovery_operation_id", "build_verifier_candidate_rejection", "build_verifier_correction_authorization",
    "build_verifier_candidate_contract_violation",
    "build_verifier_correction_binding", "build_verifier_correction_termination", "build_verifier_recovery_control_event",
    "validate_verifier_local_recovery_facts", "build_verifier_correction_feedback", "build_verifier_recovery_feedback",
    "VerifierProjectInputBasisV1",
    "ActiveVerifiedClaimViewSchema",
    "ClaimAuthorizedSourceRefSchema",
    "ClaimBindingFactSchema",
    "ClaimBindingFactsCandidateSchema",
    "ClaimBindingFactsRecordSchema",
    "ClaimBindingFactsRefSchema",
    "ClaimBindingRelation",
    "ClaimBindingSupportKind",
    "ClaimCurrentHeadSchema",
    "ClaimLifecycleTransitionReceiptSchema",
    "ClaimOperationContextSchema",
    "ClaimVerificationCommitReceiptSchema",
    "ClaimVerificationRevisionRecordSchema",
    "ClaimVerificationRevisionRefSchema",
    "VerificationResultRecordSchema",
    "VerificationResultRefSchema",
    "VerificationVerdict",
    "VerifierClaimInputSchema",
    "VerifierIssueHeadSchema",
    "VerifierIssueLifecycleStatus",
    "VerifierIssueLifecycleTransitionReceiptSchema",
    "VerifierIssueRecordSchema",
    "VerifierIssueRefSchema",
    "VerifierSemanticIssueCandidateSchema",
    "VerifierSemanticResultCandidateSchema",
]
