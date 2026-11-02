# FINAL PREPARATION CONSOLIDATION 03; TXT ONLY, NOT parsed/imported/collected/run.
# Combines immutable draft01 with approved content/identity/count amendment02.
# 5 kinds x 2 routes x 2 modes x 2 strategies = 40 authored controls.
# Exact B4 Packer da2575954bbc92190b35c497cdfe81a5b256b56537ff3f68a3cddfa6755264f8
# Gate 7ae488fe4ca12a9b8f474c291f95f4bd10b4fd197b0d928bb1f8a95ee0aa04ad.
# Retains original native producer, injection, consumer and legal-atom oracles.
# Same prepared-object identity exercised at each actual Packer call; no claim
# that independent direct-pack calls share one admission object. Existing B4
# controls separately cover Gate/Packer sharing the exact canonical admission.

# PREPARATION ONLY. NOT IMPORTED, COLLECTED OR EXECUTED. 2026-10-08.
# Future test-file contents, after root's B4/test-preparation authorization.
# Actual four chunkers -> native normalizers -> pre-Gov -> SAME prepared object
# -> actual Packer -> actual mapper/final Gov -> committed Package/Paragraph/
# authored Claim -> actual source/binding/read/reopen/Support consumer scope.
# No classifier/semantic Model/Provider call, Support verdict or business PASS.
# Do not use local_verifier_recovery_helpers.create_fixture: its two-EVIDENCE
# assertion makes an empty negative fail in SETUP instead of exercising sources.

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from core.hashing import canonical_sha256
from core.token_estimator import DeterministicTokenEstimator
from schemas.claim import ClaimType
from schemas.rag import (
    EvidenceRequirementsSchema, RAGToolInputSchema, RetrievalAccessScopeSchema,
)
from rag.context.context_packer import ContextPacker
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.plugins.chunkers.project_fact_record import ProjectFactRecordChunker
from rag.plugins.chunkers.table_row_record import TableRowRecordChunker
from rag.plugins.chunkers.policy_article_clause import PolicyArticleClauseChunker
from rag.plugins.chunkers.template_section import TemplateSectionChunker
from rag.retriever.bm25_child_retriever import normalize_child_chunk_record
from rag.retriever.milvus_child_retriever import normalize_milvus_child_hit
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.structured_evidence import build_structured_evidence
from rag.schema.prompt_evidence_candidate_v1 import build_prompt_evidence_candidate_v1
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanContentSchema
from apps.enterprise_document.services.agent_rag_v1.claim_pipeline import TargetClaimMaterializer
from apps.enterprise_document.services.agent_rag_v1.writer_evidence_projection import project_writer_evidence
from backend.tests import local_verifier_recovery_helpers as native
from backend.tests.test_fpbca_a1_v2_1_writer_representation import _catalog
from backend.tests.test_stage_c_structured_chunkers import _unit
from test_support.stage_d_fixtures import document_profile
from backend.tests.test_stage_d_structured_evidence_projection import _dense_hit


STRATEGIES = ("default", "lost_in_middle_aware")
KINDS = ("scalar", "fact_table_json", "pure_table", "policy", "template")
TAIL = "SOURCE_ATOM_LAST_CELL_OR_CONDITION_54c9e33f"
RAW_EXAMPLE = "RAW_TEMPLATE_EXAMPLE_296f1b72"
LONG = "受控条件说明" * 180
SOURCE_PATHS = (
    "backend/rag/plugins/chunkers/project_fact_record.py",
    "backend/rag/plugins/chunkers/table_row_record.py",
    "backend/rag/plugins/chunkers/policy_article_clause.py",
    "backend/rag/plugins/chunkers/template_section.py",
    "backend/rag/retriever/bm25_child_retriever.py",
    "backend/rag/retriever/milvus_child_retriever.py",
    "backend/rag/context/context_packer.py",
    "backend/rag/mapping/result_mapper.py",
    "backend/rag/evidence/governance_v2.py",
    "backend/rag/schema/prompt_evidence_candidate_v1.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/claim_composition.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/claim_support_composition.py",
)


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", round_trip=True)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _prepared_content_material(prepared):
    # Explicit JSON snapshots, NEVER generic asdict of this owned projection.
    candidates = prepared._candidate_snapshot()
    return {
        "test_trace_material_version": "prepared-public-snapshots-v1",
        "candidate_priority_order": [
            {"priority_index": index, "result": _jsonable(member.result),
             "prompt_visible_text": member.text,
             "prompt_visible_bytes_utf8": list(member.text.encode("utf-8")),
             "prompt_visible_sha256": sha256(member.text.encode("utf-8")).hexdigest(),
             "truncated": member.truncated, "prefix_sha256": member.prefix_sha256}
            for index, member in enumerate(candidates)
        ],
        "initial_dropped_results": _jsonable(prepared._dropped_snapshot()),
        "public_estimated_cost": {"tokens": prepared.estimated_tokens,
                                  "chars": prepared.estimated_chars},
    }


def _pack_prepared(packer, prepared, original_identity_token, **budgets):
    assert prepared._identity_token() is original_identity_token
    result = packer.pack([], prepared_projection=prepared, **budgets)
    assert prepared._identity_token() is original_identity_token
    return result


def _trace_once(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True,
                       indent=2) + "\n").encode("utf-8")
    with path.open("xb") as target:
        target.write(data)
    print("NATIVE_TRACE", str(path), "sha256=" + sha256(data).hexdigest())


def _produce(kind, route):
    """Authored cleaned source/catalog input; all derived child semantics native."""
    cls = {"scalar": ProjectFactRecordChunker,
           "fact_table_json": ProjectFactRecordChunker,
           "pure_table": TableRowRecordChunker,
           "policy": PolicyArticleClauseChunker,
           "template": TemplateSectionChunker}[kind]
    doc_id = "native-c2-" + kind
    query = {"scalar": "预算金额", "fact_table_json": "预算金额",
             "pure_table": "设备配置", "policy": "访问控制",
             "template": "建设方案"}[kind]
    claim_type = {"scalar": ClaimType.CURRENT_PROJECT_COST,
                  "fact_table_json": ClaimType.CURRENT_PROJECT_FACT,
                  "pure_table": ClaimType.CURRENT_PROJECT_FACT,
                  "policy": ClaimType.POLICY_REQUIREMENT,
                  "template": ClaimType.TEMPLATE_STRUCTURE}[kind]
    if kind == "template":
        profile = document_profile(document_id=doc_id, kb_id="template_style_kb",
            document_type="document_template", evidence_role="template_style",
            project_relation="general", project_id=None,
            allowed_claim_types=["template_structure", "style_guidance"])
        text_rows = [("第一章 建设方案", "title", None),
            ("必填：建设方案" + LONG + TAIL + "\n假设：" + RAW_EXAMPLE,
             "paragraph", None)]
    elif kind == "policy":
        profile = document_profile(document_id=doc_id, kb_id="policy_standard_kb",
            document_type="policy_standard", evidence_role="policy_standard",
            project_relation="general", project_id=None,
            allowed_claim_types=["policy_requirement"])
        text_rows = [("第一条 访问控制应记录审批条件。" + LONG +
                      "未经审批不得停用审计。" + TAIL, "paragraph", None)]
    else:
        profile = document_profile(document_id=doc_id,
            document_type="current_project_requirement", evidence_role="project_fact",
            allowed_claim_types=["current_project_cost", "current_project_fact"])
        if kind == "scalar":
            # Native infer_fact_status sees source-authored 预算; native value/unit
            # and exact rendered source_span are never replaced in the test.
            text_rows = [("预算金额：" + LONG + TAIL + "约250万元。", "paragraph", None)]
        else:
            header = ["设备配置", "预算金额"]
            structured = {"column_count": 2, "row_count": 2,
                "merged_cells": [], "rows": [header, [LONG + TAIL, "约250万元"]]}
            text_rows = [("| 设备配置 | 预算金额 |", "table", structured)]
    profile["parsing"]["chunk_strategy"] = cls.STRATEGY
    profile["parsing"]["parser_strategy"] = {
        "scalar": "project_fact_extraction", "fact_table_json": "project_fact_extraction",
        "pure_table": "structured_table", "policy": "policy_clause",
        "template": "template_document"}[kind]
    profile["source_sha256"] = sha256(json.dumps(text_rows, ensure_ascii=False,
        sort_keys=True).encode("utf-8")).hexdigest()
    profile["profile_sha256"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profile_sha256"})
    _catalog(profile)  # existing real DocumentProfileSchema validation
    units = []
    for order, (text, unit_type, table) in enumerate(text_rows, 1):
        unit = _unit(document_id=doc_id, strategy=cls.STRATEGY, order=order,
            text=text, unit_type=unit_type, kb_id=profile["kb_id"],
            evidence_role=profile["evidence_role"])
        unit["extra"]["document_profile"] = deepcopy(profile)
        if table is not None:
            unit["extra"]["structured_table"] = deepcopy(table)
        units.append(unit)
    chunks = cls(parent_chunk_size=12000, child_chunk_size=12000).chunk_records(units)
    child = next(item for item in chunks.children if TAIL in item["text"])
    parent = next(item for item in chunks.parents
                  if item["parent_chunk_id"] == child["parent_chunk_id"])
    if route == "dense":
        normalized = normalize_milvus_child_hit(_dense_hit({"child_chunk": child}), rank=1)["child_chunk"]
    else:
        normalized = normalize_child_chunk_record(child)
    result = build_retrieval_result_v2(child_chunk=normalized, parent_chunk=parent,
        rank=1, score=.99, context_granularity="child")
    # Explicit existing child-granularity boundary isolates this ORIGINAL legal
    # child atom. No atom parser, rewritten output, hand-authored Fact schema,
    # hand-selected visibility bit or manual supported capability is introduced.
    return {"producer": cls.__name__, "units": units, "profile": profile,
        "original_child": child, "original_parent": parent,
        "normalized_child": normalized, "result": result,
        "structured": build_structured_evidence(child),
        "query": query, "claim_type": claim_type}


def _prepared(produced):
    request = RAGToolInputSchema(task_id=native.research_tests.TASK_ID,
        run_id=native.research_tests.RUN_ID, agent_name="ResearchAgent",
        query=produced["query"], access_scope=RetrievalAccessScopeSchema(
            tenant_id="default", authorized_kb_ids=["project_fact_kb",
                "policy_standard_kb", "template_style_kb"]),
        max_context_chars=30000, max_context_items=1,
        retrieval_trace_id="trace-native-c2-" + produced["producer"],
        retrieval_scope="section", evidence_requirements=EvidenceRequirementsSchema.from_topics(
            section_id=native.research_tests.SECTION_ID, section_title="Section 1",
            section_type="general", topics=[produced["query"]]))
    invocation = RAGRequestMapper().map(request)
    rows = [deepcopy(produced["result"])]
    initial = OnlineEvidenceGovernanceV2().evaluate(query=produced["query"],
        results=rows, runtime_context=invocation.payload["extra_metadata"])
    accepted = []
    for row, assessment in zip(rows, initial.item_assessments):
        row["metadata"]["evidence_assessment_v2"] = assessment.model_dump(mode="json")
        if assessment.eligibility:  # same native pre-context rejection boundary
            accepted.append(row)
    packers = [ContextPacker(max_context_chars=30000, max_items=1,
                packing_strategy=strategy, include_metadata=False)
               for strategy in STRATEGIES]
    prepared = packers[0].prepare_projection(accepted)  # B4 must already be GREEN
    original_identity_token = prepared._identity_token()
    full = [_pack_prepared(packer, prepared, original_identity_token, token_budget=100000)
            for packer in packers]
    return request, invocation, rows, initial, packers, prepared, full, original_identity_token


def _mapped(request, invocation, original_rows, pack):
    return RAGResultMapper().map(request=request, invocation=invocation,
        raw_result={"data": {"retrieval_results": original_rows,
                             "context_pack": pack.to_dict()}},
        latency_ms=0, rag_project_root=str(Path(__file__).resolve().parents[2]),
        service_name="native-c2-v10-v11-v12-local-control")


def _consumer(root, bundle, claim_type, authored_paragraph_text):
    """Reuse real low-level fixture owners; 0/1 Evidence is a LEGAL observation.

    Authored paragraph/Claim/type is input to an exact source protocol test, not
    actual generated prose, a classifier output, or a semantic Support result.
    """
    root.mkdir(parents=True, exist_ok=True)
    env = native.research_tests._setup(root / "upstream")
    rt = native.research_tests
    old_fact = env["fact"].read_binding(env["fact_binding"], require_current=True)
    content = env["fact"].build_content_from_authoritative_sources(
        normalization_contract_version="project-input-normalization-v2",
        source_bindings=old_fact.source_bindings, facts=old_fact.facts)
    logical = env["fact_binding"].version_ref.logical_ref
    version = env["fact"].commit_snapshot(logical_ref=logical, content=content,
        operation_ref=rt._op("c2-fact-commit", "ARTIFACT_VERSION_COMMIT"))
    env["fact_binding"] = env["fact"].promote_snapshot(logical_ref=logical,
        target_version_ref=version, expected_head_revision=1,
        expected_active_version_id=env["fact_binding"].version_ref.artifact_version_id,
        operation_ref=rt._op("c2-fact-promote", "ARTIFACT_ACTIVE_PROMOTION"))
    old_plan, _ = env["artifact"].read_version(env["plan_version"])
    plan = SectionPlanContentSchema.model_validate({**old_plan,
        "fact_snapshot_version_ref": version.model_dump(mode="json")})
    env["plan_version"] = env["plan"].commit_plan(candidate_content=plan,
        logical_ref=env["plan_logical"], fact_snapshot_binding=env["fact_binding"],
        expected_head_revision=1,
        expected_active_version_id=env["plan_version"].artifact_version_id,
        operations=native.SectionPlanCommitOperations(
            version_commit=rt._op("c2-plan-commit", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=rt._op("c2-plan-promote", "ARTIFACT_ACTIVE_PROMOTION")))
    env["plan_binding"] = env["plan"].resolve_current_plan_binding(
        run_id=rt.RUN_ID, document_scope_ref="document:doc-1", section_id=rt.SECTION_ID)
    # MINIMAL INJECTION SEAM: completed actual producer/packer/mapper Bundle is
    # registered BEFORE NEW Package and Paragraph immutable source snapshots.
    handle = "rag-evidence-bundle://opaque/native-c2-" + native.evidence_bundle_sha256(bundle)
    env["bundles"][handle] = bundle
    binding = native.CanonicalEvidenceBundleBindingSchema(evidence_bundle_handle=handle,
        evidence_bundle_sha256="sha256:" + native.evidence_bundle_sha256(bundle))
    package = env["package_content"].model_copy(update={"evidence_bundle_binding": binding,
        "section_plan_source": native.SectionPlanSourceRefSchema(
            plan_version_ref=env["plan_version"], section_id=rt.SECTION_ID),
        "selections": tuple(native.EvidencePackageSelectionSchema(evidence_id=eid,
            requirement_ids=("req-1",)) for eid in bundle.selected_evidence_ids)})
    lineage = env["lineage"]
    env["package_version"] = env["research"].commit_evidence_package(
        candidate_content=package, logical_ref=env["package_logical"],
        plan_binding=env["plan_binding"], expected_head_revision=1,
        expected_active_version_id=env["package_version"].artifact_version_id,
        operations=native.EvidencePackageCommitOperations(
            version_commit=rt._op("c2-package-commit", "ARTIFACT_VERSION_COMMIT",
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id),
            active_promotion=rt._op("c2-package-promote", "ARTIFACT_ACTIVE_PROMOTION",
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id)),
        commit_guard_context=lineage)
    env["bundle"], env["bundle_binding"], env["package_content"] = bundle, binding, package

    def paragraph_authorization(operation, active):
        assert operation.run_id == active.run_id and operation.write_instance_id == active.attempt_id

    def paragraph_source(source, active):
        current = env["research"].resolve_current_evidence_package_binding(
            run_id=active.run_id, section_id=rt.SECTION_ID)
        assert current.version_ref == source.evidence_package_version_ref
        return env["research"].validate_evidence_package_binding_current(
            current, require_source_fresh=True)

    def manifest_authorization(operation, active):
        assert operation.manifest_ref.attempt_id == active.attempt_id

    paragraph = native.ParagraphRuntime(artifact_runtime=env["artifact"],
        authorization_validator=paragraph_authorization, source_validator=paragraph_source,
        manifest_authorization_validator=manifest_authorization,
        manifest_ledger_path=root / "paragraph-manifest.jsonl")
    candidate = native.paragraph_tests._candidate(env, paragraph,
        slot="native-c2-source-paragraph", text=authored_paragraph_text)
    paragraph_version, _ = native.paragraph_tests._commit_promote(env, paragraph, candidate)
    manifest = native.paragraph_tests._manifest_ref(env, paragraph)
    native.paragraph_tests._append(env, paragraph, manifest, expected_members=(),
        resulting_members=(paragraph_version,), revision=0,
        authorization_ref=candidate.operation_ref.authorization_ref, slot="native-c2-append")
    runtime = native.ClaimVerificationRuntime(paragraph_runtime=paragraph,
        binding_source_validator=lambda *args: None, ledger_path=root / "claim-verification.jsonl")
    sources = native.TargetClaimRuntimeValidators(artifact_runtime=env["artifact"],
        paragraph_runtime=paragraph, research_runtime=env["research"],
        fact_snapshot_runtime=env["fact"], claim_resolver=runtime.resolve_claim)
    runtime.binding_source_validator = sources.validate_binding_sources
    context = native.ClaimOperationContextSchema(active_lineage=lineage,
        authorization_ref="native-c2-authored-claim", operation_purpose="claim-verification")
    materializer = TargetClaimMaterializer()
    claim_candidate = materializer.build_candidate(run_id=lineage.run_id,
        section_id=rt.SECTION_ID, paragraph_version_ref=paragraph_version,
        paragraph_text=authored_paragraph_text, claim_type=claim_type,
        start_offset=0, end_offset=len(authored_paragraph_text),
        normalized_value=authored_paragraph_text, unit=None,
        extraction_origin=native.ClaimExtractionOrigin.DETERMINISTIC_EXTRACTOR,
        extraction_occurrence_ref="claim-extraction://authored-native-c2-fixture",
        span_locator_policy_id="exact-character-offset", span_locator_policy_version="v1",
        normalization_policy_id="literal-claim-text", normalization_policy_version="v1",
        claim_identity_policy_id="claim-occurrence", claim_identity_policy_version="v1")
    coverage = materializer.build_whole_paragraph_coverage(
        paragraph_version_ref=paragraph_version, paragraph_text=authored_paragraph_text,
        candidates=(claim_candidate,), extraction_occurrence_ref=claim_candidate.extraction_occurrence_ref,
        coverage_scope_ref=lineage.attempt_scope_ref, coverage_semantic_contract_version="claim-coverage-v1")
    claim = runtime.commit_claim_records((claim_candidate,), coverage, context).records[0]
    bound = sources.bind_claim_records(claim_runtime=runtime, records=(claim,), operation_context=context)[0]
    rows = sources.materialize_sources(claim, lineage)
    assert sources.read_current_sources(claim, lineage) == rows
    converted = runtime._binding_record_candidate(bound)  # NOT the persisted Record as candidate
    assert converted == sources._candidate_from_sources(claim, rows)
    evidence = tuple(row for row in rows if row.source_ref.source_kind == "EVIDENCE")
    support = native.TargetClaimSupportComposition(claim_runtime=runtime,
        source_composition=sources, model_gateway=None)
    subject = native.ClaimSupportSubjectRefSchema(
        authority_kind=native.ClaimSupportAuthorityKind.SIF07_CLAIM,
        subject_ref=claim.claim_ref.claim_id)
    target = support.runtime.materialize_evidence_target_set(subject,
        tuple(row.source_ref.exact_source_ref for row in evidence), native.TARGET_SET_CONTRACT)
    assert support._scope(target, context)[2] == evidence
    assert support._read_scope(target, context)[2] == evidence
    semantic = support._input_material(claim, bound, evidence, target)
    # No _package/evaluate/provider/Support commit. Only exact existing consumer
    # scope/input projection; this cannot be credited as semantic Support PASS.
    package_binding = env["research"].resolve_current_evidence_package_binding(
        run_id=lineage.run_id, section_id=rt.SECTION_ID)
    visible_writer = project_writer_evidence(binding=package_binding, package=package,
        bundle=bundle, context_text=bundle.context.context_text, citations=bundle.citations)

    # Cold source-reader reopen: recreate only existing local native readers.
    lifecycle2 = native.RunAttemptLifecycleService(root / "upstream/lifecycle.jsonl",
        clock=rt._Clock(), id_generator=rt._Ids())
    artifact2 = native.ArtifactRuntime(root / "upstream/artifact-runtime.jsonl", lifecycle_service=lifecycle2)
    fact2 = native.FactSnapshotRuntime(artifact_runtime=artifact2,
        source_binding_resolver=lambda value: bool(value.source_version_ref))
    plan2 = native.SectionPlanRuntime(artifact_runtime=artifact2, fact_snapshot_runtime=fact2,
        requirements_resolver=lambda ref: rt._requirements())
    research2 = native.ResearchRuntime(artifact_runtime=artifact2, section_plan_runtime=plan2,
        fact_snapshot_runtime=fact2, evidence_bundle_resolver=lambda ref: env["bundles"][ref],
        authorization_validator=lambda package_binding, round_ref: None,
        lifecycle_service=lifecycle2, ledger_path=root / "upstream/research-runtime.jsonl")
    paragraph2 = native.ParagraphRuntime(artifact_runtime=artifact2,
        authorization_validator=paragraph_authorization, source_validator=paragraph_source,
        manifest_authorization_validator=manifest_authorization,
        manifest_ledger_path=root / "paragraph-manifest.jsonl")
    runtime2 = native.ClaimVerificationRuntime(paragraph_runtime=paragraph2,
        binding_source_validator=lambda *args: None, ledger_path=root / "claim-verification.jsonl")
    sources2 = native.TargetClaimRuntimeValidators(artifact_runtime=artifact2,
        paragraph_runtime=paragraph2, research_runtime=research2,
        fact_snapshot_runtime=fact2, claim_resolver=runtime2.resolve_claim)
    runtime2.binding_source_validator = sources2.validate_binding_sources
    claim2 = runtime2.resolve_claim_by_id(claim.claim_ref.claim_id)
    assert claim2 == claim
    reopened_rows = sources2.read_current_sources(claim2, lineage)
    assert reopened_rows == rows
    counts = {str(path.relative_to(root)): len(path.read_text(encoding="utf-8").splitlines())
              for path in root.rglob("*.jsonl")}
    return {"package_content": package, "package_binding": package_binding,
        "paragraph_version_ref": paragraph_version, "paragraph_source_binding": candidate.source_binding,
        "claim": claim, "authored_claim_candidate": claim_candidate, "coverage": coverage,
        "binding": bound, "binding_candidate": converted, "target": target,
        "rows": rows, "evidence": evidence, "reopened_rows": reopened_rows,
        "semantic_material": semantic, "visible_writer": visible_writer,
        "journal_line_counts": counts, "gateway_supplied": False,
        "native_model_gateway_actual_count": 0,
        "native_model_gateway_count_basis": {
            "classification": "SOURCE_BOUND_NO_MODEL_GATEWAY_CONFIGURED",
            "model_gateway_supplied": False,
            "actual_consumer_path": ["_scope", "_read_scope", "_input_material"],
            "semantic_dispatch_or_package_evaluate_invoked": False,
            "physical_call_spy_or_provider_receipt": "NOT_COLLECTED",
        },
        "physical_fake_model_rows": {
            "classification": "SEPARATE_PHYSICAL_ROW_ACCOUNTING",
            "count": None,
            "state": "NOT_INSTRUMENTED_BY_THIS_NATIVE_SOURCE_ONLY_FIXTURE",
        },
        "support_verdict_commits": {
            "count": 0,
            "classification": "SOURCE_BOUND_NO_SUPPORT_EVALUATE_OR_COMMIT_PATH",
        }}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ("dense", "bm25"))
@pytest.mark.parametrize("mode", ("full", "tight"))
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_native_producer_prompt_final_claim_visibility(tmp_path, kind, route, mode, strategy):
    """40 separately recorded local cases. No semantic Model claims."""
    produced = _produce(kind, route)
    request, invocation, raw, initial, packers, prepared, full, original_identity_token = _prepared(produced)
    prepared_content = _prepared_content_material(prepared)
    prepared_content_sha256 = canonical_sha256(prepared_content)
    original_atom = produced["original_child"]["text"].strip()
    if kind == "template":
        # The legal atom is the actual safe governed template projection.
        # Raw example body is NEVER this positive oracle.
        legal_atom = full[0].selected_results[0]["prompt_visible_text"] if full[0].selected_results else ""
    else:
        legal_atom = original_atom
    outcomes = []
    index = STRATEGIES.index(strategy)
    for strategy, packer, complete in ((strategy, packers[index], full[index]),):
        budget = 100000
        pack = complete
        observed_budget_trials = []
        if mode == "tight":
            real_full_cost = max(row.tokens_used for row in full)
            # Bounded exact-renderer probes, not guessed reserve or a new owner.
            budgets = sorted({65, max(65, real_full_cost // 2), max(65, real_full_cost - 1)})
            trials = [(b, _pack_prepared(packer, prepared, original_identity_token, token_budget=b))
                      for b in budgets if b < real_full_cost]
            observed_budget_trials = [{"budget": b, "pack": row.to_dict()} for b, row in trials]
            incomplete = [(b, row) for b, row in trials if row.selected_results
                and legal_atom not in row.selected_results[0]["prompt_visible_text"]]
            rejected = [(b, row) for b, row in trials if not row.selected_results]
            # Prefer a REAL retained partial atom when reachable. After the
            # authorized fix rejects it, the same bounded budget can lawfully drop.
            chosen = incomplete or rejected
            if not chosen:
                _trace_once(tmp_path / strategy / "premise-not-reached.json", {
                    "status": "INVALID_OR_UNREACHED_NEGATIVE_PREMISE", "produced": produced,
                    "full_pack": complete.to_dict(), "budget_trials": observed_budget_trials})
                pytest.fail("No actual prefix/drop premise reached; do not count setup/fixture refusal as visibility proof")
            budget, pack = chosen[0]
        bundle = _mapped(request, invocation, raw, pack)
        prompt = (build_prompt_evidence_candidate_v1(pack.selected_results[0])
                  if pack.selected_results else None)
        authored_text = legal_atom or original_atom
        consumer = _consumer(tmp_path / strategy / "owners", bundle,
            produced["claim_type"], authored_text)
        eligible = TargetClaimMaterializer.eligible_evidence_ids(bundle, produced["claim_type"])
        rows = consumer["evidence"]
        retained = tuple(row["prompt_visible_text"] for row in pack.selected_results)
        evidence_ids = tuple(sorted(row.material["evidence_id"] for row in rows))
        atomic = bool(retained) and all(legal_atom in value for value in retained)
        repo = Path(__file__).resolve().parents[2]
        trace = {
            "classification": "ACTUAL_NATIVE_PRODUCER_VISIBILITY_AND_EXACT_SOURCE_PROTOCOL",
            "case": {"kind": kind, "route": route, "mode": mode, "strategy": strategy},
            "authored_input": {"cleaned_source_units": produced["units"],
                "catalog_profile": produced["profile"], "paragraph_and_claim_text": authored_text,
                "claim_type": produced["claim_type"].value,
                "classification_or_semantic_model_output": False},
            "actual_producer": {key: value for key, value in produced.items()
                if key not in {"units", "profile"}},
            "original_legal_atom": legal_atom, "original_legal_atom_sha256": sha256(legal_atom.encode("utf-8")).hexdigest(),
            "template_atom_is_governed_projection": kind == "template",
            "initial_native_assessments": initial.item_assessments,
            "initial_premise_eligible": all(row.eligibility for row in initial.item_assessments),
            "prepared_projection_content": prepared_content,
            "prepared_projection_content_sha256": prepared_content_sha256,
            "prepared_estimated_tokens": prepared.estimated_tokens,
            "prepared_estimated_chars": prepared.estimated_chars,
            "prepared_process_identity": {
                "pid": os.getpid(), "object_id": id(prepared),
                "identity_token_id": id(original_identity_token),
                "classification": "PROCESS_LOCAL_DIAGNOSTIC_NOT_DURABLE_CONTENT_ID",
            },
            "packed_result_content_sha256": canonical_sha256(_jsonable(pack.to_dict())),
            "budget": budget, "actual_full_render_costs": [row.tokens_used for row in full],
            "measured_budget_trials": observed_budget_trials,
            "packed": pack.to_dict(), "retained_bytes_utf8": [list(value.encode("utf-8")) for value in retained],
            "retained_sha256": [sha256(value.encode("utf-8")).hexdigest() for value in retained],
            "retained_prefix_hash_without_delivery_suffix": [sha256(value.removesuffix("\n...[TRUNCATED]").encode("utf-8")).hexdigest() for value in retained],
            "whole_legal_atom_retained": atomic, "prompt_candidate": prompt,
            "final_completed_bundle": bundle, "direct_eligible_evidence_ids": eligible,
            "final_consumer": {**consumer, "rows": [{"source_ref": row.source_ref,
                "fact": row.fact, "material": row.material} for row in consumer["rows"]],
                "evidence": [{"source_ref": row.source_ref, "fact": row.fact,
                    "material": row.material} for row in rows],
                "reopened_rows": [{"source_ref": row.source_ref, "fact": row.fact,
                    "material": row.material} for row in consumer["reopened_rows"]]},
            "actual_counts": {"pack_selected": len(pack.selected_results),
                "pack_dropped": len(pack.dropped_results), "bundle_selected": len(bundle.selected_evidence_ids),
                "bundle_dropped": len(bundle.dropped_evidence_ids), "direct_eligible": len(eligible),
                "Claim_EVIDENCE_rows": len(rows), "support_target_members": len(consumer["target"].evidence_member_refs)},
            "production_source_hashes": {name: sha256((repo / name).read_bytes()).hexdigest() for name in SOURCE_PATHS},
            "semantic_Model_Support_Verifier_quality_release": "UNPROVEN_NOT_RUN",
        }
        # CREATE-ONCE artifact precedes the frozen acceptance assertions below.
        # A native partial atom is preserved even when this directed control REDs.
        _trace_once(tmp_path / strategy / "native-producer-final-consumer-trace.json", trace)
        assert initial.item_assessments and all(row.eligibility for row in initial.item_assessments), "Invalid native relevance/status premise, not a visibility negative"
        assert bundle.assessment.assessment_state == "completed"
        assert pack.tokens_used == DeterministicTokenEstimator.estimate(pack.context)
        assert pack.tokens_used <= budget
        assert evidence_ids == tuple(sorted(eligible))
        assert consumer["visible_writer"]["context_text"] == bundle.context.context_text
        selected_items = {item.evidence_id: item for item in bundle.items
                          if item.evidence_id in bundle.selected_evidence_ids}
        for row in rows:
            assert row.material["context_text"] == selected_items[row.material["evidence_id"]].context_text
            assert row.material["context_text"] in pack.context
            assert row.material["context_text_sha256"] == canonical_sha256(row.material["context_text"])
        if mode == "full":
            assert atomic and len(pack.selected_results) == 1
            assert not pack.selected_results[0]["metadata"]["context_truncated"]
            assert eligible == tuple(sorted(bundle.selected_evidence_ids)) and len(rows) == 1
            if kind in {"scalar", "fact_table_json"}:
                assert prompt.structured_evidence.fact is not None
                assert prompt.fact_visible_in_prompt is True
                assert prompt.structured_evidence.fact.source_span["text"] in retained[0]
            if kind == "template":
                assert prompt.structured_evidence.template.required_fields
                assert all(field in legal_atom for field in prompt.structured_evidence.template.required_fields)
                assert RAW_EXAMPLE not in legal_atom
        elif kind in {"scalar", "fact_table_json"}:
            if prompt is not None:
                assert not atomic and prompt.context_truncated
                assert prompt.fact_visible_in_prompt is False
            assert eligible == () and rows == ()
        else:
            # FROZEN V11: original producer table/policy atom, or actual governed
            # template atom, is COMPLETE or rejected. A broad capability,
            # fact=None, visibility=False or missing TAIL cannot substitute.
            assert not retained or atomic, "Native Packer retained an incomplete original legal atom"
            if not retained:
                assert eligible == () and rows == ()
        outcomes.append((tuple(sorted(item["child_chunk_id"] for item in pack.selected_results)),
                         tuple(sorted(item["child_chunk_id"] for item in pack.dropped_results))))
    same_budget = [_pack_prepared(packer, prepared, original_identity_token, token_budget=budget)
                   for packer in packers]
    cross_strategy_ids = [(tuple(sorted(item["child_chunk_id"] for item in row.selected_results)),
                           tuple(sorted(item["child_chunk_id"] for item in row.dropped_results)))
                          for row in same_budget]
    assert cross_strategy_ids[0] == cross_strategy_ids[1]  # one prepared membership authority
    assert prepared._identity_token() is original_identity_token
    assert _prepared_content_material(prepared) == prepared_content
