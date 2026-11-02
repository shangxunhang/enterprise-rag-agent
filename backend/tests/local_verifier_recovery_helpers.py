"""Isolated production-owner fixture for Local Verifier Recovery acceptance.

The Provider is a self-registered Fake client. Upstream source/admission callbacks
seed controlled fixture inputs; Source materialization, canonical recovery
authorizers, M6 guards, Artifact/Paragraph/Writer/Lifecycle, Gateway and Budget
execute their production implementations without replacing their decisions.
The persisted manifest lets independent Windows spawn processes reopen the exact
same owners without re-creating a Run or selecting a latest Writer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from contracts.base_client import BaseLLMClient
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.evidence_common import EvidenceStatus
from schemas.model import ModelResponseSchema, TokenUsageSchema
from schemas.rag import EvidenceBundleSchema, RAGEvidenceAssessmentSchema, RetrievedChunkSchema
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ArtifactClaimCoverageUnitSchema, ClaimCandidateSchema, ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState, ClaimExtractionOrigin, ClaimRecordSchema, ClaimTextSpanSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportCandidateSchema, ClaimEvidenceSupportRecordSchema,
    ClaimSupportAuthorityKind, ClaimSupportSubjectRefSchema, SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    CanonicalEvidenceBundleBindingSchema, EvidencePackageContentSchema,
    EvidencePackageSelectionSchema, SectionPlanSourceRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema, ClaimOperationContextSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationInvocationSchema, WriterLogicalExecutionKey,
    WriterExecutionResultSchema, WriterExecutionStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.services.agent_rag_v1 import (
    ArtifactRuntime, BudgetManager, CanonicalInvocationBudgetAdapter,
    EvidencePackageCommitOperations, FactSnapshotRuntime, ResearchRuntime, SectionPlanRuntime,
    SectionPlanCommitOperations,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import ParagraphRuntime
from apps.enterprise_document.services.agent_rag_v1.claim_composition import TargetClaimRuntimeValidators
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationRuntime
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    TargetClaimSupportComposition, SUPPORT_SEMANTIC_CONTRACT, TARGET_SET_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
from backend.tests import test_multi_agent_v3_research_runtime as research_tests
from backend.tests import test_multi_agent_v3_paragraph_runtime as paragraph_tests
from backend.tests.test_trusted_evidence_v1_eligibility_contract import _assessment


NOW = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
TEXT = "The controlled project supplies a capacity of 100 MW."
MODEL = "local-verifier-recovery-production-fake"


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _completed_two_evidence_bundle():
    chunks, citations = [], []
    for ordinal in (1, 2):
        chunks.append(RetrievedChunkSchema(rank=ordinal, matched_chunk_id=f"child-{ordinal}",
            context_chunk_id=f"parent-{ordinal}", child_chunk_id=f"child-{ordinal}",
            parent_chunk_id=f"parent-{ordinal}", doc_id=f"doc-{ordinal}",
            match_text=TEXT if ordinal == 1 else "A distinct contextual observation.",
            context_text=f"Full controlled evidence material {ordinal}.", title=f"Evidence {ordinal}"))
        citations.append(CitationSchema(citation_id=f"source-{ordinal}", source_type="document",
            source_document_id=f"doc-{ordinal}", doc_id=f"doc-{ordinal}", child_chunk_id=f"child-{ordinal}",
            parent_chunk_id=f"parent-{ordinal}", quote_text=chunks[-1].context_text, title=f"Evidence {ordinal}"))
    bundle = build_evidence_bundle(query=TEXT, rewritten_queries=[], selected_chunks=chunks,
        dropped_chunks=[], citations=citations, trace=None, max_context_chars=6000)
    assessments = [_assessment(evidence_id=f"E{i}").model_copy(update={"document_id": f"doc-{i}"}) for i in (1, 2)]
    return EvidenceBundleSchema.model_validate(bundle.model_copy(update={
        "task_id": research_tests.TASK_ID, "run_id": research_tests.RUN_ID,
        "retrieval_trace_id": "lvr-fake-source-trace", "retrieval_scope": "section",
        "item_assessments": assessments,
        "assessment": RAGEvidenceAssessmentSchema(assessment_state="completed", status=EvidenceStatus.SUFFICIENT,
            coverage_ratio=1.0, required_topics=["current project fact"], covered_topics=["current project fact"],
            reason_codes=["coverage_sufficient"]),
    }).model_dump(mode="python", round_trip=True))


class ScriptedProvider(BaseLLMClient):
    """Self-registered Fake Provider with durable physical-call observations."""
    model_name = MODEL

    def __init__(self, root: Path, script):
        self.root = Path(root)
        self.script = list(script)
        self.calls = []
        self.before_response = None

    def generate(self, request):
        with InterProcessFileLock(self.root / "fake-provider-observations.lock"):
            path = self.root / "fake-provider-calls.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            ordinal = len(lines)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"ordinal": ordinal, "pid": os.getpid(),
                    "request": request.model_dump(mode="json")}, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        self.calls.append(request.model_copy(deep=True))
        if self.before_response is not None:
            self.before_response(request, ordinal)
        if ordinal >= len(self.script):
            raise AssertionError(f"unauthorized additional fake Provider dispatch {ordinal + 1}")
        reply = self.script[ordinal]
        if reply == "INTERRUPT":
            raise KeyboardInterrupt("durable fake dispatch with unknown outcome")
        if reply == "UNKNOWN":
            from schemas.failure import FailureOrigin
            return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
                run_id=request.run_id, model_name=MODEL, success=False, error_message="unknown fake provider outcome",
                failure_origin=FailureOrigin.UNKNOWN, created_at=NOW.isoformat())
        if reply == "UNAVAILABLE":
            from schemas.failure import FailureOrigin
            return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
                run_id=request.run_id, model_name=MODEL, success=False, error_message="fake provider unavailable",
                failure_origin=FailureOrigin.PROVIDER_RUNTIME, created_at=NOW.isoformat())
        unknown_usage = isinstance(reply, dict) and reply.get("_usage_unknown")
        usage = TokenUsageSchema(prompt_tokens=None, completion_tokens=None, total_tokens=None) if unknown_usage else TokenUsageSchema(
            prompt_tokens=17 + ordinal, completion_tokens=5 + ordinal, total_tokens=22 + 2 * ordinal)
        content = {key: value for key, value in reply.items() if not key.startswith("_")} if isinstance(reply, dict) else reply
        if isinstance(content, dict) and content.get("positive_basis_refs") in (["@all_evidence"], ["@supporting"]):
            manifest = json.loads((self.root / "owner-manifest.json").read_text(encoding="utf-8"))
            support = manifest["support"]
            content["positive_basis_refs"] = (support["evidence_target_set_ref"]["evidence_member_refs"]
                if content["positive_basis_refs"] == ["@all_evidence"] else support["supporting_refs"])
        return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
            run_id=request.run_id, model_name=MODEL, success=True, finish_reason="stop",
            content=json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content,
            token_usage=usage, metadata={"usage_complete": not unknown_usage}, created_at=NOW.isoformat())


def _gateway(root, script, lifecycle, *, budget_tokens=1_000_000, budget_mode="adapter", require_existing=False):
    limits = CanonicalBudgetVectorSchema(logical_model_calls=16, logical_tool_calls=4,
        model_tokens_total=budget_tokens, research_rounds=2, repair_attempts=2,
        generation_attempts=4, replan_attempts=2, true_retry_attempts=2)
    def owner(task_id, run_id, *, existing):
        return BudgetManager.canonical(task_id=task_id, run_id=run_id,
            limits=limits, max_elapsed_seconds=3600, storage_path=Path(root) / "budget.jsonl",
            policy_ref="budget-policy://local-verifier-recovery-fake-only", policy_version="v1", clock=lambda: NOW,
            run_commit_guard=lifecycle.assert_run_committable, require_existing_account=existing)
    manager = owner(research_tests.TASK_ID, research_tests.RUN_ID, existing=require_existing)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    lookups = {"provider": [], "existing": []}
    if budget_mode == "router":
        from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetRouter
        def provider(task_id, run_id):
            lookups["provider"].append((task_id, run_id))
            return owner(task_id, run_id, existing=False)
        def existing_provider(task_id, run_id):
            lookups["existing"].append((task_id, run_id))
            return owner(task_id, run_id, existing=True)
        budget_runtime = CanonicalInvocationBudgetRouter(provider, existing_manager_provider=existing_provider)
    else:
        assert budget_mode == "adapter"
        budget_runtime = adapter
    router = ModelRouter(MODEL, profiles=[ModelProfile(profile_id="lvr-production-fake", model_name=MODEL,
        provider="self-registered-test-provider", context_window=65536, max_output_tokens=1024)],
        policies=[RoutingPolicy(role=ModelRole.GENERAL, candidates=["lvr-production-fake"], availability_fallback=False)])
    gateway = ModelGateway(default_model_name=MODEL, router=router, budget_runtime=budget_runtime)
    client = ScriptedProvider(Path(root), script)
    gateway.register_client(client)
    return gateway, client, manager, adapter, lookups


def create_fixture(root, script=(), *, budget_tokens=1_000_000, budget_mode="adapter", support_relation="SUPPORTED"):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    env = research_tests._setup(root / "upstream")
    # The older Research owner fixture intentionally used an arbitrary facts-v1
    # test contract. Production Claim sources require the registered contract.
    old_fact = env["fact"].read_binding(env["fact_binding"], require_current=True)
    normalized = env["fact"].build_content_from_authoritative_sources(
        normalization_contract_version="project-input-normalization-v2",
        source_bindings=old_fact.source_bindings, facts=old_fact.facts)
    fact_logical = env["fact_binding"].version_ref.logical_ref
    fact_version = env["fact"].commit_snapshot(logical_ref=fact_logical, content=normalized,
        operation_ref=research_tests._op("lvr-fact-commit", "ARTIFACT_VERSION_COMMIT"))
    env["fact_binding"] = env["fact"].promote_snapshot(logical_ref=fact_logical,
        target_version_ref=fact_version, expected_head_revision=1,
        expected_active_version_id=env["fact_binding"].version_ref.artifact_version_id,
        operation_ref=research_tests._op("lvr-fact-promote", "ARTIFACT_ACTIVE_PROMOTION"))
    old_plan, _ = env["artifact"].read_version(env["plan_version"])
    from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanContentSchema
    plan_content = SectionPlanContentSchema.model_validate({**old_plan,
        "fact_snapshot_version_ref": fact_version.model_dump(mode="json")})
    env["plan_version"] = env["plan"].commit_plan(candidate_content=plan_content,
        logical_ref=env["plan_logical"], fact_snapshot_binding=env["fact_binding"], expected_head_revision=1,
        expected_active_version_id=env["plan_version"].artifact_version_id,
        operations=SectionPlanCommitOperations(version_commit=research_tests._op("lvr-plan-commit", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=research_tests._op("lvr-plan-promote", "ARTIFACT_ACTIVE_PROMOTION")))
    env["plan_binding"] = env["plan"].resolve_current_plan_binding(run_id=research_tests.RUN_ID,
        document_scope_ref="document:doc-1", section_id=research_tests.SECTION_ID)
    bundle = _completed_two_evidence_bundle()
    handle = "rag-evidence-bundle://opaque/lvr-fake-two-items"
    env["bundles"][handle] = bundle
    binding = CanonicalEvidenceBundleBindingSchema(evidence_bundle_handle=handle,
        evidence_bundle_sha256="sha256:" + evidence_bundle_sha256(bundle))
    package = env["package_content"].model_copy(update={"evidence_bundle_binding": binding,
        "section_plan_source": SectionPlanSourceRefSchema(plan_version_ref=env["plan_version"], section_id=research_tests.SECTION_ID),
        "selections": tuple(EvidencePackageSelectionSchema(evidence_id=f"E{i}", requirement_ids=("req-1",)) for i in (1, 2))})
    lineage = env["lineage"]
    env["package_version"] = env["research"].commit_evidence_package(candidate_content=package,
        logical_ref=env["package_logical"], plan_binding=env["plan_binding"], expected_head_revision=1,
        expected_active_version_id=env["package_version"].artifact_version_id,
        operations=EvidencePackageCommitOperations(version_commit=research_tests._op("lvr-package-commit", "ARTIFACT_VERSION_COMMIT",
            attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id),
            active_promotion=research_tests._op("lvr-package-promote", "ARTIFACT_ACTIVE_PROMOTION",
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id)), commit_guard_context=lineage)
    env["bundle"], env["bundle_binding"], env["package_content"] = bundle, binding, package
    paragraph = ParagraphRuntime(artifact_runtime=env["artifact"], authorization_validator=lambda operation, active: (
        operation.run_id == active.run_id and operation.write_instance_id == active.attempt_id) or (_ for _ in ()).throw(ValueError("bad paragraph authorization")),
        source_validator=lambda source, active: env["research"].validate_evidence_package_binding_current(
            env["research"].resolve_current_evidence_package_binding(run_id=active.run_id, section_id=research_tests.SECTION_ID)),
        manifest_authorization_validator=lambda operation, active: (operation.manifest_ref.attempt_id == active.attempt_id)
            or (_ for _ in ()).throw(ValueError("bad manifest authorization")), manifest_ledger_path=root / "paragraph-manifest.jsonl")
    candidate = paragraph_tests._candidate(env, paragraph, slot="lvr-initial-paragraph", text=TEXT)
    version, _ = paragraph_tests._commit_promote(env, paragraph, candidate)
    manifest = paragraph_tests._manifest_ref(env, paragraph)
    paragraph_tests._append(env, paragraph, manifest, expected_members=(), resulting_members=(version,), revision=0,
        authorization_ref=candidate.operation_ref.authorization_ref, slot="lvr-manifest-append")
    claim_runtime = ClaimVerificationRuntime(paragraph_runtime=paragraph, binding_source_validator=lambda *args: None,
        ledger_path=root / "claim-verification.jsonl")
    sources = TargetClaimRuntimeValidators(artifact_runtime=env["artifact"], paragraph_runtime=paragraph,
        research_runtime=env["research"], fact_snapshot_runtime=env["fact"], claim_resolver=claim_runtime.resolve_claim)
    claim_runtime.binding_source_validator = sources.validate_binding_sources
    context = ClaimOperationContextSchema(active_lineage=lineage, authorization_ref="lvr-prospective-original",
        operation_purpose="claim-verification")
    text_hash, value_hash = hashlib.sha256(TEXT.encode()).hexdigest(), canonical_sha256(TEXT)
    occurrence = claim_runtime.derive_claim_occurrence_ref(run_id=lineage.run_id, section_id=research_tests.SECTION_ID,
        paragraph_version_ref=version, claim_type=ClaimType.CURRENT_PROJECT_FACT.value, start_offset=0, end_offset=len(TEXT),
        text_span_hash=text_hash, span_locator_policy_id="exact-character-offset", span_locator_policy_version="v1",
        normalized_value_hash=value_hash, normalization_policy_id="literal-claim-text", normalization_policy_version="v1",
        claim_identity_policy_id="claim-occurrence", claim_identity_policy_version="v1")
    claim_candidate = ClaimCandidateSchema(claim_occurrence_ref=occurrence, paragraph_version_ref=version,
        section_id=research_tests.SECTION_ID, claim_type=ClaimType.CURRENT_PROJECT_FACT.value,
        text_span=ClaimTextSpanSchema(start_offset=0, end_offset=len(TEXT), text=TEXT), text_span_hash=text_hash,
        normalized_value=TEXT, normalized_value_hash=value_hash, extraction_origin=ClaimExtractionOrigin.DETERMINISTIC_EXTRACTOR,
        extraction_occurrence_ref="claim-extraction://lvr-fake")
    coverage = ClaimCoverageAccountingSchema(coverage_scope_ref=lineage.attempt_scope_ref, paragraph_version_refs=(version,),
        coverage_units=(ArtifactClaimCoverageUnitSchema(paragraph_version_ref=version, coverage_unit_id="lvr-unit",
            start_offset=0, end_offset=len(TEXT), accounting_state=ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED,
            claim_occurrence_refs=(occurrence,)),), accounting_state=ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED,
        extraction_occurrence_ref=claim_candidate.extraction_occurrence_ref, coverage_semantic_contract_version="claim-coverage-v1")
    claim = claim_runtime.commit_claim_records((claim_candidate,), coverage, context).records[0]
    bound = sources.bind_claim_records(claim_runtime=claim_runtime, records=(claim,), operation_context=context)[0]
    support = TargetClaimSupportComposition(claim_runtime=claim_runtime, source_composition=sources, model_gateway=None)
    subject = ClaimSupportSubjectRefSchema(authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM, subject_ref=claim.claim_ref.claim_id)
    evidence = tuple(item.exact_source_ref for item in bound.exact_authorized_source_refs if item.source_kind == "EVIDENCE")
    assert len(evidence) == 2
    target = support.runtime.materialize_evidence_target_set(subject, evidence, TARGET_SET_CONTRACT)
    relation = SupportRelation(support_relation)
    assert relation in {SupportRelation.SUPPORTED, SupportRelation.CONTRADICTED}
    support_record = support.runtime.commit_support(ClaimEvidenceSupportCandidateSchema(claim_support_subject_ref=subject,
        evidence_target_set_ref=target, support_semantic_contract_version=SUPPORT_SEMANTIC_CONTRACT,
        relation=relation, supporting_refs=(evidence[0],) if relation is SupportRelation.SUPPORTED else (),
        contradicting_refs=(evidence[0],) if relation is SupportRelation.CONTRADICTED else (),
        evaluation_occurrence_ref="support-eval://lvr-fake", evaluator_policy_id=support.POLICY_ID,
        evaluator_policy_version=support.POLICY_VERSION), context)
    key = paragraph_tests._writer_key(env)
    project = ProjectInputSchema(task_id=research_tests.TASK_ID, task_type="document", user_query=TEXT,
        total_staff=12, generation_requirements={"required_sections": ["Section 1"]})
    writer_input = WriterGenerationInvocationSchema(writer_subject_ref=key.writer_subject_ref,
        write_instance_id=key.write_instance_id, route_decision_ref=key.route_decision_ref,
        generation_class=candidate.source_binding.generation_class,
        canonical_evidence_input={**bundle.model_dump(mode="json"), "evidence_bundle_sha256": evidence_bundle_sha256(bundle)},
        unified_writer_input_snapshot={"project_input": project.model_dump(mode="json")})
    writer = SQLitePreWriteExecutionStore(root / "writer.sqlite3")
    digest = canonical_sha256(writer_input.model_dump(mode="json"))
    writer.reserve_writer_execution(key, semantic_fingerprint=digest)
    writer.mark_writer_dispatched(key, semantic_fingerprint=digest)
    writer.persist_writer_result(key, semantic_fingerprint=digest, result=WriterExecutionResultSchema(
        logical_execution_key=key, status=WriterExecutionStatus.SUCCESS,
        artifact=SchemeSectionSchema(section_id=research_tests.SECTION_ID, section_title="Section 1", section_order=1,
            content=TEXT, input={"writer_generation_invocation": writer_input.model_dump(mode="json")}),
        generation_provenance={"generation_class": candidate.source_binding.generation_class}))
    # Create the fresh native account exactly once. Every subsequent reopen is
    # an existing-only read, including the real Router's response resolver.
    _gateway(root, script, env["lifecycle"], budget_tokens=budget_tokens, budget_mode=budget_mode)
    _write_json(root / "owner-manifest.json", {"schema_version": "lvr-production-fake-owner-manifest-v1",
        "bundle": bundle.model_dump(mode="json"), "bundle_handle": handle,
        "claim": claim.model_dump(mode="json"), "binding": bound.model_dump(mode="json"),
        "support": support_record.model_dump(mode="json"), "context": context.model_dump(mode="json"), "script": list(script),
        "budget_tokens": budget_tokens, "budget_mode": budget_mode})
    return reopen_fixture(root)


def reopen_fixture(root, *, script=None):
    root = Path(root)
    manifest = json.loads((root / "owner-manifest.json").read_text(encoding="utf-8"))
    lifecycle = RunAttemptLifecycleService(root / "upstream/lifecycle.jsonl", clock=research_tests._Clock(), id_generator=research_tests._Ids())
    artifact = ArtifactRuntime(root / "upstream/artifact-runtime.jsonl", lifecycle_service=lifecycle)
    fact = FactSnapshotRuntime(artifact_runtime=artifact, source_binding_resolver=lambda value: bool(value.source_version_ref))
    plan = SectionPlanRuntime(artifact_runtime=artifact, fact_snapshot_runtime=fact, requirements_resolver=lambda ref: research_tests._requirements())
    bundle = EvidenceBundleSchema.model_validate(manifest["bundle"])
    research = ResearchRuntime(artifact_runtime=artifact, section_plan_runtime=plan, fact_snapshot_runtime=fact,
        evidence_bundle_resolver=lambda handle: bundle if handle == manifest["bundle_handle"] else research_tests._bundle(),
        authorization_validator=lambda binding, ref: None, lifecycle_service=lifecycle,
        ledger_path=root / "upstream/research-runtime.jsonl")
    paragraph = ParagraphRuntime(artifact_runtime=artifact, authorization_validator=lambda *args: None,
        source_validator=lambda *args: None, manifest_authorization_validator=lambda *args: None,
        manifest_ledger_path=root / "paragraph-manifest.jsonl")
    runtime = ClaimVerificationRuntime(paragraph_runtime=paragraph, binding_source_validator=lambda *args: None,
        ledger_path=root / "claim-verification.jsonl")
    sources = TargetClaimRuntimeValidators(artifact_runtime=artifact, paragraph_runtime=paragraph,
        research_runtime=research, fact_snapshot_runtime=fact, claim_resolver=runtime.resolve_claim)
    runtime.binding_source_validator = sources.validate_binding_sources
    support = TargetClaimSupportComposition(claim_runtime=runtime, source_composition=sources, model_gateway=None)
    writer = SQLitePreWriteExecutionStore(root / "writer.sqlite3")
    gateway, client, budget, adapter, lookups = _gateway(root, manifest["script"] if script is None else script, lifecycle,
        budget_tokens=manifest["budget_tokens"], budget_mode=manifest.get("budget_mode", "adapter"), require_existing=True)
    composer = TargetClaimVerifierComposition(claim_runtime=runtime, support_composition=support,
        model_gateway=gateway, writer_execution_store=writer)
    runtime.support_resolver = support.runtime.resolve_support
    runtime.verification_source_validator = composer.validate_verification_sources
    runtime.verification_identity_validator = composer.validate_v2_identity
    return SimpleNamespace(root=root, lifecycle=lifecycle, artifact=artifact, fact=fact, plan=plan, research=research,
        paragraph=paragraph, runtime=runtime, sources=sources, support_composition=support, writer=writer,
        gateway=gateway, client=client, budget=budget, adapter=adapter, budget_lookups=lookups, composer=composer,
        claim=ClaimRecordSchema.model_validate(manifest["claim"]), binding=ClaimBindingFactsRecordSchema.model_validate(manifest["binding"]),
        support=ClaimEvidenceSupportRecordSchema.model_validate(manifest["support"]), context=ClaimOperationContextSchema.model_validate(manifest["context"]))


def legal_reply(verdict="UNSUPPORTED", positive_refs=()):
    return {"verdict": verdict, "positive_basis_refs": list(positive_refs),
        "rationale": "Fresh controlled full judgment for production integration.", "issues": []}


def provider_rows(fx):
    path = fx.root / "fake-provider-calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def verify(fx, *, context=None):
    return fx.composer.verify_claims(records=(fx.claim,), binding_records=(fx.binding,), support_records=(fx.support,),
        operation_context=context or fx.context)
