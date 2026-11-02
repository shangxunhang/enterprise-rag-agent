"""Finite ordered retrieval evidence, loss localization, and guarded rankings."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from ..contracts import (EdgeObservation, Finding, MetricResult, ObservationBatch,
                         StageObservation, canonical_hash)
from ..snapshot import SnapshotView, diagnostic_capture_complete, original_diagnostic_frame


KEY_UNITS = {"R-SCOPE": "C01", "R-QUERY": "C03", "R-DENSE": "C04", "R-BM25": "C05",
    "R-SOURCE-FUSION": "C06", "R-PARENT": "C07", "R-QUERY-FUSION": "C07",
    "R-RERANK": "C08", "R-CRAG": "C09", "R-CORRECT": "C10", "R-GOV": "C11", "R-PACK": "C12"}
EDGE_KEYS = {"X03": ("R-SOURCE-FUSION", "R-PARENT"), "X04": ("R-DENSE", "R-SOURCE-FUSION"),
    "X05": ("R-PARENT", "R-RERANK"), "X06": ("R-RERANK", "R-GOV"),
    "X07": ("R-GOV", "R-PACK")}


def check_retrieval_edge(edge_id, source, target, *, complete=True, expected_useful_ids=None,
                         allowed_ids=None, required_text=None):
    """Frozen X03-X07 semantics with explicit independent obligations."""
    if edge_id not in EDGE_KEYS:
        raise ValueError("UNKNOWN_RETRIEVAL_EDGE")
    result = evaluate_pool_transition(source, target, complete=complete,
        grain="parent" if edge_id == "X05" else "child",
        allowed_transformation="CHILD_TO_PARENT" if edge_id == "X03" else "ORDER_FILTER_REPRESENTATION",
        expected_useful_ids=expected_useful_ids, allowed_ids=allowed_ids, required_text=required_text)
    if edge_id == "X04" and result["state"] == "MEASURED":
        result["assertion"] = result["assertion"] and set(result["source_order"]) == set(result["target_order"])
    result["edge_id"] = edge_id
    return result


def candidate_key(item: Mapping[str, Any], *, grain="child") -> str:
    identifier = (item.get("parent_chunk_id") or item.get("context_chunk_id")) if grain == "parent" else (
        item.get("child_chunk_id") or item.get("matched_chunk_id") or item.get("chunk_id"))
    if grain == "doc":
        identifier = item.get("doc_id")
    return str(identifier or "")


def source_key(item: Mapping[str, Any], *, grain="child") -> tuple[Any, ...]:
    metadata = item.get("metadata") or {}
    child = item.get("child_chunk") or {}
    profile = item.get("document_profile") or metadata.get("document_profile") or child.get("document_profile") or {}
    return (item.get("tenant_id") or metadata.get("tenant_id") or child.get("tenant_id") or profile.get("tenant_id"),
            item.get("index_version") or metadata.get("index_version") or child.get("index_version"),
            item.get("doc_id") or child.get("doc_id"), candidate_key(item, grain=grain))


def evaluate_pool_transition(source: Sequence[Mapping[str, Any]] | None,
                             target: Sequence[Mapping[str, Any]] | None, *,
                             complete=True, grain="child", allowed_transformation="IDENTITY",
                             expected_useful_ids=None, allowed_ids=None, required_text=None) -> dict[str, Any]:
    if source is None or target is None or not complete:
        return {"state": "UNMEASURED", "assertion": None, "reason": "FINITE_POOL_NOT_PROVEN"}
    source_ids = [candidate_key(item, grain=grain) for item in source]
    target_ids = [candidate_key(item, grain=grain) for item in target]
    preserved = set(target_ids)
    if allowed_transformation == "CHILD_TO_PARENT":
        preserved = set()
        for item in target:
            metadata = item.get("metadata") or {}
            preserved.update(item.get("matched_child_chunk_ids") or metadata.get("matched_child_chunk_ids") or [])
            preserved.update(candidate_key(child) for child in (item.get("matched_child_chunks") or metadata.get("matched_child_chunks") or []))
            preserved.add(candidate_key(item))
    dropped = [identifier for identifier in source_ids if identifier not in preserved]
    allowed = set(allowed_ids) if allowed_ids is not None else set(source_ids)
    useful = set(expected_useful_ids or []) & set(source_ids) & allowed
    useful_dropped = sorted(useful - preserved)
    wrong_use_allow = sorted(set(target_ids) - allowed) if allowed_ids is not None else []
    wrong_binding = []
    by_id = {candidate_key(item, grain=grain): source_key(item, grain=grain) for item in source}
    for item in target:
        identifier = candidate_key(item, grain=grain)
        if identifier in by_id and source_key(item, grain=grain) != by_id[identifier]:
            wrong_binding.append(identifier)
    text_lost = []
    if required_text is not None:
        by_target = {candidate_key(item, grain=grain): item for item in target}
        for identifier, text in required_text.items():
            item = by_target.get(identifier)
            # Only exact prompt representation is evidence of visibility.
            if item is None or "prompt_visible_text" not in item or text not in str(item["prompt_visible_text"]):
                text_lost.append(identifier)
    return {"state": "MEASURED", "assertion": not useful_dropped and not wrong_use_allow and not wrong_binding and not text_lost,
            "source_order": source_ids, "target_order": target_ids, "dropped": dropped,
            "useful_dropped": useful_dropped, "wrong_use_allow": wrong_use_allow,
            "wrong_binding": wrong_binding, "visibility_lost": text_lost,
            "loss_rate": len(useful_dropped) / len(useful) if useful else None,
            "loss_state": "MEASURED" if useful else "UNMEASURED"}


def compare_rankings(before, after, gold_ids, *, top_k, grain="child", reviewed=False, synthetic=False,
                     complete=True, before_scope=None, after_scope=None,
                     before_prompt=None, after_prompt=None, response_replayed=False):
    """Rank deltas require the same candidate space and reviewed independent labels."""
    if response_replayed and before_prompt != after_prompt:
        return {"state": "NOT_COMPARABLE", "reason": "OLD_RESPONSE_NEW_PROMPT_MECHANISM_ONLY"}
    if not complete or before_scope != after_scope or {source_key(row, grain=grain) for row in before} != {source_key(row, grain=grain) for row in after}:
        return {"state": "NOT_COMPARABLE", "reason": "POOL_OR_SCOPE_DIFFERENT"}
    from ..metrics import ranked_metrics
    left = ranked_metrics(before, gold_ids, grain=grain, top_k=top_k, reviewed=reviewed,
                          judged_complete=True, pool_complete=complete, unit_id="C08", synthetic=synthetic)
    right = ranked_metrics(after, gold_ids, grain=grain, top_k=top_k, reviewed=reviewed,
                           judged_complete=True, pool_complete=complete, unit_id="C08", synthetic=synthetic)
    if any(item.measurement_state != "MEASURED" for item in left + right):
        return {"state": "UNMEASURED", "reason": "INDEPENDENT_REFERENCE_NOT_MEASURED"}
    by_name = {item.metric_id: item.value for item in left}
    return {"state": "MEASURED", "delta": {item.metric_id: item.value - by_name[item.metric_id] for item in right}}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


_SOURCE_FIELDS = (
    "child_chunk_id", "chunk_id", "matched_chunk_id", "parent_chunk_id", "context_chunk_id",
    "doc_id", "tenant_id", "kb_id", "file_id", "text", "parent_text", "child_text", "match_text",
    "title", "section", "page_start", "page_end", "source_unit_ids", "source_sha256", "content_sha256", "profile_sha256",
    "cleaning_version", "parent_chunk_version", "child_chunk_version", "embedding_model", "embedding_version",
    "index_name", "index_version", "vector_db", "schema_version", "version", "source_version", "document_version",
    "chunk_version", "parser_version", "quote_text", "matched_granularity",
    "context_granularity", "document_profile", "matched_child_chunk_ids", "structured_evidence",
    "type_aware_chunking", "evidence_assessment_v2",
)


def _source_payload(item, *, context_text=False, allow_governance_annotation=False):
    """Source content/versions only; ranking and packing metadata are mutable."""
    fields = _SOURCE_FIELDS + (("context_text",) if context_text else ())
    result = {key: item[key] for key in fields if key in item}
    for key in ("metadata", "extra", "child_chunk"):
        if isinstance(item.get(key), Mapping):
            result[key] = _source_payload(item[key], context_text=context_text)
            if key == "metadata" and allow_governance_annotation:
                # Governance annotates this exact path, not nested source records.
                result[key].pop("evidence_assessment_v2", None)
    if "matched_child_chunks" in item:
        rows = item["matched_child_chunks"]
        result["matched_child_chunks"] = [
            _source_payload(row, context_text=context_text) if isinstance(row, Mapping) else row
            for row in rows] if isinstance(rows, list) else rows
    return result


def _source_identity_proven(item, grain):
    """Do not hide conflicting claims behind fallback `or` expressions."""
    claims = {key: set() for key in ("tenant_id", "doc_id", "index_version", "source_sha256", "profile_sha256")}
    if item.get("child_chunk_id") and item.get("chunk_id") and item["child_chunk_id"] != item["chunk_id"]:
        return False
    if item.get("context_granularity") == "parent" and item.get("parent_chunk_id") and item.get("context_chunk_id") and item["parent_chunk_id"] != item["context_chunk_id"]:
        return False

    def visit(row, profile=False):
        for key in claims:
            value = row.get("document_id" if profile and key == "doc_id" else key)
            if value not in (None, ""):
                claims[key].add(str(value))
        for key in ("metadata", "extra", "child_chunk", "document_profile"):
            if isinstance(row.get(key), Mapping):
                visit(row[key], key == "document_profile")

    visit(item)
    return bool(candidate_key(item, grain=grain) and all(len(values) <= 1 for values in claims.values())
                and claims["tenant_id"] and claims["doc_id"]
                and (claims["index_version"] or claims["source_sha256"]))


def _preserved_source_content(source, target, *, grain, context_text=None, allow_governance_annotation=False):
    """Bind selected output to the actual input, never a repeated query's last row."""
    if any(not isinstance(row, Mapping) or not _source_identity_proven(row, grain) for row in source):
        return {}, "SOURCE_IDENTITY_OR_VERSION_NOT_PROVEN"
    if any(not isinstance(row, Mapping) or not candidate_key(row, grain=grain) for row in target):
        return {}, "SOURCE_IDENTITY_OR_VERSION_NOT_PROVEN"
    ids = lambda rows: [candidate_key(row, grain=grain) for row in rows]
    if len(set(ids(source))) != len(source) or len(set(ids(target))) != len(target):
        return {}, "SOURCE_CONTENT_BINDING_AMBIGUOUS"
    by_id = {candidate_key(row, grain=grain): row for row in source}
    checks, unknown = {}, any(not _source_identity_proven(row, grain) for row in target)
    for row in target:
        identifier = candidate_key(row, grain=grain)
        original = by_id.get(identifier)
        if original is None:
            checks["source_content:" + identifier] = False
            continue
        options = {"context_text": grain == "parent" if context_text is None else context_text,
                   "allow_governance_annotation": allow_governance_annotation}
        left, right = _source_payload(original, **options), _source_payload(row, **options)
        if not any(original.get(key) for key in ("text", "parent_text", "child_text", "match_text")):
            unknown = True

        def compare(a, b):
            nonlocal unknown
            equal = True
            for key in a.keys() | b.keys():
                if key not in a or key not in b:
                    unknown = True
                elif isinstance(a[key], Mapping) and isinstance(b[key], Mapping):
                    equal = compare(a[key], b[key]) and equal
                elif a[key] != b[key]:
                    equal = False
            return equal

        checks["source_content:" + identifier] = compare(left, right)
    return checks, "SOURCE_CONTENT_ENDPOINT_NOT_PROVEN" if unknown and all(checks.values()) else ""


def _pack_source_projection(source, selected, pack):
    """Replay known pure projection rules using producer material, not consumer text."""
    from rag.context.context_packer import ContextPacker

    if not isinstance(selected.get("metadata"), Mapping) or not isinstance(source.get("metadata", {}), Mapping):
        return None, "PROMPT_PROJECTION_RULE_NOT_PROVEN"
    mode = selected["metadata"].get("prompt_projection_mode")
    known = {"historical_reference_restricted", "template_structure_only", "governed_full_text", "legacy_unprojected"}
    if mode not in known:
        return None, "PROMPT_PROJECTION_RULE_NOT_PROVEN"
    if not any(source.get(key) for key in ("text", "parent_text", "child_text", "match_text")):
        return None, "SOURCE_CONTENT_ENDPOINT_NOT_PROVEN"
    # This object invokes pure methods only. No current settings or constructor
    # defaults are evidence of an unrecorded historical configuration.
    projector = ContextPacker.__new__(ContextPacker)
    projector.text_field = ""
    text, metadata = projector._prompt_projection(dict(source))
    actual_mode = metadata["prompt_projection_mode"]
    if actual_mode in {"governed_full_text", "legacy_unprojected"}:
        representations = {str(source[key]).strip() for key in ("text", "parent_text", "child_text") if source.get(key)}
        if len(representations) != 1:
            return None, "PACK_TEXT_FIELD_NOT_PROVEN"
        # The frozen packer accepts custom field names too. A differing value
        # that could come from such a field is ambiguous without INPUT proof;
        # never choose a field using consumer-declared configuration.
        visible = selected.get("prompt_visible_text")
        prefix = visible[:-len("\n...[TRUNCATED]")] if isinstance(visible, str) and visible.endswith("\n...[TRUNCATED]") else None
        if visible != text and any(str(value).strip() == visible or (prefix is not None and str(value).strip().startswith(prefix))
                                   for key, value in source.items() if value and key not in {"text", "parent_text", "child_text"}):
            return None, "PACK_TEXT_FIELD_NOT_PROVEN"
    return (text, metadata, projector), ""


def _pack_fidelity(source, target, context, pack):
    """Source-derived projection, citations and bounded rendering/truncation proof."""
    from core.token_estimator import DeterministicTokenEstimator

    checks, reason = _preserved_source_content(source, target, grain="child")
    if reason or not all(checks.values()):
        return checks, reason
    if pack.get("packing_strategy") not in {"default", "lost_in_middle_aware"}:
        return checks, "PACK_RENDERING_RULE_NOT_PROVEN"
    by_id = {candidate_key(row): row for row in source}
    projections, citation_ids, next_id = [], {}, 1
    for rank, row in enumerate(target, 1):
        original = by_id[candidate_key(row)]
        projection, reason = _pack_source_projection(original, row, pack)
        if reason:
            return checks, reason
        text, metadata, projector = projection
        checks["projection_mode:" + candidate_key(row)] = all((row.get("metadata") or {}).get(key) == value for key, value in metadata.items())
        markers = []
        for citation in projector._citation_sources(dict(original)):
            if citation.child_id not in citation_ids:
                citation_ids[citation.child_id] = "C" + str(next_id)
                next_id += 1
            if citation_ids[citation.child_id] not in markers:
                markers.append(citation_ids[citation.child_id])
        visible = row.get("prompt_visible_text")
        checks["context_text:" + candidate_key(row)] = row.get("context_text") == visible
        truncated = (row.get("metadata") or {}).get("context_truncated")
        if not isinstance(truncated, bool):
            return checks, "PACK_TRUNCATION_BOUNDARY_NOT_PROVEN"
        if not truncated:
            checks["source_projection:" + candidate_key(row)] = visible == text
        else:
            suffix = "\n...[TRUNCATED]"
            prefix = visible[:-len(suffix)] if isinstance(visible, str) and visible.endswith(suffix) else None
            meta = row.get("metadata") or {}
            if not meta.get("truncation_reason") or not meta.get("truncated_source_id") or "truncated_item_ids" not in pack:
                return checks, "PACK_TRUNCATION_BOUNDARY_NOT_PROVEN"
            source_id = str(original.get("parent_chunk_id") or original.get("child_chunk_id") or original.get("chunk_id") or "")
            checks["source_projection:" + candidate_key(row)] = bool(rank == 1 and prefix is not None and text.startswith(prefix)
                and prefix == prefix.rstrip() and meta.get("truncation_reason") == "token_or_character_budget"
                and meta.get("truncated_source_id") == source_id and source_id in (pack.get("truncated_item_ids") or []))
        projections.append((original, row, text, projector, markers, truncated))
    if not all(checks.values()):
        return checks, ""
    if not target:
        checks["source_rendered_context"] = context == ""
        return checks, ""
    char_budget, token_budget = pack.get("max_context_chars"), pack.get("token_budget")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (char_budget, token_budget)):
        return checks, "PACK_EFFECTIVE_BUDGET_NOT_PROVEN"
    checks["actual_context_budget"] = len(context) <= char_budget and DeterministicTokenEstimator.estimate(context) <= token_budget
    # Selection/dedup settings are not recorded. Enumerate the finite allowed
    # rendering boundary, independently reconstructed from producer fields.
    # No consumer header is accepted as source material or current config.
    matches = []
    for include_metadata in (False, True):
        for total in range(len(target), len(source) + 1):
            parts, truncation_valid = [], True
            for rank, (original, row, text, projector, markers, truncated) in enumerate(projections, 1):
                projector.include_metadata = include_metadata
                projector.packing_strategy = pack["packing_strategy"]
                render = lambda value: projector._format_item(dict(original), rank, value, total, markers)
                parts.append(render(row["prompt_visible_text"]))
                if truncated:
                    full = render(text)
                    if char_budget <= 300 or token_budget <= 64 or (len(full) <= char_budget and DeterministicTokenEstimator.estimate(full) <= token_budget):
                        truncation_valid = False
                        continue
                    low, high, best = 0, len(text), None
                    while low <= high:
                        middle = (low + high) // 2
                        candidate_text = text[:middle].rstrip() + "\n...[TRUNCATED]"
                        rendered = render(candidate_text)
                        if len(rendered) <= char_budget and DeterministicTokenEstimator.estimate(rendered) <= token_budget:
                            best, low = candidate_text, middle + 1
                        else:
                            high = middle - 1
                    truncation_valid = truncation_valid and row["prompt_visible_text"] == best
            if "\n\n".join(parts) == context:
                matches.append(truncation_valid)
    checks["source_rendered_context"] = bool(matches)
    checks["source_budget_truncation"] = any(matches)
    return checks, ""


def _events(snapshot):
    for source in snapshot.sources.values():
        rows = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                yield original_diagnostic_frame(row), replace(source.evidence, pointer=f"/{index}")


def _event_bindings(events):
    """Use original operation boundaries, never a last candidate or a title.

    Existing native R-SCOPE starts each pipeline invocation on its original
    worker. Component operation names alone do not identify an invocation.
    An unanchored explicit protocol must carry a common operation identity.
    """
    contexts, result = {}, {}
    for row, ref in events:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else row
        worker = tuple(row.get(key) for key in ("session_id", "segment_id", "process_id", "thread_id"))
        key = row.get("evidence_key") or row.get("event_type")
        if key == "R-SCOPE" and row.get("sequence") is not None:
            contexts[worker] = {"operation": canonical_hash([ref.locator, ref.pointer, row.get("frame_sha256")]),
                "origin": "ORIGINAL_R_SCOPE_BOUNDARY", "root_query": payload.get("query"), "round": 0,
                "anchor_ref": ref.locator + ref.pointer}
        context = contexts.get(worker, {})
        if key == "R-CORRECT" and payload.get("phase") == "BEGIN" and isinstance(payload.get("round"), int):
            context["round"] = payload["round"]
        explicit = (row.get("retrieval_operation_id") or payload.get("retrieval_operation_id")
                    or row.get("root_operation_ref") or payload.get("root_operation_ref"))
        operation = explicit or context.get("operation") or row.get("operation_ref")
        invocation = canonical_hash([*worker, row.get("run_id") or payload.get("run_id"),
            row.get("task_id") or payload.get("task_id"), row.get("section_id") or payload.get("section_id"), operation]) if operation else None
        round_id = payload.get("round", row.get("round", context.get("round", 0)))
        stage_round = re.fullmatch(r"correction_round_(\d+)", str(payload.get("stage", row.get("stage", ""))))
        if stage_round:
            round_id = int(stage_round[1])
        result[id(row)] = {"invocation": invocation, "query": payload.get("query") or row.get("query") or context.get("root_query"),
            "root_query": context.get("root_query"), "round": round_id,
            "component_operation": row.get("operation_ref"), "identity_origin": context.get("origin", "EXPLICIT_OPERATION"),
            "anchor_ref": context.get("anchor_ref")}
    return result


def extract_retrieval(snapshot: SnapshotView, *, subject_id=None) -> ObservationBatch:
    batch = ObservationBatch("retrieval", limitations=list(snapshot.limitations))
    events = list(_events(snapshot))
    bindings = _event_bindings(events)
    pools, seen, key_stages = {}, set(), {}
    capture_complete = diagnostic_capture_complete(snapshot)
    if not capture_complete:
        batch.limitations.append("CAPTURE_INCOMPLETE")
    for row, ref in events:
        kind = row.get("event_type")
        pool_id = row.get("pool_id")
        if kind == "POOL_BEGIN":
            pools[(ref.locator, row.get("session_id"), row.get("segment_id"), pool_id)] = {"header": row, "items": [], "ref": ref, "ordinals": [], "end": None}
        elif kind in {"POOL_ITEM", "POOL_END"}:
            pool = pools.get((ref.locator, row.get("session_id"), row.get("segment_id"), pool_id))
            if pool and kind == "POOL_ITEM":
                pool["items"].append(row.get("item")); pool["ordinals"].append(row.get("ordinal"))
            elif pool:
                pool["end"] = row
    captured = []
    for pool_identity, pool in pools.items():
        header, items, end = pool["header"], pool["items"], pool["end"]
        digest = hashlib.sha256(b"".join(_encoded(item) + b"\n" for item in items)).hexdigest()
        complete = bool(capture_complete and end and end.get("actual_count") == len(items) == header.get("declared_count")
            and pool["ordinals"] == list(range(len(items))) and end.get("end_digest") == digest and end.get("completeness") == "COMPLETE")
        captured.append((header.get("evidence_key"), {**header, **header.get("payload", {}), "candidates": items, "complete": complete,
                         "phase": header.get("phase", "RESULT"), "_binding": bindings[id(header)]},
                         {**header, "operation_ref": header.get("pool_id")}, pool["ref"]))
    for row, ref in events:
        key = row.get("evidence_key") or row.get("event_type")
        if key in KEY_UNITS and row.get("event_type") != "POOL_BEGIN":
            payload = {**row.get("payload", row), "_binding": bindings[id(row)]}
            captured.append((key, payload, row, ref))
        if row.get("schema_version") == "rag_retrieval_run_v1" or ("p2_results" in row and "retrieval_results" in row):
            for key, field in (("R-QUERY", "query_expansion"), ("R-QUERY-FUSION", "p2_results"), ("R-CRAG", "evidence_quality"), ("R-PACK", "context_pack")):
                if row.get(field) is not None:
                    value = row[field]
                    payload = {"candidates": value, "complete": True} if isinstance(value, list) else dict(value)
                    captured.append((key, payload, {"operation_ref": "run_record:" + field, "run_id": row.get("run_id")}, ref))
            if row.get("initial_reranked_results") is not None:
                captured.append(("R-RERANK", {"candidates": row["initial_reranked_results"], "complete": True}, {"operation_ref": "run_record:initial_reranked_results"}, ref))
    for key, payload, row, ref in captured:
        if key not in KEY_UNITS or not isinstance(payload, Mapping):
            continue
        seen.add(key)
        complete = payload.get("complete", ref.completeness == "COMPLETE") and capture_complete
        failed = payload.get("phase") == "ERROR"
        state = "TECHNICAL_FAILURE" if failed else ("MEASURED" if complete else "UNMEASURED")
        stage = StageObservation(KEY_UNITS[key], str(row.get("run_id") or snapshot.snapshot_id),
            operation_id=str(row.get("operation_ref") or key), source_segment=str(row.get("segment_id") or "snapshot"),
            query=(payload.get("_binding") or {}).get("query") or payload.get("query") or row.get("query"),
            round=(payload.get("_binding") or {}).get("round", payload.get("round")),
            executed=True, observed_execution_status="FAILED" if failed else "OBSERVED", measurement_state=state,
            evidence_refs=(ref,), payload={"evidence_key": key, **dict(payload), "complete": bool(complete)})
        batch.stages.append(stage); batch.evidence.append(ref)
        key_stages.setdefault(key, []).append(stage)
    for key, unit in KEY_UNITS.items():
        if key not in seen:
            batch.stages.append(StageObservation(unit, snapshot.snapshot_id, operation_id=key,
                payload={"evidence_key": key, "availability": "NOT_RECORDED"}))
            batch.limitations.append("NOT_RECORDED:" + key)
    # Adaptive choice is separate from rewritten-query content.
    query_stages = key_stages.get("R-QUERY", [])
    batch.stages.append(StageObservation("C02", snapshot.snapshot_id, operation_id="QUERY_RULE_SELECTION",
        measurement_state="MEASURED" if query_stages else "UNMEASURED",
        payload={"rule_selections": [stage.payload.get("retrieval_plan") for stage in query_stages],
                 "quality_claim": "RULE_CONFORMANCE_ONLY"}))
    batch.edges.extend(_native_edge_observations(key_stages, snapshot.snapshot_id))
    batch.metrics.extend(_retrieval_metrics(batch))
    return batch


def _native_edge_observations(key_stages, subject):
    """Connect original finite endpoints to structural checks in OBSERVE."""
    groups = {}
    for stages in key_stages.values():
        for stage in stages:
            identity = stage.payload.get("_binding") or {}
            groups.setdefault((identity.get("invocation"), identity.get("round")), []).append(stage)
    observed = []
    for edge in EDGE_KEYS:
        edge_groups = []
        for identity, stages in groups.items():
            if edge in {"X03", "X04"}:
                keys = {"R-SOURCE-FUSION", "R-PARENT"} if edge == "X03" else {"R-DENSE", "R-BM25", "R-SOURCE-FUSION"}
                queries = {stage.query for stage in stages if stage.payload.get("evidence_key") in keys}
                edge_groups.extend((identity, query, [stage for stage in stages if stage.query == query]) for query in queries)
            else:
                edge_groups.append((identity, None, stages))
        for identity, query, stages in sorted(edge_groups, key=lambda item: repr(item[:2])):
            result, sources, targets = _check_native_transition(edge, stages)
            if identity[0] is None or (edge in {"X03", "X04"} and query is None):
                result = {"state": "UNMEASURED", "assertion": None, "reason": "OPERATION_QUERY_IDENTITY_NOT_PROVEN"}
            sources, targets = [s for s in sources if s], [s for s in targets if s]
            refs = lambda rows: tuple(ref for stage in rows for ref in stage.evidence_refs)
            observed.append(EdgeObservation(edge, subject,
                source_key=canonical_hash([stage.key for stage in sources]) if sources else None,
                target_key=canonical_hash([stage.key for stage in targets]) if targets else None,
                source_refs=refs(sources), target_refs=refs(targets),
                allowed_transformation="CHILD_TO_PARENT_GROUP_DEDUP_TOP_K" if edge == "X03" else "ORDER_FILTER_REPRESENTATION",
                measurement_state=result["state"], assertion_result=result["assertion"],
                availability="PERSISTED" if sources and targets else "NOT_RECORDED",
                completeness="COMPLETE" if result["state"] == "MEASURED" else "UNKNOWN",
                reason=result.get("reason", ""), payload={**result, "invocation_identity": identity[0],
                    "round": identity[1], "query": query, "reference_quality_state": "UNMEASURED",
                    "quality_reason": "INDEPENDENT_REVIEWED_GOLD_REQUIRED"}))
        if not any(item.edge_id == edge for item in observed):
            observed.append(EdgeObservation(edge, subject, reason="FINITE_POOL_NOT_PROVEN"))
    return observed


def _check_native_transition(edge, stages):
    def select(key, phase="RESULT", operations=()):
        rows = [stage for stage in stages if stage.payload.get("evidence_key") == key
                and stage.payload.get("phase", "RESULT") == phase
                and (isinstance(stage.payload.get("candidates"), list) or key == "R-PACK")]
        for operation in operations:
            exact = [stage for stage in rows if (stage.payload.get("_binding") or {}).get("component_operation") == operation]
            if exact:
                return exact[0] if len(exact) == 1 else None
        return rows[0] if len(rows) == 1 else None

    def candidates(stage):
        return stage.payload.get("candidates") if stage else None

    def complete(rows):
        return bool(rows and all(stage and stage.payload.get("complete") for stage in rows))

    def missing(sources, targets, reason="FINITE_POOL_OR_INTERMEDIATE_NOT_PROVEN"):
        return {"state": "UNMEASURED", "assertion": None, "reason": reason}, sources, targets

    def finish(source, target, sources, targets, *, grain="child", expected=None, checks=None, required_text=None):
        if not complete(sources + targets) or source is None or target is None:
            return missing(sources, targets)
        if any(not isinstance(row, Mapping) or not candidate_key(row, grain=grain) for row in source + target):
            return missing(sources, targets, "CANDIDATE_IDENTITY_NOT_PROVEN")
        result = check_retrieval_edge(edge, source, target, complete=True,
            expected_useful_ids=expected, allowed_ids=[candidate_key(row, grain=grain) for row in source], required_text=required_text)
        result["structural_checks"] = checks or {}
        result["assertion"] = result["assertion"] and all(result["structural_checks"].values())
        result["reference_quality_state"] = "UNMEASURED"
        return result, sources, targets

    if edge == "X04":
        dense, keyword, fused = select("R-DENSE"), select("R-BM25"), select("R-SOURCE-FUSION")
        sources, targets = [dense, keyword], [fused]
        if not complete(sources + targets):
            return missing(sources, targets)
        source, target = candidates(dense) + candidates(keyword), candidates(fused)
        return finish(source, target, sources, targets, checks={
            "child_rrf_unique_union": len(target) == len({candidate_key(row) for row in source}),
            "no_duplicate_child": len(target) == len({candidate_key(row) for row in target})})

    if edge == "X03":
        fused, grouped, selected, enriched = select("R-SOURCE-FUSION"), select("R-PARENT", "BEFORE_TOP_K"), select("R-PARENT", "AFTER_TOP_K"), select("R-PARENT")
        sources, targets = [fused, grouped, selected], [enriched]
        if not complete(sources + targets):
            return missing(sources, targets)
        source, target = candidates(fused), candidates(enriched)
        top_k = grouped.payload.get("top_k")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0:
            return missing(sources, targets, "PARENT_TOP_K_NOT_PROVEN")
        parent_ids = list(dict.fromkeys(candidate_key(row, grain="parent") for row in source))
        selected_ids = [candidate_key(row, grain="parent") for row in candidates(selected)]
        target_ids = [candidate_key(row, grain="parent") for row in target]
        expected = [candidate_key(row) for row in source if candidate_key(row, grain="parent") in selected_ids]
        checks = {"grouped_parent_order": [candidate_key(row, grain="parent") for row in candidates(grouped)] == parent_ids,
            "declared_top_k_prefix": selected_ids == parent_ids[:top_k], "enriched_parent_order": target_ids == selected_ids}
        for row in target:
            parent = candidate_key(row, grain="parent")
            children = {candidate_key(item) for item in source if candidate_key(item, grain="parent") == parent}
            retained = set(row.get("matched_child_chunk_ids") or (row.get("metadata") or {}).get("matched_child_chunk_ids") or [])
            checks["exact_parent_members:" + parent] = retained == children
        return finish(source, target, sources, targets, expected=expected, checks=checks)

    if edge == "X05":
        parents = [stage for stage in stages if stage.payload.get("evidence_key") == "R-PARENT" and stage.payload.get("phase") == "RESULT" and "candidates" in stage.payload]
        inputs = [stage for stage in stages if stage.payload.get("evidence_key") == "R-QUERY-FUSION" and stage.payload.get("phase") == "INPUT" and "candidates" in stage.payload]
        before, fused = select("R-QUERY-FUSION", "BEFORE_TOP_K"), select("R-QUERY-FUSION")
        rerank_input = select("R-RERANK", "INPUT", ("initial_rerank_input",))
        reranked = select("R-RERANK", operations=("initial_rerank_output", "noop_after_top_k", "cross_encoder_after_top_k"))
        sources, targets = parents + inputs + [before, fused, rerank_input], [reranked]
        if not parents or not inputs or not complete(sources + targets):
            return missing(sources, targets)
        by_query = {}
        for stage in parents:
            by_query.setdefault(stage.query, []).append(stage)
        if set(by_query) != {stage.query for stage in inputs} or any(len(values) != 1 for values in by_query.values()):
            return missing(sources, targets, "QUERY_PARENT_INPUT_IDENTITY_AMBIGUOUS")
        top_k = fused.payload.get("top_k", before.payload.get("top_k"))
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0:
            return missing(sources, targets, "QUERY_FUSION_TOP_K_NOT_PROVEN")
        ids = lambda rows: [candidate_key(row, grain="parent") for row in rows]
        source = [row for stage in parents for row in candidates(stage)]
        target = candidates(reranked)
        checks = {"each_query_pool_preserved": all(ids(candidates(stage)) == ids(candidates(by_query[stage.query][0])) for stage in inputs),
            "query_rrf_unique_union": set(ids(candidates(before))) == set(ids(source)) and len(ids(candidates(before))) == len(set(ids(source))),
            "query_rrf_top_k_prefix": ids(candidates(fused)) == ids(candidates(before))[:top_k],
            "rerank_actual_input": ids(candidates(rerank_input)) == ids(candidates(fused))}
        internal = select("R-RERANK", operations=("noop_after_top_k", "cross_encoder_after_top_k"))
        rerank_k = internal.payload.get("top_k") if internal else None
        if isinstance(rerank_k, int) and not isinstance(rerank_k, bool):
            checks["rerank_declared_cardinality"] = len(target) == min(rerank_k, len(candidates(rerank_input)))
        else:
            return missing(sources, targets, "RERANK_TOP_K_NOT_PROVEN")
        actual_input = candidates(rerank_input)
        content_checks, reason = _preserved_source_content(actual_input, target, grain="parent")
        if reason:
            return missing(sources, targets, reason)
        checks.update(content_checks)
        input_checks, reason = _preserved_source_content(candidates(fused), actual_input, grain="parent")
        if reason:
            return missing(sources, targets, reason)
        checks.update({"rerank_input:" + key: value for key, value in input_checks.items()})
        requests = [stage for stage in stages if stage.payload.get("evidence_key") == "R-RERANK"
            and stage.payload.get("phase") == "REQUEST"
            and (stage.payload.get("_binding") or {}).get("component_operation") in {"noop_rerank", "parent_cross_encoder_request"}]
        if len(requests) != 1:
            return missing(sources, targets, "RERANK_TEXT_FIELD_REQUEST_NOT_PROVEN")
        request = requests[0].payload
        request_operation = (request.get("_binding") or {}).get("component_operation")
        internal_operation = (internal.payload.get("_binding") or {}).get("component_operation")
        expected_request = "noop_rerank" if internal_operation == "noop_after_top_k" else "parent_cross_encoder_request"
        checks["rerank_request_producer"] = request_operation == expected_request
        checks["rerank_request_query"] = request.get("query") == rerank_input.query
        checks["rerank_request_model"] = request.get("model") == internal.payload.get("model")
        if not isinstance(request.get("query"), str) or not request["query"].strip():
            return missing(sources, targets, "RERANK_REQUEST_QUERY_NOT_PROVEN")
        field = request.get("text_field")
        if not isinstance(field, str) or not field:
            return missing(sources, targets, "RERANK_TEXT_FIELD_REQUEST_NOT_PROVEN")
        from rag.reranker.parent_child_reranker import _get_candidate_text
        texts = [_get_candidate_text(row, field) for row in actual_input]
        input_by_id = {candidate_key(row, grain="parent"): row for row in actual_input}
        fused_by_id = {candidate_key(row, grain="parent"): row for row in candidates(fused)}
        checks["rerank_active_input_text"] = all(candidate_key(row, grain="parent") in fused_by_id
            and _get_candidate_text(row, field) == _get_candidate_text(fused_by_id[candidate_key(row, grain="parent")], field) for row in actual_input)
        checks["rerank_active_selected_text"] = all(candidate_key(row, grain="parent") in input_by_id
            and _get_candidate_text(row, field) == _get_candidate_text(input_by_id[candidate_key(row, grain="parent")], field) for row in target)
        if request_operation != expected_request:
            return finish(actual_input, target, sources + requests, targets, grain="parent", checks=checks)
        actual_texts = request.get("actual_texts") if expected_request == "noop_rerank" else None
        if expected_request == "parent_cross_encoder_request" and isinstance(request.get("actual_pairs"), list):
            pairs = request["actual_pairs"]
            checks["rerank_request_pairs"] = all(isinstance(pair, list) and len(pair) == 2 and pair[0] == rerank_input.query for pair in pairs)
            actual_texts = [pair[1] for pair in pairs if isinstance(pair, list) and len(pair) == 2]
            if "actual_texts" in request:
                checks["rerank_request_representations"] = request["actual_texts"] == actual_texts
        if not isinstance(actual_texts, list) or not isinstance(request.get("input_ids"), list):
            return missing(sources, targets, "RERANK_REQUEST_CONTENT_NOT_PROVEN")
        checks["rerank_request_text"] = texts == actual_texts
        checks["rerank_request_ids"] = ids(actual_input) == request["input_ids"]
        return finish(actual_input, target, sources + requests, targets, grain="parent", checks=checks)

    if edge == "X06":
        def endpoint(key, phase, operation):
            rows = [stage for stage in stages if stage.payload.get("evidence_key") == key
                    and stage.payload.get("phase", "RESULT") == phase
                    and isinstance(stage.payload.get("candidates"), list)
                    and (stage.payload.get("_binding") or {}).get("component_operation") == operation]
            return rows[0] if len(rows) == 1 else None

        corrected = [stage for stage in stages if stage.payload.get("evidence_key") == "R-CORRECT"
                     and stage.payload.get("phase") == "RESULT"
                     and isinstance(stage.payload.get("candidates"), list)]
        reranked = corrected[0] if len(corrected) == 1 else endpoint("R-RERANK", "RESULT", "initial_rerank_output")
        before = endpoint("R-CRAG", "INPUT", "final_filter_input")
        filtered = endpoint("R-CRAG", "RESULT", "final_filter_output")
        governance_input = endpoint("R-GOV", "INPUT", "governance_input")
        allowed = endpoint("R-GOV", "RESULT", "governance_output")
        sources, targets = [reranked, before, filtered, governance_input], [allowed]
        if len(corrected) > 1:
            return missing(corrected + sources, targets, "CORRECTIVE_SOURCE_BINDING_AMBIGUOUS")
        if corrected:
            identity = reranked.payload.get("_binding") or {}
            round_index = identity.get("round")
            if not isinstance(round_index, int) or isinstance(round_index, bool) or round_index < 1 or identity.get("component_operation") != "correction:" + str(round_index):
                return missing(sources, targets, "CORRECTIVE_SOURCE_BINDING_NOT_PROVEN")
        if not complete(sources + targets):
            return missing(sources, targets)
        queries = [stage.query for stage in sources + targets]
        if any(not isinstance(query, str) or not query.strip() for query in queries) or len(set(queries)) != 1:
            return missing(sources, targets, "OPERATION_QUERY_IDENTITY_NOT_PROVEN")
        ids = lambda rows: [candidate_key(row) for row in rows]
        for stage in sources + targets:
            rows = candidates(stage)
            if any(not isinstance(row, Mapping) or not candidate_key(row) for row in rows):
                return missing(sources, targets, "CANDIDATE_IDENTITY_NOT_PROVEN")
            if len(set(ids(rows))) != len(rows):
                return missing(sources, targets, "SOURCE_CONTENT_BINDING_AMBIGUOUS")
        policy = allowed.payload.get("governance")
        eligible = policy.get("eligible_candidate_ids") if isinstance(policy, Mapping) else None
        if not isinstance(eligible, list):
            return missing(sources, targets, "GOVERNANCE_ELIGIBILITY_ENDPOINT_NOT_RECORDED")
        checks = {"final_crag_input": ids(candidates(before)) == ids(candidates(reranked)),
            "crag_subset": set(ids(candidates(filtered))) <= set(ids(candidates(before))),
            "governance_actual_input": ids(candidates(governance_input)) == ids(candidates(filtered)),
            "eligibility_actual_output": set(ids(candidates(allowed))) == set(eligible),
            "eligibility_in_input": set(eligible) <= set(ids(candidates(governance_input)))}
        endpoints = sources + targets
        content_checks, reasons = {}, []
        for index, (producer, consumer) in enumerate(zip(endpoints, endpoints[1:])):
            segment, reason = _preserved_source_content(candidates(producer), candidates(consumer),
                grain="child", context_text=True, allow_governance_annotation=index == 3)
            prefix = str((consumer.payload.get("_binding") or {}).get("component_operation")) + ":"
            content_checks.update({prefix + key: value for key, value in segment.items()})
            if reason:
                reasons.append(reason)
        # A uniquely bound, proved mismatch survives another segment's missing proof.
        # Structural membership checks alone cannot establish source-body fidelity.
        if reasons and all(content_checks.values()):
            return missing(sources, targets, reasons[0])
        checks.update(content_checks)
        return finish(candidates(before), candidates(allowed), sources, targets, checks=checks)

    allowed, packed = select("R-GOV"), select("R-PACK")
    sources, targets = [allowed], [packed]
    if not complete(sources + targets):
        return missing(sources, targets)
    pack = packed.payload.get("context_pack", packed.payload)
    target, context = pack.get("selected_results"), pack.get("context", pack.get("actual_context"))
    if not isinstance(target, list) or not isinstance(context, str) or any(not isinstance(row, Mapping) or "prompt_visible_text" not in row for row in target):
        return missing(sources, targets, "ACTUAL_PROMPT_PROJECTION_NOT_RECORDED")
    required = {candidate_key(row): str(row["prompt_visible_text"]) for row in target}
    checks = {"selected_text_in_actual_context": all(text and text in context for text in required.values()),
        "selection_count": pack.get("selected_count", len(target)) == len(target),
        "no_duplicate_selection": len(target) == len(required)}
    fidelity, reason = _pack_fidelity(candidates(allowed), target, context, pack)
    if reason:
        return missing(sources, targets, reason)
    checks.update(fidelity)
    return finish(candidates(allowed), target, sources, targets, checks=checks, required_text=required)


def _retrieval_metrics(batch):
    metrics = []
    by_key = {key: [stage.payload for stage in batch.stages if stage.payload.get("evidence_key") == key and stage.evidence_refs] for key in KEY_UNITS}
    def add(name, unit, value=None, complete=False, reason="FINITE_POOL_OR_REFERENCE_NOT_PROVEN", **kwargs):
        metrics.append(MetricResult(name, unit, value=value, measurement_state="MEASURED" if complete else "UNMEASURED",
            reason="" if complete else reason, representation="DERIVED", **kwargs))
    for key in ("R-DENSE", "R-BM25", "R-SOURCE-FUSION", "R-PARENT", "R-QUERY-FUSION", "R-RERANK", "R-CRAG", "R-CORRECT", "R-GOV"):
        pools = [payload for payload in by_key[key] if "candidates" in payload]
        add(key + "_finite_pool_counts", KEY_UNITS[key], [{"phase": payload.get("phase"), "query": payload.get("query"),
            "query_index": payload.get("query_index"), "round": payload.get("round"), "count": len(payload["candidates"]),
            "complete": payload.get("complete")} for payload in pools] or None,
            bool(pools and all(payload.get("complete") for payload in pools)))
    dense_scopes = [payload.get("scope") for payload in by_key["R-DENSE"] if "candidates" in payload]
    keyword_scopes = [payload.get("scope") for payload in by_key["R-BM25"] if "candidates" in payload]
    known_scope = bool(dense_scopes and keyword_scopes and all(scope is not None for scope in dense_scopes + keyword_scopes))
    add("retriever_scope_equality", "C01", dense_scopes == keyword_scopes if known_scope else None, known_scope)
    selections = []
    for payload in by_key["R-QUERY"]:
        plan, expansion = payload.get("retrieval_plan"), payload.get("expansion")
        if isinstance(plan, Mapping) and isinstance(expansion, Mapping):
            selections.append(plan.get("query_transform_mode") == expansion.get("strategy"))
    add("query_rule_conformance", "C02", selections or None, bool(selections))
    ordered_queries = [payload.get("ordered_queries") for payload in by_key["R-QUERY"] if payload.get("ordered_queries") is not None]
    add("query_duplicate_count", "C03", [len(queries) - len(set(queries)) for queries in ordered_queries] or None, bool(ordered_queries))
    add("query_constraint_retention", "C03", reason="INDEPENDENT_QUERY_CONSTRAINT_REFERENCE_REQUIRED")
    for unit in ("C04", "C05", "C06", "C08"):
        add("rank_quality", unit, reason="INDEPENDENT_G2_REVIEWED_JUDGED_REFERENCE_REQUIRED")
    parent_pools = [payload for payload in by_key["R-PARENT"] if payload.get("phase") == "RESULT" and "candidates" in payload]
    add("parent_backfill_missing", "C07", [[candidate_key(item, grain="parent") for item in payload["candidates"] if (item.get("metadata") or {}).get("parent_found") is False] for payload in parent_pools] or None,
        bool(parent_pools and all(payload.get("complete") for payload in parent_pools)))
    token_inputs = [payload for payload in by_key["R-RERANK"] if payload.get("phase") == "TOKENIZED"]
    add("actual_rerank_token_summary", "C08", token_inputs or None, bool(token_inputs))
    add("rerank_required_span_visibility", "C08", reason="TRUSTED_TOKENIZER_OFFSET_AND_INDEPENDENT_SPAN_REQUIRED")
    add("relevance_confusion", "C09", reason="INDEPENDENT_G2_JUDGMENTS_REQUIRED")
    beginnings = [payload for payload in by_key["R-CORRECT"] if payload.get("phase") == "BEGIN"]
    endings = [payload for payload in by_key["R-CORRECT"] if payload.get("phase") == "RESULT"]
    decisions = [payload for payload in by_key["R-CORRECT"] if payload.get("phase") == "DECISION"]
    if decisions:
        add("correction_execution", "C10", {"triggered_rounds": len(beginnings), "completed_rounds": len(endings),
            "partial_rounds": len(beginnings) - len(endings), "not_triggered_is_success": False}, True)
    else:
        add("correction_execution", "C10")
    add("wrong_use_allow_reject", "C11", reason="INDEPENDENT_G2_USE_LABELS_REQUIRED")
    packs = []
    for payload in by_key["R-PACK"]:
        pack = payload.get("context_pack", payload)
        selected = pack.get("selected_results")
        if isinstance(selected, list):
            context = pack.get("context", pack.get("actual_context"))
            packs.append({"selected_ids": [candidate_key(item) for item in selected], "visible_text_recorded": all("prompt_visible_text" in item for item in selected),
                "actual_context_recorded": isinstance(context, str), "chars": len(context) if isinstance(context, str) else None})
    add("actual_prompt_visible_projection", "C12", packs or None, bool(packs and all(pack["visible_text_recorded"] and pack["actual_context_recorded"] for pack in packs)))
    add("prompt_useful_recall", "C12", reason="INDEPENDENT_ALLOWED_SOURCE_EVIDENCE_REFERENCE_REQUIRED")
    return metrics


def evaluate_retrieval(snapshot: SnapshotView, *, subject_id=None, transitions=()) -> ObservationBatch:
    batch = extract_retrieval(snapshot, subject_id=subject_id)
    for transition in transitions:
        result = evaluate_pool_transition(**{key: value for key, value in transition.items() if key not in {"edge_id", "unit_id"}})
        edge_id = transition.get("edge_id", "X07")
        batch.edges.append(EdgeObservation(edge_id, snapshot.snapshot_id,
            allowed_transformation=transition.get("allowed_transformation", "IDENTITY"),
            measurement_state=result["state"], assertion_result=result["assertion"], payload=result))
        if result["assertion"] is False:
            batch.findings.append(Finding("RETRIEVAL_EDGE_LOSS", snapshot.snapshot_id,
                "Observed finite source/target edge failed independent expectation", edge_id=edge_id,
                evidence_level="MECHANISM", candidate_causes=("SELECTION_OR_REPRESENTATION_LOSS",)))
    return batch
