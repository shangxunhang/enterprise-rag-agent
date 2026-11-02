"""Exact production Claim-source composition; no new source or verdict authority.

Evidence remains RAG-owned. Artifact refs accompanying item locators are the
versioned Package/Fact scope required to re-resolve authorization, not replacement
Evidence content identities. Binding commits remain ClaimVerificationRuntime-owned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSnapshotBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema, AttemptLifecycleStatus, RunLifecycleStatus,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimAuthorizedSourceRefSchema, ClaimBindingFactSchema,
    ClaimBindingFactsCandidateSchema, ClaimBindingRelation, ClaimBindingSupportKind,
    ClaimOperationContextSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanBindingSchema, SectionPlanContentSchema
from core.hashing import canonical_sha256
from schemas.claim import ClaimType, CURRENT_PROJECT_CLAIM_TYPES
from schemas.evidence_assessment import EvidenceRelevance
from schemas.evidence_source import AuthorityLevel, EvidenceRole
from schemas.rag import EvidenceBundleSchema
from .claim_pipeline import TargetClaimMaterializer
from .claim_verification_runtime import ClaimVerificationRuntime
from .writer_context import TargetWriterContextComposer
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    project_input_version_for_fact_normalization,
)


class TargetClaimCompositionError(RuntimeError):
    """A Claim source cannot be proven by its exact canonical owner."""


@dataclass(frozen=True)
class ClaimSourceMaterial:
    """Ephemeral resolved source observation, never a second source database."""

    source_ref: ClaimAuthorizedSourceRefSchema
    fact: ClaimBindingFactSchema
    material: dict[str, Any]


class TargetClaimRuntimeValidators:
    """Resolve the current Paragraph's actual authorized source scope."""

    POLICY_ID = "target-claim-exact-writer-sources"
    POLICY_VERSION = "v1"

    def __init__(
        self, *, artifact_runtime: ArtifactRuntime, paragraph_runtime=None,
        research_runtime=None, fact_snapshot_runtime=None,
        claim_resolver: Callable | None = None,
    ) -> None:
        self.artifact_runtime = artifact_runtime
        self.paragraph_runtime = paragraph_runtime
        self.research_runtime = research_runtime
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.claim_resolver = claim_resolver

    def _require_production_sources(self) -> None:
        if any(value is None for value in (
            self.paragraph_runtime, self.research_runtime, self.fact_snapshot_runtime, self.claim_resolver,
        )):
            raise TargetClaimCompositionError("canonical Claim source composition is incomplete")

    def _current_version(self, version_ref):
        head = self.artifact_runtime.resolve_active(version_ref.logical_ref)
        if head is None or head.active_version_ref != version_ref:
            raise TargetClaimCompositionError("Claim source Artifact version is not current")
        content, exact = self.artifact_runtime.read_version(version_ref)
        if exact != version_ref:
            raise TargetClaimCompositionError("Claim source resolver returned another immutable version")
        return content, head

    @staticmethod
    def _reference(kind, identity):
        return kind + "://sha256/" + canonical_sha256(identity)

    @staticmethod
    def reference_proposal_evidence_ids(bundle, package, claim_type) -> tuple[str, ...]:
        """Reuse selected reference capability as context, never current Fact eligibility."""

        contract = EvidenceBundleSchema.model_validate(bundle)
        canonical_type = ClaimType(claim_type)
        # The existing owner enforces completed and exact selected assessments.
        TargetClaimMaterializer.eligible_evidence_ids(contract, canonical_type)
        if canonical_type not in CURRENT_PROJECT_CLAIM_TYPES:
            return ()
        selected = set(contract.selected_evidence_ids) & {
            selection.evidence_id for selection in package.selections
        }
        items = {item.evidence_id: item for item in contract.items}
        assessments = {item.evidence_id: item for item in contract.item_assessments}
        result = []
        for evidence_id in sorted(selected):
            item = items[evidence_id]
            assessment = assessments[evidence_id]
            if item.disposition != "selected" or not item.context_text.strip():
                continue
            if not assessment.eligibility or assessment.relevance is EvidenceRelevance.IRRELEVANT:
                continue
            historical_reference = (
                assessment.evidence_role in {
                    EvidenceRole.HISTORICAL_CASE, EvidenceRole.TECHNICAL_REFERENCE,
                }
                or assessment.authority_level in {AuthorityLevel.REFERENCE, AuthorityLevel.DRAFT}
            )
            if not historical_reference:
                continue
            if any(
                evidence_id in TargetClaimMaterializer.eligible_evidence_ids(contract, own_type)
                for own_type in assessment.supported_claim_types
            ):
                result.append(evidence_id)
        return tuple(result)

    @staticmethod
    def _visible_evidence_material(item, assessment) -> tuple[dict[str, Any], str]:
        """Project one canonical Evidence item to the Writer-visible semantic authority."""

        usage_metadata = dict(assessment.metadata or {})
        assessment_view = assessment.model_dump(
            mode="json",
            exclude={"metadata"},
        )
        assessment_view["usage_constraints"] = {
            key: usage_metadata[key]
            for key in (
                "fact_claim_policies",
                "fact_visible_in_prompt",
                "required_qualifiers",
            )
            if key in usage_metadata
        }
        visible_text = str(item.context_text or "")
        return (
            {
                "evidence_id": item.evidence_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "context_text": visible_text,
                "context_text_sha256": canonical_sha256(visible_text),
                "assessment": assessment_view,
            },
            visible_text,
        )

    def materialize_sources(
        self, record: ClaimRecordSchema, lineage: ActiveExecutionLineageSchema,
    ) -> tuple[ClaimSourceMaterial, ...]:
        """Resolve canonical Claim before entering any Claim commit critical section."""
        self._require_production_sources()
        record = ClaimRecordSchema.model_validate(record)
        if self.claim_resolver(record.claim_ref) != record:
            raise TargetClaimCompositionError("Claim source request differs from canonical ClaimRecord")
        return self._materialize_sources(record, lineage)

    def _materialize_sources(self, record, lineage) -> tuple[ClaimSourceMaterial, ...]:
        """Recheck only foreign owners; the M6 caller validates Claim existence itself."""
        self._require_production_sources()
        lineage = ActiveExecutionLineageSchema.model_validate(lineage)
        section_id = record.claim_occurrence_ref.section_id
        if (record.claim_occurrence_ref.run_id != lineage.run_id
                or lineage.attempt_scope_ref != f"section:{section_id}"):
            raise TargetClaimCompositionError("Claim source request belongs to another Run/Section")
        self.paragraph_runtime.lifecycle_service.assert_attempt_committable(
            run_id=lineage.run_id, attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version,
        )
        return self._resolve_source_material(record, lineage)

    def read_current_sources(self, record, lineage) -> tuple[ClaimSourceMaterial, ...]:
        """Read exact current sources, including FINALIZING; never grants a commit."""
        self._require_production_sources()
        lineage = ActiveExecutionLineageSchema.model_validate(lineage)
        current = self.paragraph_runtime.lifecycle_service.resolve_read_lineage(
            lineage.run_id, lineage.attempt_scope_ref, expected_attempt_id=lineage.attempt_id,
        )
        if (current != lineage
                or current.run_status not in {RunLifecycleStatus.RUNNING, RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS}
                or current.attempt_status not in {AttemptLifecycleStatus.RUNNING, AttemptLifecycleStatus.COMPLETED}
                or record.claim_occurrence_ref.run_id != current.run_id
                or current.attempt_scope_ref != f"section:{record.claim_occurrence_ref.section_id}"):
            raise TargetClaimCompositionError("read-only Claim sources are not exact/current")
        return self._resolve_source_material(record, current, read_only=True)

    def _resolve_source_material(self, record, lineage, *, read_only=False) -> tuple[ClaimSourceMaterial, ...]:
        # Both entry guards resolve canonical current lineage. Commit callers use
        # _materialize_sources and retain the original RUNNING/committable guard.
        section_id = record.claim_occurrence_ref.section_id
        manifest_ref = self.paragraph_runtime.manifest_ref(
            run_id=lineage.run_id, section_id=section_id,
            active_lineage=lineage, write_instance_id=lineage.attempt_id,
        )
        if read_only:
            manifest = self.paragraph_runtime.load_manifest(manifest_ref)
            _, head = self._current_version(record.paragraph_version_ref)
            if record.paragraph_version_ref not in manifest.members:
                raise TargetClaimCompositionError("read Claim Paragraph is outside current Manifest")
            binding = ParagraphBindingSchema(version_ref=record.paragraph_version_ref,
                resolved_head_revision=head.head_revision, manifest_ref=manifest_ref,
                resolved_manifest_revision=manifest.manifest_revision)
        else:
            binding = self.paragraph_runtime.resolve_paragraph_binding(
                logical_ref=record.paragraph_version_ref.logical_ref, manifest_ref=manifest_ref,
            )
        if binding is None or binding.version_ref != record.paragraph_version_ref:
            raise TargetClaimCompositionError("Claim Paragraph is outside exact current membership")
        paragraph = (self.paragraph_runtime.read_paragraph(binding.version_ref) if read_only
                     else self.paragraph_runtime.validate_paragraph_binding_current(binding))
        source = paragraph.source_binding
        plan_raw, plan_head = self._current_version(source.section_plan_source.plan_version_ref)
        plan = SectionPlanContentSchema.model_validate(plan_raw)
        plan_binding = SectionPlanBindingSchema(
            plan_version_ref=source.section_plan_source.plan_version_ref,
            section_id=section_id, resolved_head_revision=plan_head.head_revision,
        )
        self.research_runtime.section_plan_runtime.validate_plan_binding_current(plan_binding)
        sections = [item for item in plan.sections if item.section_id == section_id]
        if len(sections) != 1 or plan.fact_snapshot_version_ref != source.fact_snapshot_version_ref:
            raise TargetClaimCompositionError("Claim Writer sources differ from exact SectionPlan")
        _, fact_head = self._current_version(source.fact_snapshot_version_ref)
        fact_binding = FactSnapshotBindingSchema(
            version_ref=source.fact_snapshot_version_ref, resolved_head_revision=fact_head.head_revision,
        )
        facts = self.fact_snapshot_runtime.read_binding(fact_binding, require_current=True)
        try:
            project_input_version = project_input_version_for_fact_normalization(
                facts.normalization_contract_version
            )
        except ValueError as exc:
            raise TargetClaimCompositionError(
                "Claim FactSnapshot has an unsupported normalization contract"
            ) from exc
        relevant = TargetWriterContextComposer._relevant_fact_payload(
            facts,
            sections[0],
            run_id=lineage.run_id,
            project_input_version=project_input_version,
        )
        _, package_head = self._current_version(source.evidence_package_version_ref)
        package_binding = EvidencePackageBindingSchema(
            version_ref=source.evidence_package_version_ref, resolved_head_revision=package_head.head_revision,
        )
        package = self.research_runtime.validate_evidence_package_binding_current(package_binding, require_source_fresh=True)
        if package.section_plan_source != source.section_plan_source:
            raise TargetClaimCompositionError("Claim EvidencePackage belongs to another Writer Plan")
        bundle = self.research_runtime.resolve_evidence_bundle(package.evidence_bundle_binding)
        claim_type = ClaimType(record.claim_occurrence_ref.claim_type)
        eligible = set(TargetClaimMaterializer.eligible_evidence_ids(bundle, claim_type))
        reference = set(self.reference_proposal_evidence_ids(bundle, package, claim_type))
        selected = {item.evidence_id for item in package.selections}
        authorized = selected & (eligible | reference)
        evidence_items = {item.evidence_id: item for item in bundle.items}
        assessments = {item.evidence_id: item for item in bundle.item_assessments}
        scope = lineage.attempt_scope_ref
        proof = self._reference("claim-source-current", {
            "paragraph_binding": binding.model_dump(mode="json"),
            "plan_binding": plan_binding.model_dump(mode="json"),
            "fact_binding": fact_binding.model_dump(mode="json"),
            "package_binding": package_binding.model_dump(mode="json"),
            "policy": self.POLICY_ID, "version": self.POLICY_VERSION,
        })
        rows: list[ClaimSourceMaterial] = []

        def add(kind, exact_ref, version, material, *, observed_value=None, force_contextual=False):
            ref = ClaimAuthorizedSourceRefSchema(source_kind=kind, exact_source_ref=exact_ref,
                                                artifact_version_ref=version, source_scope_ref=scope)
            # Equality is a deterministic observation, not generic free-text support.
            occurrence = record.claim_occurrence_ref
            same_value = (
                not force_contextual
                and observed_value is not None
                and occurrence.normalization_policy_id == "literal-claim-text"
                and occurrence.normalization_policy_version == "v1"
                and canonical_sha256(observed_value) == occurrence.normalized_value_hash
            )
            fact = ClaimBindingFactSchema(
                source_kind=kind, exact_source_ref=exact_ref,
                support_kind=(ClaimBindingSupportKind.AUTHORITATIVE if kind == "FACT" and same_value
                              else ClaimBindingSupportKind.DIRECT_SUPPORT if same_value
                              else ClaimBindingSupportKind.CONTEXTUAL_SUPPORT),
                relation=ClaimBindingRelation.SUPPORTS if same_value else ClaimBindingRelation.CONTEXT_ONLY,
                observed_value_hash=record.claim_occurrence_ref.normalized_value_hash if observed_value is not None else None,
                authoritative_value_hash=canonical_sha256(observed_value) if observed_value is not None else None,
                source_currentness_proof_ref=proof, source_scope_ref=scope,
            )
            rows.append(ClaimSourceMaterial(ref, fact, material))

        # Scope anchors prove exact negative/empty observations, never Evidence membership.
        add("EVIDENCE_PACKAGE", self._reference("claim-evidence-scope", package_binding.version_ref.model_dump(mode="json")),
            package_binding.version_ref, {"bundle": bundle, "package": package, "eligible_evidence_ids": tuple(sorted(selected & eligible))})
        add("FACT_SNAPSHOT", self._reference("claim-fact-scope", {
            "version_ref": fact_binding.version_ref.model_dump(mode="json"), "section_id": section_id,
            "fact_requirements": relevant["fact_requirements"],
        }), fact_binding.version_ref, relevant)
        for value in relevant["facts"]:
            add("FACT", self._reference("fact-record", {
                "version_ref": fact_binding.version_ref.model_dump(mode="json"), "fact_id": value["fact_id"],
            }), fact_binding.version_ref, value, observed_value=value["normalized_value"])
        for evidence_id in sorted(authorized):
            item = evidence_items[evidence_id]
            material, visible_text = self._visible_evidence_material(
                item,
                assessments[evidence_id],
            )
            add("EVIDENCE", self._reference("evidence-item", {
                "bundle_binding": package.evidence_bundle_binding.model_dump(mode="json"), "evidence_id": evidence_id,
            }), package_binding.version_ref, material, observed_value=visible_text,
                force_contextual=evidence_id not in eligible)
        return tuple(sorted(rows, key=lambda row: (
            row.source_ref.source_kind, row.source_ref.exact_source_ref,
            row.source_ref.artifact_version_ref.content_hash, row.source_ref.source_scope_ref or "",
        )))

    def build_binding_candidate(self, record, lineage) -> ClaimBindingFactsCandidateSchema:
        return self._candidate_from_sources(record, self.materialize_sources(record, lineage))

    def _candidate_from_sources(self, record, rows) -> ClaimBindingFactsCandidateSchema:
        sources = tuple(row.source_ref for row in rows)
        facts = tuple(sorted((row.fact for row in rows), key=lambda item: (
            item.source_kind, item.exact_source_ref, item.relation.value,
            item.support_kind.value, item.source_scope_ref or "",
        )))
        candidate = ClaimBindingFactsCandidateSchema(
            claim_ref=record.claim_ref, claim_occurrence_ref=record.claim_occurrence_ref,
            paragraph_version_ref=record.paragraph_version_ref,
            extraction_occurrence_ref=record.extraction_occurrence_ref,
            exact_authorized_source_refs=sources, facts=facts,
            source_set_hash=canonical_sha256([item.model_dump(mode="json") for item in sources]),
            resolver_policy_id=self.POLICY_ID, resolver_policy_version=self.POLICY_VERSION,
            binding_purpose="initial-writer-claim-sources",
            candidate_semantic_fingerprint="0" * 64,
        )
        return candidate.model_copy(update={
            "candidate_semantic_fingerprint": ClaimVerificationRuntime.binding_candidate_semantic_fingerprint(candidate),
        })

    def bind_claim_records(self, *, claim_runtime, records: Iterable[ClaimRecordSchema], operation_context):
        context = ClaimOperationContextSchema.model_validate(operation_context)
        claim_runtime.validate_operation_context(context)
        return tuple(claim_runtime.commit_binding_facts(
            self.build_binding_candidate(record, context.active_lineage), context,
        ) for record in records)

    def validate_read_binding_sources(self, candidate, lineage) -> None:
        candidate = ClaimBindingFactsCandidateSchema.model_validate(candidate)
        expected = self._candidate_from_sources(candidate, self.read_current_sources(candidate, lineage))
        if candidate != expected:
            raise TargetClaimCompositionError("read Claim binding differs from canonical current sources")

    def resolve_claim_read_lineage(self, claim):
        paragraph = self.paragraph_runtime.read_paragraph(claim.paragraph_version_ref)
        source = paragraph.source_binding
        return self.paragraph_runtime.lifecycle_service.resolve_read_lineage(
            claim.claim_occurrence_ref.run_id, f"section:{claim.section_id}",
            expected_attempt_id=source.write_instance_id,
        )

    def validate_binding_sources(self, candidate, lineage) -> None:
        candidate = ClaimBindingFactsCandidateSchema.model_validate(candidate)
        lineage = ActiveExecutionLineageSchema.model_validate(lineage)
        if candidate.claim_ref.claim_occurrence_ref.run_id != lineage.run_id:
            raise TargetClaimCompositionError("Claim BindingFacts candidate belongs to another Run")
        if self.claim_resolver is not None:
            # M6 already resolved the exact Claim before invoking this callback
            # while holding its non-reentrant file lock. Never re-enter that owner.
            expected = self._candidate_from_sources(candidate, self._materialize_sources(candidate, lineage))
            if candidate != expected:
                raise TargetClaimCompositionError("Claim binding sources or deterministic observations differ from canonical owners")
            return
        # Historical standalone construction only; production always supplies all resolvers.
        for source in candidate.exact_authorized_source_refs:
            version_ref = source.artifact_version_ref
            if version_ref is None:
                raise TargetClaimCompositionError("non-Artifact Claim source has no exact production adapter")
            if version_ref.logical_ref.run_id != lineage.run_id:
                raise TargetClaimCompositionError("Claim BindingFacts source belongs to another Run")
            self._current_version(version_ref)


__all__ = ["TargetClaimCompositionError", "TargetClaimRuntimeValidators", "ClaimSourceMaterial"]
