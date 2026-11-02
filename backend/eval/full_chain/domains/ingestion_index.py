"""Read-only ingestion/index observations and deterministic edge checks.

Input populations come from the captured discovery operation, never a new scan.
Source names preserve Router case-sensitive selection and do not collapse equal
content hashes. No function in this module dispatches a parser or an encoder.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping, Sequence

from ..contracts import (EdgeObservation, EvaluationEvidenceRef, Finding,
                         MetricResult, ObservationBatch, StageObservation, canonical_hash)
from ..snapshot import SnapshotView, diagnostic_capture_complete, original_diagnostic_frame


KEY_UNITS = {"I-INPUT-REQUEST": "A01", "I-PREFILTER": "A01", "I-SOURCE": "A01",
             "I-PARSED": "A02", "I-CLEAN": "A03", "I-PROFILE": "A04",
             "I-CHUNK": "A05", "I-ENCODE": "A06", "I-INDEX": "A07", "I-LINEAGE": "A07"}
KNOWN_DISPOSITIONS = frozenset({"ROUTED_SUCCESS", "ROUTED_FAILED", "PLANNED_DRY_RUN",
    "ROUTED_SKIPPED", "SKIPPED_NOT_SELECTED", "UNSUPPORTED_FORMAT", "MISSING_REQUESTED",
    "INACCESSIBLE", "IDENTITY_COLLISION", "SOURCE_IDENTITY_MISMATCH"})


def classify_input_disposition(entry: Mapping[str, Any], outcome: Mapping[str, Any] | None = None) -> str:
    """Classify only facts from the same request/inventory snapshot."""
    outcome = outcome or {}
    if entry.get("selected") is False:
        return "SKIPPED_NOT_SELECTED"
    if entry.get("accessible") is False or entry.get("access_state") == "INACCESSIBLE":
        return "INACCESSIBLE"
    if entry.get("exists") is False and entry.get("existence_reliable") is True:
        return "MISSING_REQUESTED"
    if entry.get("exists") is not True or entry.get("regular_file") is not True:
        return "UNKNOWN_EVIDENCE_GAP"
    if entry.get("supported") is False:
        return "UNSUPPORTED_FORMAT"
    error = outcome.get("error") or {}
    error_type = error.get("type") if isinstance(error, Mapping) else None
    identity = outcome.get("identity_disposition")
    if identity == "IDENTITY_COLLISION" or error_type == "DocIdCollisionError":
        return "IDENTITY_COLLISION"
    if identity == "SOURCE_IDENTITY_MISMATCH" or error_type == "SourceIdentityError":
        return "SOURCE_IDENTITY_MISMATCH"
    status = outcome.get("status")
    if status == "success":
        return "ROUTED_SUCCESS"
    if status == "failed":
        return "ROUTED_FAILED"
    if status == "planned":
        return "PLANNED_DRY_RUN"
    if isinstance(status, str) and status.startswith("skipped"):
        return "ROUTED_SKIPPED"
    return "UNKNOWN_EVIDENCE_GAP"


def _captured(snapshot: SnapshotView):
    pools = {}
    for source in snapshot.sources.values():
        rows = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            row = original_diagnostic_frame(row)
            if row.get("event_type") == "POOL_BEGIN" and row.get("evidence_key") in KEY_UNITS:
                pools[row.get("pool_id")] = {"header": row, "items": [], "ordinals": [], "end": None, "ref": source.evidence}
            elif row.get("event_type") == "POOL_ITEM" and row.get("pool_id") in pools:
                pool = pools[row["pool_id"]]; pool["items"].append(row.get("item")); pool["ordinals"].append(row.get("ordinal"))
            elif row.get("event_type") == "POOL_END" and row.get("pool_id") in pools:
                pools[row["pool_id"]]["end"] = row
            key = row.get("evidence_key") or row.get("event_type")
            if key in KEY_UNITS and row.get("event_type") != "POOL_BEGIN":
                payload = row.get("payload", row)
                if isinstance(payload, Mapping):
                    yield key, payload, row, replace(source.evidence, pointer=f"/{ordinal}")
            elif source.family in {"ingestion", "document_router_report_v1"} and "documents" in row:
                yield "I-SOURCE", {"documents": row["documents"], "phase": "RESULT"}, row, source.evidence
            elif source.family in {"parsed", "normalized", "profiles", "chunks", "encoding", "index_manifest"}:
                key = {"parsed": "I-PARSED", "normalized": "I-CLEAN", "profiles": "I-PROFILE",
                       "chunks": "I-CHUNK", "encoding": "I-ENCODE", "index_manifest": "I-INDEX"}[source.family]
                yield key, row, row, source.evidence
            elif source.family == "ingestion_lineage":
                yield "I-LINEAGE", row, row, source.evidence
    for pool in pools.values():
        header, items, end = pool["header"], pool["items"], pool["end"]
        digest = hashlib.sha256(b"".join(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n" for item in items)).hexdigest()
        complete = bool(end and end.get("actual_count") == len(items) == header.get("declared_count")
            and pool["ordinals"] == list(range(len(items))) and end.get("end_digest") == digest and end.get("completeness") == "COMPLETE")
        yield header["evidence_key"], {**header, "records": items, "complete": complete}, header, pool["ref"]


def _population(request, inventory, outcomes, snapshot_id):
    entries = {str(item["name"]): dict(item) for item in inventory if "name" in item}
    explicit = request.get("include_mode") == "EXPLICIT"
    requested = list(dict.fromkeys(request.get("requested_names") or [])) if explicit else None
    obligations = requested if explicit else [name for name, item in entries.items() if item.get("regular_file") is True]
    complete = request.get("complete") is True and request.get("inventory_complete") is True
    subjects = []
    for name in list(entries) + [name for name in (requested or []) if name not in entries]:
        entry = entries.get(name, {"name": name, "selected": True, "exists": False if complete else None,
                                  "existence_reliable": complete, "regular_file": False})
        entry["selected"] = (name in requested) if explicit else entry.get("selected", True)
        disposition = classify_input_disposition(entry, outcomes.get(name))
        subjects.append({**entry, "subject_id": canonical_hash([snapshot_id, request.get("root_token"), name]),
            "route_obligation": name in obligations, "disposition": disposition,
            "production_outcome": outcomes.get(name),
            "reason": "NOT_REGULAR_FILE" if entry.get("exists") is True and entry.get("regular_file") is False else None})
    return subjects, obligations, complete


def extract_ingestion(snapshot: SnapshotView, *, subject_id=None) -> ObservationBatch:
    batch = ObservationBatch("ingestion_index", limitations=list(snapshot.limitations))
    seen, request, inventory, outcomes = set(), {}, [], {}
    request_refs, source_refs = [], []
    capture_complete = diagnostic_capture_complete(snapshot)
    if not capture_complete:
        batch.limitations.append("CAPTURE_INCOMPLETE")
    for key, payload, row, ref in _captured(snapshot):
        seen.add(key)
        batch.evidence.append(ref)
        if key == "I-INPUT-REQUEST":
            request.update(payload); request_refs.append(ref)
        if key == "I-PREFILTER":
            inventory.extend(payload.get("entries") or ([payload] if "name" in payload else []))
            if payload.get("phase") == "END":
                request["inventory_complete"] = payload.get("complete") is True
            request_refs.append(ref)
        if key == "I-SOURCE":
            for item in payload.get("documents") or ([payload] if "source_name" in payload else []):
                outcomes[str(item["source_name"])] = dict(item)
            source_refs.append(ref)
        failed = payload.get("phase") == "ERROR"
        actual_operation = row.get("event_type") is not None
        state = "TECHNICAL_FAILURE" if failed else ("MEASURED" if capture_complete and payload.get("complete", True) else "UNMEASURED")
        batch.stages.append(StageObservation(KEY_UNITS[key], str(row.get("subject_id") or snapshot.snapshot_id),
            operation_id=str(row.get("operation_ref") or row.get("pool_id") or key),
            source_segment=str(row.get("segment_id") or "snapshot"), executed=True if actual_operation else None,
            observed_execution_status="FAILED" if failed else ("OBSERVED" if actual_operation else "ASSET_DEPENDENCY"),
            measurement_state=state,
            evidence_refs=(ref,), payload={"evidence_key": key, **dict(payload)}))
    for key, unit in KEY_UNITS.items():
        if key not in seen:
            batch.stages.append(StageObservation(unit, snapshot.snapshot_id, operation_id=key,
                payload={"evidence_key": key, "availability": "NOT_RECORDED"}))
            batch.limitations.append("NOT_RECORDED:" + key)
    subjects, obligations, complete = _population(request, inventory, outcomes, snapshot.snapshot_id)
    complete = complete and capture_complete
    known = sum(subject["disposition"] in KNOWN_DISPOSITIONS for subject in subjects if subject["route_obligation"])
    state = "UNMEASURED" if not complete else ("N/A" if not obligations else "MEASURED")
    batch.metrics.append(MetricResult("input_disposition_coverage", "A01", value=known / len(obligations) if state == "MEASURED" else None,
        numerator=known, denominator=len(obligations) if complete else None, measurement_state=state,
        reason="REVIEWED_EMPTY_REQUEST_SET" if state == "N/A" else ("U0_U1_CAPTURE_INCOMPLETE" if not complete else ""),
        input_completeness="COMPLETE" if complete else "UNKNOWN", representation="DERIVED"))
    batch.metrics.append(MetricResult("disposition_counts", "A01", value=dict(Counter(subject["disposition"] for subject in subjects if subject["route_obligation"])),
        measurement_state="MEASURED" if complete else "UNMEASURED", representation="DERIVED"))
    batch.metrics.append(MetricResult("routed_outcome_counts", "A01", value=dict(Counter(item.get("status", "unknown") for item in outcomes.values())),
        measurement_state="MEASURED" if outcomes else "UNMEASURED", representation="DERIVED"))
    batch.stages.append(StageObservation("A01", snapshot.snapshot_id, operation_id="U0_U4_POPULATION",
        measurement_state=state, evidence_refs=tuple(request_refs + source_refs),
        payload={"subjects": subjects, "route_obligation_population": obligations,
                 "inventory_population": [item["name"] for item in subjects], "complete": complete}))
    batch.edges.append(EdgeObservation("X01", snapshot.snapshot_id, source_refs=tuple(request_refs), target_refs=tuple(source_refs),
        allowed_transformation="REQUEST_PREFILTER_DISCOVERY_IDENTITY_ROUTING", measurement_state=state,
        assertion_result=known == len(obligations) if state == "MEASURED" else None,
        availability="PERSISTED" if request_refs else "NOT_RECORDED", completeness="COMPLETE" if complete else "UNKNOWN",
        reason="" if complete else "PREFILTER_POPULATION_NOT_PROVEN", payload={"subjects": subjects}))
    batch.metrics.extend(_asset_metrics(batch))
    lineage_stages = [stage for stage in batch.stages if stage.payload.get("evidence_key") == "I-LINEAGE" and stage.evidence_refs]
    for stage in lineage_stages:
        result = check_lineage_endpoints(stage.payload.get("source"), stage.payload.get("target"),
            schema_version=stage.payload.get("schema_version"), complete=capture_complete and stage.payload.get("complete", True))
        batch.edges.append(EdgeObservation("X02", snapshot.snapshot_id, source_key=stage.key, target_key=stage.key,
            source_refs=tuple(replace(ref, pointer=ref.pointer.rstrip("/") + "/source") for ref in stage.evidence_refs),
            target_refs=tuple(replace(ref, pointer=ref.pointer.rstrip("/") + "/target") for ref in stage.evidence_refs),
            allowed_transformation="SOURCE_PARSER_CHUNK_VECTOR_BM25_SCOPE_LINEAGE", measurement_state=result["state"],
            assertion_result=result["assertion"], availability="PERSISTED", completeness="COMPLETE" if result["state"] == "MEASURED" else "UNKNOWN",
            reason=result.get("reason", ""), payload=result))
    if not any(edge.edge_id == "X02" for edge in batch.edges):
        batch.edges.extend(_native_lineage_edges(snapshot, capture_complete))
    if not any(edge.edge_id == "X02" for edge in batch.edges):
        chunk_refs = tuple(ref for stage in batch.stages if stage.unit_id == "A05" for ref in stage.evidence_refs)
        index_refs = tuple(ref for stage in batch.stages if stage.unit_id in {"A06", "A07"} for ref in stage.evidence_refs)
        batch.edges.append(EdgeObservation("X02", snapshot.snapshot_id, source_refs=chunk_refs, target_refs=index_refs,
            allowed_transformation="CHILD_TO_VECTOR_BM25_ROW_LINEAGE", reason="EXACT_ROW_MAP_REQUIRED"))
    return batch


def _native_lineage_edges(snapshot, complete):
    """Read original Router/cleaned-unit/chunk and finite row assets directly.

    These are row identity checks only. Existing assets do not prove an index
    build, vector quality, source semantics, or activation occurred.
    """
    native_families = {"document_router_report_v1", "normalized", "chunks", "vector_rows", "bm25_rows"}
    sources = [source for source in snapshot.sources.values() if source.family in native_families]
    if not any(source.family in {"vector_rows", "bm25_rows"} for source in sources):
        return []
    by_family = {family: [] for family in native_families}
    refs = {family: [] for family in native_families}
    for source in sources:
        rows = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        if not rows:
            refs[source.family].append(source.evidence)
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            ref = replace(source.evidence, pointer=f"/{index}" if isinstance(source.data, (list, tuple)) else "")
            by_family[source.family].append(row)
            refs[source.family].append(ref)
    documents = [row for report in by_family["document_router_report_v1"] for row in report.get("documents", []) if isinstance(row, Mapping)]
    units = by_family["normalized"]
    children = [row for row in by_family["chunks"] if row.get("child_chunk_id")]
    result = []
    for doc_id in dict.fromkeys([row.get("doc_id") for row in children] or [row.get("doc_id") for row in units]):
        doc_sources = [row for row in documents if row.get("doc_id") == doc_id]
        doc_units = [row for row in units if row.get("doc_id") == doc_id]
        doc_children = [row for row in children if row.get("doc_id") == doc_id]
        vectors = [row for row in by_family["vector_rows"] if row.get("doc_id") == doc_id]
        bm25 = [row for row in by_family["bm25_rows"] if row.get("doc_id") == doc_id]
        missing = (not complete or not doc_id or len(doc_sources) != 1 or not doc_units or not doc_children
            or not refs["vector_rows"] or not refs["bm25_rows"])
        required = lambda rows, fields: all(all(row.get(field) is not None for field in fields) for row in rows)
        missing = missing or not required(doc_children + vectors + bm25, ("child_chunk_id", "parent_chunk_id", "tenant_id", "kb_id"))
        sha = doc_sources[0].get("source_sha256") if len(doc_sources) == 1 else None
        parsed_shas = [(row.get("extra") or {}).get("document_profile", {}).get("source_sha256") for row in doc_units]
        scopes = [{"tenant_id": row.get("tenant_id"), "kb_id": row.get("kb_id")} for row in doc_units]
        missing = missing or not sha or not parsed_shas or any(value is None for value in parsed_shas) or not required(doc_units, ("tenant_id", "kb_id"))
        checks = {}
        if not missing:
            child_ids = [row["child_chunk_id"] for row in doc_children]
            parent_map = {row["child_chunk_id"]: row["parent_chunk_id"] for row in doc_children}
            checks = {"parsed_source_identity": all(value == sha for value in parsed_shas),
                "source_scope": all(scope == scopes[0] for scope in scopes),
                "child_scope": all({key: row[key] for key in scopes[0]} == scopes[0] for row in doc_children),
                "vector_row_identity": [row["child_chunk_id"] for row in vectors] == child_ids,
                "bm25_identity": [row["child_chunk_id"] for row in bm25] == child_ids,
                "unique_child_rows": len(child_ids) == len(set(child_ids)),
                "vector_parent_map": {row["child_chunk_id"]: row["parent_chunk_id"] for row in vectors} == parent_map,
                "bm25_parent_map": {row["child_chunk_id"]: row["parent_chunk_id"] for row in bm25} == parent_map,
                "row_scope": all({key: row[key] for key in scopes[0]} == scopes[0] for row in vectors + bm25)}
        source_refs = tuple(ref for family in ("document_router_report_v1", "normalized", "chunks") for ref in refs[family])
        target_refs = tuple(ref for family in ("vector_rows", "bm25_rows") for ref in refs[family])
        result.append(EdgeObservation("X02", snapshot.snapshot_id, source_refs=source_refs, target_refs=target_refs,
            allowed_transformation="NATIVE_SOURCE_CLEANED_CHUNK_VECTOR_BM25_ROW_LINEAGE",
            measurement_state="UNMEASURED" if missing else "MEASURED", assertion_result=None if missing else all(checks.values()),
            availability="PERSISTED", completeness="UNKNOWN" if missing else "COMPLETE",
            reason="REQUIRED_NATIVE_LINEAGE_SOURCE_MISSING_OR_AMBIGUOUS" if missing else "",
            payload={"basis": "NATIVE_ROUTER_CLEANED_CHUNK_AND_FINITE_ROW_ASSETS", "doc_id": doc_id,
                "checks": checks, "failed_checks": [key for key, value in checks.items() if not value],
                "build_activation_state": "UNMEASURED", "reference_quality_state": "UNMEASURED"}))
    return result


def check_lineage_endpoints(source, target, *, schema_version="ingestion_lineage_v1", complete=True):
    """Compute X02 from captured owner fields; ignore caller verdict labels."""
    source_fields = ("source_sha256", "scope", "child_ids", "parent_child_map")
    target_fields = ("parsed_source_sha256", "scope", "vector_row_child_ids", "bm25_child_ids", "parent_child_map")
    if not complete or schema_version != "ingestion_lineage_v1" or not isinstance(source, Mapping) or not isinstance(target, Mapping):
        return {"state": "UNMEASURED", "assertion": None, "reason": "EXACT_LINEAGE_ENDPOINTS_NOT_PROVEN"}
    if any(field not in source or source[field] is None for field in source_fields) or any(field not in target or target[field] is None for field in target_fields):
        return {"state": "UNMEASURED", "assertion": None, "reason": "REQUIRED_LINEAGE_FIELD_MISSING"}
    if not isinstance(source["scope"], Mapping) or not isinstance(target["scope"], Mapping) or not isinstance(source["parent_child_map"], Mapping) or not isinstance(target["parent_child_map"], Mapping):
        return {"state": "TECHNICAL_FAILURE", "assertion": None, "reason": "INVALID_LINEAGE_FIELD_SHAPE"}
    child_ids = source["child_ids"]
    rows, bm25 = target["vector_row_child_ids"], target["bm25_child_ids"]
    if any(not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) for values in (child_ids, rows, bm25)):
        return {"state": "TECHNICAL_FAILURE", "assertion": None, "reason": "FINITE_ORDERED_ID_LIST_REQUIRED"}
    checks = {"parsed_source_identity": source["source_sha256"] == target["parsed_source_sha256"],
        "scope": source["scope"] == target["scope"], "vector_row_identity": child_ids == rows,
        "bm25_identity": child_ids == bm25, "parent_child_map": source["parent_child_map"] == target["parent_child_map"],
        "unique_child_rows": len(child_ids) == len(set(child_ids)) == len(rows) == len(set(rows)),
        "all_children_mapped": set(child_ids) == set(source["parent_child_map"]) == set(target["parent_child_map"])}
    return {"state": "MEASURED", "assertion": all(checks.values()), "checks": checks,
        "source": dict(source), "target": dict(target), "failed_checks": [key for key, passed in checks.items() if not passed]}


def _asset_metrics(batch):
    metrics = []
    payloads = {unit: [stage.payload for stage in batch.stages if stage.unit_id == unit and stage.evidence_refs] for unit in ("A02", "A03", "A04", "A05", "A06", "A07")}
    def metric(name, unit, value=None, measured=False, reason="SOURCE_NOT_RECORDED", **kwargs):
        metrics.append(MetricResult(name, unit, value=value, measurement_state="MEASURED" if measured else "UNMEASURED",
            reason="" if measured else reason, representation="DERIVED", **kwargs))
    reports = payloads["A02"]
    page_metrics = []
    for report in reports:
        coverage = report.get("coverage") or {}
        total = coverage.get("source_page_count", report.get("source_page_count"))
        extracted = coverage.get("page_count", report.get("extracted_page_count"))
        page_metrics.append({"source_pages": total, "extracted_pages": extracted,
            "ratio": extracted / total if isinstance(total, int) and total > 0 and isinstance(extracted, int) else None,
            "nature": "STRUCTURAL_PROXY"})
    metric("page_body_coverage", "A02", page_metrics or None, bool(page_metrics and all(item["ratio"] is not None for item in page_metrics)))
    metric("normalized_text_chars", "A03", [len(str(item.get("text") or "")) for item in payloads["A03"]] or None,
        bool(payloads["A03"]), reason="NORMALIZED_UNITS_NOT_RECORDED")
    metric("source_fact_retention", "A03", reason="INDEPENDENT_G1_REFERENCE_REQUIRED")
    profiles = [item.get("document_profile", item) for item in payloads["A04"]]
    metric("profile_gold_leakage", "A04", [profile.get("document_id") for profile in profiles if profile.get("is_gold_reference") and profile.get("should_index")] if profiles else None,
        bool(profiles))
    metric("profile_accuracy", "A04", reason="INDEPENDENT_USE_REFERENCE_REQUIRED")
    chunks = [record for item in payloads["A05"] for record in (item.get("records") or [item]) if "text" in record]
    metric("chunk_length_chars", "A05", {"parent": [len(str(item["text"])) for item in chunks if item.get("parent_chunk_id") and not item.get("child_chunk_id")],
        "child": [len(str(item["text"])) for item in chunks if item.get("child_chunk_id")], "nature": "CHARS_PROXY"} if chunks else None, bool(chunks))
    metric("span_coverage", "A05", reason="REVIEWED_SOURCE_SPAN_MAPPING_REQUIRED")
    encodings = payloads["A06"]
    measured_vectors = [item for item in encodings if "values" in item and "expected_child_ids" in item and "row_child_ids" in item]
    results = [vector_integrity(item["values"], item["expected_child_ids"], item["row_child_ids"],
        expected_dim=item.get("expected_dim"), normalized=item.get("normalized")) for item in measured_vectors]
    metric("vector_integrity", "A06", results or None, bool(results))
    metric("encode_efficiency", "A06", reason="ACTUAL_BUILD_INTERVAL_REQUIRED")
    manifests = payloads["A07"]
    metric("index_asset_manifest", "A07", [{"index_version": item.get("index_version"), "source_hash": item.get("source_hash"),
        "child_count": item.get("child_chunk_count", item.get("child_count")), "activation": item.get("activation_ref"),
        "process_execution_proven": item.get("phase") == "RESULT" and "manifest_ref" in item} for item in manifests] or None, bool(manifests))
    return metrics


def check_source_asset_edges(*, source_facts=None, candidate_facts=None, source_spans=None,
                            candidate_spans=None, expected_rows=None, actual_rows=None,
                            dense_scope=None, bm25_scope=None, profiles=(), build_elapsed=None,
                            tokenizer_evidence=None, reference_reviewed=False, synthetic=False) -> dict[str, Any]:
    """Compare explicit independent expectations; never infer facts from substrings."""
    checks = {}
    if (reference_reviewed or synthetic) and source_facts is not None and candidate_facts is not None:
        fields = ("subject", "value", "unit", "negated", "relation")
        expected = {canonical_hash([fact.get(k) for k in fields]) for fact in source_facts}
        actual = {canonical_hash([fact.get(k) for k in fields]) for fact in candidate_facts}
        checks["source_fact_retention"] = {"state": "MEASURED" if expected else "N/A",
            "value": len(expected & actual) / len(expected) if expected else None,
            "missing": sorted(expected - actual), "added": sorted(actual - expected)}
    else:
        checks["source_fact_retention"] = {"state": "UNMEASURED", "value": None}
    checks["span_coverage"] = {"state": "UNMEASURED", "value": None}
    if (reference_reviewed or synthetic) and source_spans is not None and candidate_spans is not None:
        expected = {canonical_hash(span) for span in source_spans}
        actual = {canonical_hash(span) for span in candidate_spans}
        checks["span_coverage"] = {"state": "MEASURED" if expected else "N/A", "value": len(expected & actual) / len(expected) if expected else None,
                                   "missing": sorted(expected - actual)}
    checks["row_identity"] = None if expected_rows is None or actual_rows is None else list(expected_rows) == list(actual_rows)
    checks["scope_equality"] = None if dense_scope is None or bm25_scope is None else dense_scope == bm25_scope
    checks["gold_leakage"] = [str(profile.get("document_id")) for profile in profiles if profile.get("is_gold_reference") is True and profile.get("should_index") is True]
    checks["build_process_state"] = "UNMEASURED" if build_elapsed is None else "MEASURED"
    checks["evidence_truncation"] = {"state": "UNMEASURED", "value": None}
    if tokenizer_evidence and tokenizer_evidence.get("trusted_asset_sha256") and tokenizer_evidence.get("actual_input_digest_verified") is True:
        spans = tokenizer_evidence.get("required_token_ranges")
        visible = tokenizer_evidence.get("visible_token_range")
        if spans is not None and visible is not None:
            excluded = [span for span in spans if span[0] < visible[0] or span[1] > visible[1]]
            checks["evidence_truncation"] = {"state": "MEASURED" if spans else "N/A", "value": len(excluded) / len(spans) if spans else None,
                                            "excluded_ranges": excluded}
    return checks


def stable_span_comparability(source, candidate) -> str:
    required = ("doc_id", "source_sha256", "unit_id", "start", "end")
    if any(key not in source or key not in candidate for key in required):
        return "NOT_COMPARABLE"
    return "COMPARABLE" if all(source[key] == candidate[key] for key in required) else "NOT_COMPARABLE"


def vector_integrity(vectors, expected_child_ids, actual_row_ids, *, expected_dim=None, normalized=None):
    """Pure array subset of OfflineIndexVerifier checks; no storage constructor."""
    import numpy as np
    array = np.asarray(vectors)
    checks = {"shape": array.ndim == 2 and array.shape[0] == len(expected_child_ids),
              "row_identity": list(expected_child_ids) == list(actual_row_ids),
              "finite": bool(np.isfinite(array).all())}
    checks["dimension"] = None if expected_dim is None else array.ndim == 2 and array.shape[1] == expected_dim
    checks["norm"] = None if normalized is None or array.ndim != 2 else bool(np.allclose(np.linalg.norm(array, axis=1), 1, atol=1e-3)) if normalized else True
    return {"assertion": all(value is not False for value in checks.values()), "checks": checks, "shape": list(array.shape),
            "calculator_ref": "OfflineIndexVerifier shape/finite/norm/row identity pure checks", "storage_opened": False}


def evaluate_ingestion_index(snapshot: SnapshotView, *, subject_id=None, **expectations) -> ObservationBatch:
    batch = extract_ingestion(snapshot, subject_id=subject_id)
    if expectations:
        checks = check_source_asset_edges(**expectations)
        batch.metrics.append(MetricResult("source_asset_checks", "A03", value=checks, measurement_state="MEASURED", representation="DERIVED"))
        false_checks = [key for key, value in checks.items() if value is False]
        for key in false_checks:
            batch.findings.append(Finding("INGESTION_ASSET_" + key.upper(), snapshot.snapshot_id,
                "Deterministic source/asset edge mismatch", edge_id="X02", evidence_level="MECHANISM"))
        batch.edges.append(EdgeObservation("X02", snapshot.snapshot_id, allowed_transformation="SOURCE_CHUNK_ROW_LINEAGE",
            measurement_state="MEASURED", assertion_result=not false_checks and not checks["gold_leakage"],
            availability="PERSISTED", completeness="COMPLETE", payload=checks))
    return batch
