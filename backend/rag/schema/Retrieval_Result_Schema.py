# -*- coding: utf-8 -*-
"""Compatibility result construction with strict raw P2-F lineage admission."""

from copy import deepcopy
from typing import Any, Dict, Optional

from core.hashing import canonical_sha256
from rag.configs.SchemaConfig import (
    RETRIEVAL_RESULT_TEMPLATE,
    RETRIEVAL_RESULT_V2_TEMPLATE,
    CHUNK_METADATA_TEMPLATE,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_VECTOR_DB,
    DEFAULT_EMBEDDING_VERSION,
)
from rag.schema.retrieval_candidate_v2 import build_retrieval_candidate_v2
from rag.schema.structured_evidence import build_structured_evidence


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _safe_nullable_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def build_retrieval_result(
    chunk: Dict[str, Any], rank: int, score: float,
    rerank_score: Optional[float] = None,
) -> Dict[str, Any]:
    # Existing v1 behavior is unchanged.
    result = deepcopy(RETRIEVAL_RESULT_TEMPLATE)
    result["rank"] = rank
    result["score"] = score
    result["rerank_score"] = rerank_score
    result["chunk_id"] = chunk.get("chunk_id")
    result["doc_id"] = chunk.get("doc_id")
    result["text"] = chunk.get("text", "")
    result["metadata"] = chunk.get("metadata", deepcopy(CHUNK_METADATA_TEMPLATE))
    return result


def _nested_dict(record: Dict[str, Any], key: str) -> Dict[str, Any]:
    direct = record.get(key)
    if isinstance(direct, dict):
        return deepcopy(direct)
    extra = record.get("extra")
    if isinstance(extra, dict) and isinstance(extra.get(key), dict):
        return deepcopy(extra[key])
    return {}


def _p2_raw_text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"P2-F raw {key} is required")
    return value.strip()


def _p2_containers(record: dict[str, Any]) -> list[dict[str, Any]]:
    values = [record]
    for key in ("extra", "metadata"):
        value = record.get(key)
        if isinstance(value, dict):
            values.append(value)
    return values


def _p2_profile(record: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    copies: list[dict[str, Any]] = []
    for container in _p2_containers(record):
        if "document_profile" not in container:
            continue
        profile = container["document_profile"]
        if not isinstance(profile, dict) or not profile:
            raise ValueError("P2-F supplied document_profile must be nonempty")
        copies.append(profile)
    if not copies:
        if required:
            raise ValueError("P2-F raw document_profile is required")
        return {}
    fingerprint = canonical_sha256(copies[0])
    if any(canonical_sha256(profile) != fingerprint for profile in copies[1:]):
        raise ValueError("P2-F supplied document_profile copies disagree")
    # Preserve the complete StageC flat dictionary, including optional lineage.
    # The external catalog profile_sha256 is NOT a hash of this flat dictionary.
    return deepcopy(copies[0])


def _p2_alias(record: dict[str, Any], names: tuple[str, ...], *, required: bool) -> str:
    # Only direct carriers can establish a required raw child identifier.
    # Nested aliases are consistency checks below, never missing-field repair.
    supplied = [_p2_raw_text(record, name) for name in names if name in record]
    if not supplied:
        if required:
            raise ValueError("P2-F raw child identifier is required")
        return ""
    if any(value != supplied[0] for value in supplied[1:]):
        raise ValueError("P2-F supplied chunk aliases disagree")
    return supplied[0]


def _p2_check_scalars(record: dict[str, Any], candidate: Any) -> None:
    expected = candidate.model_dump(mode="json")
    expected["doc_id"] = candidate.document_id
    expected["document_id"] = candidate.document_id
    expected["chunk_id"] = candidate.child_chunk_id
    # For a child, candidate.child_chunk_id comes from a direct raw carrier.
    # For a parent, the existing private profile view sets BOTH chunk aliases
    # to the already-required native parent_chunk_id.  A native Parent has no
    # required chunk_id/child_chunk_id; present copies must match its identity.
    identity_fields = (
        "tenant_id", "kb_id", "doc_id", "document_id", "chunk_id",
        "child_chunk_id", "parent_chunk_id", "source_sha256", "profile_sha256",
    )
    for container in _p2_containers(record):
        for key in identity_fields:
            if key in container and _p2_raw_text(container, key) != expected[key]:
                raise ValueError(f"P2-F supplied {key} alias disagrees with raw binding")
        # StageC supplies project_id=None for a lawful general profile.  The
        # existing typed validator/profile remain its semantic authority.
        # Optional null governance mirrors retain existing fallback behavior.
        for key in (
            "document_type", "evidence_role", "project_relation",
            "project_id", "authority_level",
        ):
            if key in container and container[key] not in (None, ""):
                value = getattr(container[key], "value", container[key])
                if value != expected[key]:
                    raise ValueError(f"P2-F supplied {key} disagrees with raw profile")
        for key in ("allowed_claim_types", "forbidden_claim_types"):
            if key not in container:
                continue
            values = container[key]
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"P2-F supplied {key} must be a list")
            normalized = [getattr(value, "value", value) for value in values]
            if any(not isinstance(value, str) for value in normalized):
                raise ValueError(f"P2-F supplied {key} entries must be strings")
            if len(set(normalized)) != len(normalized) or set(normalized) != set(expected[key]):
                raise ValueError(f"P2-F supplied {key} disagrees with raw profile")


def _p2_profile_view(record: dict[str, Any], identity: str) -> Any:
    # This fresh validation view is private and never establishes missing raw
    # identity.  Required raw fields and all profile copies have already passed.
    profile = _p2_profile(record)
    view = deepcopy(record)
    view.update(
        chunk_id=identity, child_chunk_id=identity,
        parent_chunk_id=_p2_raw_text(record, "parent_chunk_id"),
        doc_id=_p2_raw_text(record, "doc_id"),
        tenant_id=_p2_raw_text(record, "tenant_id"),
        kb_id=_p2_raw_text(record, "kb_id"),
        document_profile=profile,
        metadata={"document_profile": deepcopy(profile)},
        rank=1,
        child_text=_safe_str(record.get("text")),
        parent_text="",
        text=_safe_str(record.get("text")),
    )
    candidate = build_retrieval_candidate_v2(view)
    _p2_check_scalars(record, candidate)
    return candidate


def _p2_validate_child(
    raw: dict[str, Any], wrapper: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("P2-F raw child must be an object")
    # These DIRECT raw fields and the DIRECT child carrier are checked before
    # profile selection/private typed view, wrapper aliases or deduplication.
    for key in ("tenant_id", "kb_id", "doc_id", "parent_chunk_id"):
        _p2_raw_text(raw, key)
    identity = _p2_alias(raw, ("chunk_id", "child_chunk_id"), required=True)
    candidate = _p2_profile_view(raw, identity)
    if wrapper is not None:
        if not isinstance(wrapper, dict):
            raise ValueError("P2-F wrapper must be an object")
        for key in ("tenant_id", "kb_id", "doc_id", "parent_chunk_id"):
            if key in wrapper and _p2_raw_text(wrapper, key) != _p2_raw_text(raw, key):
                raise ValueError(f"P2-F wrapper {key} disagrees with raw child")
        wrapper_id = _p2_alias(wrapper, ("chunk_id", "child_chunk_id"), required=False)
        if wrapper_id and wrapper_id != identity:
            raise ValueError("P2-F wrapper child alias disagrees with raw child")
        supplied_profile = _p2_profile(wrapper, required=False)
        if supplied_profile and canonical_sha256(supplied_profile) != canonical_sha256(candidate.document_profile):
            raise ValueError("P2-F wrapper profile disagrees with raw child")
        _p2_check_scalars(wrapper, candidate)
    child = deepcopy(raw)
    child.update(
        chunk_id=identity, child_chunk_id=identity,
        parent_chunk_id=candidate.parent_chunk_id,
        tenant_id=candidate.tenant_id, kb_id=candidate.kb_id,
        doc_id=candidate.document_id,
        document_profile=deepcopy(candidate.document_profile),
    )
    return child


def _p2_observation(observation: Any) -> dict[str, Any]:
    if not isinstance(observation, dict):
        raise ValueError("P2-F matched child observation must be an object")
    if "child_chunk" in observation:
        return _p2_validate_child(observation["child_chunk"], observation)
    return _p2_validate_child(observation)


def _p2_binding(child: dict[str, Any]) -> tuple[str, ...]:
    return (
        child["tenant_id"], child["kb_id"], child["doc_id"],
        child["parent_chunk_id"], canonical_sha256(_p2_profile(child)),
    )


def _p2_parent_matches(
    parent: Optional[dict[str, Any]], primary: dict[str, Any],
    raw_cohort: list[dict[str, Any]],
) -> bool:
    if not isinstance(parent, dict) or not parent or not raw_cohort:
        return False
    try:
        for key in ("tenant_id", "kb_id", "doc_id", "parent_chunk_id"):
            _p2_raw_text(parent, key)
        parent_id = _p2_raw_text(parent, "parent_chunk_id")
        for alias in ("chunk_id", "child_chunk_id"):
            if alias in parent and _p2_raw_text(parent, alias) != parent_id:
                raise ValueError("P2-F parent alias disagrees with native parent identity")
        candidate = _p2_profile_view(parent, parent_id)
        parent_binding = (
            candidate.tenant_id, candidate.kb_id, candidate.document_id,
            parent_id, canonical_sha256(candidate.document_profile),
        )
        if parent_binding != _p2_binding(primary):
            return False
        members = parent.get("child_chunk_ids")
        if not isinstance(members, (list, tuple)) or not members:
            return False
        if any(not isinstance(value, str) or not value.strip() for value in members):
            return False
        parent_members = {value.strip() for value in members}
        matched_primary = False
        for observation in raw_cohort:
            child = _p2_observation(observation)
            if _p2_binding(child) != parent_binding or child["child_chunk_id"] not in parent_members:
                return False
            matched_primary |= child["child_chunk_id"] == primary["child_chunk_id"]
        return matched_primary
    except ValueError:
        # A bad parent/cohort denies expansion.  The primary child was validated
        # independently before this call and may still supply child-only context.
        return False


_P2_RETRIEVAL_METADATA = {
    "retrieval_stage", "retrieval_sources", "fusion_score", "rrf_k",
    "dense_rank", "dense_score", "keyword_rank", "keyword_score",
    "source_ranks", "source_scores", "rrf_contributions", "dedup_parent",
    "dense_hits", "keyword_hits", "fused_candidates",
}
_P2_RETRIEVAL_EXTRA = {
    "filter_expr", "dedup_parent", "keyword_doc_id", "keyword_doc_ids",
    "use_dense", "use_keyword", "configured_retrieval_stack",
}


def build_retrieval_result_v2(
    *, child_chunk: Dict[str, Any], parent_chunk: Optional[Dict[str, Any]] = None,
    rank: int, score: float, rerank_score: Optional[float] = None,
    embedding_model: Optional[str] = None,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    index_name: Optional[str] = None, vector_db: str = DEFAULT_VECTOR_DB,
    context_granularity: str = "parent",
    metadata: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    _raw_matched_candidates: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Strict raw child admission; parent expansion requires the whole cohort."""
    child = _p2_validate_child(child_chunk)  # Error propagates; no diagnostic admission.
    document_profile = _p2_profile(child)
    supplied_metadata = deepcopy(metadata) if isinstance(metadata, dict) else {}
    supplied_extra = deepcopy(extra) if isinstance(extra, dict) else {}
    projected = supplied_metadata.get("matched_child_chunks")
    projected = list(projected) if isinstance(projected, (list, tuple)) and projected else [child_chunk]
    cohort = list(_raw_matched_candidates) if _raw_matched_candidates is not None else list(projected)
    lawful_originals: list[dict[str, Any]] = []
    for original in cohort:
        try:
            lawful_originals.append(_p2_observation(original))
        except ValueError:
            pass  # Original cohort remains untouched for expansion denial below.
    carrier_valid = True
    for carrier in (supplied_metadata, supplied_extra):
        try:
            supplied_profile = _p2_profile(carrier, required=False)
            if supplied_profile and canonical_sha256(supplied_profile) != canonical_sha256(document_profile):
                carrier_valid = False
        except ValueError:
            carrier_valid = False
    projection_children: list[dict[str, Any]] = []
    for observation in projected:
        try:
            member = _p2_observation(observation)
            if _p2_binding(member) != _p2_binding(child):
                carrier_valid = False
                continue
            if member["child_chunk_id"] == child["child_chunk_id"]:
                if canonical_sha256(member) != canonical_sha256(child):
                    carrier_valid = False
                member = deepcopy(child)  # Primary body/extra cannot be overridden.
            elif _raw_matched_candidates is not None and not any(
                canonical_sha256(original) == canonical_sha256(member)
                for original in lawful_originals
            ):
                carrier_valid = False
                continue
            if member["child_chunk_id"] not in {value["child_chunk_id"] for value in projection_children}:
                projection_children.append(member)
        except ValueError:
            carrier_valid = False
    if child["child_chunk_id"] not in {value["child_chunk_id"] for value in projection_children}:
        projection_children.insert(0, child)
        carrier_valid = False if supplied_metadata.get("matched_child_chunks") else carrier_valid
    projected_ids = [value["child_chunk_id"] for value in projection_children]
    for carrier in (supplied_metadata, supplied_extra):
        if "matched_child_chunk_ids" in carrier and carrier["matched_child_chunk_ids"] != projected_ids:
            carrier_valid = False
        if "matched_child_count" in carrier and carrier["matched_child_count"] != len(projected_ids):
            carrier_valid = False
    parent_accepted = carrier_valid and _p2_parent_matches(parent_chunk, child, cohort)
    parent = deepcopy(parent_chunk) if parent_accepted else {}
    granularity = context_granularity if parent_accepted else "child"
    child_text, parent_text = _safe_str(child.get("text")), _safe_str(parent.get("text"))
    context_text = parent_text if granularity == "parent" and parent_text else child_text
    type_aware = _nested_dict(child, "type_aware_chunking")
    structured = build_structured_evidence(child).model_dump(mode="json", exclude_none=True)
    # Rebuild semantic channels from lawful child.  Caller parent/profile/body/
    # governance/structured overlays are not retained even as diagnostic strings.
    effective_metadata = {
        key: deepcopy(value) for key, value in supplied_metadata.items()
        if key in _P2_RETRIEVAL_METADATA
    }
    effective_extra = {
        key: deepcopy(value) for key, value in supplied_extra.items()
        if key in _P2_RETRIEVAL_EXTRA
    }
    for carrier in (effective_metadata, effective_extra):
        carrier["document_profile"] = deepcopy(document_profile)
        if type_aware:
            carrier["type_aware_chunking"] = deepcopy(type_aware)
        carrier["structured_evidence"] = deepcopy(structured)
    effective_metadata.update(
        parent_found=parent_accepted,
        matched_child_chunk_ids=projected_ids,
        matched_child_chunks=deepcopy(projection_children),
        matched_child_count=len(projected_ids),
    )
    child_candidate = _p2_profile_view(child, child["child_chunk_id"])
    result = deepcopy(RETRIEVAL_RESULT_V2_TEMPLATE)
    result.update({
        "tenant_id": child["tenant_id"], "kb_id": child["kb_id"],
        "doc_id": child["doc_id"],
        "file_id": _safe_str(child.get("file_id") or parent.get("file_id")) or None,
        "document_type": child_candidate.document_type.value,
        "evidence_role": child_candidate.evidence_role.value,
        "project_relation": child_candidate.project_relation.value,
        "project_id": child_candidate.project_id,
        "authority_level": child_candidate.authority_level.value,
        "structured_evidence": deepcopy(structured),
        "rank": rank, "score": score, "rerank_score": rerank_score,
        "chunk_id": child["child_chunk_id"], "child_chunk_id": child["child_chunk_id"],
        "parent_chunk_id": child["parent_chunk_id"],
        "matched_granularity": "child", "context_granularity": granularity,
        "child_text": child_text, "parent_text": parent_text, "text": context_text,
        "title": child.get("title") or parent.get("title"),
        "section": child.get("section") or parent.get("section"),
        "page_start": _safe_nullable_int(child.get("page_start") or parent.get("page_start")),
        "page_end": _safe_nullable_int(child.get("page_end") or parent.get("page_end")),
        "child_index_in_parent": _safe_nullable_int(child.get("child_index_in_parent")),
        "source_type": _safe_str(child.get("source_type") or parent.get("source_type"), DEFAULT_SOURCE_TYPE),
        "source_unit_ids": [_safe_str(value) for value in _safe_list(child.get("source_unit_ids") or parent.get("source_unit_ids"))],
        "cleaning_version": _safe_str(child.get("cleaning_version") or parent.get("cleaning_version")),
        "parent_chunk_version": _safe_str(child.get("parent_chunk_version") or parent.get("parent_chunk_version")),
        "child_chunk_version": _safe_str(child.get("child_chunk_version")),
        "embedding_model": embedding_model, "embedding_version": embedding_version,
        "index_name": index_name, "vector_db": vector_db,
        "metadata": effective_metadata, "extra": effective_extra,
    })
    candidate = build_retrieval_candidate_v2(result)
    result["metadata"]["retrieval_candidate_v2"] = candidate.model_dump(mode="json", exclude_none=True)
    return result
