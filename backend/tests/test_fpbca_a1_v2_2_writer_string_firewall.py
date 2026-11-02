"""V2.2 independent string oracle and actual canonical-builder generation proofs.

Component/typed-boundary attacks are not physical ModelRequest witnesses.  Policy
and template scenarios additionally execute current chunker/Governance/Packer/
Mapper owners.  Physical final-message evidence is produced by the separate lane.
The oracle imports no projection-private allowlists and checks dictionary keys.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactLogicalRefSchema, ArtifactVersionRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    CanonicalEvidenceBundleBindingSchema, EvidencePackageBindingSchema,
    EvidencePackageSelectionSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import EvidenceCriterionSchema, EvidenceRequirementSchema
from apps.enterprise_document.services.agent_rag_v1 import writer_evidence_projection as production
from apps.enterprise_document.services.agent_rag_v1.citation_association import associate_selected_citations
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    BusinessProfileTemplate, TaskContractTemplate, EvidenceRequirementsMaterializer,
)
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256, _EvidenceBundleBuilder
from core.hashing import canonical_sha256
from rag.context.context_packer import ContextPacker
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.plugins.chunkers.policy_article_clause import PolicyArticleClauseChunker
from rag.plugins.chunkers.template_section import TemplateSectionChunker
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from schemas.citation import CitationSchema, citation_source_identity
from schemas.rag import (
    EvidenceBundleSchema, EvidenceDisposition, RAGToolInputSchema,
    RetrievalAccessScopeSchema, EvidenceRequirementsSchema, RetrievedChunkSchema,
)
from test_stage_c_structured_chunkers import _unit
from test_support.stage_d_fixtures import document_profile


def fresh(label):
    return "v22_hidden_" + label + "_" + uuid4().hex[:12]


def canonical(value):
    return value.model_dump(mode="json", round_trip=True) if hasattr(value, "model_dump") else deepcopy(value)


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class Frame:
    bundle: object
    binding: object
    package: object
    marker: str
    snapshot: object | None = None
    requirement_ids: tuple = ()


def frame_for(bundle, marker, *, requirement_ids=(), snapshot=None):
    bundle_hash = evidence_bundle_sha256(bundle)
    source_binding = CanonicalEvidenceBundleBindingSchema(
        evidence_bundle_handle="rag-evidence-bundle://v1/" + canonical_sha256({"fixture_bundle": bundle_hash}),
        evidence_bundle_sha256="sha256:" + bundle_hash,
    )
    version = ArtifactVersionRefSchema(
        logical_ref=ArtifactLogicalRefSchema(run_id="run-v22-focused", logical_artifact_id="evidence-package:fixture",
                                             artifact_type="evidence_package", domain_scope_ref="section:focused"),
        artifact_version_id="av_" + canonical_sha256({"fixture_version": bundle_hash}),
        content_ref="artifact://runtime/fixture", content_hash="sha256:" + canonical_sha256({"fixture": bundle_hash}),
        content_schema_version="evidence_package_content_v1", committed_by_operation_id="fixture-operation",
        semantic_fingerprint=canonical_sha256({"fixture_fingerprint": bundle_hash}),
    )
    binding = EvidencePackageBindingSchema(version_ref=version, resolved_head_revision=1)
    package = SimpleNamespace(
        selections=tuple(EvidencePackageSelectionSchema(evidence_id=eid, requirement_ids=requirement_ids)
                         for eid in bundle.selected_evidence_ids), evidence_bundle_binding=source_binding,
    )
    return Frame(bundle, binding, package, marker, snapshot, tuple(requirement_ids))


def built_bundle(marker, *, field=None, count=1, same_parent=False):
    """Aliases come from the actual builder, never from a test expectation dump."""
    chunks, citations = [], []
    for index in range(count):
        parent = "parent-shared" if same_parent else f"parent-{index}"
        child = f"child-{index}"
        chunk_data = dict(rank=index + 1, matched_chunk_id=child, context_chunk_id=parent,
            child_chunk_id=child, parent_chunk_id=parent, doc_id=f"document-{index}",
            match_text="Canonical raw source " + marker, context_text="Canonical raw source " + marker,
            title="Source metadata", section="Source section", page_start=1, page_end=2)
        citation_data = dict(citation_id=f"external-origin-{index}", source_type="document", doc_id=f"document-{index}",
            source_document_id=f"document-{index}", parent_chunk_id=parent, child_chunk_id=child, chunk_id=child,
            quote_text="Canonical raw source " + marker, title="Source metadata", section="Source section",
            page_start=1, page_end=2)
        if field in chunk_data:
            chunk_data[field] = marker
        if field in citation_data:
            citation_data[field] = marker
        if field == "file_id" or field == "table_id":
            citation_data[field] = marker
        if field == "row_ids":
            citation_data[field] = [marker]
        # Identity links must remain exact when attacking a locator.
        for name in ("parent_chunk_id", "child_chunk_id", "doc_id"):
            if field == name:
                citation_data[name] = chunk_data[name]
        if field in {"child_chunk_id", "matched_chunk_id"}:
            chunk_data["child_chunk_id"] = chunk_data["matched_chunk_id"] = marker
            citation_data["child_chunk_id"] = citation_data["chunk_id"] = marker
        if field in {"parent_chunk_id", "context_chunk_id"}:
            chunk_data["parent_chunk_id"] = chunk_data["context_chunk_id"] = marker
            citation_data["parent_chunk_id"] = marker
        if field == "doc_id":
            citation_data["source_document_id"] = marker
        chunks.append(RetrievedChunkSchema.model_validate(chunk_data))
        citations.append(CitationSchema.model_validate(citation_data))
    bundle = build_evidence_bundle(query="Visible source request", rewritten_queries=(), selected_chunks=chunks,
        dropped_chunks=(), citations=citations, trace=None, max_context_chars=12000,
        context_pack={"rendered_text": "[C1]\n合法可见正文。", "max_context_chars": 12000})
    return bundle


def producer_bundle(kind, marker):
    """Fresh policy-section / template-title BODY input; original owners execute."""
    if kind == "policy":
        texts = ["第一章 一般规定", "第一节 " + marker, "第一条 材料核验", "一、应核对适用材料。"]
        cls = PolicyArticleClauseChunker
        profile = document_profile(document_id="producer-policy-v22", kb_id="policy_standard_kb",
            document_type="policy_standard", evidence_role="policy_standard", project_relation="general", project_id=None,
            allowed_claim_types=["policy_requirement"])
        budget = 1000
    else:
        texts = ["一、" + "章节格式说明" * 100 + marker, "应采用清晰章节结构。"]
        cls = TemplateSectionChunker
        profile = document_profile(document_id="producer-template-v22", kb_id="template_style_kb",
            document_type="document_template", evidence_role="template_style", project_relation="general", project_id=None,
            allowed_claim_types=["template_structure", "style_guidance"])
        budget = 80
    profile["parsing"]["chunk_strategy"] = cls.STRATEGY
    profile["parsing"]["parser_strategy"] = "policy_clause" if kind == "policy" else "template_document"
    units = []
    for index, text in enumerate(texts, 1):
        value = _unit(document_id=profile["document_id"], strategy=cls.STRATEGY, order=index, text=text,
                      kb_id=profile["kb_id"], evidence_role=profile["evidence_role"], unit_type="paragraph")
        value["extra"]["document_profile"] = deepcopy(profile)
        units.append(value)
    chunked = cls(parent_chunk_size=6000, child_chunk_size=6000).chunk_records(units)
    child = chunked.children[-1]
    parent = next(value for value in chunked.parents if value["parent_chunk_id"] == child["parent_chunk_id"])
    record = build_retrieval_result_v2(child_chunk=child, parent_chunk=parent, rank=1, score=.98)
    request = RAGToolInputSchema(task_id="task-v22-component-" + uuid4().hex[:8], run_id="run-v22-component-" + uuid4().hex[:8],
        agent_name="ResearchAgent", query="章节材料核验", max_context_chars=12000, max_context_items=3,
        access_scope=RetrievalAccessScopeSchema(tenant_id="default", authorized_kb_ids=[profile["kb_id"]]),
        retrieval_trace_id="trace-v22-" + uuid4().hex[:8], retrieval_scope="section",
        evidence_requirements=EvidenceRequirementsSchema.from_topics(section_id="focused", section_title="独立测试",
                                                                  section_type="general", topics=["章节材料核验"]))
    invocation = RAGRequestMapper().map(request)
    gov = OnlineEvidenceGovernanceV2().evaluate(query=request.query, results=[record], runtime_context=invocation.payload["extra_metadata"])
    record["metadata"]["evidence_assessment_v2"] = gov.item_assessments[0].model_dump(mode="json")
    pack = ContextPacker(include_metadata=False, packing_strategy="default", max_context_chars=12000).pack([record], token_budget=budget)
    bundle = RAGResultMapper().map(request=request, invocation=invocation,
        raw_result={"data": {"retrieval_results": [record], "context_pack": pack.to_dict()}}, latency_ms=0,
        rag_project_root="D:/MyCode/rag-agent-shared", service_name="v22_independent_focused")
    assert bundle.selected_evidence_ids
    assert marker in serialized(canonical(bundle))
    assert marker not in bundle.context.context_text, "No hidden-governed producer witness; do not count this as production PASS"
    return bundle


def artifact_dir(label):
    root = os.environ.get("FPBCA_V22_FOCUSED_ARTIFACTS")
    if not root:
        return None
    path = Path(root) / (re.sub(r"[^A-Za-z0-9_.-]", "_", label)[:90] + "_" + uuid4().hex[:8])
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_case(path, name, value):
    if path is not None:
        (path / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def project(frame, label):
    path = artifact_dir(label)
    before = canonical(frame.bundle)
    raw_hash = canonical_sha256(before)
    write_case(path, "canonical_before.json", before)
    write_case(path, "inputs.json", {"marker": frame.marker, "binding": canonical(frame.binding),
        "bundle_binding": canonical(frame.package.evidence_bundle_binding),
        "selections": [canonical(item) for item in frame.package.selections],
        "owner_snapshot": canonical(frame.snapshot), "owner_ids": frame.requirement_ids})
    try:
        result = production.project_writer_evidence(binding=frame.binding, package=frame.package, bundle=frame.bundle,
            context_text=frame.bundle.context.context_text, citations=frame.bundle.citations,
            requirement_snapshot=frame.snapshot, requirement_ids=frame.requirement_ids)
        write_case(path, "projection.json", result)
        return result
    except Exception as exc:
        write_case(path, "first_error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        after = canonical(frame.bundle)
        write_case(path, "preservation.json", {"before_sha256": raw_hash, "after_sha256": canonical_sha256(after),
                                              "unchanged": after == before})
        assert after == before and canonical_sha256(after) == raw_hash, "Projection mutated canonical raw"


# The closed vocabulary belongs to the frozen public contracts/design, not to
# production._REASONS/_QUALIFIERS or its firewall/classification implementation.
KEYS = frozenset("""binding selections evidence_bundle_binding assessment context_text citations sources source_constraints
 citation_usage schema_version version_ref logical_ref run_id logical_artifact_id artifact_type domain_scope_ref
 artifact_version_id content_ref content_hash content_schema_version committed_by_operation_id semantic_fingerprint
 producer_attempt_scope_ref producer_attempt_id resolved_head_revision evidence_id requirement_ids
 evidence_bundle_handle evidence_bundle_sha256 assessment_ref assessment_state status evidence_available
 authoritative_evidence_present historical_or_reference_evidence_present prompt_evidence_contract_missing
 selected_evidence_count dropped_evidence_count citation_count score coverage_ratio judge_name judge_version
 prompt_evidence_source supported_claim_types forbidden_claim_types reason_codes required_qualifiers
 required_topics covered_topics missing_topics requirement_owner requirements_sha256 conflicts conflict_ref claim_type
 evidence_ids citation_id citation_ref source_locator_ref page_start page_end allowed_aliases scope disposition rank
 support_ref source_ref bundle_json_pointer sha256 candidates candidate_id child_chunk_id parent_chunk_id document_id
 citation_ids profile_sha256 source_sha256 fact_visible_in_prompt context_truncated truncation_source truncation_reason
 document_type evidence_role project_relation project_id authority_level allowed_claim_types structured_evidence
 item_assessments eligibility_semantic_policy_id eligibility_semantic_policy_version relevance semantic_relevance
 eligibility supported_topics usage_constraints fact_claim_policies certainty_level eligible representation_type
 original_strategy actual_strategy fallback used fact table policy template fact_category fact_status source_span
 source_span_ref row_index calculation_record_present is_table_of_contents is_example_content unit_order_start
 unit_order_end page start end ordinal doc_id file_id source_document_id matched_chunk_id context_chunk_id
 chunk_id table_id row_ids title section quote_text summary confidence extra""".split())
TOKENS = frozenset("""evidence_package_binding_v1 artifact_version_ref_v1 artifact_logical_ref_v1
 canonical_evidence_bundle_binding_v1 evidence_package_selection_v1 rag_evidence_assessment_v1
 evidence_assessment_v2 pending completed sufficient insufficient conflicted unknown relevant irrelevant
 partially_relevant selected dropped document_profile_gate_v2 stage_d_v1 context_pack_selected_results
 legacy_fallback missing_blocked rag_evidence_use_eligibility v1 current_section
 fact_record fact_section text_evidence paragraph_text table_row table document policy_clause policy_article
 policy_document template_section structured_evidence_v1 project_fact_record table_row_record policy_article_clause
 template_section paragraph_text_evidence document_template_section cost schedule configuration quantity decision
 requirement general unstructured_text estimated proposed planned confirmed decided not_applicable wrong_project
 unconfirmed estimate_only proposal_only plan_only confirmed_but_not_authoritative decision_authority_insufficient
 current_project_claim_requires_current_project_relation fact_status_unknown fact_status_estimated fact_status_proposed
 fact_status_planned fact_status_confirmed fact_status_decided confirmed_fact_requires_authoritative_document
 decided_fact_requires_formal_authoritative_source structured_fact_not_visible_in_prompt
 document_profile_has_no_eligible_claim_capability rejected_by_final_crag_irrelevant semantic_relevance_below_threshold
 eligible_by_profile_structure_status_and_relevance evidence_conflict_detected coverage_partial coverage_absent
 coverage_sufficient retrieval_evidence_sufficient retrieval_evidence_insufficient token_or_character_budget""".split())
# Public enum values can be producer-closed, unlike arbitrary wire strings.
from schemas.claim import ClaimType
from schemas.evidence_source import AuthorityLevel, DocumentType, EvidenceRole, ProjectRelation
from schemas.evidence_assessment import EvidenceRelevance, EvidenceStatus
from rag.catalog.document_profile import ChunkStrategy
ENUM_TOKENS = frozenset(item.value for enum in (ClaimType, AuthorityLevel, DocumentType, EvidenceRole,
    ProjectRelation, EvidenceRelevance, EvidenceStatus, ChunkStrategy) for item in enum)
QUALIFIERS = frozenset({"该信息尚未确认", "根据现有估算材料", "最终金额以审批或采购结果为准", "拟", "尚待确认", "计划",
    "依据当前可用材料", "相关事实仍需业务确认", "建议", "参考同类项目", "需结合本项目确认",
    "仅覆盖已具备证据的部分", "缺失内容待补充确认", "基于旧版 Prompt 证据兼容投影，需人工复核"})


def closed_token_for_slot(name, value):
    """Producer vocabulary membership is tied to its actual semantic slot."""
    enums = {"document_type": DocumentType, "evidence_role": EvidenceRole, "project_relation": ProjectRelation,
             "authority_level": AuthorityLevel, "relevance": EvidenceRelevance, "status": EvidenceStatus,
             "claim_type": ClaimType, "supported_claim_types": ClaimType, "forbidden_claim_types": ClaimType,
             "allowed_claim_types": ClaimType}
    if name in enums:
        return value in {item.value for item in enums[name]}
    if name in {"original_strategy", "actual_strategy"}:
        # Independent producer contracts: TemplateSectionChunker emits the
        # document_template_section fallback; text chunkers use text_evidence
        # and paragraph_text_evidence.  Their wire field is broader than the
        # catalog ChunkStrategy enum, but remains a fixed producer vocabulary.
        return value in {item.value for item in ChunkStrategy} | {
            "document_template_section", "text_evidence", "paragraph_text_evidence"}
    if name == "schema_version":
        return value in {"evidence_package_binding_v1", "artifact_version_ref_v1", "artifact_logical_ref_v1",
            "canonical_evidence_bundle_binding_v1", "evidence_package_selection_v1", "rag_evidence_assessment_v1",
            "evidence_assessment_v2", "structured_evidence_v1"}
    if name == "disposition": return value in {"selected", "dropped"}
    if name == "assessment_state": return value in {"pending", "completed"}
    if name == "scope": return value == "current_section"
    if name == "judge_name": return value == "document_profile_gate_v2"
    if name == "judge_version": return value == "stage_d_v1"
    if name == "prompt_evidence_source": return value in {"context_pack_selected_results", "legacy_fallback", "missing_blocked"}
    if name == "eligibility_semantic_policy_id": return value == "rag_evidence_use_eligibility"
    if name == "eligibility_semantic_policy_version": return value == "v1"
    if name == "required_qualifiers": return value in QUALIFIERS
    if name == "truncation_reason": return value == "token_or_character_budget"
    if name == "fact_category": return value in {"cost", "schedule", "configuration", "quantity", "decision", "requirement", "general", "unstructured_text"}
    if name == "fact_status": return value in {"unknown", "estimated", "proposed", "planned", "confirmed", "decided"}
    if name == "representation_type": return value in {"fact_record", "fact_section", "text_evidence", "paragraph_text", "table_row",
        "table", "document", "policy_clause", "policy_article", "policy_document", "template_section"}
    if name == "certainty_level": return value in {"not_applicable", "wrong_project", "unconfirmed", "estimate_only", "proposal_only",
        "plan_only", "confirmed_but_not_authoritative", "confirmed", "decided", "decision_authority_insufficient"}
    if name == "reason_codes":
        return value in TOKENS and (value.startswith("fact_status_") or value in {
            "current_project_claim_requires_current_project_relation", "confirmed_fact_requires_authoritative_document",
            "decided_fact_requires_formal_authoritative_source", "structured_fact_not_visible_in_prompt",
            "document_profile_has_no_eligible_claim_capability", "rejected_by_final_crag_irrelevant", "semantic_relevance_below_threshold",
            "eligible_by_profile_structure_status_and_relevance", "evidence_conflict_detected", "coverage_partial", "coverage_absent",
            "coverage_sufficient", "retrieval_evidence_sufficient", "retrieval_evidence_insufficient"}) or (
                value.startswith("structured_evidence_incompatible:") and value.split(":", 1)[1] in {item.value for item in ClaimType})
    return False


def resolve_pointer(value, pointer):
    for part in pointer.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def assert_final_string_firewall(result, frame):
    """Independent category/provenance oracle, including keys and string coercion.

    Hash-looking raw fields never become trusted merely by regex.  Canonical
    relative refs must resolve the exact complete object.  Caller-owned strings
    are accepted only from the exact materialized Requirement snapshot supplied.
    """
    raw = canonical(frame.bundle)
    owned = set(frame.requirement_ids)
    if frame.snapshot is not None:
        snap = canonical(frame.snapshot)
        payload = {key: value for key, value in snap.items() if key != "requirements_sha256"}
        assert canonical_sha256(payload) == snap["requirements_sha256"]
        selected = {item.requirement_id: item for item in frame.snapshot.requirements}
        assert set(frame.requirement_ids) <= selected.keys()
        owned.update("；".join(value.criterion.strip() for value in selected[rid].criteria if value.criterion.strip())
                     for rid in frame.requirement_ids)
    # Fixture actually invokes the canonical builder.  Alias values must match
    # its deterministic sequential allocation, not merely /^E\d+$/ or /^C\d+$/.
    ealiases = {f"E{index}" for index in range(1, len(frame.bundle.items) + 1)}
    caliases = {f"C{index}" for index in range(1, len(frame.bundle.citations) + 1)}
    associations = associate_selected_citations(package=frame.package, bundle=frame.bundle)
    valid_refs = {row.citation_ref for row in associations if row.source_identity_consistent}
    locator_refs = {row.source_locator_ref for row in associations if row.source_identity_consistent}
    binding_hash = canonical_sha256(canonical(frame.binding))
    bundle_binding_hash = canonical_sha256(canonical(frame.package.evidence_bundle_binding))
    bundle_hash = canonical_sha256(raw)
    traces, failures = [], []

    def visit(value, path, parent=None, name=None):
        if isinstance(value, dict):
            if "bundle_json_pointer" in value:
                pointer = value["bundle_json_pointer"]
                try:
                    exact = resolve_pointer(raw, pointer)
                except (KeyError, IndexError, TypeError, ValueError):
                    failures.append((path, "unresolvable canonical ref"))
                else:
                    if value.get("sha256") != canonical_sha256(exact):
                        failures.append((path, "nonexact canonical ref hash"))
                    if frame.marker in pointer:
                        failures.append((path, "source prose in pointer label"))
            for key, child in value.items():
                if not isinstance(key, str) or key not in KEYS and key not in {item.value for item in ClaimType}:
                    failures.append((path + "/<key>", repr(key)))
                else:
                    traces.append({"path": path + "/<key:" + key + ">", "category": "closed-key"})
                visit(child, path + "/" + str(key), value, key)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, path + "/" + str(index), parent, name)
        elif isinstance(value, str):
            category = None
            if path == "/context_text":
                assert value == frame.bundle.context.context_text, "Governed text was rewritten"
                category = "governed-context"
            elif name == "bundle_json_pointer" and isinstance(parent, dict) and "sha256" in parent:
                category = "exact-canonical-pointer"
            elif name == "sha256" and isinstance(parent, dict) and "bundle_json_pointer" in parent:
                category = "exact-canonical-value-hash"
            elif path == "/binding/sha256" and value == binding_hash:
                category = "computed-binding-hash"
            elif path == "/evidence_bundle_binding/sha256" and value == bundle_binding_hash:
                category = "computed-bundle-binding-hash"
            elif path == "/evidence_bundle_binding/evidence_bundle_sha256" and value.removeprefix("sha256:") == bundle_hash:
                category = "computed-actual-bundle-hash"
            elif name == "citation_ref" and value in valid_refs:
                category = "recomputed-citation-identity"
            elif name == "source_locator_ref" and value in locator_refs:
                category = "computed-exact-locator-ref"
            elif name == "requirements_sha256" and frame.snapshot is not None and value == frame.snapshot.requirements_sha256:
                category = "owner-snapshot-content-hash"
            elif name in {"evidence_id", "evidence_ids"} and value in ealiases:
                category = "actual-builder-E-alias"
            elif name in {"citation_id", "citation_ids", "allowed_aliases"} and value in caliases:
                category = "actual-builder-C-alias"
            elif name in {"required_topics", "covered_topics", "missing_topics", "supported_topics", "requirement_ids"} and value in owned:
                category = "exact-requirement-owner"
            elif closed_token_for_slot(name, value):
                category = "closed-producer-vocabulary"
            if category is None:
                failures.append((path, value))
            else:
                traces.append({"path": path, "category": category})
        elif value is not None and type(value) not in {bool, int, float}:
            failures.append((path, "unsupported object/string coercion: " + type(value).__name__))
    visit(result, "")
    # Unsupported objects must fail the category oracle before JSON's TypeError;
    # never permit string coercion as an implicit classification path.
    assert not failures, "Unclassified final strings/keys: " + repr(failures[:12])
    # JSON serialization is the actual string boundary, not an object-only walk.
    final_text = serialized(result)
    if frame.marker not in frame.bundle.context.context_text and frame.marker in final_text:
        failures.append(("/<serialized>", "hidden source marker"))
    assert not failures, "Unclassified final strings/keys: " + repr(failures[:12])
    return traces


def assert_absent_or_rejected(frame, label, *, forbidden=None):
    try:
        result = project(frame, label)
    except ValueError:
        return None  # Only explicit typed/integrity fail-closed, not arbitrary errors.
    assert (forbidden or frame.marker) not in serialized(result), "Source-controlled outer string reached Writer view"
    assert result["context_text"] == frame.bundle.context.context_text
    return result


@pytest.mark.parametrize("field", ["doc_id", "file_id", "source_document_id", "matched_chunk_id", "context_chunk_id",
    "child_chunk_id", "parent_chunk_id", "table_id", "row_ids", "title", "section"])
def test_actual_builder_free_locator_stays_raw_but_not_writer(field):
    marker = fresh(field)
    bundle = built_bundle(marker, field=field)
    assert marker in serialized(canonical(bundle)) and marker not in bundle.context.context_text
    result = assert_absent_or_rejected(frame_for(bundle, marker), "actual_builder_locator_" + field)
    if result is not None:
        assert_final_string_firewall(result, frame_for(bundle, marker))


@pytest.mark.parametrize("kind", ["policy", "template"])
def test_actual_chunker_governance_hidden_outer_body(kind):
    marker = fresh(kind)
    bundle = producer_bundle(kind, marker)
    frame = frame_for(bundle, marker)
    result = project(frame, "actual_" + kind + "_outer_body")
    assert marker not in serialized(result), "Producer BODY locator bypasses governed context"
    assert_final_string_firewall(result, frame)


@pytest.mark.parametrize("attack", ["evidence_free", "evidence_E999", "citation_free", "citation_C999",
    "selected_order", "item_order", "citation_source_duplicate", "item_citation_mismatch"])
def test_external_typed_bundle_nonfixed_alias_or_mapping_fail_closed(attack):
    marker = fresh(attack)
    bundle = built_bundle(marker, count=2)
    raw = canonical(bundle)
    forbidden = marker
    if attack.startswith("evidence_"):
        forbidden = marker if attack == "evidence_free" else "E999"
        raw["items"][0]["evidence_id"] = forbidden
        raw["selected_evidence_ids"][0] = forbidden
    elif attack.startswith("citation_") and attack != "citation_source_duplicate":
        forbidden = marker if attack == "citation_free" else "C999"
        raw["citations"][0]["citation_id"] = forbidden
        raw["items"][0]["citation_ids"] = [forbidden]
    elif attack == "selected_order":
        raw["selected_evidence_ids"].reverse()
    elif attack == "item_order":
        raw["items"].reverse()
    elif attack == "citation_source_duplicate":
        raw["citations"][1] = deepcopy(raw["citations"][0])
        raw["citations"][1]["citation_id"] = "C2"
        raw["items"][0]["citation_ids"] = ["C1", "C2"]
        raw["items"][1]["citation_ids"] = []
    else:
        raw["items"][0]["citation_ids"] = ["C2"]
    external = EvidenceBundleSchema.model_validate(raw)
    frame = frame_for(external, marker)
    try:
        result = project(frame, "external_nonfixed_" + attack)
    except ValueError:
        return
    assert not result.get("sources") and not result.get("citations") and not result.get("citation_usage", {}).get("allowed_aliases"), \
        "External nonfixed mapping was not denied before model-visible aliases"
    assert forbidden not in serialized(result)


@pytest.mark.parametrize("external_clone", [False, True])
def test_aliases_are_regenerated_by_actual_builder_readonly_not_regex(external_clone):
    marker = fresh("alias_proof")
    bundle = built_bundle(marker)
    if external_clone:
        # Byte-identical persisted/typed input cannot attest historic producer.
        # Its view must still emit actually recomputed canonical aliases.
        bundle = EvidenceBundleSchema.model_validate(canonical(bundle))
    frame = frame_for(bundle, marker)
    generated = []
    original = _EvidenceBundleBuilder.build
    def observe(**kwargs):
        answer = original(**kwargs)
        generated.append(canonical(answer))
        return answer
    with patch.object(_EvidenceBundleBuilder, "build", side_effect=observe):
        result = project(frame, "regenerated_alias_proof_" + str(external_clone))
    assert generated, "No actual builder generation proof; regex/current raw IDs alone are insufficient"
    assert generated[-1]["selected_evidence_ids"] == bundle.selected_evidence_ids
    assert [value["citation_id"] for value in generated[-1]["citations"]] == ["C1"]
    assert result["citation_usage"]["allowed_aliases"] == ["C1"]
    assert [value["citation_id"] for value in result["citations"]] == ["C1"]
    assert [value["evidence_id"] for value in result["sources"]] == ["E1"]
    assert_final_string_firewall(result, frame)


@pytest.mark.parametrize("attack", ["ambiguous", "identity_inconsistent", "bad_ref"])
def test_ambiguous_or_inconsistent_citation_has_no_raw_row_side_channel(attack):
    marker = fresh(attack)
    bundle = built_bundle(marker, count=2 if attack == "ambiguous" else 1, same_parent=attack == "ambiguous")
    raw = canonical(bundle)
    if attack == "ambiguous":
        raw["citations"] = raw["citations"][:1]
        for item in raw["items"]:
            item["citation_ids"] = ["C1"]
    elif attack == "identity_inconsistent":
        raw["citations"][0]["title"] = marker
    else:
        raw["citations"][0]["citation_ref"] = marker
    external = EvidenceBundleSchema.model_validate(raw)
    frame = frame_for(external, marker)
    try:
        result = project(frame, "citation_integrity_" + attack)
    except ValueError:
        return
    assert not result["citation_usage"]["allowed_aliases"]
    assert not result["citations"], "Disallowed citation survived in a raw row"
    assert all(not item.get("citation_ids") for item in result.get("sources", []))
    assert marker not in serialized(result)


@pytest.mark.parametrize("field", ["candidate_id", "child_chunk_id", "parent_chunk_id", "document_id", "project_id",
    "truncation_source", "truncation_reason", "profile_sha256", "source_sha256", "item_evidence_id", "item_document_id", "conflict_evidence_ids"])
def test_all_nested_identity_and_digest_claim_paths_default_deny(field):
    marker = fresh(field)
    bundle = built_bundle(marker)
    candidate = {"candidate_id": "source-candidate", "child_chunk_id": "source-child", "parent_chunk_id": "source-parent",
        "document_id": "source-document", "profile_sha256": "a" * 64, "source_sha256": "b" * 64,
        "citation_ids": ["C1"], "retrieval_candidate": {"document_type": "policy_standard", "evidence_role": "policy_standard",
            "project_relation": "general", "authority_level": "reference", "allowed_claim_types": ["policy_requirement"]}}
    assessment = {"evidence_id": "source-candidate", "document_id": "source-document", "schema_version": "evidence_assessment_v2",
                  "metadata": {}}
    if field == "project_id":
        candidate["retrieval_candidate"][field] = marker
    elif field == "item_evidence_id":
        assessment["evidence_id"] = marker
    elif field == "item_document_id":
        assessment["document_id"] = marker
    elif field == "conflict_evidence_ids":
        bundle.assessment.conflicts = [{"claim_type": "policy_requirement", "evidence_ids": [marker]}]
    else:
        candidate[field] = marker
    bundle.assessment.details["evidence_governance_v2"] = {"prompt_evidence_candidates": [candidate], "item_assessments": [assessment]}
    frame = frame_for(bundle, marker)
    result = assert_absent_or_rejected(frame, "nested_default_deny_" + field)
    if result is not None:
        assert_final_string_firewall(result, frame)


def test_external_64hex_metadata_is_not_actual_digest_proof():
    marker = uuid4().hex + uuid4().hex  # A valid 64hex source literal is still self-report.
    bundle = built_bundle(marker)
    bundle.assessment.details["evidence_governance_v2"] = {"prompt_evidence_candidates": [
        {"candidate_id": "claim-only", "profile_sha256": marker, "source_sha256": marker, "retrieval_candidate": {}}]}
    frame = frame_for(bundle, marker)
    result = assert_absent_or_rejected(frame, "claimed_64hex_not_provenance")
    if result is not None:
        assert_final_string_firewall(result, frame)


@pytest.mark.parametrize("field", ["run_id", "logical_artifact_id", "domain_scope_ref", "artifact_version_id", "content_ref",
    "committed_by_operation_id", "producer_attempt_scope_ref", "producer_attempt_id", "bundle_handle"])
def test_binding_identity_names_do_not_authorize_free_source_strings(field):
    marker = fresh(field)
    frame = frame_for(built_bundle(marker), marker)
    if field == "bundle_handle":
        frame.package.evidence_bundle_binding = frame.package.evidence_bundle_binding.model_copy(
            update={"evidence_bundle_handle": marker})
    else:
        data = canonical(frame.binding)
        target = data["version_ref"]["logical_ref"] if field in {"run_id", "logical_artifact_id", "domain_scope_ref"} else data["version_ref"]
        target[field] = marker
        if field.startswith("producer_attempt"):
            target["producer_attempt_scope_ref"] = marker
            target["producer_attempt_id"] = marker
        frame.binding = EvidencePackageBindingSchema.model_validate(data)
    result = assert_absent_or_rejected(frame, "binding_default_deny_" + field)
    if result is not None:
        assert_final_string_firewall(result, frame)


def owner_snapshot(topic, rid="REQ_V22_OWNER"):
    return EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=BusinessProfileTemplate(profile_ref="profile-v22-frozen", requirements=(EvidenceRequirementSchema(
            requirement_id=rid, criteria=(EvidenceCriterionSchema(criterion_id="CRIT_V22_OWNER", criterion=topic),)),)),
        task_contract=TaskContractTemplate(task_ref="task-v22-frozen", requirements=()))


def test_requirement_semantics_survive_only_exact_independent_owner():
    marker = fresh("evidence_source")
    topic = "Authored Task requirement " + uuid4().hex[:10]
    bundle = built_bundle(marker)
    bundle.assessment.required_topics = [topic, marker]
    bundle.assessment.covered_topics = [topic]
    bundle.assessment.missing_topics = [marker]
    snap = owner_snapshot(topic)
    frame = frame_for(bundle, marker, requirement_ids=("REQ_V22_OWNER",), snapshot=snap)
    result = project(frame, "exact_requirement_owner")
    assert topic in result["assessment"]["required_topics"]
    assert marker not in serialized(result)
    assert result["assessment"]["requirement_owner"]["requirements_sha256"] == snap.requirements_sha256
    assert_final_string_firewall(result, frame)


@pytest.mark.parametrize("attack", ["no_owner", "wrong_hash", "unknown_id", "duplicate_id"])
def test_requirement_owner_selection_is_not_bundle_self_report(attack):
    marker = fresh("unowned_requirement")
    bundle = built_bundle(marker)
    bundle.assessment.required_topics = [marker]
    snap = owner_snapshot("Authored requirement")
    ids = ("REQ_V22_OWNER",)
    if attack == "no_owner": snap = None
    elif attack == "wrong_hash": snap = snap.model_copy(update={"requirements_sha256": "e" * 64})
    elif attack == "unknown_id": ids = (marker,)
    else: ids = ("REQ_V22_OWNER", "REQ_V22_OWNER")
    # A duplicate owner selection is attacked at the projection boundary; the
    # canonical package selection remains typed and valid independently.
    frame = frame_for(bundle, marker, snapshot=snap,
                      requirement_ids=() if attack == "duplicate_id" else ids)
    frame.requirement_ids = tuple(ids)
    with pytest.raises(ValueError):
        project(frame, "invalid_owner_" + attack)


def test_complete_final_values_keys_and_refs_have_independent_category_proof():
    marker = fresh("inventory")
    frame = frame_for(built_bundle(marker), marker)
    result = project(frame, "complete_final_inventory")
    trace = assert_final_string_firewall(result, frame)
    assert any(item["category"] == "closed-key" for item in trace)
    assert any(item["category"] == "exact-canonical-value-hash" for item in trace)
    path = artifact_dir("classification_trace")
    write_case(path, "inventory.json", trace)


@pytest.mark.parametrize("attack", ["dict_key", "arbitrary_string", "fake_hash", "wrong_pointer", "raw_label_pointer", "source_uri", "coercion"])
def test_independent_oracle_rejects_its_own_unclassified_mutations(attack):
    marker = fresh("oracle_self_attack")
    frame = frame_for(built_bundle(marker), marker)
    # A minimal independent approved view is not copied from production output.
    view = {"context_text": frame.bundle.context.context_text, "binding": {"sha256": canonical_sha256(canonical(frame.binding))}}
    assert_final_string_firewall(view, frame)
    if attack == "dict_key": view[marker] = True
    elif attack == "arbitrary_string": view["scope"] = marker
    elif attack == "fake_hash": view["source_sha256"] = "a" * 64
    elif attack == "wrong_pointer": view["source_ref"] = {"bundle_json_pointer": "/items/0", "sha256": "a" * 64}
    elif attack == "raw_label_pointer": view["source_ref"] = {"bundle_json_pointer": "/items/" + marker, "sha256": "a" * 64}
    elif attack == "source_uri": view["source_locator_ref"] = marker + "://sha256/" + canonical_sha256(canonical(frame.bundle))
    else: view["scope"] = SimpleNamespace(__str__=lambda: marker)
    with pytest.raises(AssertionError):
        assert_final_string_firewall(view, frame)


def test_current_representation_names_final_v5_contract():
    assert (production.PROJECTION_SCHEMA_ID, production.PROJECTION_SCHEMA_VERSION) == ("writer_evidence_package_compact_v5", "v5")


STRUCTURED_FREE_FIELDS = (
    ("fact", "record_id"), ("fact", "subject"), ("fact", "predicate"), ("fact", "value"),
    ("fact", "unit"), ("fact", "source_span"),
    ("table", "table_id"), ("table", "table_title"), ("table", "header"), ("table", "cells"),
    ("table", "normalized_fields"), ("table", "inherited_cells"), ("table", "merged_cell_relations"),
    ("table", "calculation_record"),
    ("policy", "policy_name"), ("policy", "chapter_id"), ("policy", "chapter_title"),
    ("policy", "article_id"), ("policy", "article_title"), ("policy", "clause_id"), ("policy", "clause_path"),
    ("template", "section_path"), ("template", "section_title"), ("template", "required_fields"),
    ("template", "placeholder_fields"), ("template", "style_hints"),
)


@pytest.mark.parametrize("kind,field", STRUCTURED_FREE_FIELDS)
def test_every_structured_free_string_and_nested_key_remains_exact_ref(kind, field):
    from rag.schema.structured_evidence import StructuredEvidenceSchema
    marker = fresh(kind + "_" + field)
    if field in {"header", "cells", "merged_cell_relations", "clause_path", "section_path", "required_fields", "placeholder_fields", "style_hints"}:
        value = [marker]
    elif field in {"source_span", "normalized_fields", "inherited_cells", "calculation_record"}:
        value = {marker: marker, "text": marker}
    elif field == "value":
        value = {marker: [marker]}
    else:
        value = marker
    structured = StructuredEvidenceSchema.model_validate({"representation_type": {
        "fact": "fact_record", "table": "table_row", "policy": "policy_clause", "template": "template_section"}[kind],
        kind: {field: value}}).model_dump(mode="json")
    bundle = built_bundle(marker)
    candidate = {"candidate_id": "source-candidate", "citation_ids": ["C1"], "retrieval_candidate": {},
                 "structured_evidence": structured}
    bundle.assessment.details["evidence_governance_v2"] = {"prompt_evidence_candidates": [candidate]}
    frame = frame_for(bundle, marker)
    view = project(frame, "structured_" + kind + "_" + field)
    assert marker not in serialized(view)
    assert view["source_constraints"]["candidates"][0]["structured_evidence"][kind]["support_ref"] == {
        "bundle_json_pointer": "/assessment/details/evidence_governance_v2/prompt_evidence_candidates/0/structured_evidence/" + kind,
        "sha256": canonical_sha256(structured[kind])}
    assert_final_string_firewall(view, frame)


@pytest.mark.parametrize("field", ("judge_name", "judge_version", "prompt_evidence_source", "reason_codes",
    "required_qualifiers", "required_topics", "covered_topics", "missing_topics", "details"))
def test_assessment_free_diagnostics_never_become_second_body(field):
    marker = fresh("assessment_" + field)
    bundle = built_bundle(marker)
    raw = canonical(bundle)
    value = [marker] if field in {"reason_codes", "required_qualifiers", "required_topics", "covered_topics", "missing_topics"} else marker
    if field == "details": value = {marker: {"nested": marker}}
    raw["assessment"][field] = value
    frame = frame_for(EvidenceBundleSchema.model_validate(raw), marker)
    view = project(frame, "assessment_" + field)
    assert marker not in serialized(view)
    assert_final_string_firewall(view, frame)


@pytest.mark.parametrize("attack", ("claim_key", "certainty", "qualifier", "reason", "topic", "unknown_nested_key"))
def test_item_assessment_usage_policy_all_keys_and_values_default_deny(attack):
    marker = fresh("item_usage_" + attack)
    assessment = {"evidence_id": "source-assessment-id", "document_id": "source-document-id",
        "schema_version": "evidence_assessment_v2", "supported_topics": [], "metadata": {
            "fact_claim_policies": {"policy_requirement": {"eligible": True, "certainty_level": "confirmed",
                "required_qualifiers": [], "reason_codes": []}}}}
    policy = assessment["metadata"]["fact_claim_policies"]["policy_requirement"]
    if attack == "claim_key": assessment["metadata"]["fact_claim_policies"][marker] = policy
    elif attack == "certainty": policy["certainty_level"] = marker
    elif attack == "qualifier": policy["required_qualifiers"] = [marker]
    elif attack == "reason": policy["reason_codes"] = [marker]
    elif attack == "topic": assessment["supported_topics"] = [marker]
    else: policy[marker] = {marker: marker}
    bundle = built_bundle(marker)
    bundle.assessment.details["evidence_governance_v2"] = {"item_assessments": [assessment]}
    frame = frame_for(bundle, marker)
    view = project(frame, "item_usage_" + attack)
    assert marker not in serialized(view)
    assert_final_string_firewall(view, frame)


def test_oracle_closed_tokens_cannot_authorize_source_identity_slot():
    frame = frame_for(built_bundle(fresh("enum_slot_collision")), fresh("oracle_marker"))
    with pytest.raises(AssertionError):
        assert_final_string_firewall({"context_text": frame.bundle.context.context_text,
                                      "sources": [{"doc_id": "general"}]}, frame)


def test_legitimate_governed_body_and_title_are_preserved_once():
    marker = fresh("visible_title_body")
    bundle = built_bundle(marker)
    raw = canonical(bundle)
    text = "[C1]\n合法政策章节标题 " + marker + "\n应核验材料。"
    raw["context"]["context_text"] = text
    raw["context"]["used_context_chars"] = len(text)
    bundle = EvidenceBundleSchema.model_validate(raw)
    frame = frame_for(bundle, marker)
    view = project(frame, "visible_title_body")
    assert view["context_text"] == text
    assert serialized(view).count(marker) == 1
    assert_final_string_firewall(view, frame)


@pytest.mark.parametrize("alias_mode", ("fixed", "free_E", "free_C"))
def test_actual_legacy_canonical_ingress_uses_same_alias_proof_and_string_boundary(alias_mode):
    from agent.runtime.shared_state_schema import SharedStateSchema
    from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import LegacyEvidenceAdapter
    from schemas.tool import ToolResultSchema
    marker = fresh("legacy_" + alias_mode)
    raw = canonical(built_bundle(marker, field="section"))
    if alias_mode == "free_E":
        raw["items"][0]["evidence_id"] = raw["selected_evidence_ids"][0] = marker
    elif alias_mode == "free_C":
        raw["citations"][0]["citation_id"] = marker
        raw["items"][0]["citation_ids"] = [marker]
    shared = SharedStateSchema(task_id="task-legacy-v22", run_id="run-legacy-v22", task_type="scheme_generation",
        user_input="核验材料", created_at="2026-10-07T00:00:00Z", context_bundle={
            "user": {"user_query": "核验材料"}, "task": {"task_id": "task-legacy-v22", "run_id": "run-legacy-v22",
                "task_type": "scheme_generation"}})
    tool = ToolResultSchema(tool_call_id="call-legacy-v22", task_id=shared.task_id, run_id=shared.run_id,
        tool_name="rag", success=True, created_at="2026-10-07T00:00:00Z",
        result={"schema_version": "rag_tool_output_v1", "evidence": raw})
    original = canonical(tool)
    bundle = LegacyEvidenceAdapter.upgrade(shared, tool)
    frame = frame_for(bundle, marker)
    if alias_mode == "fixed":
        view = project(frame, "legacy_fixed_ingress")
        assert view["citation_usage"]["allowed_aliases"] == ["C1"]
        assert marker not in serialized(view)
        assert_final_string_firewall(view, frame)
    else:
        view = assert_absent_or_rejected(frame, "legacy_nonfixed_" + alias_mode)
        if view is not None:
            assert not view.get("sources") and not view.get("citations")
    assert canonical(tool) == original, "Legacy upgrade/projection rewrote original raw tool payload"


# The old live fixture supplies runtime assembly only.  These oracles and the
# complete 3x3 version matrix are new; no old expected PASS fixture is reused.
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer, TargetWriterContextIntegrityError,
)
from model_gateway.fake_llm_client import FakeLLMClient
from test_multi_agent_v3_writer_input_boundary import r1_mainline
from test_fpbca_a1_v2_1_writer_representation import _stale_projection_identity


V22_IDENTITY_PAIRS = tuple((f"writer_evidence_package_compact_v{schema}", f"v{version}")
                          for schema in (3, 4, 5) for version in (3, 4, 5))


@pytest.fixture(scope="module", params=("normal", "fallback"))
def actual_v22_predispatch_matrix(request, tmp_path_factory):
    """Run original guards on current runtime owners before actual dispatch.

    Mutations preserve the locked payload and recompute the public package
    semantic binding.  Neither stale package is dispatched nor any currentness
    guard suppressed.  The normal/fallback decisions come from the live owners.
    """
    original_validate = TargetWriterContextComposer.validate_pre_model_call
    original_final = TargetWriterContextComposer.final_messages
    original_generate = FakeLLMClient.generate
    observations, provider_calls = [], []
    active_probe = [False]

    def spy_generate(client, model_request):
        provider_calls.append({"during_identity_probe": active_probe[0],
                               "purpose": model_request.extra.get("call_purpose")})
        return original_generate(client, model_request)

    def observe(composer, package, *, project_input):
        current = original_validate(composer, package, project_input=project_input)
        records = []
        # Nested final_messages re-enters the same original guard.  Restoring
        # its actual method avoids recursion while retaining every check.
        with patch.object(TargetWriterContextComposer, "validate_pre_model_call", original_validate):
            for schema_id, schema_version in V22_IDENTITY_PAIRS:
                rebound = _stale_projection_identity(current, schema_id, schema_version)
                assert rebound.model_input.final_input_material == current.model_input.final_input_material
                for reader, method in (("validate_pre_model_call", original_validate), ("final_messages", original_final)):
                    active_probe[0] = True
                    try:
                        method(composer, rebound, project_input=project_input)
                    except TargetWriterContextIntegrityError as exc:
                        record = {"schema_id": schema_id, "schema_version": schema_version,
                                  "reader": reader, "accepted": False,
                                  "error_type": type(exc).__name__, "error": str(exc)}
                    else:
                        record = {"schema_id": schema_id, "schema_version": schema_version,
                                  "reader": reader, "accepted": True, "error_type": None, "error": None}
                    finally:
                        active_probe[0] = False
                    records.append(record)
            fresh = original_validate(composer, current, project_input=project_input)
            messages = original_final(composer, fresh, project_input=project_input)
        observations.append({"records": records, "fresh": fresh, "messages": messages})
        return current

    with patch.object(FakeLLMClient, "generate", spy_generate), patch.object(
            TargetWriterContextComposer, "validate_pre_model_call", observe):
        result = r1_mainline.__wrapped__(request, tmp_path_factory)
    assert observations, "No actual current predispatch witness"
    path = artifact_dir("actual_predispatch_identity_" + request.param)
    write_case(path, "matrix.json", {"mode": request.param, "provider_calls": provider_calls,
        "identity_probe_dispatch_count": sum(row["during_identity_probe"] for row in provider_calls),
        "observations": [{"records": row["records"], "fresh_package": canonical(row["fresh"]),
                          "fresh_messages": row["messages"]} for row in observations],
        "actual_request": canonical(result[1]), "summary": result[6]})
    return observations, provider_calls, result


@pytest.mark.parametrize("schema_id,schema_version", V22_IDENTITY_PAIRS)
@pytest.mark.parametrize("reader", ("validate_pre_model_call", "final_messages"))
def test_actual_predispatch_accepts_only_complete_v5_pair(actual_v22_predispatch_matrix, schema_id, schema_version, reader):
    observations, provider_calls, _ = actual_v22_predispatch_matrix
    assert not any(row["during_identity_probe"] for row in provider_calls), "Stale/mixed identity probe dispatched a model"
    expected_acceptance = (schema_id, schema_version) == ("writer_evidence_package_compact_v5", "v5")
    for observation in observations:
        record = next(row for row in observation["records"] if (row["schema_id"], row["schema_version"], row["reader"])
                      == (schema_id, schema_version, reader))
        assert record["accepted"] is expected_acceptance, record
        if not expected_acceptance:
            assert record["error_type"] == "TargetWriterContextIntegrityError"


def test_actual_fresh_v5_keeps_locked_model_request_and_original_route(actual_v22_predispatch_matrix):
    observations, provider_calls, result = actual_v22_predispatch_matrix
    actual_request = result[1]
    assert len([row for row in provider_calls if row["purpose"] == "scheme_section_generation"]) == 1
    assert not any(row["during_identity_probe"] for row in provider_calls)
    for observation in observations:
        current = observation["fresh"]
        evidence = next(item for item in current.selected_materializations if item.candidate_id == "writer-current-evidence-package")
        assert (evidence.projection_schema_id, evidence.projection_schema_version) == ("writer_evidence_package_compact_v5", "v5")
        assert observation["messages"] == actual_request.messages
        assert current.model_input.final_input_material.inline_final_input_payload == {
            "input_kind": "MESSAGES", "messages": actual_request.messages}
    invocation = result[5]
    route = result[3].route_decision_store.resolve_exact(invocation.route_decision_ref)
    expected_route = "fallback" if invocation.writer_subject_ref.run_id.endswith("fallback") else "normal"
    assert route.selected_route.value == expected_route
