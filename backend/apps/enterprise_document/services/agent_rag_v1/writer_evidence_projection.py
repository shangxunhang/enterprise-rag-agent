"""Writer presentation of canonical Evidence, never a replacement truth store.

The canonical governed context is retained once, without rewriting or trimming.
Source bodies and diagnostic trees stay in the hash-bound immutable bundle.
"""
from __future__ import annotations

from copy import deepcopy
import math

from contracts.evidence import build_evidence_bundle
from apps.enterprise_document.schemas.pre_write_admission import EvidenceRequirementsSnapshotSchema
from schemas.citation import citation_source_identity
from schemas.rag import RetrievedChunkSchema
from schemas.claim import ClaimType
from schemas.evidence_source import AuthorityLevel, DocumentType, EvidenceRole, ProjectRelation
from schemas.evidence_assessment import EvidenceRelevance, EvidenceStatus
from rag.catalog.document_profile import ChunkStrategy
from rag.evidence.eligibility_policy import EVIDENCE_USE_ELIGIBILITY_POLICY_ID, EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION

from core.hashing import canonical_sha256
from rag.schema.structured_evidence import StructuredEvidenceSchema

from .citation_association import associate_selected_citations, writer_allowed_aliases


PROJECTION_SCHEMA_ID = "writer_evidence_package_compact_v5"
PROJECTION_SCHEMA_VERSION = "v5"
PROTECTED_FIELDS = (
    "binding", "selections", "evidence_bundle_binding", "assessment",
    "context_text", "citations", "sources", "source_constraints", "citation_usage",
)


def _ref(path: str, value) -> dict:
    # Path is relative to the exact evidence_bundle_binding, not a mutable URI.
    return {"bundle_json_pointer": path, "sha256": canonical_sha256(value)}


def _builder_alias_witness(bundle):
    """Generate aliases with the canonical owner; do not trust persisted labels.

    This proves a current deterministic fixed point, not historical attestation.
    The witness is transient: the canonical Bundle and governed text stay intact.
    Legacy inputs with a different alias/source/order mapping fail closed.
    """
    chunks = {}
    for item in bundle.items:
        chunks[item.evidence_id] = RetrievedChunkSchema(
            rank=item.rank, score=item.score, score_type=item.score_type,
            rerank_score=item.rerank_score, rerank_score_type=item.rerank_score_type,
            matched_chunk_id=item.matched_chunk_id, context_chunk_id=item.context_chunk_id,
            child_chunk_id=item.child_chunk_id, parent_chunk_id=item.parent_chunk_id,
            doc_id=item.doc_id, match_text=item.match_text, context_text=item.context_text,
            title=item.title, section=item.section, page_start=item.page_start, page_end=item.page_end,
            matched_granularity=str(item.extra.get("matched_granularity") or "child"),
            context_granularity=str(item.extra.get("context_granularity") or "parent"),
            retrieval_sources=list(item.retrieval_sources),
            metadata=deepcopy(item.metadata), extra=deepcopy(item.extra),
        )
    witness = build_evidence_bundle(
        query=bundle.query, rewritten_queries=bundle.rewritten_queries,
        selected_chunks=[chunks[eid] for eid in bundle.selected_evidence_ids],
        dropped_chunks=[chunks[eid] for eid in bundle.dropped_evidence_ids],
        citations=bundle.citations, trace=None,
        max_context_chars=bundle.context.max_context_chars,
        context_pack={"rendered_text": bundle.context.context_text,
                      "max_context_chars": bundle.context.max_context_chars},
    )
    fields = ("evidence_id", "disposition", "matched_chunk_id", "context_chunk_id",
              "child_chunk_id", "parent_chunk_id", "doc_id", "citation_ids")
    def mapping(items):
        return [tuple(getattr(item, field) for field in fields) for item in items]
    def citation_mapping(citations):
        return [(citation.citation_id, citation.citation_ref, citation_source_identity(citation))
                for citation in citations]
    if (mapping(witness.items) != mapping(bundle.items)
            or witness.selected_evidence_ids != bundle.selected_evidence_ids
            or witness.dropped_evidence_ids != bundle.dropped_evidence_ids
            or citation_mapping(witness.citations) != citation_mapping(bundle.citations)):
        raise ValueError("Writer aliases are not the canonical builder source/order mapping")
    return witness


_SOURCE_SPAN_LOCATION_FIELDS = (
    "unit_order_start",
    "unit_order_end",
    "page",
    "page_start",
    "page_end",
    "start",
    "end",
    "ordinal",
)

# These are producer-owned tokens, not all strings accepted by the wire schema.
_REPRESENTATIONS = frozenset({"fact_record", "fact_section", "text_evidence", "paragraph_text",
    "table_row", "table", "document", "policy_clause", "policy_article", "policy_document", "template_section"})
_STRATEGIES = frozenset(item.value for item in ChunkStrategy) | frozenset({
    "text_evidence", "paragraph_text_evidence", "document_template_section"})
_FACT_CATEGORIES = frozenset({"cost", "schedule", "configuration", "quantity", "decision", "requirement", "general", "unstructured_text"})
_FACT_STATUSES = frozenset({"unknown", "estimated", "proposed", "planned", "confirmed", "decided"})
_CLAIM_TYPES = frozenset(item.value for item in ClaimType)
_REASONS = frozenset({
    "current_project_claim_requires_current_project_relation", "fact_status_unknown", "fact_status_estimated",
    "fact_status_proposed", "fact_status_planned", "fact_status_confirmed", "fact_status_decided",
    "confirmed_fact_requires_authoritative_document", "decided_fact_requires_formal_authoritative_source",
    "structured_fact_not_visible_in_prompt", "document_profile_has_no_eligible_claim_capability",
    "rejected_by_final_crag_irrelevant", "semantic_relevance_below_threshold",
    "eligible_by_profile_structure_status_and_relevance", "evidence_conflict_detected",
    "coverage_partial", "coverage_absent", "coverage_sufficient",
    "retrieval_evidence_sufficient", "retrieval_evidence_insufficient",
}) | frozenset("structured_evidence_incompatible:" + item for item in _CLAIM_TYPES)
_QUALIFIERS = frozenset({
    "该信息尚未确认", "根据现有估算材料", "最终金额以审批或采购结果为准", "拟", "尚待确认", "计划",
    "依据当前可用材料", "相关事实仍需业务确认", "建议", "参考同类项目", "需结合本项目确认",
    "仅覆盖已具备证据的部分", "缺失内容待补充确认", "基于旧版 Prompt 证据兼容投影，需人工复核",
})
_CERTAINTY_LEVELS = frozenset({"not_applicable", "wrong_project", "unconfirmed", "estimate_only", "proposal_only",
    "plan_only", "confirmed_but_not_authoritative", "confirmed", "decided", "decision_authority_insufficient"})
_PROMPT_SOURCES = frozenset({"context_pack_selected_results", "legacy_fallback", "missing_blocked"})


def _closed_list(value, allowed) -> list:
    return [deepcopy(item) for item in value if isinstance(item, str) and item in allowed] if isinstance(value, list) else []


def _closed(value: dict, key: str, allowed, result: dict) -> None:
    item = value.get(key)
    if isinstance(item, str) and item in allowed:
        result[key] = item


def _bool(value: dict, key: str, result: dict) -> None:
    if type(value.get(key)) is bool:
        result[key] = value[key]


def _number(value: dict, key: str, result: dict, *, integer=False) -> None:
    item = value.get(key)
    if type(item) is int or (not integer and type(item) is float and math.isfinite(item)):
        result[key] = item


def _structured(value: dict, path: str) -> dict:
    # Fail on a new unknown typed field rather than silently losing its meaning.
    StructuredEvidenceSchema.model_validate(value)
    result = {"source_ref": _ref(path, value)}
    _closed(value, "schema_version", {"structured_evidence_v1"}, result)
    _closed(value, "representation_type", _REPRESENTATIONS, result)
    for key in ("original_strategy", "actual_strategy"):
        _closed(value, key, _STRATEGIES, result)
    fallback = value.get("fallback")
    if isinstance(fallback, dict):
        result["fallback"] = {}
        _bool(fallback, "used", result["fallback"])
    for kind in ("fact", "table", "policy", "template"):
        content = value.get(kind)
        if content is not None:
            projected = {"support_ref": _ref(f"{path}/{kind}", content)}
            if kind == "fact":
                _closed(content, "fact_category", _FACT_CATEGORIES, projected)
                _closed(content, "fact_status", _FACT_STATUSES, projected)
                # source_span is a canonical provenance container, not Writer prose.
                # Keep only location coordinates in the compact Writer projection;
                # bind the complete immutable span (including any raw text) by hash.
                raw_span = content.get("source_span")
                if isinstance(raw_span, dict):
                    location = {}
                    for key in _SOURCE_SPAN_LOCATION_FIELDS:
                        _number(raw_span, key, location, integer=True)
                    if location:
                        projected["source_span"] = location
                    projected["source_span_ref"] = _ref(
                        f"{path}/{kind}/source_span",
                        raw_span,
                    )
            if kind == "table":
                _number(content, "row_index", projected, integer=True)
                if content.get("calculation_record") is not None:
                    projected["calculation_record_present"] = bool(content["calculation_record"])
            if kind == "policy":
                _bool(content, "is_table_of_contents", projected)
            if kind == "template":
                _bool(content, "is_example_content", projected)
            result[kind] = projected
    return result


def _requirement_owner(snapshot, requirement_ids) -> tuple[frozenset[str], dict | None]:
    """Accept only the exact requirement snapshot resolved by the caller's owner.

    The production composer resolves current Plan/Task/Profile authorization.
    An arbitrary Bundle requirement envelope is never an ownership proof here.
    """
    if snapshot is None:
        if requirement_ids:
            raise ValueError("Writer requirement ids have no owner-resolved snapshot")
        return frozenset(), None
    snapshot = EvidenceRequirementsSnapshotSchema.model_validate(snapshot)
    payload = snapshot.model_dump(mode="json", exclude={"requirements_sha256"}, round_trip=True)
    if canonical_sha256(payload) != snapshot.requirements_sha256:
        raise ValueError("Writer requirement owner snapshot hash mismatch")
    ids = tuple(requirement_ids)
    by_id = {item.requirement_id: item for item in snapshot.requirements}
    if not ids or len(ids) != len(set(ids)) or any(item not in by_id for item in ids):
        raise ValueError("Writer requirement owner selection is not exact")
    topics = frozenset("；".join(item.criterion.strip() for item in by_id[rid].criteria
                              if item.criterion.strip()) for rid in ids)
    return topics, {"requirements_sha256": snapshot.requirements_sha256,
                    "requirement_ids": list(ids)}


def project_writer_assessment(assessment, *, owned_topics=frozenset(), requirement_owner=None) -> dict:
    """Whitelist Writer-safe state; full canonical assessment stays hash-bound."""
    raw = assessment.model_dump(mode="json")
    result = {"assessment_ref": _ref("/assessment", raw)}
    _closed(raw, "schema_version", {"rag_evidence_assessment_v1"}, result)
    _closed(raw, "assessment_state", {"pending", "completed"}, result)
    _closed(raw, "status", {item.value for item in EvidenceStatus}, result)
    if raw.get("status") is None:
        result["status"] = None
    for key in ("evidence_available", "authoritative_evidence_present", "historical_or_reference_evidence_present", "prompt_evidence_contract_missing"):
        _bool(raw, key, result)
    for key in ("selected_evidence_count", "dropped_evidence_count", "citation_count"):
        _number(raw, key, result, integer=True)
    for key in ("score", "coverage_ratio"):
        _number(raw, key, result)
    _closed(raw, "judge_name", {"document_profile_gate_v2"}, result)
    _closed(raw, "judge_version", {"stage_d_v1"}, result)
    _closed(raw, "prompt_evidence_source", _PROMPT_SOURCES, result)
    for key in ("supported_claim_types", "forbidden_claim_types"):
        result[key] = _closed_list(raw.get(key), _CLAIM_TYPES)
    result["reason_codes"] = _closed_list(raw.get("reason_codes"), _REASONS)
    result["required_qualifiers"] = _closed_list(raw.get("required_qualifiers"), _QUALIFIERS)
    for key in ("required_topics", "covered_topics", "missing_topics"):
        result[key] = _closed_list(raw.get(key), owned_topics)
    if requirement_owner is not None:
        result["requirement_owner"] = deepcopy(requirement_owner)
    result["conflicts"] = []
    for index, conflict in enumerate(raw.get("conflicts") or []):
        safe = {"conflict_ref": _ref(f"/assessment/conflicts/{index}", conflict)}
        _closed(conflict, "claim_type", _CLAIM_TYPES, safe)
        # Public participants can be source-derived record/document IDs. The
        # exact whole-conflict ref preserves them without a second text channel.
        result["conflicts"].append(safe)
    return result


def _item_assessment(raw: dict, path: str, *, owned_topics) -> dict:
    result = {"assessment_ref": _ref(path, raw)}
    # Source assessment identities stay behind the exact whole-assessment ref.
    _closed(raw, "schema_version", {"evidence_assessment_v2"}, result)
    _closed(raw, "eligibility_semantic_policy_id", {EVIDENCE_USE_ELIGIBILITY_POLICY_ID}, result)
    _closed(raw, "eligibility_semantic_policy_version", {EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION}, result)
    for key, enum in (("document_type", DocumentType), ("evidence_role", EvidenceRole),
                      ("project_relation", ProjectRelation), ("authority_level", AuthorityLevel), ("relevance", EvidenceRelevance)):
        _closed(raw, key, {item.value for item in enum}, result)
    _number(raw, "semantic_relevance", result)
    _bool(raw, "eligibility", result)
    for key in ("supported_claim_types", "forbidden_claim_types"):
        result[key] = _closed_list(raw.get(key), _CLAIM_TYPES)
    result["supported_topics"] = _closed_list(raw.get("supported_topics"), owned_topics)
    result["reason_codes"] = _closed_list(raw.get("reason_codes"), _REASONS)
    metadata = raw.get("metadata") or {}
    usage = {"required_qualifiers": _closed_list(metadata.get("required_qualifiers"), _QUALIFIERS)}
    _bool(metadata, "fact_visible_in_prompt", usage)
    policies = metadata.get("fact_claim_policies")
    if isinstance(policies, dict):
        safe_policies = {}
        for claim, policy in policies.items():
            if claim not in _CLAIM_TYPES or not isinstance(policy, dict):
                continue
            safe = {}
            _bool(policy, "eligible", safe)
            _closed(policy, "certainty_level", _CERTAINTY_LEVELS, safe)
            safe["required_qualifiers"] = _closed_list(policy.get("required_qualifiers"), _QUALIFIERS)
            safe["reason_codes"] = _closed_list(policy.get("reason_codes"), _REASONS)
            safe_policies[claim] = safe
        usage["fact_claim_policies"] = safe_policies
    result["usage_constraints"] = usage
    return result


def project_writer_evidence(*, binding, package, bundle, context_text: str, citations,
                            requirement_snapshot=None, requirement_ids=()) -> dict:
    """Preserve governed text, identity, support links and per-source constraints."""
    witness = _builder_alias_witness(bundle)
    generated_evidence = {item.evidence_id: item for item in witness.items}
    generated_citations = {item.citation_id: item for item in witness.citations}
    associations = associate_selected_citations(package=package, bundle=bundle)
    allowed = tuple(alias for alias in writer_allowed_aliases(associations)
                    if alias in generated_citations)
    allowed_set = frozenset(allowed)
    rows = {row.alias: row for row in associations if row.alias in allowed_set}
    selected = set(bundle.selected_evidence_ids) & {item.evidence_id for item in package.selections}
    sources = []
    for index, item in enumerate(bundle.items):
        if item.evidence_id not in selected:
            continue
        raw = item.model_dump(mode="json")
        safe = {
            "evidence_id": generated_evidence[item.evidence_id].evidence_id,
            "citation_ids": [alias for alias in generated_evidence[item.evidence_id].citation_ids
                             if alias in allowed_set],
            "support_ref": _ref(f"/items/{index}", raw),
        }
        _closed(raw, "disposition", {"selected", "dropped"}, safe)
        for key in ("rank", "page_start", "page_end"):
            _number(raw, key, safe, integer=True)
        sources.append(safe)

    citation_view = []
    for alias in allowed:
        row = rows[alias]
        safe = {"citation_id": generated_citations[alias].citation_id,
                "citation_ref": row.citation_source_identity,
                "source_locator_ref": row.source_locator_ref}
        for key in ("page_start", "page_end"):
            _number(row.source_locator, key, safe, integer=True)
        citation_view.append(safe)

    governance = bundle.assessment.details.get("evidence_governance_v2") or {}
    candidates = governance.get("prompt_evidence_candidates") or []
    constraints = []
    for index, candidate in enumerate(candidates):
        path = f"/assessment/details/evidence_governance_v2/prompt_evidence_candidates/{index}"
        retrieval = candidate.get("retrieval_candidate") or {}
        entry = {"source_ref": _ref(path, candidate),
                 "citation_ids": _closed_list(candidate.get("citation_ids"), allowed_set)}
        for key in ("fact_visible_in_prompt", "context_truncated"):
            _bool(candidate, key, entry)
        _closed(candidate, "truncation_reason", {"token_or_character_budget"}, entry)
        for key, enum in (("document_type", DocumentType), ("evidence_role", EvidenceRole),
                          ("project_relation", ProjectRelation), ("authority_level", AuthorityLevel)):
            _closed(retrieval, key, {item.value for item in enum}, entry)
        for key in ("allowed_claim_types", "forbidden_claim_types"):
            entry[key] = _closed_list(retrieval.get(key), _CLAIM_TYPES)
        if candidate.get("structured_evidence") is not None:
            entry["structured_evidence"] = _structured(candidate["structured_evidence"], path + "/structured_evidence")
        constraints.append(entry)

    owned_topics, owner = _requirement_owner(requirement_snapshot, requirement_ids)
    owned_ids = frozenset(owner["requirement_ids"]) if owner is not None else frozenset()
    selections = [{"evidence_id": generated_evidence[item.evidence_id].evidence_id,
                   "requirement_ids": [rid for rid in item.requirement_ids if rid in owned_ids]}
                  for item in package.selections if item.evidence_id in selected]
    item_assessments = [_item_assessment(assessment,
        f"/assessment/details/evidence_governance_v2/item_assessments/{index}", owned_topics=owned_topics)
        for index, assessment in enumerate(governance.get("item_assessments") or [])]

    return {
        # Neither a resolver identity nor a hash-looking external claim proves
        # opacity. Bind the exact owning objects with newly computed hashes.
        "binding": {"sha256": canonical_sha256(binding.model_dump(mode="json"))},
        "selections": selections,
        "evidence_bundle_binding": {
            "sha256": canonical_sha256(package.evidence_bundle_binding.model_dump(mode="json")),
            "evidence_bundle_sha256": canonical_sha256(bundle.model_dump(mode="json", round_trip=True)),
        },
        "assessment": project_writer_assessment(bundle.assessment, owned_topics=owned_topics, requirement_owner=owner),
        "context_text": context_text,
        # One uniquely resolvable, source-consistent association authority for
        # citation rows, source aliases and usage. No raw-row fallback.
        "citations": citation_view,
        "citation_usage": {
            "allowed_aliases": list(allowed),
            "scope": "current_section",
        },
        "sources": sources,
        "source_constraints": {"candidates": constraints, "item_assessments": item_assessments},
    }
