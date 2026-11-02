"""Derived SIF-11 acceptance/dependency composition over existing owners.

FileArtifactStore retains only immutable source-ref projections. There is no new
canonical Store/head: each current read rechecks the canonical acceptance scope.
"""
from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimCoverageAccountingState
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.section_dependency import (
    SectionSourceBindingSchema, SectionDependencyEdgeSchema, SectionDependencyAssessmentSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanBindingSchema, SectionPlanContentSchema
from core.hashing import canonical_sha256


class SectionDependencyIntegrityError(ValueError):
    """Missing/corrupt/stale exact source is not a business acceptance result."""


class SectionDependencyNotReady(RuntimeError):
    """No dependent dispatch; assessment retains WAITING vs UNSATISFIABLE."""
    def __init__(self, assessment):
        self.assessment = assessment
        self.error_code = "SECTION_DEPENDENCY_" + assessment.aggregate
        super().__init__(self.error_code + ": " + assessment.fingerprint)


class SectionDependencyRuntime:
    def __init__(self, *, plan_runtime, claim_runtime, verifier_composition, writer_execution_store):
        if verifier_composition.claim_runtime is not claim_runtime:
            raise ValueError("dependency composition must share the canonical Claim owner")
        self.plan_runtime = plan_runtime
        self.claim_runtime = claim_runtime
        self.verifier = verifier_composition
        self.writer_execution_store = writer_execution_store
        self.paragraph_runtime = claim_runtime.paragraph_runtime
        self.lifecycle = claim_runtime.lifecycle_service
        self.store = self.paragraph_runtime.artifact_runtime.artifact_store

    def plan(self, binding):
        binding = self.plan_runtime.validate_plan_binding_current(SectionPlanBindingSchema.model_validate(binding))
        raw, exact = self.plan_runtime.artifact_runtime.read_version(binding.plan_version_ref)
        if exact != binding.plan_version_ref:
            raise SectionDependencyIntegrityError("Plan Artifact identity mismatch")
        plan = SectionPlanContentSchema.model_validate(raw)
        matches = [row for row in plan.sections if row.section_id == binding.section_id]
        if len(matches) != 1:
            raise SectionDependencyIntegrityError("Section is not in the current Plan")
        return binding, plan, matches[0]

    @staticmethod
    def _locator(binding, attempt_id):
        return "section-source://sha256/" + canonical_sha256({
            "contract": "section-source-binding-v1", "plan_binding": binding.model_dump(mode="json"),
            "attempt_id": attempt_id,
        })

    @staticmethod
    def _create(**payload):
        # Construct through validation only; the default policy/schema participate in hashing.
        payload.setdefault("schema_version", "section_source_binding_v1")
        payload.setdefault("acceptance_policy", "sif11-complete-current-claim-scope-v1")
        semantic = {k: v.model_dump(mode="json") if hasattr(v, "model_dump") else
                    [x.model_dump(mode="json") for x in v] if isinstance(v, tuple) else v
                    for k, v in payload.items() if k != "source_ref"}
        return SectionSourceBindingSchema(**payload, source_sha256=canonical_sha256(semantic))

    def _identity(self, binding, attempt_id):
        return {"task_id": self.lifecycle.load_run_identity(binding.plan_version_ref.logical_ref.run_id).task_id,
                "run_id": binding.plan_version_ref.logical_ref.run_id,
                "section_id": binding.section_id, "attempt_id": attempt_id}

    def _read(self, binding, attempt_id):
        source = SectionSourceBindingSchema.model_validate(self.store.get_json(
            self._locator(binding, attempt_id), **self._identity(binding, attempt_id)))
        if source.plan_binding != binding or source.producer_attempt_id != attempt_id:
            raise SectionDependencyIntegrityError("stored source binds another Plan/Attempt")
        return source

    def resolve_section_source(self, current_plan_binding, upstream_section_id):
        consumer, plan, section = self.plan(current_plan_binding)
        if upstream_section_id not in section.hard_dependencies:
            raise SectionDependencyIntegrityError("consumer has no explicit dependency authorization")
        upstream = SectionPlanBindingSchema(plan_version_ref=consumer.plan_version_ref,
            resolved_head_revision=consumer.resolved_head_revision, section_id=upstream_section_id)
        self.plan(upstream)
        latest = self.lifecycle.load_latest_attempt(consumer.plan_version_ref.logical_ref.run_id,
            f"section:{upstream_section_id}")
        if latest is None:
            return None
        source = self._read(upstream, latest.identity.attempt_id)
        self.validate_source(source)
        return source

    @staticmethod
    def _unique(rows, key, label):
        rows = tuple(rows)
        if len({key(row) for row in rows}) != len(rows):
            raise SectionDependencyIntegrityError("duplicate " + label)
        return rows

    def _evaluate(self, source):
        """Resolve a complete exact source scope; return acceptance and existing records."""
        source = SectionSourceBindingSchema.model_validate(source)
        binding, _, _ = self.plan(source.plan_binding)
        if source.source_ref != self._locator(binding, source.producer_attempt_id):
            raise SectionDependencyIntegrityError("source locator does not bind exact Plan/producer")
        lineage = self.lifecycle.resolve_read_lineage(binding.plan_version_ref.logical_ref.run_id,
            f"section:{binding.section_id}", expected_attempt_id=source.producer_attempt_id)
        if lineage.input_snapshot_version != source.input_snapshot_version:
            raise SectionDependencyIntegrityError("source input snapshot is stale")
        manifest = source.paragraph_manifest
        if (self.paragraph_runtime.load_manifest(manifest.manifest_ref) != manifest
                or manifest.generation_membership_state != "SEALED" or not manifest.members):
            raise SectionDependencyIntegrityError("source requires exact current SEALED Paragraph scope")
        invocation = self.writer_execution_store.resolve_writer_generation_invocation(source.writer_invocation_ref)
        execution = self.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
        if (execution.status != "success" or execution.artifact is None
                or execution.artifact.section_id != binding.section_id):
            raise SectionDependencyIntegrityError("source Writer artifact is unresolved or wrong Section")
        body = execution.artifact.content
        texts, cursor = {}, 0
        for version in manifest.members:
            head = self.paragraph_runtime.artifact_runtime.resolve_active(version.logical_ref)
            if head is None or head.active_version_ref != version:
                raise SectionDependencyIntegrityError("source Paragraph Artifact is stale")
            paragraph = self.paragraph_runtime.read_paragraph(version)
            writer_source = paragraph.source_binding
            if (writer_source.section_plan_source.plan_version_ref != binding.plan_version_ref
                    or writer_source.section_plan_source.section_id != binding.section_id
                    or writer_source.writer_subject_ref != invocation.writer_subject_ref
                    or writer_source.write_instance_id != invocation.write_instance_id
                    or writer_source.route_decision_ref != invocation.route_decision_ref):
                raise SectionDependencyIntegrityError("Paragraph has another exact Plan/Writer source")
            start = body.find(paragraph.text, cursor)
            if start < 0 or body[cursor:start].strip():
                raise SectionDependencyIntegrityError("Writer Artifact contains unaccounted body text")
            cursor = start + len(paragraph.text)
            texts[version] = paragraph.text
        if body[cursor:].strip():
            raise SectionDependencyIntegrityError("Writer Artifact suffix is not covered")

        proofs = self._unique(source.coverage_proof_refs, lambda r: r.coverage_proof_id, "coverage ref")
        if not proofs:
            raise SectionDependencyIntegrityError("source lacks canonical coverage proof")
        coverages = tuple(self.claim_runtime.resolve_claim_coverage(ref) for ref in proofs)
        versions = [v for c in coverages for v in c.paragraph_version_refs]
        if len(versions) != len(set(versions)) or set(versions) != set(manifest.members):
            raise SectionDependencyIntegrityError("coverage does not close exact source Manifest")
        occurrences = []
        complete = source.producer_execution_status == "success"
        for coverage in coverages:
            if coverage.coverage_scope_ref != f"section:{binding.section_id}":
                raise SectionDependencyIntegrityError("coverage belongs to another Section")
            complete &= coverage.accounting_state is not ClaimCoverageAccountingState.INDETERMINATE
            for version in coverage.paragraph_version_refs:
                units = sorted((u for u in coverage.coverage_units if u.paragraph_version_ref == version), key=lambda u: u.start_offset)
                if not units or units[0].start_offset != 0 or units[-1].end_offset != len(texts[version]):
                    raise SectionDependencyIntegrityError("coverage omits Paragraph boundaries")
                occurrences.extend(ref for unit in units for ref in unit.claim_occurrence_refs)
        occurrences = self._unique(occurrences, lambda r: canonical_sha256(r.model_dump(mode="json")), "Claim occurrence")
        claims = tuple(sorted((self.claim_runtime.resolve_claim(self.claim_runtime.derive_claim_ref(ref))
                               for ref in occurrences), key=lambda c: c.claim_ref.claim_id))
        refs = self._unique(source.verification_refs, lambda r: r.verification_result_id, "verification ref")
        if len(refs) != len(claims) or {r.claim_ref for r in refs} != {c.claim_ref for c in claims}:
            raise SectionDependencyIntegrityError("verification set does not close the exact Claim scope")
        results = {ref.claim_ref: self.claim_runtime.resolve_verification_result(ref) for ref in refs}
        observed_heads, observed_issue_heads = {}, {}
        for claim in claims:
            result = results[claim.claim_ref]
            head = self.claim_runtime.resolve_claim_head(claim.claim_ref)
            observed_heads[claim.claim_ref] = head
            if (head is None or head.head_updated_by_operation_id != result.committed_operation_id
                    or result.paragraph_version_ref != claim.paragraph_version_ref):
                raise SectionDependencyIntegrityError("source verification no longer names exact current Claim head")
            view = self.claim_runtime.resolve_active_verified_claim(claim.claim_ref)
            # Reuse the canonical resolver's exact Binding/Support/Verifier checks,
            # including for non-VERIFIED outcomes; do not run a second source resolver.
            if not all((view.claim_head_current, view.paragraph_self_current,
                        view.paragraph_membership_current, view.binding_source_current,
                        view.verification_current)):
                raise SectionDependencyIntegrityError("canonical Claim verification sources are not current")
            # Non-VERIFIED is an unresolved scope, not an integrity failure or invented verdict.
            complete &= view.active_verified_claim
            for issue_ref in result.issue_refs:
                issue = self.claim_runtime.resolve_issue(issue_ref)
                issue_head = self.claim_runtime.resolve_issue_head(issue_ref)
                observed_issue_heads[issue_ref] = issue_head
                if issue.claim_ref != claim.claim_ref or issue_head is None:
                    raise SectionDependencyIntegrityError("source Issue authority is unresolved")
                complete &= issue_head.lifecycle_status.value != "OPEN"
        # Close the read window against lifecycle/Plan/source promotion.
        self.plan(binding)
        if self.paragraph_runtime.load_manifest(manifest.manifest_ref) != manifest:
            raise SectionDependencyIntegrityError("source Manifest changed during acceptance evaluation")
        for version in manifest.members:
            head = self.paragraph_runtime.artifact_runtime.resolve_active(version.logical_ref)
            if head is None or head.active_version_ref != version:
                raise SectionDependencyIntegrityError("source Paragraph changed during acceptance evaluation")
        if any(self.claim_runtime.resolve_claim_head(ref) != head for ref, head in observed_heads.items()):
            raise SectionDependencyIntegrityError("source Claim head changed during acceptance evaluation")
        if any(self.claim_runtime.resolve_issue_head(ref) != head for ref, head in observed_issue_heads.items()):
            raise SectionDependencyIntegrityError("source Issue head changed during acceptance evaluation")
        if self.lifecycle.resolve_read_lineage(lineage.run_id, lineage.attempt_scope_ref,
                expected_attempt_id=lineage.attempt_id) != lineage:
            raise SectionDependencyIntegrityError("source lifecycle changed during acceptance evaluation")
        return bool(complete), claims, results

    def _validated_source_evaluation(self, source, *, require_accepted=False):
        try:
            source = SectionSourceBindingSchema.model_validate(source)
            if self._read(source.plan_binding, source.producer_attempt_id) != source:
                raise SectionDependencyIntegrityError("source is not the exact durable Graph completion projection")
            accepted, claims, results = self._evaluate(source)
            if source.workflow_status != ("ACCEPTED" if accepted else "VERIFYING"):
                raise SectionDependencyIntegrityError("stored source acceptance is stale or forged")
            if require_accepted and not accepted:
                raise SectionDependencyIntegrityError("upstream Section is not ACCEPTED")
            return source, claims, results
        except SectionDependencyIntegrityError:
            raise
        except Exception as exc:
            raise SectionDependencyIntegrityError("Section source cannot be resolved/current") from exc

    def validate_source(self, source, *, require_accepted=False):
        source, _, _ = self._validated_source_evaluation(
            source, require_accepted=require_accepted
        )
        return source

    def complete_source(self, result):
        """Guarded graph completion projection after canonical pipeline; no truth commits."""
        plan = result.canonical_plan_binding
        lineage = result.active_execution_lineage
        if plan is None or lineage is None:
            raise SectionDependencyIntegrityError("Section completion lacks canonical Plan/lineage")
        payload = dict(source_ref=self._locator(plan, lineage.attempt_id), plan_binding=plan,
            producer_execution_status=result.section.status.value,
            producer_attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version,
            paragraph_manifest=result.section_paragraph_manifest, writer_execution_ref=result.writer_execution_ref,
            writer_invocation_ref=result.writer_generation_invocation_ref,
            coverage_proof_refs=tuple(result.canonical_claim_coverage_proof_refs),
            verification_refs=tuple(result.canonical_claim_verification_refs), workflow_status="VERIFYING")
        candidate = self._create(**payload)
        accepted, _, _ = self._evaluate(candidate)
        payload["workflow_status"] = "ACCEPTED" if accepted else "VERIFYING"
        source = self._create(**payload)
        try:
            prior = self._read(plan, lineage.attempt_id)
        except FileNotFoundError:
            self.lifecycle.assert_attempt_committable(run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version)
            self.store.put_json(source.model_dump(mode="json"), artifact_ref=source.source_ref,
                artifact_type="section_source_projection", schema_version=source.schema_version,
                created_by="sif11-section-source-projection", input_snapshot_version=lineage.input_snapshot_version,
                **self._identity(plan, lineage.attempt_id))
        else:
            if prior != source:
                raise SectionDependencyIntegrityError("same source occurrence cannot be rewritten")
        # The guarded evaluation above is the completion observation. A second full
        # canonical read here adds no atomicity; every consumer/replay read validates
        # the persisted observation independently against current owners.
        if self._read(plan, lineage.attempt_id) != source:
            raise SectionDependencyIntegrityError("completion projection transport mismatch")
        return source

    def assess_dependencies(self, consumer_plan_binding):
        consumer, _, section = self.plan(consumer_plan_binding)
        edges = []
        for section_id in section.hard_dependencies:
            latest = self.lifecycle.load_latest_attempt(consumer.plan_version_ref.logical_ref.run_id, f"section:{section_id}")
            source, state, reason = None, "WAITING", "UPSTREAM_NOT_ACCEPTED"
            if latest is not None and latest.status in {AttemptLifecycleStatus.FAILED, AttemptLifecycleStatus.CANCELLED,
                                                        AttemptLifecycleStatus.SUPERSEDED}:
                state, reason = "UNSATISFIABLE", "UPSTREAM_TERMINAL_UNAVAILABLE"
            else:
                try:
                    source = self.resolve_section_source(consumer, section_id)
                    if source is not None and source.workflow_status == "ACCEPTED":
                        state, reason = "SATISFIED", "EXACT_UPSTREAM_ACCEPTED"
                    elif source is not None and source.producer_execution_status != "success":
                        state, reason = "UNSATISFIABLE", "UPSTREAM_EXECUTION_NOT_FULL_SUCCESS"
                except FileNotFoundError:
                    # No completion projection yet is normal while the producer is active.
                    if latest is not None and latest.status is AttemptLifecycleStatus.COMPLETED:
                        state, reason = "INTEGRITY_INVALID", "COMPLETED_SOURCE_MISSING"
                except Exception:
                    state, reason = "INTEGRITY_INVALID", "UPSTREAM_SOURCE_INVALID"
            edges.append(SectionDependencyEdgeSchema(upstream_section_id=section_id,
                result=state, reason=reason, source_binding=source))
        aggregate = next((value for value in ("INTEGRITY_INVALID", "UNSATISFIABLE", "WAITING")
                          if any(edge.result == value for edge in edges)), "ALL_SATISFIED")
        return SectionDependencyAssessmentSchema(consumer_plan_binding=consumer, edges=tuple(edges), aggregate=aggregate)

    def require_dependencies(self, consumer_plan_binding):
        assessment = self.assess_dependencies(consumer_plan_binding)
        if assessment.aggregate == "INTEGRITY_INVALID":
            raise SectionDependencyIntegrityError("dependency source integrity invalid: " + assessment.fingerprint)
        if assessment.aggregate != "ALL_SATISFIED":
            raise SectionDependencyNotReady(assessment)
        return assessment

    def read_active_verified_source(self, claim_ref, source):
        source, claims, results = self._validated_source_evaluation(
            source, require_accepted=True
        )
        matches = [claim for claim in claims if claim.claim_ref == claim_ref]
        if len(matches) != 1:
            raise SectionDependencyIntegrityError("Claim is outside accepted source scope")
        view = self.claim_runtime.resolve_active_verified_claim(claim_ref)
        if not view.active_verified_claim:
            raise SectionDependencyIntegrityError("Claim is not current active verified")
        return matches[0], results[claim_ref], view
