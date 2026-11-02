"""Pure runtime accounting, timeline and completeness readers.

No owner is instantiated here. UNKNOWN is retained across accounting and capture
axes, and a healthy result never fills in a missing physical representation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import base64
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts import (EdgeObservation, EvaluationEvidenceRef, Finding,
                         MetricResult, ObservationBatch, StageObservation, canonical_hash)
from ..snapshot import (SnapshotView, safe_path, read_jsonl_prefix, _read_bounded,
                        DiagnosticEvent, diagnostic_frame_valid,
                        diagnostic_capture_complete, original_diagnostic_frame)


def read_diagnostic_prefix(path: str | Path, allowed_roots: Iterable[str | Path], *, max_bytes=32 * 1024 * 1024, max_records=100000, expected_session_id: str | None = None, allowed_process_ids: Iterable[int] | None = None, allowed_representation_files: Iterable[str] | None = None) -> dict[str, Any]:
    """Read and verify an append-only prefix without repairing any bytes."""
    target = safe_path(path, allowed_roots)
    if target.stat().st_size > max_bytes:
        raise ValueError("DIAGNOSTIC_BYTE_CAP_EXCEEDED")
    original = _read_bounded(target, max_bytes, "DIAGNOSTIC_BYTE_CAP_EXCEEDED")
    rows, limitations = read_jsonl_prefix(original, max_records=max_records)
    problems = list(limitations)
    events, seal = [], None
    sequence = 0
    session, segment, process = None, None, None
    total = len(original)
    permitted = set(allowed_representation_files) if allowed_representation_files is not None else None
    materials = {}
    for row in rows:
        if not isinstance(row, dict):
            problems.append("NON_OBJECT_EVENT")
            break
        if row.get("event_type") == "SESSION_SEAL":
            if row.get("frame_sha256") != canonical_hash({k:v for k,v in row.items() if k != "frame_sha256"}):
                problems.append("SEAL_HASH_MISMATCH")
            if seal is not None:
                problems.append("MULTIPLE_SEALS")
            seal = row
            continue
        if seal is not None:
            problems.append("EVENT_AFTER_SEAL")
        actual_frame = row.get("frame_sha256")
        if actual_frame != canonical_hash({k: v for k, v in row.items() if k != "frame_sha256"}):
            problems.append("FRAME_HASH_MISMATCH")
            break
        if row.get("sequence") != sequence + 1:
            problems.append("SEQUENCE_GAP")
            break
        sequence += 1
        if session is None:
            session, segment, process = row.get("session_id"), row.get("segment_id"), row.get("process_id")
        if (row.get("session_id"), row.get("segment_id"), row.get("process_id")) != (session, segment, process):
            problems.append("SESSION_SEGMENT_PROCESS_MISMATCH")
            break
        if expected_session_id and session != expected_session_id:
            raise ValueError("SESSION_PERMISSION_MISMATCH")
        if allowed_process_ids is not None and process not in allowed_process_ids:
            raise ValueError("PROCESS_PERMISSION_MISMATCH")
        event = DiagnosticEvent(row)
        ref = row.get("payload_ref")
        if ref:
            try:
                if permitted is not None and ref["relative_path"] not in permitted:
                    raise ValueError("DIAGNOSTIC_REPRESENTATION_NOT_DECLARED")
                locator = safe_path(target.parent / ref["relative_path"], [target.parent])
                if locator not in materials:
                    material = _read_bounded(locator, max_bytes - total, "REPRESENTATION_READ_CAP")
                    total += len(material)
                    materials[locator] = material
                else:
                    material = materials[locator]
                if len(material) != ref.get("bytes") or sha256(material).hexdigest() != ref.get("sha256"):
                    raise ValueError("REPRESENTATION_HASH_MISMATCH")
                event.hydrate_material(material)
            except (OSError, ValueError, KeyError) as error:
                problems.append(str(error))
                event["completeness"] = "CAPTURE_INCOMPLETE"
        events.append(event)
    if seal is None:
        problems.append("NO_SEAL")
    elif seal.get("session_id") != session or seal.get("segment_id") != segment or seal.get("last_sequence") != sequence or seal.get("flushed_sequence") != sequence or seal.get("lost_count") != 0:
        problems.append("SEAL_INCOMPLETE_OR_MISMATCH")
    if _read_bounded(target, len(original), "DIAGNOSTIC_BYTE_CAP_EXCEEDED") != original:
        problems.append("SOURCE_CHANGED_DURING_READ")
    # A cap hides committed records; an earlier seal cannot certify that tail.
    if "RECORD_CAP_EXCEEDED" in problems:
        seal = None
    for event in events:
        event.reader_limitations = tuple(problems)
    return {"events": events, "seal": seal, "session_id": session,
            "completeness": "CAPTURE_INCOMPLETE" if problems else "COMPLETE",
            "limitations": problems, "source_sha256": sha256(original).hexdigest()}


def reconcile_usage(records: Iterable[Mapping[str, Any]], *, dispatches: Iterable[Mapping[str, Any]] | None = None, price: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = list(records)
    logical, physical, reservations = set(), {}, set()
    findings = []
    unknown = False
    for row in rows:
        if row.get("model_call_id"):
            logical.add(row["model_call_id"])
        attempt = row.get("provider_attempt_id")
        if attempt:
            if attempt in physical and physical[attempt] != dict(row):
                # Multiple report layers may repeat the same usage; compare only
                # facts that belong to the physical provider boundary.
                old = physical[attempt]
                for field in ("model_call_id", "token_usage", "outcome"):
                    if old.get(field) is not None and row.get(field) is not None and old[field] != row[field]:
                        findings.append("CONFLICTING_PHYSICAL_USAGE:" + str(attempt))
            else:
                physical[attempt] = dict(row)
        if row.get("reservation_id"):
            reservations.add(row["reservation_id"])
        if row.get("outcome") in {"UNKNOWN", "INFLIGHT"}:
            unknown = True
    dispatch_rows = list(dispatches) if dispatches is not None else None
    counts = defaultdict(int)
    if dispatch_rows is not None:
        for row in dispatch_rows:
            if row.get("provider_attempt_id"):
                counts[row["provider_attempt_id"]] += 1
        if any(count > 1 for count in counts.values()):
            findings.append("REPEATED_PHYSICAL_DISPATCH")
    totals = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [(row.get("token_usage") or {}).get(key) for row in physical.values()]
        totals[key] = sum(values) if values and all(isinstance(v, int) and v >= 0 for v in values) and not unknown else None
    cost = None
    if price and price.get("version") and price.get("currency") and not unknown and totals["prompt_tokens"] is not None and totals["completion_tokens"] is not None:
        if isinstance(price.get("input_per_million"), (float, int)) and isinstance(price.get("output_per_million"), (float, int)):
            cost = (totals["prompt_tokens"] * price["input_per_million"] + totals["completion_tokens"] * price["output_per_million"]) / 1_000_000
    return {"logical_calls": len(logical), "physical_attempts": len(physical), "reservations": len(reservations), "dispatch_count": sum(counts.values()) if dispatch_rows is not None else None, "dispatch_proven": dispatch_rows is not None, "token_usage": totals, "cost": cost, "cost_state": "MEASURED" if cost is not None else "UNMEASURED", "unknown_outcome": unknown, "unknown_reservation_release_allowed": False, "findings": findings}


def _union(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for begin, end in sorted(intervals):
        if end < begin:
            continue
        if result and begin <= result[-1][1]:
            result[-1] = result[-1][0], max(end, result[-1][1])
        else:
            result.append((begin, end))
    return result


def _complement(begin: int, end: int, intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    gaps, cursor = [], begin
    for left, right in _union((max(begin, l), min(end, r)) for l, r in intervals if l < end and r > begin):
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


_ACTIVITY_CATEGORIES = frozenset({"PROVIDER", "DB_API_ELAPSED", "DB_TRANSACTION_SCOPE",
    "FS_API_ELAPSED", "FS_PARSE_SCOPE", "LOCK_WAIT", "LOCK_HELD", "QUEUE",
    "WORKER", "WORKER_JOIN_WAIT", "DRAIN", "DRAIN_WAIT"})


def timeline_spans_from_stages(stages: Iterable[StageObservation]) -> list[dict[str, Any]]:
    """Prove native timer boundaries without modifying their signed frames.

    A complete begin/result pair proves where elapsed time was spent. It does
    not prove a disk, DB engine, lock-owner or Provider-internal cause.
    """
    stages = list(stages)
    begins = {}
    for stage in stages:
        row = stage.payload
        if stage.measurement_state == "MEASURED" and row.get("event_type") == "SPAN_BEGIN" and diagnostic_frame_valid(row):
            frame = original_diagnostic_frame(row)
            key = tuple(frame.get(name) for name in ("session_id", "segment_id", "process_id", "span_id"))
            begins.setdefault(key, []).append(frame)
    result = []
    for stage in stages:
        row = stage.payload
        if row.get("event_type") not in {"SPAN_RESULT", "SPAN_ERROR", "LOCK_HELD_RESULT"}:
            continue
        frame = original_diagnostic_frame(row)
        native = (stage.measurement_state == "MEASURED" and diagnostic_frame_valid(row)
                  and not getattr(row, "reader_limitations", ())
                  and frame.get("schema_version") == "diagnostic_event_v1")
        start, end, clock = frame.get("begin_ns"), frame.get("end_ns"), frame.get("clock_domain")
        interval = (isinstance(start, int) and not isinstance(start, bool)
                    and isinstance(end, int) and not isinstance(end, bool) and end >= start
                    and clock == f"process:{frame.get('process_id')}" and frame.get("process_id") is not None)
        if frame.get("event_type") in {"SPAN_RESULT", "SPAN_ERROR"}:
            key = tuple(frame.get(name) for name in ("session_id", "segment_id", "process_id", "span_id"))
            candidates = begins.get(key, [])
            paired = (len(candidates) == 1 and frame.get("span_id") is not None
                and all(candidates[0].get(name) == frame.get(name) for name in
                        ("begin_ns", "clock_domain", "category", "operation_ref", "thread_id"))
                and candidates[0].get("sequence", 0) < frame.get("sequence", 0))
        else:
            paired = frame.get("category") == "LOCK_HELD"
        # Timing is derived from the encoded projection, never from mutable
        # hydrated-view fields whose replacement is outside that frame hash.
        derived = {**dict(row), **frame}
        derived["activity_proven"] = bool(native and interval and paired and frame.get("category") in _ACTIVITY_CATEGORIES)
        derived["boundary_measurement_state"] = "MEASURED" if native and interval and paired else "UNMEASURED"
        derived["activity_basis"] = "VERIFIED_NATIVE_TIMER_BOUNDARY" if derived["activity_proven"] else "NOT_ESTABLISHED"
        derived["internal_cause_state"] = "UNKNOWN"
        result.append(derived)
    return result


def compute_timeline(spans: Iterable[Mapping[str, Any]], *, window: Mapping[str, Any] | None = None, dependencies: Iterable[tuple[str, str]] | None = None) -> dict[str, Any]:
    rows = list(spans)
    # Each child belongs to one original parent/clock pair. Index once rather
    # than rescanning every captured span for every owner loader/lock span.
    children_by_parent=defaultdict(list)
    for child in rows:
        if child.get("parent_span_id") is not None and child.get("clock_domain") is not None and isinstance(child.get("begin_ns"),int) and isinstance(child.get("end_ns"),int):
            children_by_parent[(child["parent_span_id"],child["clock_domain"])].append((child["begin_ns"],child["end_ns"]))
    provider_by_clock = defaultdict(list)
    measures, valid = [], {}
    for row in rows:
        start, end = row.get("begin_ns"), row.get("end_ns")
        clock = row.get("clock_domain")
        measured = isinstance(start, int) and isinstance(end, int) and end >= start and clock is not None
        if not measured:
            measures.append({"operation_ref": row.get("operation_ref"), "measurement_state": "UNMEASURED", "reason": "MISSING_OR_INCOMPARABLE_CLOCK"})
            continue
        children = [(left,right) for left,right in children_by_parent.get((row.get("span_id"),clock),()) if start <= left <= right <= end] if row.get("span_id") is not None else []
        wall = end - start
        measures.append({"operation_ref": row.get("operation_ref"), "span_id": row.get("span_id"), "clock_domain": clock, "measurement_state": "MEASURED", "inclusive_wall_ns": wall, "exclusive_wall_ns": wall - sum(r-l for l,r in _union(children)), "thread_cpu_ns": row.get("thread_cpu_ns"), "thread_cpu_scope": row.get("thread_id"), "process_cpu_ns": row.get("process_cpu_ns"), "process_cpu_scope": row.get("process_id"), "category": row.get("category")})
        if row.get("span_id"):
            valid[row["span_id"]] = row
        if row.get("category") == "PROVIDER":
            provider_by_clock[clock].append((start, end))
    known_by_clock = defaultdict(list)
    known_by_category = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if (row.get("activity_proven") is True
                and isinstance(row.get("begin_ns"), int) and isinstance(row.get("end_ns"), int)
                and row["end_ns"] >= row["begin_ns"] and row.get("clock_domain") is not None):
            interval = (row["begin_ns"], row["end_ns"])
            known_by_clock[row["clock_domain"]].append(interval)
            known_by_category[row["clock_domain"]][row.get("category", "EXPLICIT_ACTIVITY")].append(interval)
    unexplained = []
    if window and isinstance(window.get("begin_ns"), int) and isinstance(window.get("end_ns"), int):
        clock = window.get("clock_domain")
        intervals = known_by_clock.get(clock, [])
        unexplained = [{"begin_ns": l, "end_ns": r, "clock_domain": clock, "reason": "UNEXPLAINED_INTERVAL", "candidate_edges": list(window.get("candidate_edges", []))} for l, r in _complement(window["begin_ns"], window["end_ns"], intervals)]
    critical_path, critical_state = None, "UNKNOWN"
    if dependencies is not None and valid:
        edges = list(dependencies)
        clocks = {r["clock_domain"] for r in valid.values()}
        if len(clocks) == 1 and all(a in valid and b in valid and valid[a]["end_ns"] <= valid[b]["begin_ns"] for a, b in edges):
            incoming = {k: [] for k in valid}
            outgoing = {k: [] for k in valid}
            for a, b in edges:
                incoming[b].append(a)
                outgoing[a].append(b)
            scores = {}
            todo = [k for k in valid if not incoming[k]]
            while todo:
                node = todo.pop(0)
                if node in scores:
                    continue
                row = valid[node]
                scores[node] = row["end_ns"] - row["begin_ns"] + max((scores[p] for p in incoming[node]), default=0)
                todo.extend(n for n in outgoing[node] if all(p in scores for p in incoming[n]))
            if len(scores) == len(valid):
                critical_path, critical_state = max(scores.values()), "MEASURED"
    return {"spans": measures, "provider_union_ns": {clock: sum(r-l for l,r in _union(intervals)) for clock, intervals in provider_by_clock.items()},
        "known_activity_union_ns": {clock: sum(r-l for l,r in _union(intervals)) for clock,intervals in known_by_clock.items()},
        "known_activity_by_category_ns": {clock: {category: sum(r-l for l,r in _union(intervals)) for category,intervals in categories.items()} for clock,categories in known_by_category.items()},
        "known_activity_cause_state": "BOUNDARY_ONLY_INTERNAL_CAUSE_UNKNOWN", "category_intervals_overlap": True,
        "cross_clock_alignment": "UNPROVEN" if len({r.get("clock_domain") for r in rows}) > 1 else "SINGLE_CLOCK", "critical_path_ns": critical_path, "critical_path_state": critical_state, "unexplained_intervals": unexplained, "cpu_is_wall_partition": False}


def check_lifecycle_and_replay(record: Mapping[str, Any]) -> list[str]:
    findings = []
    if record.get("actual_predecessors") is not None and record.get("recovery_predecessors") is not None and set(record["actual_predecessors"]) != set(record["recovery_predecessors"]):
        findings.append("RECOVERY_PREDECESSOR_MISMATCH")
    if record.get("os_exited") and record.get("close_status") != "CLOSED":
        findings.append("OS_EXIT_DOES_NOT_PROVE_SAFE_CLOSE")
    if record.get("worker_still_active") or record.get("unknown_call"):
        findings.append("ACTIVE_OR_UNKNOWN_EXECUTION")
    if record.get("task_status") == "FAILED" and record.get("run_status") == "RUNNING":
        findings.append("TASK_RUN_STATUS_DIVERGENCE")
    if record.get("healthy_terminal_read") and any(record.get(k, 0) != 0 for k in ("new_provider_calls", "new_cost", "new_business_commits")):
        findings.append("HEALTHY_READ_SIDE_EFFECT")
    if record.get("raw_payload") is not None and record.get("raw_sha256") and sha256(record["raw_payload"].encode()).hexdigest() != record["raw_sha256"]:
        findings.append("ARTIFACT_PAYLOAD_CORRUPTION")
    if record.get("budget_account_present") is False:
        findings.append("ORIGINAL_BUDGET_ACCOUNT_MISSING")
    if record.get("durable_memory_required") is True and record.get("locator_current") is False:
        findings.append("MEMORY_LOCATOR_STALE")
    return findings


def evaluate_runtime_edge(edge_id: str, source: Mapping[str, Any] | None, target: Mapping[str, Any] | None) -> dict[str, Any]:
    """Calculate original endpoint obligations; never accept an input verdict."""
    def missing(reason):
        return {"measurement_state":"UNMEASURED", "assertion_result":None, "reason":reason}
    def result(checks):
        wrong=[name for name,passed in checks.items() if not passed]
        return {"measurement_state":"MEASURED", "assertion_result":not wrong, "reason":",".join(wrong), "checks":checks}
    if source is None or target is None:
        return missing("MISSING_RUNTIME_EDGE_ENDPOINT")
    if edge_id=="X22":
        fields=("checkpoint_ref","run_id","attempt_id","namespace","source_head","route","graph_ref")
        if any(k not in source or k not in target for k in fields) or "actual_predecessors" not in source or "recovery_predecessors" not in target or "redispatched_attempt_ids" not in target or "dispatched_attempt_ids" not in source or "unknown_attempt_ids" not in source:
            return missing("MISSING_CHECKPOINT_ROUTE_CURRENTNESS_OR_DISPATCH")
        blocked=set(source["dispatched_attempt_ids"]) | set(source["unknown_attempt_ids"])
        return result({**{k:source[k]==target[k] for k in fields}, "actual_predecessors":set(source["actual_predecessors"])==set(target["recovery_predecessors"]), "no_blind_redispatch":not (blocked & set(target["redispatched_attempt_ids"]))})
    if edge_id=="X23":
        if "physical_attempts" not in source or "usage_attempts" not in target or "dispatch_count" not in source or "dispatch_count" not in target:
            return missing("MISSING_INDEPENDENT_PHYSICAL_DISPATCH_OR_USAGE")
        observed={r.get("provider_attempt_id"):r for r in source["physical_attempts"]}
        accounted={r.get("provider_attempt_id"):r for r in target["usage_attempts"]}
        if None in observed or None in accounted:
            return missing("MISSING_ORIGINAL_PHYSICAL_ID")
        checks={"attempt_membership":set(observed)==set(accounted), "dispatch_count":source["dispatch_count"]==target["dispatch_count"]}
        for attempt in set(observed)&set(accounted):
            original,ledger=observed[attempt],accounted[attempt]
            if "model_call_id" not in original or "model_call_id" not in ledger or "outcome" not in original or "reservation_state" not in ledger:
                return missing("MISSING_LOGICAL_OR_RESERVATION_OUTCOME")
            checks[attempt+":logical_identity"]=original["model_call_id"]==ledger["model_call_id"]
            if original["outcome"] in {"UNKNOWN","INFLIGHT"}:
                checks[attempt+":unknown_retained"]=ledger["reservation_state"] not in {"RELEASED","ZEROED"} and ledger.get("actual_cost") is None
            else:
                if not isinstance(original.get("token_usage"),Mapping) or not isinstance(ledger.get("token_usage"),Mapping):
                    return missing("TOKEN_USAGE_NOT_RECORDED")
                checks[attempt+":actual_tokens"]=all(original["token_usage"].get(k)==ledger["token_usage"].get(k) for k in ("prompt_tokens","completion_tokens","total_tokens"))
                checks[attempt+":settlement"]=ledger["reservation_state"]=="SETTLED"
        return result(checks)
    if edge_id=="X24":
        if "expected_event_types" not in source or "session_id" not in source or "events" not in target or "completeness" not in target:
            return missing("MISSING_RUNTIME_CAPTURE_CONTRACT")
        events=target["events"]
        expected=list(source["expected_event_types"])
        actual=[r.get("event_type") for r in events if not source.get("operation_ref") or r.get("operation_ref")==source["operation_ref"]]
        checks={"session":source["session_id"]==target.get("session_id"), "prefix_complete":target["completeness"]=="COMPLETE", "seal":bool(target.get("seal") and target["seal"].get("lost_count")==0), "expected_event_order":all(kind in actual for kind in expected)}
        if checks["expected_event_order"]:
            checks["expected_event_order"]=[actual.index(k) for k in expected]==sorted(actual.index(k) for k in expected)
        for pool in source.get("pool_ids",[]):
            rows=[r for r in events if r.get("pool_id")==pool]
            end=next((r for r in rows if r.get("event_type")=="POOL_END"),None)
            items=sorted((r for r in rows if r.get("event_type")=="POOL_ITEM"),key=lambda r:r.get("ordinal",-1))
            checks["pool:"+pool]=bool(end and end.get("declared_count")==len(items) and [r.get("ordinal") for r in items]==list(range(len(items))) and end.get("end_digest")==sha256(b"".join(json.dumps(r["item"],ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()+b"\n" for r in items)).hexdigest())
        return result(checks)
    if edge_id=="X28":
        fields=("run_id","attempt_id")
        required=("active_workers","unknown_calls","drain_status","owned_close_status")
        if any(k not in source or k not in target for k in fields) or "terminal_status" not in source or "usage_finalized" not in source or any(k not in target for k in required):
            return missing("MISSING_TERMINAL_USAGE_OR_PHYSICAL_DRAIN")
        return result({**{k:source[k]==target[k] for k in fields}, "legal_terminal":source["terminal_status"] in {"SUCCESS","SUCCEEDED","FAILED","CANCELLED","OUTPUT_BEST_EFFORT"}, "usage_finalized":source["usage_finalized"] is True, "physical_idle":target["active_workers"]==0 and target["unknown_calls"]==0 and target["drain_status"]=="IDLE", "owned_close":target["owned_close_status"]=="CLOSED"})
    if edge_id=="X29":
        if target.get("durable_memory_required") is False:
            return {"measurement_state":"N/A","assertion_result":None,"reason":"DURABLE_MEMORY_DISABLED"}
        fields=("source_ref","source_version","raw_sha256")
        if target.get("durable_memory_required") is not True or any(k not in source or k not in target for k in fields) or "readback_payload" not in target or "locator_current" not in target or "source_authorized" not in target:
            return missing("MISSING_MEMORY_ENABLEMENT_OR_SOURCE_READBACK")
        raw=target["readback_payload"]
        if not isinstance(raw,(str,bytes)):
            return missing("SOURCE_READBACK_BYTES_UNAVAILABLE")
        return result({**{k:source[k]==target[k] for k in fields}, "source_hash":sha256(raw.encode() if isinstance(raw,str) else raw).hexdigest()==source["raw_sha256"], "currentness":target["locator_current"] is True, "permission":target["source_authorized"] is True})
    return missing("UNKNOWN_RUNTIME_EDGE")


def _native_result(checks, missing, *, basis):
    """A proved violation survives missing conjuncts; absence cannot prove PASS."""
    failed = [key for key, value in checks.items() if value is False]
    state = "MEASURED" if failed or not missing else "UNMEASURED"
    return {"measurement_state": state,
            "assertion_result": False if failed else True if not missing else None,
            "reason": ",".join(failed or missing), "checks": checks,
            "missing_obligations": list(missing), "basis": basis}


def _diagnostic_material_valid(row):
    frame = original_diagnostic_frame(row)
    ref = frame.get("payload_ref")
    return ref is None or (isinstance(row, DiagnosticEvent) and row.material_valid
        and isinstance(ref, Mapping) and ref.get("representation") == frame.get("event_type"))


def _native_capture_edge(snapshot, records):
    """Check each original session channel and its committed finite boundaries."""
    groups = defaultdict(list)
    for row in records:
        if row.get("schema_version") in {"diagnostic_event_v1", "diagnostic_seal_v1"}:
            frame = original_diagnostic_frame(row)
            groups[(frame.get("session_id"), frame.get("segment_id"))].append(row)
    if not groups:
        return _native_result({}, ["NATIVE_DIAGNOSTIC_CHANNEL_NOT_RECORDED"], basis="NATIVE_CHANNEL"), []
    checks, missing = {}, []
    for (session, segment), rows in groups.items():
        key = f"channel:{session}:{segment}:"
        events = [original_diagnostic_frame(r) for r in rows if original_diagnostic_frame(r).get("event_type") != "SESSION_SEAL"]
        seals = [r for r in rows if original_diagnostic_frame(r).get("event_type") == "SESSION_SEAL"]
        checks[key+"original_frames"] = all(diagnostic_frame_valid(r) for r in rows)
        checks[key+"single_original_process"] = len({r.get("process_id") for r in events}) == 1
        checks[key+"sequence"] = [r.get("sequence") for r in events] == list(range(1, len(events)+1))
        if len(seals) != 1:
            missing.append(key+"UNIQUE_FINAL_SEAL_NOT_RECORDED")
        else:
            seal = original_diagnostic_frame(seals[0])
            checks[key+"final_seal"] = original_diagnostic_frame(rows[-1]).get("event_type") == "SESSION_SEAL"
            checks[key+"flushed_prefix"] = seal.get("last_sequence") == seal.get("flushed_sequence") == len(events)
            checks[key+"lost_zero"] = seal.get("lost_count") == 0
            checks[key+"complete"] = seal.get("completeness") == "COMPLETE" and all(r.get("completeness") == "COMPLETE" for r in events)
        limitations = [str(value) for r in rows for value in getattr(r, "reader_limitations", ())]
        if limitations:
            # A missing seal alone is absence. A committed corrupt/capped/read
            # representation is a known failure of channel completeness.
            known = [value for value in limitations if value != "NO_SEAL"]
            if known:
                checks[key+"reader_integrity_and_caps"] = False
            else:
                missing.append(key+"NO_SEAL")
        spans, pools, physical = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in events:
            if row.get("event_type") in {"SPAN_BEGIN", "SPAN_RESULT", "SPAN_ERROR"}:
                spans[row.get("span_id")].append(row)
            if row.get("event_type") in {"POOL_BEGIN", "POOL_ITEM", "POOL_END"}:
                pools[row.get("pool_id")].append(row)
            if row.get("event_type") in {"MODEL_ATTEMPT_REQUEST", "MODEL_CLIENT_RAW_RESPONSE", "MODEL_CLIENT_EXCEPTION", "MODEL_GATEWAY_PROJECTED_RESPONSE"}:
                physical[row.get("provider_attempt_id")].append(row)
        for span_id, chain in spans.items():
            begin = [r for r in chain if r["event_type"] == "SPAN_BEGIN"]
            end = [r for r in chain if r["event_type"] in {"SPAN_RESULT", "SPAN_ERROR"}]
            if len(seals) != 1 and (not begin or not end):
                missing.append(key+"UNSEALED_SPAN_BOUNDARY:"+str(span_id))
                continue
            checks[key+"span:"+str(span_id)] = bool(span_id and len(begin) == len(end) == 1
                and begin[0]["sequence"] < end[0]["sequence"]
                and all(begin[0].get(field) == end[0].get(field) for field in
                        ("span_id", "operation_ref", "category", "begin_ns", "clock_domain", "thread_id")))
        for pool_id, chain in pools.items():
            # Caller pool_id is semantic (for example a Context package), so
            # another emission may reuse it.  Original thread and BEGIN
            # sequence identify each finite invocation without changing frames.
            threads = defaultdict(list)
            for row in chain:
                threads[(row.get("process_id"), row.get("thread_id"))].append(row)
            for origin, ordered in threads.items():
                if None in origin:
                    missing.append(key+"NATIVE_POOL_THREAD_ID_NOT_RECORDED:"+str(pool_id))
                    continue
                pending, depth, ambiguous = [], 0, False
                occurrences = []
                for row in ordered:
                    if row["event_type"] == "POOL_BEGIN":
                        ambiguous = ambiguous or depth > 0
                        depth += 1
                        pending.append(row)
                    elif not depth:
                        marker = key+"pool:"+str(pool_id)+":orphan:"+str(row.get("sequence"))
                        if len(seals) != 1:
                            missing.append(marker+":UNSEALED_POOL_BOUNDARY")
                        else:
                            checks[marker] = False
                    else:
                        pending.append(row)
                        if row["event_type"] == "POOL_END":
                            depth -= 1
                            if not depth:
                                occurrences.append((pending, ambiguous))
                                pending, ambiguous = [], False
                if pending:
                    occurrences.append((pending, ambiguous))
                for occurrence, overlap in occurrences:
                    marker = key+"pool:"+str(pool_id)+":begin:"+str(occurrence[0].get("sequence"))
                    if overlap:
                        missing.append(marker+":AMBIGUOUS_POOL_BOUNDARY")
                        continue
                    begin = [r for r in occurrence if r["event_type"] == "POOL_BEGIN"]
                    items = [r for r in occurrence if r["event_type"] == "POOL_ITEM"]
                    end = [r for r in occurrence if r["event_type"] == "POOL_END"]
                    if len(seals) != 1 and (not begin or not end):
                        missing.append(marker+":UNSEALED_POOL_BOUNDARY")
                        continue
                    digest = sha256(b"".join(json.dumps(r.get("item"), ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode()+b"\n" for r in items)).hexdigest()
                    checks[marker] = bool(pool_id and len(begin) == len(end) == 1
                        and occurrence[0] is begin[0] and occurrence[-1] is end[0]
                        and begin[0].get("declared_count") == end[0].get("declared_count") == len(items)
                        and [r.get("ordinal") for r in items] == list(range(len(items)))
                        and end[0].get("end_digest") == digest)
        hydrated = {r.get("sequence"): r for r in rows}
        for attempt_id, chain in physical.items():
            kinds = [r["event_type"] for r in chain]
            raw = "MODEL_CLIENT_RAW_RESPONSE" in kinds
            expected = ["MODEL_ATTEMPT_REQUEST", "MODEL_CLIENT_RAW_RESPONSE", "MODEL_GATEWAY_PROJECTED_RESPONSE"] if raw else ["MODEL_ATTEMPT_REQUEST", "MODEL_CLIENT_EXCEPTION"]
            # A control exception can be projected by Gateway; BaseException
            # propagates without a fabricated projected/raw response.
            valid_order = kinds == expected or (not raw and kinds == expected+["MODEL_GATEWAY_PROJECTED_RESPONSE"])
            if len(seals) != 1 and not valid_order:
                missing.append(key+"UNSEALED_PHYSICAL_BOUNDARY:"+str(attempt_id))
                continue
            checks[key+"physical:"+str(attempt_id)] = bool(attempt_id and valid_order
                and len({r.get("model_call_id") for r in chain}) == 1)
            for row in chain:
                if row.get("payload_ref") and "material" not in hydrated.get(row.get("sequence"), {}):
                    missing.append(key+"REPRESENTATION_READBACK_NOT_RECORDED:"+str(attempt_id))
                elif row.get("payload_ref"):
                    checks[key+"representation:"+str(row.get("sequence"))] = _diagnostic_material_valid(hydrated[row["sequence"]])
    if snapshot.limitations and not any(value is False for value in checks.values()):
        missing.extend("SNAPSHOT:"+value for value in snapshot.limitations)
    return _native_result(checks, list(dict.fromkeys(missing)), basis="ORIGINAL_FRAME_SEQUENCE_SEAL_AND_BOUNDARIES"), [r for rows in groups.values() for r in rows]


def _native_runtime_edge(edge_id, snapshot, records):
    """Thin adapters for existing owner exports, retaining unrecorded obligations.

    These selectors consume original producer/schema shapes, never an input
    assertion_result or an invented source/target endpoint verdict.
    """
    if edge_id == "X24":
        return _native_capture_edge(snapshot, records)
    if edge_id == "X22":
        owner_exports = [r for r in records if r.get("owner_type") == "apps.enterprise_document.workflows.top_level_business_graph.EnterpriseDocumentWorkflowEngine"
                         and all(k in r for k in ("inspection", "recovery_plan", "checkpoint_history", "workflow"))]
        attempts = [r for r in records if r.get("schema_version") == "attempt_lifecycle_record_v1"]
        preflight = [r for r in records if r.get("operation_ref") == "engine.recovery_plan" and r.get("event_type") == "SPAN_BEGIN"]
        if len(owner_exports) == len(attempts) == len(preflight) == 1 and diagnostic_capture_complete(snapshot):
            owner, attempt, boundary = owner_exports[0], attempts[0], preflight[0]
            inspection, plan, workflow = owner["inspection"], owner["recovery_plan"], owner["workflow"]
            config = plan.get("checkpoint_config", {}).get("configurable", {})
            selected = [r for r in owner["checkpoint_history"] if r.get("config", {}).get("configurable", {}).get("checkpoint_id") == config.get("checkpoint_id")]
            finished = [r for r in records if r.get("span_id") == boundary.get("span_id") and r.get("event_type") == "SPAN_RESULT"]
            if len(selected) != 1:
                return _native_result({"original_checkpoint_membership": False}, [], basis="NATIVE_GRAPH_CHECKPOINT_HISTORY_RECOVERY_PREFLIGHT"), owner_exports+attempts+[boundary]
            if len(finished) == 1:
                checkpoint = selected[0]
                values = checkpoint.get("checkpoint", {}).get("channel_values", {})
                original_config = checkpoint.get("config", {}).get("configurable", {})
                predecessor = values.get("current_business_node")
                namespace = original_config.get("workflow_namespace") or original_config.get("checkpoint_ns")
                expected_namespace = f"{workflow.get('workflow_id')}:{workflow.get('workflow_version')}"
                source_attempt = attempt.get("identity", {}).get("attempt_id")
                before = [r for r in records if r.get("session_id") == boundary.get("session_id") and r.get("sequence", 0) < boundary["sequence"]]
                dispatched = {r.get("provider_attempt_id") for r in before if r.get("event_type") == "MODEL_ATTEMPT_REQUEST"}
                returned = {r.get("provider_attempt_id") for r in before if r.get("event_type") == "MODEL_CLIENT_RAW_RESPONSE"}
                redispatched = {r.get("provider_attempt_id") for r in records if r.get("session_id") == boundary.get("session_id")
                    and boundary["sequence"] < r.get("sequence", 0) < finished[0]["sequence"] and r.get("event_type") == "MODEL_ATTEMPT_REQUEST"}
                source = {"checkpoint_ref": original_config.get("checkpoint_id"), "run_id": values.get("run_id"),
                    "attempt_id": source_attempt, "namespace": namespace, "source_head": original_config.get("checkpoint_id"),
                    "route": values.get("business_route"), "graph_ref": f"{values.get('workflow_id')}:{values.get('workflow_version')}",
                    "actual_predecessors": [predecessor], "dispatched_attempt_ids": list(dispatched), "unknown_attempt_ids": list(dispatched-returned)}
                target = {"checkpoint_ref": config.get("checkpoint_id"), "run_id": config.get("thread_id"),
                    "attempt_id": boundary.get("attempt_id"), "namespace": config.get("workflow_namespace") or config.get("checkpoint_ns"),
                    "source_head": config.get("checkpoint_id"), "route": values.get("business_route"), "graph_ref": expected_namespace,
                    "recovery_predecessors": [plan.get("predecessor") or predecessor], "redispatched_attempt_ids": list(redispatched)}
                needed = [key for key in ("checkpoint_ref", "run_id", "attempt_id", "namespace", "source_head", "route", "graph_ref") if not source.get(key) or not target.get(key)]
                if predecessor is None: needed.append("NATIVE_CHECKPOINT_CURRENT_BUSINESS_NODE")
                if not needed:
                    verdict = evaluate_runtime_edge("X22", source, target)
                    checks = {**verdict.get("checks", {}),
                        "inspection_run": inspection.get("run_id") == values.get("run_id"),
                        "attempt_run": attempt.get("identity", {}).get("run_id") == values.get("run_id"),
                        "boundary_run": boundary.get("run_id") == values.get("run_id"),
                        "native_workflow_namespace": namespace == expected_namespace,
                        "native_preflight_completed": finished[0].get("run_id") == values.get("run_id"),
                        "preflight_has_no_physical_dispatch": not redispatched}
                    return _native_result(checks, [], basis="NATIVE_GRAPH_CHECKPOINT_HISTORY_RECOVERY_PREFLIGHT"), owner_exports+attempts+[boundary,finished[0]]
        checkpoints = [r for r in records if isinstance(r.get("state"), Mapping) and "state_hash" in r and "event_sequence" in r]
        plans = [r for r in records if isinstance(r.get("checkpoint_config"), Mapping)]
        checks = {}
        for index, row in enumerate(checkpoints):
            meta = row["state"].get("state_meta", {})
            checks[f"checkpoint:{index}:state_hash"] = row["state_hash"] == canonical_hash(row["state"])
            checks[f"checkpoint:{index}:identity"] = all(row.get(k) == meta.get(k) for k in ("task_id", "run_id", "event_sequence"))
        missing = ["RECOVERY_ROUTE_NAMESPACE_SOURCE_HEAD_GRAPH_AUTHORITY_NOT_RECORDED",
                   "ACTUAL_PREDECESSORS_AND_PHYSICAL_REDISPATCH_SET_NOT_RECORDED"]
        if not checkpoints: missing.append("NATIVE_CHECKPOINT_ENVELOPE_NOT_RECORDED")
        if not plans: missing.append("NATIVE_RECOVERY_CHECKPOINT_CONFIG_NOT_RECORDED")
        return _native_result(checks, missing, basis="CHECKPOINT_STATE_ENVELOPE_AND_RECOVERY_CONFIG"), checkpoints+plans+owner_exports+attempts+preflight
    if edge_id == "X23":
        usages = [r for r in records if r.get("schema_version") == "model_usage_v1" and isinstance(r.get("usage"), Mapping)]
        budgets = [r for r in records if r.get("schema_version") == "canonical_budget_state_v1"]
        requests = [r for r in records if r.get("event_type") == "MODEL_ATTEMPT_REQUEST"]
        responses = [r for r in records if r.get("event_type") in {"MODEL_CLIENT_RAW_RESPONSE", "MODEL_CLIENT_EXCEPTION"}]
        selected = requests+responses+usages+budgets
        missing, checks = [], {}
        if not diagnostic_capture_complete(snapshot): missing.append("COMPLETE_ORIGINAL_PHYSICAL_CHANNEL_NOT_RECORDED")
        if len(usages) != 1: missing.append("UNIQUE_NATIVE_FINAL_USAGE_EXPORT_NOT_RECORDED")
        if len(budgets) != 1: missing.append("UNIQUE_CANONICAL_RESERVATION_STATE_NOT_RECORDED")
        if not requests or len(responses) != len(requests): missing.append("REQUEST_RESPONSE_ATTEMPT_PAIR_NOT_RECORDED")
        if missing:
            return _native_result(checks, missing, basis="PHYSICAL_CHANNEL_USAGE_V1_CANONICAL_BUDGET"), selected
        usage, budget = usages[0], budgets[0]
        physical = {r.get("provider_attempt_id"): r for r in requests}
        returned = {r.get("provider_attempt_id"): r for r in responses}
        reservations = [r for r in budget.get("reservations", []) if r.get("reservation_ref", {}).get("reservation_kind") == "model_provider_attempt_tokens"]
        accounted = {r.get("reservation_ref", {}).get("owner_occurrence_ref"): r for r in reservations}
        checks["unique_physical_attempts"] = None not in physical and len(physical) == len(requests)
        checks["request_response_membership"] = set(physical) == set(returned)
        # A complete session need not cover all historic Run attempts. Equality
        # of the canonical physical reservation set is the independent scope join.
        if set(physical) != set(accounted) or len(accounted) != len(reservations):
            missing.append("CAPTURE_TO_RUN_RESERVATION_SCOPE_NOT_ESTABLISHED")
        checks["usage_budget_identity"] = all(usage.get(k) == budget.get(k) for k in ("task_id", "run_id"))
        checks["usage_finalized"] = usage.get("finalized") is True
        unknown = False
        totals = defaultdict(int)
        for attempt, req in physical.items():
            material = req.get("material")
            raw = returned.get(attempt, {})
            if not isinstance(material, Mapping) or (raw.get("event_type") != "MODEL_CLIENT_EXCEPTION" and not isinstance(raw.get("material"), Mapping)):
                missing.append("ORIGINAL_REQUEST_RESPONSE_READBACK_NOT_RECORDED")
                continue
            checks[attempt+":representation_hashes"] = _diagnostic_material_valid(req) and _diagnostic_material_valid(raw)
            checks[attempt+":original_identity"] = all(material.get(k) == usage.get(k) for k in ("task_id", "run_id")) and raw.get("model_call_id") == req.get("model_call_id")
            reservation = accounted.get(attempt)
            if not reservation:
                continue
            ref = reservation.get("reservation_ref", {})
            checks[attempt+":reservation_identity"] = ref.get("operation_ref") == req.get("model_call_id") and all(ref.get(k) == material.get(k) for k in ("task_id", "run_id"))
            if raw.get("event_type") == "MODEL_CLIENT_EXCEPTION":
                unknown = True
                checks[attempt+":unknown_retained"] = reservation.get("status") != "released" and usage["usage"].get("cost_if_available") is None
                missing.append("UNKNOWN_PROVIDER_EXACT_USAGE_NOT_RECORDED")
            else:
                tokens = raw["material"].get("token_usage")
                if not isinstance(tokens, Mapping) or any(not isinstance(tokens.get(k), int) or isinstance(tokens.get(k), bool) for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
                    missing.append("ACTUAL_PROVIDER_TOKENS_NOT_RECORDED")
                    continue
                for k, value in tokens.items():
                    if k in {"prompt_tokens", "completion_tokens", "total_tokens"}: totals[k] += value
                checks[attempt+":consumed"] = reservation.get("status") == "consumed"
                checks[attempt+":actual_total_tokens"] = reservation.get("actual", {}).get("model_tokens_total") == tokens["total_tokens"]
        if not missing and not unknown:
            checks["provider_attempt_count"] = usage["usage"].get("provider_attempts") == len(physical)
            checks.update({"aggregate:"+k: usage["usage"].get(k) == totals[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")})
        return _native_result(checks, list(dict.fromkeys(missing)), basis="PHYSICAL_CHANNEL_USAGE_V1_CANONICAL_BUDGET"), selected
    if edge_id == "X28":
        public = [r for r in records if r.get("event_type") == "S-PUBLIC"]
        usages = [r for r in records if r.get("schema_version") == "model_usage_v1"]
        drain = [r for r in records if r.get("category") in {"DRAIN", "DRAIN_WAIT"} and r.get("event_type") in {"SPAN_RESULT", "SPAN_ERROR"}]
        workers = [r for r in records if r.get("event_type") in {"WORKER_START", "WORKER_FINISH"}]
        runs = [r for r in records if r.get("schema_version") == "run_lifecycle_record_v2"]
        attempts = [r for r in records if r.get("schema_version") == "attempt_lifecycle_record_v1"]
        owners = [r for r in records if r.get("owner_type") == "agent.supervisor_agent.SupervisorAgent"
                  and "_resources_closed" in r and "active_worker_count" in r and "wait_for_idle_result" in r]
        # A read-only owner export freezes real attributes/method return values;
        # it adds neither a producer schema nor a closure assertion verdict.
        # Bind it through original execution-control identity and capture session.
        if len(runs) == len(attempts) == len(owners) == len(public) == len(usages) == 1:
            run, attempt, owner, published, usage = runs[0], attempts[0], owners[0], public[0], usages[0]
            run_id = run.get("identity", {}).get("run_id")
            attempt_id = attempt.get("identity", {}).get("attempt_id")
            execution_id = owner.get("execution_control", {}).get("execution_id")
            close = [r for r in drain if r.get("operation_ref") == "supervisor.close"
                     and r.get("session_id") == owner.get("diagnostic_config", {}).get("session_id")]
            required = ("shutdown_diagnostics", "execution_control", "diagnostic_config")
            if (run_id and attempt_id and len(close) == 1 and all(k in owner for k in required)
                    and diagnostic_capture_complete(snapshot)):
                unknown = {r.get("provider_attempt_id") for r in records if r.get("event_type") == "MODEL_CLIENT_EXCEPTION"}
                original_requests = {r.get("provider_attempt_id") for r in records if r.get("event_type") == "MODEL_ATTEMPT_REQUEST"}
                original_returned = {r.get("provider_attempt_id") for r in records if r.get("event_type") == "MODEL_CLIENT_RAW_RESPONSE"}
                unknown |= original_requests-original_returned
                source = {"run_id": run_id, "attempt_id": attempt_id,
                    "terminal_status": str(run.get("status", "")).upper(), "usage_finalized": usage.get("finalized")}
                target = {"run_id": attempt.get("identity", {}).get("run_id"), "attempt_id": execution_id,
                    "active_workers": owner["active_worker_count"], "unknown_calls": len(unknown),
                    "drain_status": "IDLE" if owner["wait_for_idle_result"] is True else "ACTIVE",
                    "owned_close_status": "CLOSED" if owner["_resources_closed"] is True and not owner["shutdown_diagnostics"] and close[0].get("event_type") == "SPAN_RESULT" else "NOT_CLOSED"}
                verdict = evaluate_runtime_edge("X28", source, target)
                extra = {"public_run": published.get("payload", {}).get("run_id") == run_id,
                    "public_task": published.get("payload", {}).get("task_id") == run.get("identity", {}).get("task_id"),
                    "usage_run": usage.get("run_id") == run_id,
                    "usage_task": usage.get("task_id") == run.get("identity", {}).get("task_id"),
                    "attempt_terminal": str(attempt.get("status", "")).upper() in {"SUCCESS", "SUCCEEDED", "FAILED", "CANCELLED"},
                    "independent_dispatch_count": usage.get("usage", {}).get("provider_attempts") == len(original_requests)}
                verdict = _native_result({**verdict.get("checks", {}), **extra}, [], basis="RUN_ATTEMPT_PUBLIC_USAGE_ACTUAL_SUPERVISOR_AND_WORKER_EXPORT")
                return verdict, public+usages+drain+workers+runs+attempts+owners
        checks = {}
        for index, row in enumerate(public):
            payload = row.get("payload", {})
            if payload.get("status") is not None:
                checks[f"public:{index}:legal_terminal"] = payload.get("status") in {"SUCCESS", "SUCCEEDED", "FAILED", "CANCELLED", "OUTPUT_BEST_EFFORT", "partial_success", "success", "failed", "cancelled"}
            for usage in usages:
                if (usage.get("task_id"), usage.get("run_id")) == (payload.get("task_id"), payload.get("run_id")):
                    checks[f"public:{index}:usage_finalized"] = usage.get("finalized") is True
        for index, row in enumerate(drain):
            if diagnostic_frame_valid(row) and diagnostic_capture_complete(snapshot):
                checks[f"drain:{index}:returned_without_error"] = row.get("event_type") != "SPAN_ERROR"
        missing = ["TERMINAL_ATTEMPT_AND_USAGE_SETTLEMENT_JOIN_NOT_RECORDED",
                   "EXPLICIT_PHYSICAL_IDLE_UNKNOWN_CALL_AND_OWNED_RESOURCE_CLOSE_PROOF_NOT_RECORDED"]
        if not public: missing.append("NATIVE_PUBLIC_TERMINAL_NOT_RECORDED")
        return _native_result(checks, missing, basis="PUBLIC_USAGE_AND_DRAIN_BOUNDARY"), public+usages+drain+workers+runs+attempts+owners
    if edge_id == "X29":
        from core.hashing import canonical_sha256
        by_family = defaultdict(list)
        for loaded in snapshot.sources.values():
            data = loaded.data if isinstance(loaded.data, (list, tuple)) else [loaded.data]
            by_family[loaded.family].extend(r for r in data if isinstance(r, Mapping))
        required_families = ("source_fact", "artifact_runtime", "memory_record", "memory_readback",
                             "memory_freshness", "memory_source_resolution", "memory_capability")
        if any(by_family[name] for name in required_families):
            missing = ["NATIVE_MEMORY_SOURCE_FAMILY_NOT_RECORDED:"+name for name in required_families if not by_family[name]]
            singleton = [name for name in required_families if name != "artifact_runtime" and len(by_family[name]) != 1]
            missing += ["UNIQUE_NATIVE_MEMORY_SOURCE_NOT_RECORDED:"+name for name in singleton if by_family[name]]
            used = [r for name in required_families for r in by_family[name]]
            if missing:
                return _native_result({}, missing, basis="NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"), used
            original, readback = by_family["memory_record"][0], by_family["memory_readback"][0]
            physical, resolution = by_family["source_fact"][0], by_family["memory_source_resolution"][0]
            freshness, capability = by_family["memory_freshness"][0], by_family["memory_capability"][0]
            primary = original.get("source_binding", {}).get("primary_source", {})
            exact = primary.get("exact_source_ref", {})
            reference = physical.get("reference", {})
            try:
                raw = base64.b64decode(physical["content_base64"], validate=True)
                content = json.loads(raw)
            except (KeyError, ValueError, TypeError):
                return _native_result({"original_source_bytes_decode": False}, [], basis="NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"), used
            versions = [r for r in by_family["artifact_runtime"] if r.get("event_type") == "version_committed"
                        and r.get("version_ref", {}).get("artifact_version_id") == exact.get("artifact_version_id")]
            promotions = [r.get("receipt", {}) for r in by_family["artifact_runtime"] if r.get("event_type") == "promotion_committed"
                          and r.get("receipt", {}).get("logical_ref") == exact.get("logical_ref")]
            if len(versions) != 1 or not promotions:
                return _native_result({}, ["ORIGINAL_ARTIFACT_VERSION_AND_ACTIVE_HEAD_NOT_RECORDED"], basis="NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"), used
            head_revision, active_id = 0, None
            valid_heads = True
            for promotion in promotions:
                valid_heads &= (promotion.get("previous_head_revision") == head_revision
                    and promotion.get("previous_active_version_id") == active_id
                    and promotion.get("resulting_head_revision") == head_revision+1)
                head_revision, active_id = promotion.get("resulting_head_revision"), promotion.get("promoted_version_id")
            recalled = readback.get("projection", {}).get("projection_payload", {}).get("source_ref", {})
            resolver_key = [primary.get("source_authority"), primary.get("source_kind"), primary.get("exact_source_ref_schema_id")]
            source = {"source_ref": exact.get("content_ref"), "source_version": exact.get("artifact_version_id"),
                      "raw_sha256": sha256(raw).hexdigest()}
            target = {"source_ref": recalled.get("content_ref"), "source_version": recalled.get("artifact_version_id"),
                "raw_sha256": str(recalled.get("content_hash", "")).removeprefix("sha256:"),
                "readback_payload": raw, "durable_memory_required": capability.get("required"),
                "locator_current": valid_heads and active_id == exact.get("artifact_version_id") and resolution.get("current") is True
                    and freshness.get("primary_source_current") is True,
                "source_authorized": resolver_key in capability.get("source_resolver_keys", [])
                    and primary.get("source_scope_ref") == exact.get("logical_ref", {}).get("domain_scope_ref")
                    and primary.get("source_kind") == exact.get("logical_ref", {}).get("artifact_type")}
            verdict = evaluate_runtime_edge("X29", source, target)
            if verdict["measurement_state"] == "N/A":
                return {**verdict, "basis": "NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"}, used
            if verdict["measurement_state"] != "MEASURED":
                return {**verdict, "basis": "NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"}, used
            binding = original.get("source_binding", {})
            checks = {**verdict.get("checks", {}),
                "original_source_size": reference.get("size_bytes") == len(raw),
                "original_source_hash": reference.get("content_hash") == exact.get("content_hash") == "sha256:"+sha256(raw).hexdigest(),
                "physical_exact_content_ref": reference.get("artifact_ref") == exact.get("content_ref"),
                "physical_exact_version_schema": reference.get("schema_version") == exact.get("content_schema_version"),
                "version_ledger_exact_ref": versions[0].get("version_ref") == exact,
                "source_run_scope": original.get("memory_ref", {}).get("run_id") == exact.get("logical_ref", {}).get("run_id") == reference.get("run_id"),
                "exact_ref_hash": primary.get("exact_source_ref_sha256") == "sha256:"+canonical_sha256(exact),
                "source_semantic_hash": primary.get("source_semantic_fingerprint") == resolution.get("source_semantic_fingerprint") == "sha256:"+canonical_sha256(content),
                "independent_source_resolution": resolution.get("exact_source_payload") == exact and resolution.get("source_resolvable") is True and resolution.get("integrity_valid") is True,
                "independent_memory_readback": original == readback,
                "locator_exact_source": recalled == exact,
                "freshness_exact_binding": freshness.get("memory_ref") == original.get("memory_ref") and freshness.get("assessed_source_set_sha256") == binding.get("source_set_sha256")
                    and freshness.get("assessed_with_resolver_id") == binding.get("currentness_resolver_id") and freshness.get("assessed_with_policy_version") == binding.get("currentness_policy_version"),
                "freshness_admission": freshness.get("eligibility") == "ACTIVE_RECALL" and freshness.get("source_resolvable") is True and freshness.get("integrity_valid") is True,
                "active_head_chain": valid_heads}
            if binding.get("dependency_sources"):
                return _native_result(checks, ["INDEPENDENT_DEPENDENCY_SOURCE_READBACK_NOT_RECORDED"], basis="NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"), used
            return _native_result(checks, [], basis="NATIVE_ARTIFACT_MEMORY_OWNER_WRITE_READBACK"), used
        memories = [r for r in records if r.get("schema_version") == "durable_memory_record_v1"]
        assessments = [r for r in records if r.get("schema_version") == "memory_freshness_assessment_v1"]
        checks = {}
        for index, row in enumerate(memories):
            binding = row.get("source_binding", {})
            primary = binding.get("primary_source", {})
            checks[f"memory:{index}:exact_ref_hash"] = primary.get("exact_source_ref_sha256") == "sha256:"+canonical_sha256(primary.get("exact_source_ref"))
            for assessment in assessments:
                if (assessment.get("memory_ref") == row.get("memory_ref")
                    and assessment.get("assessed_source_set_sha256") == binding.get("source_set_sha256")
                    and assessment.get("eligibility") == "ACTIVE_RECALL"):
                    checks[f"memory:{index}:source_resolvable"] = assessment.get("source_resolvable") is True
                    checks[f"memory:{index}:source_integrity"] = assessment.get("integrity_valid") is True
                    checks[f"memory:{index}:primary_current"] = assessment.get("primary_source_current") is True
        missing = ["DURABLE_MEMORY_ENABLEMENT_AND_SOURCE_READBACK_RAW_HASH_NOT_RECORDED",
                   "ORIGINAL_SOURCE_AUTHORIZATION_NOT_RECORDED"]
        if not memories: missing.append("NATIVE_MEMORY_RECORD_NOT_RECORDED")
        return _native_result(checks, missing, basis="MEMORY_ENVELOPE_AND_EXACT_FRESHNESS_ASSESSMENT"), memories+assessments
    return _native_result({}, ["UNKNOWN_RUNTIME_EDGE"], basis="NONE"), []


def extract_runtime_observations(snapshot: SnapshotView | Mapping[str, Any], subject_id="unknown", **_kwargs) -> ObservationBatch:
    if not isinstance(snapshot, SnapshotView):
        snapshot = SnapshotView.from_records(snapshot)
    batch = ObservationBatch("06")
    records = snapshot.records()
    record_refs = {}
    for source in snapshot.sources.values():
        data = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for index, row in enumerate(data):
            if isinstance(row, Mapping):
                record_refs[id(row)] = replace(source.evidence, pointer=f"/{index}" if isinstance(source.data, (list, tuple)) else "")
    capture_incomplete=bool(snapshot.limitations) or not diagnostic_capture_complete(snapshot)
    for source in snapshot.sources.values():
        if source.family.startswith(("runtime", "diagnostic", "usage", "lifecycle", "artifact", "memory")):
            batch.evidence.append(source.evidence)
        if source.family.startswith("diagnostic"):
            data=source.data if isinstance(source.data,(list,tuple)) else [source.data]
            seals=[r for r in data if isinstance(r,dict) and r.get("event_type")=="SESSION_SEAL"]
            if not seals or any(r.get("lost_count")!=0 or r.get("completeness")!="COMPLETE" for r in seals):
                capture_incomplete=True
    explicit = [(r,r["unit_id"]) for r in records if r.get("unit_id") in {"F01", "F02", "F03", "F05", "F06"}]
    explicit += [(r,"F06") for r in records if r.get("unit_id") is None and r.get("event_type") in {"SPAN_RESULT","SPAN_ERROR"}]
    for row,unit in explicit:
        complete = row.get("completeness") == "COMPLETE" and not snapshot.limitations and (not str(row.get("schema_version", "")).startswith("diagnostic_") or not capture_incomplete)
        disabled = unit == "F03" and row.get("durable_memory_required") is False
        state = "N/A" if disabled else "MEASURED" if complete else "UNMEASURED"
        instance = row.get("span_id") if row.get("span_id") and diagnostic_frame_valid(row) else row.get("operation_ref")
        attempt = row.get("provider_attempt_id") or ("event:"+str(row["sequence"]) if row.get("span_id") is None and row.get("sequence") is not None else None)
        segment = ":".join(str(row.get(name, "unknown")) for name in ("session_id", "segment_id", "process_id")) if row.get("session_id") else snapshot.snapshot_id
        batch.stages.append(StageObservation(unit, subject_id, operation_id=instance, source_segment=segment, scope=str(row.get("scope", row.get("event_type", "runtime"))), attempt=attempt, enabled=not disabled, applicable=not disabled, executed=True, observed_execution_status=str(row.get("status", "OBSERVED")), measurement_state=state, payload=row, evidence_refs=(record_refs[id(row)],), identity_quality="ORIGINAL_SPAN_ID" if row.get("span_id") and diagnostic_frame_valid(row) else str(row.get("identity_quality", "ORIGINAL_SOURCE"))))
        for code in check_lifecycle_and_replay(row):
            batch.findings.append(Finding(code, subject_id, code, unit_id=unit, origin="production"))
    for unit in ("F01", "F02", "F03", "F05", "F06"):
        if not any(s.unit_id == unit for s in batch.stages):
            batch.stages.append(StageObservation(unit, subject_id, source_segment=snapshot.snapshot_id, measurement_state="UNMEASURED", payload={"reason": "NOT_RECORDED"}))
    usage = [{**r,"token_usage":(r.get("material") or r).get("token_usage"),"outcome":"UNKNOWN" if r.get("event_type")=="MODEL_CLIENT_EXCEPTION" else r.get("outcome","RETURNED")} for r in records if r.get("provider_attempt_id") and (r.get("event_type") in {"MODEL_CLIENT_RAW_RESPONSE","MODEL_CLIENT_EXCEPTION"} or (not r.get("event_type") and r.get("token_usage")))]
    if usage:
        accounting = reconcile_usage(usage, dispatches=[r for r in records if r.get("event_type") == "MODEL_ATTEMPT_REQUEST"] or None)
        batch.metrics.append(MetricResult("physical_attempts", "F01", "runtime", None if capture_incomplete else accounting["physical_attempts"], measurement_state="UNMEASURED" if capture_incomplete else "MEASURED", input_completeness="PARTIAL" if capture_incomplete else "COMPLETE", sample_counts={"observed_physical_prefix":accounting["physical_attempts"]}, reason="full count unavailable for incomplete prefix" if capture_incomplete else "original physical identity deduplication"))
        batch.metrics.append(MetricResult("actual_cost", "F01", "runtime", accounting["cost"], measurement_state=accounting["cost_state"], reason="frozen price version required"))
    for edge in ("X22", "X23", "X24", "X28", "X29"):
        row = next((r for r in records if r.get("edge_id") == edge), None)
        if row:
            verdict=evaluate_runtime_edge(edge,row.get("source"),row.get("target"))
            endpoints, used = row, [row]
        else:
            verdict, used = _native_runtime_edge(edge, snapshot, records)
            endpoints = {"representation": "NATIVE_OWNER_EXPORTS", "records": used}
        if snapshot.limitations and edge != "X24":
            verdict={"measurement_state":"UNMEASURED","assertion_result":None,"reason":"SOURCE_SNAPSHOT_INCOMPLETE"}
        complete=verdict["measurement_state"]=="MEASURED"
        refs=tuple(dict.fromkeys(record_refs[id(r)] for r in used if id(r) in record_refs))
        batch.edges.append(EdgeObservation(edge, subject_id, source_refs=refs, target_refs=refs, measurement_state=verdict["measurement_state"], assertion_result=verdict["assertion_result"], availability="PERSISTED" if used else "NOT_RECORDED", completeness="COMPLETE" if complete else "UNKNOWN", reason=verdict["reason"], payload={"endpoints":endpoints,"calculation":verdict}))
    batch.limitations.extend(snapshot.limitations)
    if capture_incomplete:
        batch.limitations.append("F06:CAPTURE_INCOMPLETE")
    batch.limitations.append("T15:PENDING_RUN_AUTHORIZATION")
    return batch
