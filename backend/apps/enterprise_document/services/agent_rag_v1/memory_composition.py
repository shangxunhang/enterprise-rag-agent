"""351 source-owned Memory composition. No source writes or model/tool calls.

The runtime's immutable records are locator projections. Every read goes back
through the original domain owners; neither cached source content nor checkpoint
data can establish currentness. Unconfigured, non-required execution is a no-op.
"""
from __future__ import annotations

from typing import Any

from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactVersionRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSnapshotContentSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RUN_TERMINAL_STATUSES
from apps.enterprise_document.schemas.agent_rag_v1.memory import (
    CanonicalMemorySourceRefEnvelopeSchema, DomainMemoryType, MemoryFreshnessEligibility,
    MemoryProjectionRequestSchema, MemoryQuerySchema, MemoryRecallMode,
)
from .memory_runtime import (
    DurableMemoryRuntime, MemoryProjectionRegistration, MemorySourceResolution,
    MemorySelectorRegistration, MemoryRecordIntegrityInvalid,
)
from .memory_store import PostgresDurableMemoryStore, MemoryStoreError, MemoryStoreIntegrityError


class DurableMemoryCapabilityError(RuntimeError):
    """Explicit technical capability error; never a business verdict."""


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def _hash(value):
    return 'sha256:' + canonical_sha256(_json(value))


class TargetDurableMemoryComposition:
    POLICY = 'step7-canonical-memory-locator'
    VERSION = 'v1'
    MAX_SCOPE_RECORDS = 200
    ARTIFACT_TYPES = {
        'fact_snapshot': (DomainMemoryType.FACT, 'fact_snapshot_domain'),
        'evidence_package': (DomainMemoryType.EVIDENCE, 'evidence_package_domain'),
        'paragraph': (DomainMemoryType.ARTIFACT_REFERENCE, 'artifact_runtime'),
    }

    def __init__(self, *, required: bool, runtime: DurableMemoryRuntime | None,
                 fact_snapshot_runtime=None, research_runtime=None, claim_runtime=None,
                 lifecycle_service=None):
        if type(required) is not bool:
            raise TypeError('durable_memory_required must be an explicit bool')
        if required and runtime is None:
            raise DurableMemoryCapabilityError('MEMORY_CONFIGURATION_REQUIRED')
        self.required = required
        self.runtime = runtime
        self.facts = fact_snapshot_runtime
        self.research = research_runtime
        self.claims = claim_runtime
        self.lifecycle = lifecycle_service
        self.artifacts = fact_snapshot_runtime.artifact_runtime if fact_snapshot_runtime else None
        if runtime is not None:
            self._register()

    @classmethod
    def from_settings(cls, *, required, settings, **owners):
        if type(required) is not bool:
            raise TypeError('durable_memory_required must be an explicit bool')
        # A DSN alone does not impose a system-wide startup prerequisite. Only
        # this explicitly required execution profile activates the capability.
        if not required:
            return cls(required=False, runtime=None, **owners)
        if not str(settings.memory_database_url or '').strip():
            raise DurableMemoryCapabilityError('MEMORY_CONFIGURATION_REQUIRED')
        try:
            store = PostgresDurableMemoryStore(
                database_url=settings.memory_database_url,
                statement_timeout_ms=settings.memory_database_statement_timeout_ms,
                schema_version=settings.memory_database_schema_version,
                ensure_schema=False,
            )
            store.validate_schema()
        except (MemoryStoreError, ValueError) as exc:
            raise DurableMemoryCapabilityError('MEMORY_BACKEND_CAPABILITY_UNAVAILABLE') from exc
        return cls(required=True, runtime=DurableMemoryRuntime(store=store), **owners)

    def _register(self):
        for kind, (_, authority) in self.ARTIFACT_TYPES.items():
            self.runtime.register_source_resolver(source_authority=authority, source_kind=kind,
                exact_source_ref_schema_id='artifact_version_ref_v1', resolver=self._resolve_artifact)
        # Dependencies have their own exact registered source envelopes.
        self.runtime.register_source_resolver(source_authority='section_plan_domain', source_kind='section_plan',
            exact_source_ref_schema_id='artifact_version_ref_v1', resolver=self._resolve_artifact)
        for kind in ('evidence_bundle', 'research_round_record', 'research_authorization',
                     'verified_claim_revision', 'claim_record', 'claim_head', 'verification_result',
                     'binding_facts', 'bound_source', 'paragraph_membership', 'verifier_issue'):
            self.runtime.register_source_resolver(source_authority=self._authority(kind), source_kind=kind,
                exact_source_ref_schema_id=self._schema(kind), resolver=self._resolve_other)
        for memory_type in DomainMemoryType:
            self.runtime.register_projection(MemoryProjectionRegistration(
                memory_type=memory_type, projection_schema_id=self._payload_schema(memory_type),
                projection_schema_version=self.VERSION, projection_policy_id=self.POLICY,
                projection_policy_version=self.VERSION,
                currentness_resolver_id=self._currentness(memory_type), currentness_policy_version=self.VERSION,
                projection_validator=self._validate_payload,
                projection_builder=lambda request, primary, deps: {'source_ref': request.primary_source_ref.exact_source_ref},
                source_binding_validator=self._validate_binding,
            ))
            self.runtime.register_selector(MemorySelectorRegistration(
                memory_type=memory_type, selector_schema_id='canonical_memory_locator_selector_v1',
                selector_schema_version='v1', allowed_keys=frozenset({'logical_source_ref'}),
                selector_validator=self._validate_selector,
                selector_projector=lambda record: {'logical_source_ref': record.source_binding.primary_source.logical_source_ref},
                selector_clause_validator=self._validate_clause,
                index_policy_id='canonical-memory-locator-index', index_policy_version='v1',
            ))

    @staticmethod
    def _authority(kind):
        return ('external_evidence_domain' if kind == 'evidence_bundle' else
                'research_round_domain' if kind in ('research_round_record', 'research_authorization') else
                'verifier_issue_domain' if kind == 'verifier_issue' else
                'paragraph_domain' if kind == 'paragraph_membership' else 'claim_domain')

    @staticmethod
    def _schema(kind):
        return {'research_round_record': 'research_round_ref_v1',
                'verified_claim_revision': 'claim_verification_revision_ref_v1',
                'verifier_issue': 'verifier_issue_head_observation_v1'}.get(kind, kind + '_memory_source_ref_v1')

    @staticmethod
    def _currentness(memory_type):
        return {
            DomainMemoryType.FACT: 'fact_snapshot_artifact_head_currentness_v1',
            DomainMemoryType.EVIDENCE: 'evidence_package_artifact_head_currentness_v1',
            DomainMemoryType.VERIFIED_CLAIM: 'sif07_active_verified_claim_intrinsic_resolver_v1',
            DomainMemoryType.RESEARCH_HISTORY: 'research_round_record_integrity_v1',
            DomainMemoryType.VERIFIER_ISSUE: 'verifier_issue_head_currentness_v1',
            DomainMemoryType.ARTIFACT_REFERENCE: 'artifact_active_head_currentness_v1',
        }[memory_type]

    @staticmethod
    def _payload_schema(memory_type):
        return memory_type.value + '_canonical_locator_v1'

    @staticmethod
    def _validate_payload(payload):
        if set(payload) != {'source_ref'} or not isinstance(payload['source_ref'], dict) or not payload['source_ref']:
            raise ValueError('Memory locator must contain exactly one nonempty exact source ref')

    @staticmethod
    def _validate_selector(value):
        if set(value) != {'logical_source_ref'} or not isinstance(value['logical_source_ref'], str) or not value['logical_source_ref']:
            raise ValueError('invalid canonical locator selector')

    @staticmethod
    def _validate_clause(clause):
        if clause.key != 'logical_source_ref' or clause.operator.value != 'EQ' or not isinstance(clause.value, str):
            raise ValueError('locator selector supports exact string EQ only')

    def _terminal(self, run_id):
        if self.lifecycle is None:
            raise DurableMemoryCapabilityError('MEMORY_RUN_AUTHORITY_UNAVAILABLE')
        return self.lifecycle.load_run(run_id).status in RUN_TERMINAL_STATUSES

    def _observation(self, source, content, *, current, historical=True, active=None, reasons=()):
        return MemorySourceResolution(exact_source_payload=source.exact_source_ref,
            source_semantic_fingerprint=_hash(content), source_resolvable=True, integrity_valid=True,
            current=current, lifecycle_active=active, historical_eligible=historical,
            reason_codes=reasons or (('SOURCE_CURRENT',) if current else ('SOURCE_SUPERSEDED',)))

    def _artifact_content(self, raw):
        ref = ArtifactVersionRefSchema.model_validate(raw)
        if self.artifacts is None:
            raise DurableMemoryCapabilityError('MEMORY_SOURCE_OWNER_UNAVAILABLE')
        content, exact = self.artifacts.read_version(ref)
        if exact != ref:
            raise ValueError('Memory Artifact source ref mismatch')
        return ref, content

    def _resolve_artifact(self, source):
        ref, content = self._artifact_content(source.exact_source_ref)
        if ref.logical_ref.domain_scope_ref != source.source_scope_ref or ref.logical_ref.artifact_type != source.source_kind:
            raise ValueError('Artifact Memory scope/type mismatch')
        head = self.artifacts.resolve_active(ref.logical_ref)
        current = bool(head and head.active_version_ref == ref)
        if source.source_kind == 'fact_snapshot':
            facts = FactSnapshotContentSchema.model_validate(content)
            for binding in facts.source_bindings:
                self.facts._validate_source_binding(binding)
        elif source.source_kind == 'evidence_package':
            from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageContentSchema
            package = EvidencePackageContentSchema.model_validate(content)
            bundle = self.research.resolve_evidence_bundle(package.evidence_bundle_binding)
            if bundle.run_id is not None and bundle.run_id != ref.logical_ref.run_id:
                raise ValueError('Memory EvidenceBundle Run mismatch')
            if not {item.evidence_id for item in package.selections} <= set(bundle.selected_evidence_ids):
                raise ValueError('Memory package selected Evidence is not canonical')
            current = current and self._resolve_artifact(
                self._artifact_envelope(package.section_plan_source.plan_version_ref)).current
            if package.direct_fact_snapshot_version_ref is not None:
                current = current and self._resolve_artifact(
                    self._artifact_envelope(package.direct_fact_snapshot_version_ref)).current
        elif source.source_kind == 'section_plan':
            from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanContentSchema
            plan = SectionPlanContentSchema.model_validate(content)
            fact_source = self._artifact_envelope(plan.fact_snapshot_version_ref)
            current = current and self._resolve_artifact(fact_source).current
        return self._observation(source, content, current=current)

    def _envelope(self, *, kind, ref, scope, logical, authority=None, schema=None):
        raw = _json(ref)
        source = CanonicalMemorySourceRefEnvelopeSchema(
            source_authority=authority or self._authority(kind), source_kind=kind,
            source_scope_ref=scope, logical_source_ref=logical, exact_source_ref=raw,
            exact_source_ref_schema_id=schema or self._schema(kind), exact_source_ref_sha256=_hash(raw),
            # Resolve owner content before finalizing the envelope fingerprint.
            source_semantic_fingerprint='sha256:' + '0' * 64,
        )
        resolution = self._resolve_artifact(source) if schema == 'artifact_version_ref_v1' else self._resolve_other(source)
        return source.model_copy(update={'source_semantic_fingerprint': resolution.source_semantic_fingerprint})

    def _artifact_envelope(self, ref):
        ref = ArtifactVersionRefSchema.model_validate(ref)
        kind = ref.logical_ref.artifact_type
        authority = self.ARTIFACT_TYPES[kind][1] if kind in self.ARTIFACT_TYPES else 'section_plan_domain'
        if kind not in {*self.ARTIFACT_TYPES, 'section_plan'}:
            raise ValueError('Artifact family not registered for Memory')
        # Building the immutable source envelope needs the exact committed
        # Artifact payload fingerprint, not a currentness decision. The generic
        # Memory runtime immediately performs the full registered source
        # resolution for Step 2, and ABSENT commits independently repeat it at
        # the frozen Step 9 precommit fence. Avoid doing that same transitive
        # Plan/Fact currentness traversal merely to populate the envelope hash.
        exact, content = self._artifact_content(ref)
        raw = _json(exact)
        return CanonicalMemorySourceRefEnvelopeSchema(
            source_authority=authority,
            source_kind=kind,
            source_scope_ref=exact.logical_ref.domain_scope_ref,
            logical_source_ref=exact.logical_ref.logical_artifact_id,
            exact_source_ref=raw,
            exact_source_ref_schema_id='artifact_version_ref_v1',
            exact_source_ref_sha256=_hash(raw),
            source_semantic_fingerprint=_hash(content),
        )

    def _claim_state(self):
        if self.claims is None:
            raise DurableMemoryCapabilityError('MEMORY_CLAIM_OWNER_UNAVAILABLE')
        with self.claims._lock, InterProcessFileLock(self.claims.lock_path):
            return self.claims._load_state_unlocked()

    def _claim_revision(self, raw):
        from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimVerificationRevisionRefSchema
        ref = ClaimVerificationRevisionRefSchema.model_validate(raw)
        state = self._claim_state()
        revision = state.verification_revisions[(ref.claim_ref.claim_id, ref.verification_revision)]
        if revision.revision_ref != ref:
            raise ValueError('Memory Claim revision mismatch')
        result = state.verification_results[revision.verification_result_ref.verification_result_id]
        operation = state.operations[revision.committed_operation_id]
        if result.verdict.value != 'VERIFIED' or operation.event_type != 'verification_committed':
            raise ValueError('Memory source was never legally VERIFIED')
        return state, revision, result, operation

    def _resolve_other(self, source):
        kind, raw = source.source_kind, source.exact_source_ref
        if kind == 'evidence_bundle':
            from apps.enterprise_document.schemas.agent_rag_v1.research import CanonicalEvidenceBundleBindingSchema
            binding = CanonicalEvidenceBundleBindingSchema.model_validate(raw)
            bundle = self.research.resolve_evidence_bundle(binding)
            return self._observation(source, bundle, current=True)
        if kind in ('research_round_record', 'research_authorization'):
            from apps.enterprise_document.schemas.agent_rag_v1.research import ResearchRoundRefSchema
            round_ref = ResearchRoundRefSchema.model_validate(raw if kind == 'research_round_record' else raw['round_ref'])
            record = self.research.resolve_round(round_ref)
            if source.source_scope_ref != round_ref.attempt_scope_ref:
                raise ValueError('Memory Research scope mismatch')
            if kind == 'research_authorization':
                if raw['authorization'] != _json(record.authorization_binding):
                    raise ValueError('Memory Research authorization mismatch')
                return self._observation(source, record.authorization_binding, current=True)
            valid = record.execution_outcome.value != 'TECHNICAL_FAILURE'
            return self._observation(source, record, current=False, historical=valid,
                reasons=('RESEARCH_HISTORY_COMMITTED',) if valid else ('SOURCE_INTEGRITY_INVALID',))
        if kind == 'paragraph_membership':
            paragraph = self.claims.paragraph_runtime
            with paragraph._lock, InterProcessFileLock(paragraph.manifest_lock_path):
                manifests, operations = paragraph._load_manifest_state_unlocked()
            matches = [receipt for receipt, _ in operations.values() if _json(receipt) == raw['receipt']]
            if len(matches) != 1 or raw['paragraph'] not in [_json(ref) for ref in matches[0].resulting_members]:
                raise ValueError('Memory membership source does not resolve')
            current = paragraph.load_manifest(matches[0].operation_ref.manifest_ref)
            return self._observation(source, raw, current=raw['paragraph'] in [_json(ref) for ref in current.members])
        state = self._claim_state()
        if kind == 'verified_claim_revision':
            state, revision, result, operation = self._claim_revision(raw)
            current = self.claims.resolve_active_verified_claim(revision.claim_ref).active_verified_claim
            head = state.claim_heads[revision.claim_ref.claim_id]
            current = current and head.active_verification_revision_ref == revision.revision_ref
            return self._observation(source, revision, current=current)
        if kind == 'claim_record':
            record = state.claims[raw['claim_id']]
            if _json(record.claim_ref) != raw:
                raise ValueError('Claim Memory ref mismatch')
            return self._observation(source, record, current=True)
        if kind == 'claim_head':
            operation = state.operations[raw['head_updated_by_operation_id']]
            receipt = operation.payload['receipt']
            if receipt.get('resulting_claim_head', receipt.get('resulting_head')) != raw:
                raise ValueError('Claim Memory historical head does not resolve')
            current = _json(state.claim_heads[raw['claim_ref']['claim_id']]) == raw
            return self._observation(source, raw, current=current)
        if kind == 'verification_result':
            record = state.verification_results[raw['verification_result_id']]
            if _json(record.verification_result_ref) != raw:
                raise ValueError('Verification Memory ref mismatch')
            return self._observation(source, record, current=True)
        if kind in ('binding_facts', 'bound_source'):
            binding_ref = raw if kind == 'binding_facts' else raw['binding_facts_ref']
            record = state.bindings[binding_ref['binding_facts_id']]
            if _json(record.binding_facts_ref) != binding_ref:
                raise ValueError('Binding Memory ref mismatch')
            if kind == 'binding_facts':
                return self._observation(source, record, current=True)
            if raw['source_ref'] not in [_json(item) for item in record.exact_authorized_source_refs]:
                raise ValueError('Memory bound source not authorized by canonical BindingFacts')
            version = raw['source_ref']['artifact_version_ref']
            if version is None:
                raise ValueError('Memory bound source lacks canonical Artifact locator')
            observation = self._resolve_artifact(self._artifact_envelope(version))
            return self._observation(source, raw, current=observation.current,
                historical=observation.historical_eligible)
        if kind == 'verifier_issue':
            head = raw
            issue_ref = head['issue_ref']
            issue = state.issues[issue_ref['issue_id']]
            operation = state.operations[head['head_updated_by_operation_id']]
            observed = operation.payload.get('issue_heads', [])
            transition = operation.payload.get('receipt', {}).get('resulting_head')
            if raw not in observed and raw != transition:
                raise ValueError('Memory Issue historical head does not resolve')
            current_head = state.issue_heads[issue_ref['issue_id']]
            status = current_head.lifecycle_status.value
            return self._observation(source, {'record': _json(issue), 'head': raw},
                current=_json(current_head) == raw and status == 'OPEN',
                historical=status != 'INVALIDATED', active=status == 'OPEN',
                reasons=('SOURCE_CURRENT',) if status == 'OPEN' else ('SOURCE_LIFECYCLE_'+status,))
        raise ValueError('unregistered canonical Memory source kind')

    def _dependencies(self, primary):
        kind, raw, scope = primary.source_kind, primary.exact_source_ref, primary.source_scope_ref
        deps = []
        def add(kind, ref, logical):
            deps.append(self._envelope(kind=kind, ref=ref, scope=scope, logical=logical))
        if kind == 'evidence_package':
            _, package = self._artifact_content(raw)
            deps.append(self._artifact_envelope(package['section_plan_source']['plan_version_ref']))
            add('evidence_bundle', package['evidence_bundle_binding'], 'bundle:'+canonical_sha256(package['evidence_bundle_binding']))
            if package.get('direct_fact_snapshot_version_ref'):
                deps.append(self._artifact_envelope(package['direct_fact_snapshot_version_ref']))
        elif kind == 'research_round_record':
            from apps.enterprise_document.schemas.agent_rag_v1.research import ResearchRoundRefSchema
            record = self.research.resolve_round(ResearchRoundRefSchema.model_validate(raw))
            add('research_authorization', {'round_ref': raw, 'authorization': _json(record.authorization_binding)},
                record.round_ref.research_round_id+':authorization')
            deps.append(self._artifact_envelope(record.source_plan_version_ref))
            if record.prior_committed_round_ref:
                add('research_round_record', record.prior_committed_round_ref, record.prior_committed_round_ref.research_round_id)
            for ref in (record.package_progress.prior_evidence_package_version_ref,
                        record.package_progress.resulting_evidence_package_version_ref):
                if ref:
                    deps.append(self._artifact_envelope(ref))
                    deps.extend(self._dependencies(self._artifact_envelope(ref)))
        elif kind == 'verified_claim_revision':
            state, revision, result, operation = self._claim_revision(raw)
            claim = state.claims[revision.claim_ref.claim_id]
            binding = state.bindings[revision.binding_facts_ref.binding_facts_id]
            add('claim_record', claim.claim_ref, claim.claim_ref.claim_id)
            add('claim_head', operation.payload['receipt']['resulting_claim_head'], claim.claim_ref.claim_id+':head')
            add('verification_result', result.verification_result_ref, result.verification_result_ref.verification_result_id)
            add('binding_facts', binding.binding_facts_ref, binding.binding_facts_ref.binding_facts_id)
            deps.append(self._artifact_envelope(claim.paragraph_version_ref))
            paragraph = self.claims.paragraph_runtime
            with paragraph._lock, InterProcessFileLock(paragraph.manifest_lock_path):
                _, operations = paragraph._load_manifest_state_unlocked()
            # The first canonical membership receipt for the exact Paragraph and
            # original verification Attempt is stable under later manifest growth.
            receipts = [receipt for receipt, _ in operations.values()
                if claim.paragraph_version_ref in receipt.resulting_members
                and receipt.operation_ref.manifest_ref.attempt_id == operation.operation_ref.attempt_id]
            if not receipts:
                raise ValueError('Memory verified chain has no canonical membership observation')
            receipt = min(receipts, key=lambda item: item.resulting_manifest_revision)
            add('paragraph_membership', {'receipt': _json(receipt), 'paragraph': _json(claim.paragraph_version_ref)},
                receipt.operation_ref.manifest_operation_id)
            for source in binding.exact_authorized_source_refs:
                add('bound_source', {'binding_facts_ref': _json(binding.binding_facts_ref), 'source_ref': _json(source)}, source.exact_source_ref)
        elif kind == 'verifier_issue':
            state = self._claim_state()
            issue = state.issues[raw['issue_ref']['issue_id']]
            result = state.verification_results[issue.verification_result_ref.verification_result_id]
            add('verification_result', issue.verification_result_ref, issue.verification_result_ref.verification_result_id)
            if issue.claim_ref:
                add('claim_record', issue.claim_ref, issue.claim_ref.claim_id)
            if issue.paragraph_version_ref:
                deps.append(self._artifact_envelope(issue.paragraph_version_ref))
            binding = state.bindings[result.binding_facts_ref.binding_facts_id]
            for source in binding.exact_authorized_source_refs:
                if source.exact_source_ref in issue.source_refs:
                    add('bound_source', {'binding_facts_ref': _json(binding.binding_facts_ref), 'source_ref': _json(source)}, source.exact_source_ref)
        unique = {self._source_key(item): item for item in deps}
        return tuple(unique[key] for key in sorted(unique))

    @staticmethod
    def _source_key(source):
        import json
        return (source.source_authority, source.source_kind, source.source_scope_ref,
            source.logical_source_ref, json.dumps(source.exact_source_ref, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

    def _validate_binding(self, request, primary, dependencies):
        source = request.primary_source_ref
        expected_type = {**{kind: pair[0] for kind, pair in self.ARTIFACT_TYPES.items()},
            'verified_claim_revision': DomainMemoryType.VERIFIED_CLAIM,
            'research_round_record': DomainMemoryType.RESEARCH_HISTORY,
            'verifier_issue': DomainMemoryType.VERIFIER_ISSUE}.get(source.source_kind)
        if request.memory_type != expected_type or request.scope_ref != source.source_scope_ref:
            raise ValueError('Memory type/scope differs from canonical source')
        raw = source.exact_source_ref
        if source.source_kind in self.ARTIFACT_TYPES:
            run_id = raw['logical_ref']['run_id']
        elif source.source_kind == 'research_round_record':
            run_id = raw['run_id']
        else:
            claim_ref = raw['claim_ref'] if source.source_kind == 'verified_claim_revision' else raw['issue_ref']['verification_result_ref']['claim_ref']
            run_id = claim_ref['claim_occurrence_ref']['run_id']
        if request.run_id != run_id:
            raise ValueError('Memory source belongs to another Run')
        self.lifecycle.load_run(run_id)
        if self._dependencies(source) != request.dependency_source_refs:
            raise ValueError('Memory canonical dependency set is incomplete or changed')

    def _request(self, memory_type, primary, run_id):
        return MemoryProjectionRequestSchema(run_id=run_id, memory_type=memory_type,
            scope_ref=primary.source_scope_ref, primary_source_ref=primary,
            dependency_source_refs=self._dependencies(primary), projection_schema_id=self._payload_schema(memory_type),
            projection_schema_version=self.VERSION, projection_policy_id=self.POLICY, projection_policy_version=self.VERSION)

    def _project(self, request):
        try:
            if self._terminal(request.run_id):
                ref, binding, projection, _ = self.runtime._build_projection(request)
                record = self.runtime.resolve_record(ref)
                if record is None:
                    raise DurableMemoryCapabilityError('MEMORY_TERMINAL_RECORD_MISSING')
                if record.source_binding != binding or record.projection != projection:
                    raise MemoryRecordIntegrityInvalid('terminal Memory projection differs')
                return record.memory_ref
            record = self.runtime.project_source(request)
            try:
                index = self.runtime.store.get_selector_index(record.memory_ref)
                if index is None:
                    raise ValueError('derived selector index is missing')
                self.runtime._validate_index(index, record)
            except (ValueError, MemoryStoreIntegrityError):
                self.runtime.upsert_selector_index(record)
            return record.memory_ref
        except MemoryStoreError as exc:
            raise DurableMemoryCapabilityError('MEMORY_PERSISTENCE_UNAVAILABLE') from exc

    def project_artifact(self, ref):
        if self.runtime is None:
            return ()
        ref = ArtifactVersionRefSchema.model_validate(ref)
        kind = ref.logical_ref.artifact_type
        if kind not in self.ARTIFACT_TYPES:
            raise ValueError('Artifact family not authorized for Memory projection')
        return (self._project(self._request(self.ARTIFACT_TYPES[kind][0], self._artifact_envelope(ref), ref.logical_ref.run_id)),)

    def project_round(self, ref):
        if self.runtime is None:
            return ()
        source = self._envelope(kind='research_round_record', ref=ref,
            scope=ref.attempt_scope_ref, logical=ref.research_round_id)
        return (self._project(self._request(DomainMemoryType.RESEARCH_HISTORY, source, ref.run_id)),)

    def project_claim_revision(self, ref):
        if self.runtime is None:
            return ()
        occurrence = ref.claim_ref.claim_occurrence_ref
        source = self._envelope(kind='verified_claim_revision', ref=ref,
            scope='section:'+occurrence.section_id, logical=ref.claim_ref.claim_id)
        return (self._project(self._request(DomainMemoryType.VERIFIED_CLAIM, source, occurrence.run_id)),)

    def project_issue(self, head):
        if self.runtime is None:
            return ()
        occurrence = head.issue_ref.verification_result_ref.claim_ref.claim_occurrence_ref
        source = self._envelope(kind='verifier_issue', ref=head,
            scope='section:'+occurrence.section_id, logical=head.issue_ref.issue_id)
        return (self._project(self._request(DomainMemoryType.VERIFIER_ISSUE, source, occurrence.run_id)),)

    def recall(self, *, run_id, scope_ref, memory_types=(), exact_source_refs=()):
        if self.runtime is None:
            return ()
        # No selectors => exact immutable-record path; terminal reads never
        # repair an index. Scope is exact, not a prefix or inferred permission.
        result = self.runtime.query(MemoryQuerySchema(run_id=run_id, scope_refs=(scope_ref,),
            memory_types=tuple(sorted(memory_types, key=lambda item: item.value)),
            exact_source_refs=exact_source_refs, recall_mode=MemoryRecallMode.ACTIVE_ONLY,
            limit=self.MAX_SCOPE_RECORDS))
        if result.next_cursor:
            raise DurableMemoryCapabilityError('MEMORY_SCOPE_BOUND_EXCEEDED')
        return tuple(item.memory_record for item in result.items)

    def bind_owners(self, *, fact_snapshot_runtime, research_runtime, claim_runtime, lifecycle_service):
        if self.facts is not None:
            raise ValueError('Memory source owners already bound')
        self.facts, self.research, self.claims, self.lifecycle = (
            fact_snapshot_runtime, research_runtime, claim_runtime, lifecycle_service)
        self.artifacts = fact_snapshot_runtime.artifact_runtime
        if (research_runtime.artifact_runtime is not self.artifacts
                or claim_runtime.paragraph_runtime.artifact_runtime is not self.artifacts
                or self.artifacts.lifecycle_service is not lifecycle_service):
            raise ValueError('Memory must share the canonical production source owners')

    def reconcile_run(self, run_id):
        """Explicit bounded recovery/completion reconciliation, from sources only."""
        if self.runtime is None:
            return ()
        self.lifecycle.load_run(run_id)
        with self.artifacts._lock, InterProcessFileLock(self.artifacts.lock_path):
            artifact_state = self.artifacts._load_state_unlocked()
        sources = [('artifact', stored.ref) for stored in artifact_state.versions.values()
            if stored.ref.logical_ref.run_id == run_id
            and stored.ref.logical_ref.artifact_type in self.ARTIFACT_TYPES]
        if self.research is not None:
            with self.research._lock, InterProcessFileLock(self.research.lock_path):
                state = self.research._load_state_unlocked()
            sources.extend(('round', stored.record.round_ref) for stored in state.rounds.values()
                if stored.record.round_ref.run_id == run_id)
        if self.claims is not None:
            state = self._claim_state()
            for revision in state.verification_revisions.values():
                if (revision.claim_ref.claim_occurrence_ref.run_id == run_id
                        and state.verification_results[revision.verification_result_ref.verification_result_id].verdict.value == 'VERIFIED'):
                    sources.append(('claim_revision', revision.revision_ref))
            for operation in state.operations.values():
                if operation.operation_ref.run_id != run_id:
                    continue
                if operation.event_type == 'verification_committed':
                    heads = operation.payload['issue_heads']
                elif operation.event_type == 'issue_lifecycle_transition':
                    heads = (operation.payload['receipt']['resulting_head'],)
                else:
                    heads = ()
                from apps.enterprise_document.schemas.agent_rag_v1.verification import VerifierIssueHeadSchema
                sources.extend(('issue', VerifierIssueHeadSchema.model_validate(head)) for head in heads)
        if len(sources) > self.MAX_SCOPE_RECORDS:
            raise DurableMemoryCapabilityError('MEMORY_RECONCILE_BOUND_EXCEEDED')
        result = []
        for kind, ref in sources:
            result.extend(getattr(self, 'project_'+kind)(ref))
        return tuple(result)
