"""Read-only Post-write projection of exact M6 Claim and shared Support results.

This boundary proves which current paragraph/body bytes the upstream work covers.
It does not extract, evaluate, verify, rewrite, or promote any Claim. Orthogonal
Faithfulness rules remain separate required completion items in the composition.
"""
from __future__ import annotations

from typing import Literal
from apps.enterprise_document.schemas.post_write.verification_generation import (
    VerificationGenerationScopeRefV1, VerificationGenerationEntryV1, VerificationGenerationManifestV1,
)

from pydantic import BaseModel, ConfigDict

from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimCoverageAccountingSchema, ClaimCoverageAccountingState, ClaimCoverageProofRefSchema,
    ClaimRecordSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema, VerificationResultRecordSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema, ExpectedItemExecutionAccountingSchema, ExpectedItemExecutionState,
    StageDescriptorSchema, StagePlanSchema, ValidationCompletionItemSchema,
    ValidationCompletionItemState, ValidationFactFamily, ValidationFactSchema,
    ValidationIntermediateKind, ValidationScope, ValidationSemanticState,
    ValidatorExecutionRecordSchema, ValidatorTerminalExecutionState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    UpstreamSIF07ClaimAuthorityResolutionSchema, UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.schemas.scheme_writer.section_execution import SectionExecutionResultSchema
from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionRefSchema, WriterGenerationInvocationRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import SectionParagraphManifestSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus, RunLifecycleStatus
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from apps.enterprise_document.services.post_write.faithfulness import FaithfulnessProjector
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer
from core.hashing import canonical_sha256
from core.runtime.execution_control import checkpoint_current_execution


class PostWriteClaimProjectionError(ValueError):
    """A current Artifact cannot be tied to one complete, exact upstream source set."""


class SectionClaimSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["section_claim_source_snapshot_v1"] = "section_claim_source_snapshot_v1"
    section_id: str
    section_title: str
    body_sha256: str
    writer_execution_ref: WriterExecutionRefSchema
    writer_invocation_ref: WriterGenerationInvocationRefSchema
    paragraph_manifest: SectionParagraphManifestSchema
    paragraph_body_spans: tuple[dict, ...]
    coverage_proof_refs: tuple[ClaimCoverageProofRefSchema, ...]
    coverage_records: tuple[ClaimCoverageAccountingSchema, ...]
    claim_records: tuple[ClaimRecordSchema, ...]
    binding_records: tuple[ClaimBindingFactsRecordSchema, ...]
    support_records: tuple[ClaimEvidenceSupportRecordSchema, ...]
    verification_records: tuple[VerificationResultRecordSchema, ...]


class PostWriteClaimSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["post_write_claim_source_snapshot_v1"] = "post_write_claim_source_snapshot_v1"
    artifact_state_ref: ArtifactStateRefSchema
    validation_scope: ValidationScope
    sections: tuple[SectionClaimSourceSnapshot, ...]
    assembly_input_manifest_ref: dict | None = None


class TargetPostWriteClaimProjection:
    """Compose existing source owners and persist only Post-write projections."""

    STAGE_REF = "stage-plan:post-write-shared-faithfulness:v3"
    BODY_FACT_CONTRACT_VERSION = "faithfulness_body_accounting_v1"
    CLAIM_PROJECTION_CONTRACT_VERSION = "claim-projection-v1"

    def __init__(self, *, claim_runtime, verifier_composition):
        if verifier_composition.claim_runtime is not claim_runtime:
            raise ValueError("Post-write Claim projection must share the canonical Claim owner")
        self.claim_runtime = claim_runtime
        self.verifier = verifier_composition
        self.support = verifier_composition.support
        self.paragraph_runtime = claim_runtime.paragraph_runtime

    @staticmethod
    def _unique(values, key, label):
        rows = tuple(values)
        identities = [key(row) for row in rows]
        if len(identities) != len(set(identities)):
            raise PostWriteClaimProjectionError(f"duplicate {label}")
        return rows

    def _section_sources(self, post_write, section):
        result = SectionExecutionResultSchema.model_validate(section)
        section_id = result.section.section_id
        lineage = result.active_execution_lineage
        manifest = result.section_paragraph_manifest
        if lineage is None or manifest is None or not manifest.members:
            raise PostWriteClaimProjectionError("Post-write requires current Section lineage and Paragraph membership")
        if manifest.manifest_ref.run_id != lineage.run_id or manifest.manifest_ref.section_id != section_id:
            raise PostWriteClaimProjectionError("Post-write Paragraph manifest belongs to another scope")
        if self.paragraph_runtime.load_manifest(manifest.manifest_ref) != manifest:
            raise PostWriteClaimProjectionError("Post-write Paragraph manifest changed")
        if result.writer_generation_invocation_ref is None or result.writer_execution_ref is None:
            raise PostWriteClaimProjectionError("Post-write Claim sources require exact Writer references")
        key = result.writer_execution_ref.logical_execution_key
        if (result.writer_generation_invocation_ref.logical_execution_key != key
                or key.writer_subject_ref.run_id != lineage.run_id
                or key.writer_subject_ref.section_id != section_id
                or key.write_instance_id != lineage.attempt_id):
            raise PostWriteClaimProjectionError("Post-write Writer refs do not match exact Section execution")
        invocation = post_write.writer_execution_store.resolve_writer_generation_invocation(result.writer_generation_invocation_ref)
        writer = post_write.writer_execution_store.resolve_writer_execution(result.writer_execution_ref)
        if (writer.artifact is None or writer.artifact.section_id != section_id
                or writer.artifact.content != result.section.content):
            raise PostWriteClaimProjectionError("Post-write Section body differs from the durable Writer artifact")
        current = self.claim_runtime.lifecycle_service.resolve_active_lineage(
            lineage.run_id, lineage.attempt_scope_ref,
        )
        if (current.attempt_id != lineage.attempt_id
                or current.input_snapshot_version != lineage.input_snapshot_version
                or current.attempt_lifecycle_revision != lineage.attempt_lifecycle_revision
                or current.attempt_status is not AttemptLifecycleStatus.RUNNING
                or current.run_status not in {RunLifecycleStatus.RUNNING, RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS}):
            raise PostWriteClaimProjectionError("Post-write read lineage is stale or not available")
        # Resolve the actual current lifecycle. Never make a terminal Run writable.
        lineage = current
        texts, spans, cursor = {}, [], 0
        body = result.section.content
        for member in manifest.members:
            binding = self.paragraph_runtime.resolve_paragraph_binding(logical_ref=member.logical_ref, manifest_ref=manifest.manifest_ref)
            if binding is None or binding.version_ref != member:
                # Manifest members are exact ArtifactVersionRefs, never ordinal IDs.
                raise PostWriteClaimProjectionError("Post-write Paragraph membership/version mismatch")
            content = self.paragraph_runtime.validate_paragraph_binding_current(binding)
            source = content.source_binding
            if (source.writer_subject_ref != invocation.writer_subject_ref
                    or source.write_instance_id != invocation.write_instance_id
                    or source.route_decision_ref != invocation.route_decision_ref):
                raise PostWriteClaimProjectionError("Paragraph source belongs to another Writer invocation")
            start = body.find(content.text, cursor)
            if start < 0 or body[cursor:start].strip():
                raise PostWriteClaimProjectionError("Post-write body contains unaccounted non-whitespace text")
            end = start + len(content.text)
            spans.append({"paragraph_version_ref": binding.version_ref.model_dump(mode="json"),
                          "start_offset": start, "end_offset": end,
                          "preceding_whitespace": body[cursor:start]})
            texts[binding.version_ref] = content.text
            cursor = end
        if body[cursor:].strip():
            raise PostWriteClaimProjectionError("Post-write body suffix is not covered by current Paragraphs")
        if spans:
            spans[-1]["trailing_whitespace"] = body[cursor:]

        proof_refs = self._unique(result.canonical_claim_coverage_proof_refs, lambda item: item.coverage_proof_id, "Claim coverage proof")
        if not proof_refs:
            raise PostWriteClaimProjectionError("Post-write is missing required Claim coverage")
        coverages = tuple(self.claim_runtime.resolve_claim_coverage(ref) for ref in proof_refs)
        covered_versions = [ref for item in coverages for ref in item.paragraph_version_refs]
        if len(covered_versions) != len(set(covered_versions)) or set(covered_versions) != set(texts):
            raise PostWriteClaimProjectionError("Claim coverage does not cover the exact current Paragraph set")
        occurrences = []
        for accounting in coverages:
            if accounting.coverage_scope_ref != f"section:{section_id}":
                raise PostWriteClaimProjectionError("Claim coverage belongs to another Section")
            for version in accounting.paragraph_version_refs:
                units = sorted((unit for unit in accounting.coverage_units if unit.paragraph_version_ref == version), key=lambda unit: unit.start_offset)
                if not units or units[0].start_offset != 0 or units[-1].end_offset != len(texts[version]):
                    raise PostWriteClaimProjectionError("Claim coverage does not reach exact Paragraph boundaries")
            occurrences.extend(occurrence for unit in accounting.coverage_units for occurrence in unit.claim_occurrence_refs)
        occurrences = self._unique(occurrences, lambda item: canonical_sha256(item.model_dump(mode="json")), "Claim occurrence")
        claims = tuple(sorted((self.claim_runtime.resolve_claim(self.claim_runtime.derive_claim_ref(item)) for item in occurrences), key=lambda item: item.claim_ref.claim_id))
        expected_claims = {item.claim_ref.claim_id: item for item in claims}
        verification_refs = self._unique(result.canonical_claim_verification_refs, lambda item: item.verification_result_id, "VerificationResult ref")
        if len(verification_refs) != len(claims) or {item.claim_ref.claim_id for item in verification_refs} != set(expected_claims):
            raise PostWriteClaimProjectionError("Post-write Verification refs do not match the complete Claim set")
        verifications = tuple(sorted((self.claim_runtime.resolve_verification_result(ref) for ref in verification_refs), key=lambda item: item.claim_ref.claim_id))
        bindings, supports = [], []
        for verification in verifications:
            claim = expected_claims[verification.claim_ref.claim_id]
            if verification.claim_ref != claim.claim_ref or verification.paragraph_version_ref != claim.paragraph_version_ref:
                raise PostWriteClaimProjectionError("Verification refers to another exact Claim/Paragraph")
            head = self.claim_runtime.resolve_claim_head(claim.claim_ref)
            if head is None or head.lifecycle_status.value != "active" or head.head_updated_by_operation_id != verification.committed_operation_id:
                raise PostWriteClaimProjectionError("Verification no longer names the current active Claim result")
            binding = self.claim_runtime.resolve_binding_facts(verification.binding_facts_ref)
            support = self.support.runtime.resolve_support(verification.claim_evidence_support_ref)
            if binding.claim_ref != claim.claim_ref or support.claim_support_subject_ref.subject_ref != claim.claim_ref.claim_id:
                raise PostWriteClaimProjectionError("Claim, Binding and shared Support disagree")
            self.verifier.validate_read_sources(binding, support, lineage)
            self.verifier.validate_v2_identity(claim, binding, support, verification, lineage)
            bindings.append(binding)
            supports.append(support)
        return SectionClaimSourceSnapshot(
            section_id=section_id, section_title=result.section.section_title,
            body_sha256=canonical_sha256(body), paragraph_body_spans=tuple(spans),
            writer_execution_ref=result.writer_execution_ref,
            writer_invocation_ref=result.writer_generation_invocation_ref,
            paragraph_manifest=manifest,
            coverage_proof_refs=proof_refs, coverage_records=coverages, claim_records=claims,
            binding_records=tuple(bindings), support_records=tuple(supports), verification_records=verifications,
        )

    def resolve_sources(self, *, post_write, artifact_ref, scope, source_sections, assembly_input_manifest_ref=None, citation_validation_run_id=None, citation_subject_hash=None, generation_identity_only=False):
        scope = ValidationScope(scope)
        source_sections = self._unique(source_sections, lambda item: item.section.section_id, "Section source")
        if not source_sections and scope is ValidationScope.SECTION:
            raise PostWriteClaimProjectionError("Post-write Claim projection requires Section sources")
        record = post_write.artifact_store.resolve_exact(artifact_ref)
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)
        expected_payloads = {}
        if scope is ValidationScope.SECTION:
            if len(source_sections) != 1 or payload.get("payload_kind") != "section":
                raise PostWriteClaimProjectionError("Section projection scope mismatch")
            expected_payloads[payload.get("section_id")] = payload
        elif scope is ValidationScope.DOCUMENT:
            if assembly_input_manifest_ref is None or payload.get("payload_kind") != "document":
                raise PostWriteClaimProjectionError("Document projection requires exact assembly inputs")
            manifest = post_write.binding_store.resolve_assembly_manifest(assembly_input_manifest_ref)
            if payload.get("document_logical_id") != manifest.document_logical_id:
                raise PostWriteClaimProjectionError("Document logical scope differs from the assembly manifest")
            source_by_id = {item.section.section_id: item for item in source_sections}
            visible = payload.get("included_sections")
            if not isinstance(visible, list):
                raise PostWriteClaimProjectionError("Document visible Section list is missing")
            expected = []
            for entry in manifest.ordered_section_entries:
                if entry.section_assembly_candidate is not None:
                    candidate = entry.section_assembly_candidate
                    source_section = source_by_id.get(entry.section_key)
                    if (source_section is None or candidate.section_evidence_snapshot_ref.writer_invocation_ref
                            != source_section.writer_generation_invocation_ref):
                        raise PostWriteClaimProjectionError("Document assembly names another exact Writer invocation")
                    source_record = post_write.artifact_store.resolve_exact(candidate.section_artifact_ref)
                    # Existing assembler embeds the exact canonical payload,
                    # including its bound visible-projection metadata.
                    expected.append({"section_key": entry.section_key, "visible_payload": dict(source_record.canonical_payload_ref)})
            # Assembly may remap display aliases, but it must retain a frozen,
            # provenance-checked mapping. Validation never re-allocates labels.
            if generation_identity_only:
                # Identity precedes the ValidationRun and its citation accounting.
                # Complete-item, pre-Gate and finalization reads still validate
                # the exact frozen projection against committed citation records.
                pass
            elif citation_validation_run_id is None:
                post_write.citation_integrity.validate_document_projection(payload, manifest)
            else:
                post_write.citation_integrity.validate_committed_document(payload, manifest,
                    validation_run_id=citation_validation_run_id, subject_hash=citation_subject_hash,
                    artifact_ref=artifact_ref, source_sections=source_sections)
            expected_payloads = {item["visible_payload"]["section_id"]: item["visible_payload"] for item in expected}
            if len(expected_payloads) != len(expected):
                raise PostWriteClaimProjectionError("Document repeats a Section source")
        else:
            raise PostWriteClaimProjectionError("unsupported Post-write Claim projection scope")
        if set(expected_payloads) != {item.section.section_id for item in source_sections}:
            raise PostWriteClaimProjectionError("Post-write Section source set differs from actual Artifact")
        snapshots = []
        for section in source_sections:
            expected = expected_payloads[section.section.section_id]
            if expected.get("content") != section.section.content or expected.get("section_title") != section.section.section_title:
                raise PostWriteClaimProjectionError("Post-write Section content/title is not the current Artifact payload")
            snapshots.append(self._section_sources(post_write, section))
        return PostWriteClaimSourceSnapshot(
            artifact_state_ref=artifact_ref, validation_scope=scope, sections=tuple(snapshots),
            assembly_input_manifest_ref=(assembly_input_manifest_ref.model_dump(mode="json") if assembly_input_manifest_ref is not None else None),
        )

    def build_verification_generation(self, *, snapshot, run_id, scope, target_ref, strict_currentness=False):
        """Use I5 enumeration; prove exact canonical active set and currentness."""
        scope_ref = VerificationGenerationScopeRefV1(run_id=run_id, validation_scope=scope, target_ref=target_ref)
        if snapshot.validation_scope != scope_ref.validation_scope:
            raise PostWriteClaimProjectionError("generation snapshot scope differs")
        if scope_ref.validation_scope is ValidationScope.SECTION and (
                len(snapshot.sections) != 1 or snapshot.sections[0].section_id != target_ref):
            raise PostWriteClaimProjectionError("generation Section target differs")
        entries, observations = [], []
        self._unique(snapshot.sections, lambda row: row.section_id, "generation Section")
        for section in snapshot.sections:
            claims = self._unique(section.claim_records, lambda row: row.claim_ref.claim_id, "generation Claim")
            results = self._unique(section.verification_records, lambda row: row.claim_ref.claim_id, "generation Result")
            sources = self.claim_runtime.resolve_current_generation_sources(run_id=run_id,
                section_id=section.section_id, paragraph_version_refs=section.paragraph_manifest.members)
            if ({claim.claim_ref.claim_id: claim for claim in claims}
                    != {row[0].claim_ref.claim_id: row[0] for row in sources}
                    or {result.claim_ref.claim_id: result for result in results}
                    != {row[0].claim_ref.claim_id: row[3] for row in sources}):
                raise PostWriteClaimProjectionError("generation snapshot differs from complete current Claim/head/result set")
            for claim, head, revision, result in sources:
                view = self.claim_runtime.resolve_active_verified_claim(claim.claim_ref, strict_currentness=strict_currentness)
                if (not view.verification_current or not view.claim_head_current or not view.lifecycle_active
                        or self.claim_runtime.resolve_claim_head(claim.claim_ref) != head
                        or revision.revision_ref != head.active_verification_revision_ref
                        or result.verification_result_ref != revision.verification_result_ref
                        or result.schema_version != "verification_result_record_v2"):
                    raise PostWriteClaimProjectionError("generation verification/head is not current")
                entries.append(VerificationGenerationEntryV1(claim_ref=claim.claim_ref,
                    claim_head_revision=head.head_revision, verification_result_ref=result.verification_result_ref))
            observations.append((section, sources))
        for section, sources in observations:
            if self.claim_runtime.resolve_current_generation_sources(run_id=run_id, section_id=section.section_id,
                    paragraph_version_refs=section.paragraph_manifest.members) != sources:
                raise PostWriteClaimProjectionError("generation sources changed during read")
        return VerificationGenerationManifestV1.build(scope_ref, entries)

    @classmethod
    def stage_plan(cls):
        stage = StageDescriptorSchema(stage_ref="post-write-shared-faithfulness-stage:v3", stage_version="v3", sequence_index=0,
            stage_input_schema_ref="schema://post-write-claim-source-snapshot/v1", stage_output_schema_ref="schema://faithfulness-verification-fact/v1",
            execution_policy_ref="policy://post-write-shared-faithfulness/v3", execution_policy_version="v3", timeout_seconds=10, max_attempts=1)
        base = {"schema_version": "validator_stage_plan_v1", "stage_plan_ref": cls.STAGE_REF,
            "validator_id": FaithfulnessProjector.VALIDATOR_ID, "validator_version": FaithfulnessProjector.VALIDATOR_VERSION,
            "stage_plan_version": "v3", "stages": [stage.model_dump(mode="json")]}
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    @classmethod
    def _facts(cls, snapshot, *, validation_run_id, subject_hash):
        states = {record.accounting_state for section in snapshot.sections for record in section.coverage_records}
        state = ValidationSemanticState.INDETERMINATE if ClaimCoverageAccountingState.INDETERMINATE in states else ValidationSemanticState.PASS
        snapshot_hash = canonical_sha256(snapshot.model_dump(mode="json"))
        base = {"contract_version": cls.BODY_FACT_CONTRACT_VERSION, "fact_id": "validation-fact:faithfulness-body:" + canonical_sha256({"run": validation_run_id, "subject": subject_hash, "sources": snapshot_hash}),
            "validation_run_id": validation_run_id, "subject_hash": subject_hash,
            "producer_validator_id": FaithfulnessProjector.VALIDATOR_ID, "producer_validator_version": FaithfulnessProjector.VALIDATOR_VERSION,
            "family": ValidationFactFamily.FAITHFULNESS, "target_ref": "body-claim-accounting:" + snapshot_hash,
            "semantic_state": state, "payload": {"source_snapshot_sha256": snapshot_hash,
                "accounting_states": sorted(item.value for item in states), "checked_scope": "exact_current_paragraph_body",
                "remaining_scope_and_certainty_rules": "NOT_EVALUATED_BY_THIS_PROJECTION"}, "schema_version": "validation_fact_v1"}
        facts = [ValidationFactSchema(**base, fact_sha256=canonical_sha256(base))]
        for section in snapshot.sections:
            results = {record.claim_ref: record for record in cls._unique(
                section.verification_records, lambda record: record.claim_ref, "Claim Result")}
            claims = cls._unique(section.claim_records, lambda record: record.claim_ref, "Claim")
            if set(results) != {record.claim_ref for record in claims}:
                raise PostWriteClaimProjectionError("Claim and Result exact sets differ")
            if not section.claim_records:
                # Empty upstream accounting is not permission for foreign re-extraction.
                continue
            authority = UpstreamSIF07ClaimAuthorityResolutionSchema(
                state=UpstreamSIF07ClaimAuthorityState.PRESENT,
                claim_refs=tuple(record.claim_ref for record in section.claim_records),
                resolution_reason_ref="post-write-claim-source://sha256/" + snapshot_hash,
            )
            targets = ArtifactClaimTargetMaterializer.materialize(authority_resolution=authority,
                artifact_state_ref=snapshot.artifact_state_ref, validation_subject_hash=subject_hash, semantic_version=cls.CLAIM_PROJECTION_CONTRACT_VERSION,
                resolved_claim_records={record.claim_ref.claim_id: record for record in section.claim_records})
            for target in targets:
                facts.append(FaithfulnessProjector.project(validation_run_id=validation_run_id, subject_hash=subject_hash,
                    claim_target=target, verification_result=results[target.source_claim_ref], source_snapshot_sha256=snapshot_hash,
                    additional_rule_ids=("PW-R-FTH-02", "PW-R-FTH-04"),
                    observation_codes=("EXACT_UPSTREAM_VERIFICATION_RESULT_PROJECTION",)))
        return tuple(facts)

    def is_terminal_replay(self, source_sections) -> bool:
        # This predicate controls complete_item's new Validation writes as well
        # as SUCCESS replay. FINALIZING read access must not open that branch.
        run_ids = {section.active_execution_lineage.run_id for section in source_sections
                   if section.active_execution_lineage is not None}
        if len(run_ids) != 1:
            raise PostWriteClaimProjectionError("Post-write sources must name one exact Run")
        run = self.claim_runtime.lifecycle_service.load_run_lifecycle(next(iter(run_ids)))
        if run.status not in {RunLifecycleStatus.RUNNING, RunLifecycleStatus.SUCCESS}:
            raise PostWriteClaimProjectionError("Post-write cannot validate a failed or unavailable Run")
        return run.status is RunLifecycleStatus.SUCCESS

    def resolve_committed_source_snapshot(
        self,
        *,
        post_write,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        connection=None,
        validate_canonical_results=True,
    ) -> PostWriteClaimSourceSnapshot:
        """Reuse the exact source snapshot committed by ``faithfulness:shared``.

        This is a same-ValidationRun physical reuse seam only.  It does not
        inherit a semantic PASS and it does not replace the FTH-03 pre-commit
        currentness recheck.
        """
        item_ref = "faithfulness:shared:" + artifact_ref.artifact_hash
        read_kwargs = {} if connection is None else {'connection': connection}
        record = post_write.validation_store.find_validator_execution_for_item(
            validation_run_id=validation_run_id,
            validator_id=FaithfulnessProjector.VALIDATOR_ID,
            expected_item_ref=item_ref,
            **read_kwargs,
        )
        if record is None:
            raise PostWriteClaimProjectionError(
                "FTH-03 requires the committed shared-Faithfulness source snapshot"
            )
        if (
            record.validation_run_id != validation_run_id
            or record.subject_hash != subject_hash
            or record.validator_id != self.stage_plan().validator_id
            or record.validator_version != FaithfulnessProjector.VALIDATOR_VERSION
            or record.stage_plan_ref != self.stage_plan().stage_plan_ref
            or record.stage_plan_version != self.stage_plan().stage_plan_version
            or record.terminal_execution_state is not ValidatorTerminalExecutionState.COMPLETED
            or len(record.intermediate_refs) != 1
            or len(record.expected_item_accounting) != 1
            or record.expected_item_accounting[0].expected_validation_item_ref != item_ref
        ):
            raise PostWriteClaimProjectionError(
                "committed shared-Faithfulness source execution differs"
            )
        snapshot = post_write.validation_intermediate_runtime.resolve_typed(
            record.intermediate_refs[0],
            PostWriteClaimSourceSnapshot,
            **read_kwargs,
        )
        if (
            snapshot.artifact_state_ref != artifact_ref
            or snapshot.validation_scope is not ValidationScope(scope)
        ):
            raise PostWriteClaimProjectionError(
                "committed shared-Faithfulness snapshot targets another Artifact/scope"
            )
        # Rebuild through the exact V2 records. This proves payload verdict/ref
        # equality on replay rather than trusting a durable enum or Fact hash.
        if validate_canonical_results:
            for section in snapshot.sections:
                for result in section.verification_records:
                    if self.claim_runtime.resolve_verification_result(result.verification_result_ref) != result:
                        raise PostWriteClaimProjectionError("committed snapshot Result differs from canonical owner")
        expected = self._facts(snapshot, validation_run_id=validation_run_id, subject_hash=subject_hash)
        if tuple(post_write.validation_store.resolve_fact(ref, **read_kwargs) for ref in record.produced_fact_refs) != expected:
            raise PostWriteClaimProjectionError("committed Faithfulness facts differ from exact Result projection")
        provenance = ("post-write-claim-source://sha256/" + canonical_sha256(snapshot.model_dump(mode="json")),)
        envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=post_write.validation_view_resolver).build(
            validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
            stage_plan=self.stage_plan(), applicable_expected_validation_item_refs=(item_ref,), exact_authority_refs=provenance,
            connection=connection)
        accounting = record.expected_item_accounting[0]
        if (record.input_hash != envelope.input_hash or accounting.fact_refs != record.produced_fact_refs
                or accounting.execution_state is not ExpectedItemExecutionState.COMPLETED
                or accounting.provenance_refs != provenance):
            raise PostWriteClaimProjectionError("committed Faithfulness execution input/accounting differs")
        return snapshot

    def complete_item(self, *, post_write, validation_run_id, subject_hash, artifact_ref, scope, source_sections, assembly_input_manifest_ref=None):
        checkpoint_current_execution()
        arguments = dict(post_write=post_write, artifact_ref=artifact_ref, scope=scope,
                         source_sections=source_sections, assembly_input_manifest_ref=assembly_input_manifest_ref,
                         citation_validation_run_id=validation_run_id, citation_subject_hash=subject_hash)
        snapshot = self.resolve_sources(**arguments)
        source_hash = canonical_sha256(snapshot.model_dump(mode="json"))
        facts = self._facts(snapshot, validation_run_id=validation_run_id, subject_hash=subject_hash)
        item_ref = "faithfulness:shared:" + artifact_ref.artifact_hash
        plan = self.stage_plan()
        provenance = ("post-write-claim-source://sha256/" + source_hash,)
        envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=post_write.validation_view_resolver).build(
            validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
            stage_plan=plan, applicable_expected_validation_item_refs=(item_ref,), exact_authority_refs=provenance)
        execution_ref = "validator-execution:shared-faithfulness:" + canonical_sha256({"input_hash": envelope.input_hash, "item_ref": item_ref})
        try:
            existing = post_write.validation_store.resolve_validator_execution_record(execution_ref)
        except PostWritePersistenceIntegrityError as exc:
            if str(exc) != "required ValidatorExecutionRecord is missing":
                raise
            existing = None
        if existing is not None:
            if (existing.input_hash != envelope.input_hash or existing.subject_hash != subject_hash
                    or existing.validation_run_id != validation_run_id or existing.stage_plan_ref != plan.stage_plan_ref
                    or existing.validator_id != plan.validator_id or existing.validator_version != plan.validator_version
                    or existing.stage_plan_version != plan.stage_plan_version
                    or existing.terminal_execution_state is not ValidatorTerminalExecutionState.COMPLETED):
                raise PostWriteClaimProjectionError("replayed Post-write Claim execution differs from exact input")
            fact_refs = existing.produced_fact_refs
            if tuple(post_write.validation_store.resolve_fact(ref) for ref in fact_refs) != facts:
                raise PostWriteClaimProjectionError("replayed Post-write Claim facts differ from canonical projection")
            if self.resolve_committed_source_snapshot(post_write=post_write, validation_run_id=validation_run_id,
                    subject_hash=subject_hash, artifact_ref=artifact_ref, scope=scope) != snapshot:
                raise PostWriteClaimProjectionError("replayed Claim snapshot differs from current sources")
        else:
            if self.is_terminal_replay(source_sections):
                raise PostWriteClaimProjectionError("terminal Run is missing the exact committed Claim projection; new validation is forbidden")
            intermediate = post_write.validation_intermediate_runtime.commit(validation_run_id=validation_run_id,
                producer_ref=plan.validator_id, target_ref=item_ref, typed_intermediate=snapshot,
                intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING, occurred_at_epoch=0.0)
            fact_refs = tuple(post_write.validation_store.put_fact_if_absent_or_same(fact) for fact in facts)
            if self.resolve_sources(**arguments) != snapshot:
                raise PostWriteClaimProjectionError("Post-write Claim sources changed before execution commit")
            checkpoint_current_execution()
            with post_write.database.read_transaction() as connection:
                sequence = post_write.validation_store._next_event_sequence(connection, validation_run_id)
            event = post_write.validation_store._event(validation_run_id=validation_run_id,
                event_id="validator-terminal:shared-faithfulness:" + canonical_sha256(execution_ref), sequence=sequence,
                event_kind="VALIDATOR_COMPLETED", payload={"validator_execution_ref": execution_ref}, occurred_at_epoch=0.0)
            accounting = ExpectedItemExecutionAccountingSchema(expected_validation_item_ref=item_ref,
                execution_state=ExpectedItemExecutionState.COMPLETED, fact_refs=fact_refs, provenance_refs=provenance)
            base = {"schema_version": "validator_execution_record_v1", "validator_execution_ref": execution_ref,
                "validation_run_id": validation_run_id, "subject_hash": subject_hash, "validator_id": plan.validator_id,
                "validator_version": plan.validator_version, "stage_plan_ref": plan.stage_plan_ref, "stage_plan_version": plan.stage_plan_version,
                "input_hash": envelope.input_hash, "terminal_event_id": event.event_id,
                "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED.value,
                "expected_item_accounting": [accounting.model_dump(mode="json")],
                "produced_fact_refs": [ref.model_dump(mode="json") for ref in fact_refs],
                "intermediate_refs": [intermediate.model_dump(mode="json")]}
            post_write.validation_store.finalize_validator_execution(event,
                ValidatorExecutionRecordSchema(**base, record_sha256=canonical_sha256(base)), stage_plan=plan, input_envelope=envelope)
        semantic = {fact.semantic_state for fact in facts}
        state = ValidationCompletionItemState.FAIL if ValidationSemanticState.FAIL in semantic else ValidationCompletionItemState.INDETERMINATE if ValidationSemanticState.INDETERMINATE in semantic else ValidationCompletionItemState.PASS
        return ValidationCompletionItemSchema(expected_validation_item_ref=item_ref, required=True, state=state, fact_refs=fact_refs)
