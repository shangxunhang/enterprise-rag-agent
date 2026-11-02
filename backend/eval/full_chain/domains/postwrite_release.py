"""Passive PostWrite exact-row decoding, completion accounting and release findings."""
from __future__ import annotations

from dataclasses import replace
import base64
from hashlib import sha256
import json
from typing import Any, Mapping
from core.hashing import canonical_sha256 as production_semantic_hash

from ..contracts import EdgeObservation, Finding, MetricResult, ObservationBatch, StageObservation, canonical_hash
from ..snapshot import SnapshotView


CURRENT_POSTWRITE_SCHEMA = "gate_v2_post_write_store_v4"
STAGES = {"S-PRECHECK": "E01", "S-DRAFT": "E02", "S-ENTRY": "E03", "S-PLAN": "E03",
    "S-ITEM": "E04", "S-GATE": "E09", "S-CONTROL": "E09", "S-ASSEMBLY": "E10",
    "S-ASSESS": "E11", "S-PUBLISH": "E11", "S-PUBLIC": "E11", "S-REPLAY": "E12"}
QUALITY_UNITS = {"faithfulness": "E04", "citation": "E05", "relevancy": "E06", "coverage": "E07", "consistency": "E08"}
PRODUCER_UNITS = {"adherence": "E06", "integrity": "E05", "grounding": "E05", "coverage": "E07",
    "structural_coverage": "E07", "shared_projection": "E04", "FTH03": "E04", "consistency": "E08", "gate": "E09"}
FACT_UNITS = {"CLAIM_EVIDENCE_SUPPORT": "E04", "FAITHFULNESS": "E04", "REQUIREMENT_ADHERENCE": "E06",
    "CITATION_INTEGRITY": "E05", "CITATION_GROUNDING": "E05", "COVERAGE_COMPLETENESS": "E07", "ARTIFACT_CONSISTENCY": "E08"}


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _map(value):
    return value if isinstance(value,Mapping) else {}


def _original_projection_equal(source, target):
    """A recorded allowlist projection keeps every original captured field."""
    if isinstance(source, Mapping) and isinstance(target, Mapping):
        return bool(source) and "capture" not in source and "capture" not in target and all(
            key in target and _original_projection_equal(value, target[key]) for key, value in source.items())
    if isinstance(source, (list, tuple)) and isinstance(target, (list, tuple)):
        return len(source) == len(target) and all(_original_projection_equal(a,b) for a,b in zip(source,target))
    return source == target


def _native_owner_rows(snapshot):
    """Read original bounded owner rows; never construct persistence owners."""
    records = {}
    for source in snapshot.sources.values():
        if source.family == "postwrite" and isinstance(source.data, Mapping):
            decoded = read_postwrite_records(snapshot, source.name)
            for table in ("validation_completions", "delivery_gate_decisions", "post_write_control_decisions", "artifact_states", "post_write_entries"):
                for row in decoded["records"].get(table, ()):
                    record = row["record"]
                    records.setdefault(record.get("schema_version"), []).append((record, row["evidence"]))
        elif source.family in {"lifecycle", "run_completion_assessments", "release_payload", "release_manifest", "assembly_manifest", "control_records", "document_precheck"}:
            values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
            for ordinal, row in enumerate(values):
                if not isinstance(row, Mapping):
                    continue
                record = row.get("record", row)
                evidence = replace(source.evidence, pointer="/" + str(ordinal))
                if row.get("content_encoding") == "json-utf8" or (source.family == "release_payload"
                    and row.get("content_encoding") == "binary" and row.get("reference", {}).get("schema_version") == "post_write_release_payload_v1"):
                    try:
                        encoded = row["content_base64"]
                        if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
                            raise ValueError("bounded release content exceeded")
                        raw = base64.b64decode(encoded, validate=True)
                        if "sha256:" + sha256(raw).hexdigest() != row["reference"].get("content_hash"):
                            raise ValueError("release content hash mismatch")
                        record = {"reference":row["reference"], "content":json.loads(raw),
                            "schema_version":row["reference"].get("schema_version")}
                    except (ValueError, TypeError, KeyError):
                        continue
                if isinstance(record, Mapping):
                    try:
                        if record.get("schema_version") == "run_lifecycle_record_v2":
                            from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleRecordSchema
                            RunLifecycleRecordSchema.model_validate(record)
                        elif record.get("schema_version") == "run_completion_assessment_record_v1":
                            from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunCompletionAssessmentRecordSchema
                            RunCompletionAssessmentRecordSchema.model_validate(record)
                        elif record.get("schema_version") == "assembly_input_manifest_v2":
                            from apps.enterprise_document.schemas.post_write.document import AssemblyInputManifestSchema
                            AssemblyInputManifestSchema.model_validate(record)
                        elif record.get("schema_version") == "post_write_control_decision_v1":
                            from apps.enterprise_document.schemas.post_write.control import PostWriteControlDecisionSchema
                            PostWriteControlDecisionSchema.model_validate(record)
                    except ValueError:
                        # Preserve a faulty native consumer as observed evidence;
                        # invalid source schemas cannot establish authority.
                        record = {**record, "_native_schema_invalid":True}
                    if source.family == "document_precheck":
                        records.setdefault("document_precheck_boundary", []).append((record, evidence))
                    records.setdefault(record.get("schema_version"), []).append((record, evidence))
    return records


def _native_edge(edge_id, source, target, source_refs=(), target_refs=(), *, result=None, reason="NATIVE_ENDPOINT_JOIN_NOT_PROVEN"):
    edge = EdgeObservation(edge_id, str((source or target or {}).get("run_id") or "evaluation-local:unknown"),
        source_key=canonical_hash(source) if source is not None else None,
        target_key=canonical_hash(target) if target is not None else None,
        source_refs=tuple(source_refs), target_refs=tuple(target_refs), assertion_result=result,
        measurement_state="MEASURED" if result is not None else "UNMEASURED", reason=reason,
        allowed_transformation="ORIGINAL_NATIVE_STRUCTURAL_BINDING", availability="PERSISTED",
        completeness="COMPLETE" if result is not None else "PARTIAL",
        payload={"source_observation":source,"target_observation":target})
    return edge


def _native_index(rows, key):
    buckets = {}
    for row, evidence in rows:
        value = key(row)
        if value is not None:
            buckets.setdefault(canonical_hash(value), []).append((row,evidence))
    return lambda value: buckets.get(canonical_hash(value), ()) if value is not None else ()


def _native_postwrite_edges(snapshot):
    """Native operands reach the same structural checkers, with independent refs."""
    owners = _native_owner_rows(snapshot)
    completions = owners.get("validation_completion_v1", ())
    gates = owners.get("delivery_gate_decision_v1", ()) or [(row,ref) for row,ref in _endpoint_rows(snapshot,"S-GATE")
        if row.get("schema_version")=="delivery_gate_decision_v1" and row.get("delivery_gate_decision_id") and row.get("decision_hash")]
    controls = owners.get("post_write_control_decision_v1", ())
    control_by_id = _native_index(controls, lambda row:row.get("control_decision_id"))
    gate_by_id = _native_index(gates, lambda row:row.get("delivery_gate_decision_id"))
    gate_by_validation = _native_index(gates, lambda row:row.get("validation_run_id"))
    completion_by_id = _native_index(completions, lambda row:row.get("completion_id"))
    completion_by_subject = _native_index(completions, lambda row:(row.get("validation_run_id"),row.get("subject_hash")))
    result = {}
    for boundary, evidence in owners.get("document_precheck_boundary", ()):
        if boundary.get("owner_method") != "apps.enterprise_document.services.agent_rag_v1.document_verifier.DocumentVerifierService.verify_mainline":
            continue
        source, target = boundary.get("input"), boundary.get("result")
        edge = check_postwrite_edge("X16", {"native_verifier_input":source} if isinstance(source,Mapping) else None,
            {"native_verifier_result":target} if isinstance(target,Mapping) else None)
        result.setdefault("X16", []).append(replace(edge,
            source_refs=(replace(evidence,pointer=evidence.pointer + "/input"),),
            target_refs=(replace(evidence,pointer=evidence.pointer + "/result"),) if target is not None else (),
            payload={"source_observation":source,"target_observation":target}))
    manifests = owners.get("assembly_input_manifest_v2", ())
    manifest_by_id = _native_index(manifests, lambda row:row.get("manifest_id"))
    if manifests or controls:
        edges = []
        for manifest, evidence in manifests:
            required = [entry.get("selected_section_control_decision_ref") for entry in manifest.get("ordered_section_entries", ())
                if entry.get("selection_state") != "MISSING"]
            linked, refs, proven = [], [], True
            for expected in required:
                candidates = control_by_id(expected.get("control_decision_id")) if isinstance(expected,Mapping) else []
                refs.extend(r for _,r in candidates)
                if len(candidates) != 1 or candidates[0][0].get("_native_schema_invalid"):
                    proven = False
                else:
                    linked.append(candidates[0][0])
            source = {"controls":linked} if proven and required else None
            edge = check_postwrite_edge("X19", source, {"assembly_manifest":manifest})
            edges.append(replace(edge, source_refs=tuple(refs), target_refs=(evidence,),
                payload={"source_observation":source,"target_observation":manifest}))
        if not manifests:
            edges = [_native_edge("X19", c, None, (r,)) for c,r in controls]
        result["X19"] = edges
    assessments = owners.get("run_completion_assessment_record_v1", ())
    document_entries = owners.get("document_post_write_entry_v1", ())
    entry_by_id = _native_index(document_entries, lambda row:row.get("identity",{}).get("entry_id"))
    if manifests or assessments:
        edges = []
        for assessment, evidence in assessments:
            expected_entry = assessment.get("document_post_write_entry_ref", {}).get("exact_ref_payload", {})
            entries = entry_by_id(expected_entry.get("entry_id"))
            expected_control = assessment.get("post_write_control_decision_ref", {}).get("exact_ref_payload", {})
            linked_controls = control_by_id(expected_control.get("control_decision_id"))
            source, target, refs = None, None, [ref for _,ref in (*entries,*linked_controls)]
            if len(entries) == len(linked_controls) == 1:
                entry, entry_ref = entries[0]; control, control_ref = linked_controls[0]
                manifest_ref = entry.get("identity", {}).get("assembly_input_manifest_ref", {})
                matching = manifest_by_id(manifest_ref.get("manifest_id"))
                expected_gate = control.get("delivery_gate_decision_ref", {})
                linked_gates = gate_by_id(expected_gate.get("delivery_gate_decision_id"))
                refs.extend(ref for _,ref in (*matching,*linked_gates))
                if len(matching) == len(linked_gates) == 1:
                    manifest, manifest_evidence = matching[0]; gate, gate_ref = linked_gates[0]
                    expected_completion = gate.get("gate_input_manifest", {}).get("validation_completion_ref", {})
                    linked_completions = completion_by_id(expected_completion.get("completion_id"))
                    refs.extend(ref for _,ref in linked_completions)
                    if len(linked_completions) == 1 and not any(x.get("_native_schema_invalid") for x in (entry,control,manifest,gate)):
                        completion, completion_ref = linked_completions[0]
                        source = {"assembly_manifest":manifest,"document_entry":entry}
                        target = {"assessment":assessment,"control":control,"gate":gate,"completion":completion}
            edge = check_postwrite_edge("X20", source, target)
            edges.append(replace(edge, source_refs=tuple(refs), target_refs=(evidence,),
                payload={"source_observation":source,"target_observation":target or assessment}))
        if not assessments:
            edges = [_native_edge("X20", m, None, (r,)) for m,r in manifests]
        result["X20"] = edges
    plans = _endpoint_rows(snapshot, "S-PLAN")
    plan_by_subject = _native_index(plans, lambda row:(row.get("validation_run_id"),row.get("subject_hash")))
    # The original S-PLAN is the independent obligation declaration. Completion
    # is not reused to declare its own required membership.
    if plans or gates:
        edges = []
        for gate, gate_ref in gates:
            validation = gate.get("validation_run_id")
            identity = (validation,gate.get("gate_input_manifest",{}).get("subject_hash"))
            declarations = plan_by_subject(identity) if validation else []
            formal = completion_by_subject(identity) if validation else []
            source = {"declaration":declarations[0][0], "completion":formal[0][0]} if len(declarations) == len(formal) == 1 else None
            edge = check_postwrite_edge("X17", source, gate)
            edges.append(replace(edge, source_refs=tuple(r for _,r in (*declarations,*formal)), target_refs=(gate_ref,),
                reason="NATIVE_REQUIRED_COMPLETION_GATE_BINDING" if edge.assertion_result is not None else "NATIVE_REQUIRED_DECLARATION_OR_COMPLETION_NOT_PROVEN",
                payload={"source_observation":source,"target_observation":gate}))
        if not gates:
            edges = [_native_edge("X17", p, None, (r,)) for p,r in plans]
        result["X17"] = edges
    capture_controls = _endpoint_rows(snapshot, "S-CONTROL")
    consumers = capture_controls or list(controls)
    if gates or controls:
        edges = []
        for control, control_ref in consumers:
            validation = control.get("validation_run_id")
            expected = control.get("delivery_gate_decision_ref", control.get("gate_ref", {}))
            candidates = gate_by_validation(validation) if validation else gate_by_id(expected.get("delivery_gate_decision_id")) if isinstance(expected,Mapping) else []
            if len(candidates) == 1:
                gate, ref = candidates[0]
                projected = {**gate, "gate_ref":{k:gate[k] for k in
                    ("schema_version","delivery_gate_decision_id","gate_contract_version","decision_hash") if k in gate}}
                edge = check_control_and_assembly(projected, control)
                edges.append(replace(edge, source_refs=(ref,), target_refs=(control_ref,),
                    reason="NATIVE_GATE_CONTROL_EXACT_BINDING" if edge.assertion_result is not None else edge.reason,
                    payload={"source_observation":gate,"target_observation":control}))
            else:
                edges.append(_native_edge("X18", None, control, tuple(r for _,r in candidates), (control_ref,)))
        if not consumers:
            edges = [_native_edge("X18", g, None, (r,)) for g,r in gates]
        result["X18"] = edges
    publications = [(row,ref) for row,ref in _endpoint_rows(snapshot, "S-PUBLISH")
        if _map(row.get("request")).get("schema_version")=="post_write_release_request_v1"]
    terminals = [(r,e) for r,e in owners.get("run_lifecycle_record_v2", ()) if r.get("status") == "success"]
    terminal_by_revision = _native_index(terminals, lambda row:(row.get("identity",{}).get("run_id"),row.get("lifecycle_revision")))
    assessment_by_id = _native_index(assessments, lambda row:row.get("assessment_id"))
    if publications or terminals:
        edges = []
        for publication, evidence in publications:
            request = publication.get("request", {})
            run = request.get("run_id")
            candidates = terminal_by_revision((run,publication.get("run_lifecycle_revision"))) if run else []
            source, refs = None, [r for _,r in candidates]
            if len(candidates) == 1:
                terminal = candidates[0][0]
                expected = terminal.get("run_completion_assessment_ref", {})
                matched = assessment_by_id(expected.get("assessment_id"))
                refs.extend(r for _,r in matched)
                if len(matched) == 1 and not terminal.get("_native_schema_invalid") and not matched[0][0].get("_native_schema_invalid"):
                    source = {"terminal":terminal,"assessment":matched[0][0],"run_id":run}
            edge = check_postwrite_edge("X21", source, publication)
            edges.append(replace(edge, source_refs=tuple(refs), target_refs=(evidence,),
                payload={"source_observation":source,"target_observation":publication}))
        if not publications:
            edges = [_native_edge("X21", t, None, (r,)) for t,r in terminals]
        result["X21"] = edges
    publics = [(row,ref) for row,ref in _endpoint_rows(snapshot, "S-PUBLIC")
        if _map(_map(row.get("target_release_publication")).get("manifest")).get("schema_version")=="post_write_release_manifest_v1"]
    payloads = owners.get("post_write_release_payload_v1", ())
    publication_by_run = _native_index(publications, lambda row:row.get("request",{}).get("run_id"))
    payload_by_ref = _native_index(payloads, lambda row:row.get("reference",{}).get("artifact_ref"))
    if publications or publics:
        edges = []
        for public, evidence in publics:
            run = public.get("run_id")
            candidates = publication_by_run(run)
            source, refs = None, [r for _,r in candidates]
            if len(candidates) == 1:
                publication = candidates[0][0]
                manifest = publication.get("manifest", publication.get("result", {}).get("manifest", {}))
                bound = manifest.get("release_payload_artifact", {})
                actual = payload_by_ref(bound.get("artifact_ref"))
                refs.extend(r for _,r in actual)
                if len(actual) == 1:
                    source = {"manifest":manifest,"release_payload":actual[0][0],"run_id":run}
            edge = check_postwrite_edge("X27", source, public)
            edges.append(replace(edge, source_refs=tuple(refs), target_refs=(evidence,),
                payload={"source_observation":source,"target_observation":public}))
        if not publics:
            edges = [_native_edge("X27", p, None, (r,)) for p,r in publications]
        result["X27"] = edges
    return result


def _rows(snapshot, key):
    value = snapshot.get(key)
    rows = list(value) if isinstance(value, (list, tuple)) else [value] if isinstance(value, dict) else []
    rows.extend(row for row in snapshot.records("diagnostics")
        if str(row.get("event_type", "")).startswith(key))
    if key == "S-ITEM":
        rows.extend(row for row in snapshot.records("diagnostics") if row.get("stage") == "S-ITEM")
    return [{**row, **row.get("payload", {})} if isinstance(row.get("payload"), dict) else row for row in rows]


def _endpoint_rows(snapshot, key):
    """Keep exact event locators; begin/error frames cannot prove result edges."""
    result = []
    for source in snapshot.sources.values():
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, original in enumerate(values):
            if not isinstance(original, Mapping):
                continue
            kind = original.get("event_type", "")
            if not (source.name == key or source.family == key or kind == key or kind.startswith(key + "-")):
                continue
            if kind.endswith(("-BEGIN", "-ERROR")):
                continue
            row = {**original, **original.get("payload", {})} if isinstance(original.get("payload"), Mapping) else dict(original)
            if isinstance(row.get("result"), Mapping):
                # The original result remains present; only captured exact fields
                # are projected, without manufacturing any expected obligation.
                row = {**row, **row["result"]}
            result.append((row, replace(source.evidence, pointer="/" + str(ordinal))))
    return result


def _endpoint_identity(row):
    """Only explicit original lineage, never parse IDs or display text."""
    fields = ("run_id", "task_id", "section_id", "document_id", "scope", "scope_ref", "attempt_id", "validation_run_id")
    containers = [row]
    for key in ("identity", "artifact_ref", "document_artifact_ref", "terminal_ref", "request", "result", "operation_ref"):
        if isinstance(row.get(key), Mapping):
            containers.append(row[key])
    values = {}
    for field in fields:
        distinct = {canonical_hash(container[field]): container[field] for container in containers
            if container.get(field) is not None and container.get(field) != "unknown"}
        if len(distinct) == 1:
            values[field] = next(iter(distinct.values()))
        elif len(distinct) > 1:
            values[field] = {"conflicting_original_identity": True}
    return values


def _parent_join_ref(edge_id, row, *, source):
    """An existing exact parent ref is a join key, never a new obligation."""
    if edge_id == "X17":
        before = row.get("completion", {}) if source else row.get("gate_input_manifest", row).get("validation_completion_ref", {})
        keys = ("completion_id", "completion_sha256", "validation_run_id", "subject_hash")
        return {key: before[key] for key in keys} if isinstance(before, Mapping) and all(before.get(k) is not None for k in keys) else None
    if edge_id == "X18":
        return row.get("gate_ref")
    if edge_id in {"X19", "X20"}:
        return row.get("document_artifact_ref") or (row.get("document_checks", {}).get("artifact_ref") if not source else None)
    if edge_id == "X21":
        request = row if source else row.get("request", {})
        return {k: request[k] for k in ("terminal_ref", "assessment_ref")} if isinstance(request, Mapping) and all(request.get(k) is not None for k in ("terminal_ref", "assessment_ref")) else None
    if edge_id == "X27":
        return row.get("artifact_ref")
    if edge_id == "X16":
        return row.get("section_refs" if source else "input_section_refs")
    return None


def _endpoint_candidate_index(rows, edge_id, *, source):
    """Index identity masks once; retain at most two matches to prove ambiguity."""
    identities = [_endpoint_identity(row) for row, _ in rows]
    parent_refs = [_parent_join_ref(edge_id, row, source=source) for row, _ in rows]
    groups, cache = {}, {}
    for index, identity in enumerate(identities):
        if any(value == {"conflicting_original_identity": True} for value in identity.values()):
            continue
        groups.setdefault(tuple(sorted(identity)), []).append(index)

    def lookup(row, *, other_source):
        identity = _endpoint_identity(row)
        if any(value == {"conflicting_original_identity": True} for value in identity.values()):
            return []
        parent = _parent_join_ref(edge_id, row, source=other_source)
        for use_parent in (False, True):
            if use_parent and parent is None:
                return []
            matches = []
            for mask, indices in groups.items():
                shared = tuple(sorted(set(mask) & set(identity)))
                if not shared and not use_parent:
                    continue
                cache_key = (mask, shared, use_parent)
                if cache_key not in cache:
                    buckets = {}
                    for index in indices:
                        if use_parent and parent_refs[index] is None:
                            continue
                        key = tuple(canonical_hash(identities[index][k]) for k in shared)
                        if use_parent:
                            key += (canonical_hash(parent_refs[index]),)
                        # Two entries are sufficient to prove non-uniqueness;
                        # every original endpoint is still returned as evidence.
                        bucket = buckets.setdefault(key, [])
                        if len(bucket) < 2:
                            bucket.append(index)
                    cache[cache_key] = buckets
                key = tuple(canonical_hash(identity[k]) for k in shared)
                if use_parent:
                    key += (canonical_hash(parent),)
                matches.extend(cache[cache_key].get(key, ())[:2-len(matches)])
                if len(matches) == 2:
                    break
            if len(matches) == 1:
                return matches
        return matches
    return lookup


def _joined_edge_observations(snapshot, edge_id, source_key, target_key):
    sources, targets = _endpoint_rows(snapshot, source_key), _endpoint_rows(snapshot, target_key)
    if not sources and not targets:
        check = check_control_and_assembly if edge_id == "X18" else lambda s, t, **kw: check_postwrite_edge(edge_id, s, t, **kw)
        return [check(None, None)]
    find_target = _endpoint_candidate_index(targets, edge_id, source=False)
    find_source = _endpoint_candidate_index(sources, edge_id, source=True)
    result, matched = [], set()
    for i, (source, evidence) in enumerate(sources):
        # Explicit singleton endpoint families retain the established pure
        # obligation oracle. Multi-instance inputs need a unique join both ways.
        linked = [0] if len(sources) == len(targets) == 1 else find_target(source, other_source=True)
        reverse = [i] if len(sources) == len(targets) == 1 else find_source(targets[linked[0]][0], other_source=False) if len(linked) == 1 else []
        target_index = linked[0] if len(linked) == 1 and reverse == [i] else None
        target, target_evidence = targets[target_index] if target_index is not None else (None, None)
        subject = str(source.get("subject_id") or _endpoint_identity(source).get("run_id") or "evaluation-local:unknown")
        edge = (check_control_and_assembly(source, target, subject_id=subject) if edge_id == "X18"
            else check_postwrite_edge(edge_id, source, target, subject_id=subject))
        if target_index is None:
            edge = replace(edge, reason="POSTWRITE_ORIGINAL_ENDPOINT_JOIN_NOT_PROVEN")
        elif any(value == {"conflicting_original_identity": True} for row in (source, target)
                for value in _endpoint_identity(row).values()) or any(
                _endpoint_identity(source)[k] != _endpoint_identity(target)[k]
                for k in set(_endpoint_identity(source)) & set(_endpoint_identity(target))):
            edge = replace(edge, assertion_result=None, measurement_state="UNMEASURED", completeness="PARTIAL",
                reason="POSTWRITE_ORIGINAL_LINEAGE_CONFLICT")
        elif source.get("completeness") == "CAPTURE_INCOMPLETE" or target.get("completeness") == "CAPTURE_INCOMPLETE":
            edge = replace(edge, assertion_result=None, measurement_state="UNMEASURED", completeness="PARTIAL",
                reason="POSTWRITE_ENDPOINT_CAPTURE_INCOMPLETE")
        result.append(replace(edge, source_refs=(evidence,), target_refs=(target_evidence,) if target_evidence else (),
            payload={"source_observation": source, "target_observation": target}))
        if target_index is not None:
            matched.add(target_index)
    for j, (target, evidence) in enumerate(targets):
        if j not in matched:
            edge = EdgeObservation(edge_id, str(target.get("run_id") or "evaluation-local:unknown"),
                target_key=canonical_hash(target), target_refs=(evidence,), reason="POSTWRITE_ORIGINAL_ENDPOINT_JOIN_NOT_PROVEN",
                payload={"source_observation": None, "target_observation": target})
            result.append(edge)
    return result


def read_postwrite_records(snapshot: SnapshotView, source_name="postwrite"):
    """Decode snapshot rows using schemas only; never construct a SQLite owner.

    The SnapshotView loader owns frozen/WAL consistency. Row and exact metadata
    mismatches remain errors with their table/row location, never repaired data.
    """
    from pydantic import TypeAdapter
    from apps.enterprise_document.schemas.post_write.foundation import (
        ArtifactStateRecordSchema, ValidationCompletionSchema, ValidationFactSchema, ValidatorExecutionRecordSchema)
    from apps.enterprise_document.schemas.post_write.entry import PostWriteEntrySchema, entry_ref_from_identity
    from apps.enterprise_document.schemas.post_write.gate import DeliveryGateDecisionSchema, QualityDeliveryContractSchema
    from apps.enterprise_document.schemas.post_write.control import PostWriteControlDecisionSchema
    from apps.enterprise_document.schemas.post_write.requirement import PostWriteRequirementSnapshotSchema

    source = snapshot.sources.get(source_name)
    if source is None or not isinstance(source.data, Mapping):
        return {"records": {}, "errors": ["POSTWRITE_SOURCE_UNAVAILABLE"], "limitations": list(snapshot.limitations)}
    tables = source.data
    metadata = {row.get("key"): row.get("value") for row in tables.get("post_write_metadata", [])}
    if metadata.get("schema_version") != CURRENT_POSTWRITE_SCHEMA or source.evidence.schema_version != CURRENT_POSTWRITE_SCHEMA:
        return {"records": {}, "errors": ["POSTWRITE_SCHEMA_VERSION_MISMATCH"], "limitations": list(snapshot.limitations)}
    decoders = {
        "artifact_states": ("record_json", ArtifactStateRecordSchema, {"artifact_id": "artifact_ref.artifact_id", "artifact_version": "artifact_ref.artifact_version", "artifact_hash": "artifact_ref.artifact_hash"}),
        "post_write_entries": ("entry_json", PostWriteEntrySchema, {"entry_id": "identity.entry_id", "entry_schema_version": "schema_version"}),
        "validation_completions": ("completion_json", ValidationCompletionSchema, {"completion_id": "completion_id", "validation_run_id": "validation_run_id", "subject_hash": "subject_hash", "completion_schema_version": "schema_version", "completion_contract_version": "contract_version", "completion_sha256": "completion_sha256"}),
        "validation_facts": ("fact_json", ValidationFactSchema, {"fact_id": "fact_id", "validation_run_id": "validation_run_id", "subject_hash": "subject_hash", "fact_schema_version": "schema_version"}),
        "validator_execution_records": ("record_json", ValidatorExecutionRecordSchema, {"validation_run_id": "validation_run_id", "validator_id": "validator_id"}),
        "delivery_gate_decisions": ("decision_json", DeliveryGateDecisionSchema, {"delivery_gate_decision_id": "delivery_gate_decision_id", "decision_schema_version": "schema_version", "decision_hash": "decision_hash"}),
        "post_write_control_decisions": ("decision_json", PostWriteControlDecisionSchema, {"control_decision_id": "control_decision_id", "decision_schema_version": "schema_version", "control_decision_hash": "control_decision_hash"}),
        "requirement_snapshots": ("snapshot_json", PostWriteRequirementSnapshotSchema, {"requirement_snapshot_id": "requirement_snapshot_id", "requirement_schema_version": "schema_version", "requirement_hash": "requirement_hash"}),
        "quality_delivery_contracts": ("contract_json", QualityDeliveryContractSchema, {"contract_id": "contract_id", "contract_schema_version": "schema_version", "contract_hash": "contract_hash"}),
    }
    records, errors = {}, []
    for table, (column, model, bindings) in decoders.items():
        records[table] = []
        for ordinal, row in enumerate(tables.get(table, [])):
            location = f"{source.evidence.locator}#{table}/row/{ordinal}"
            try:
                decoded = TypeAdapter(model).validate_json(row[column])
                value = decoded.model_dump(mode="json")
                for column_key, field in bindings.items():
                    field_value = value
                    for component in field.split("."):
                        field_value = field_value.get(component) if isinstance(field_value, dict) else None
                    if column_key in row and row[column_key] != field_value:
                        raise ValueError("EXACT_ROW_METADATA_MISMATCH:" + column_key)
                if table == "validation_completions":
                    material = dict(value); material.pop("completion_sha256")
                    if production_semantic_hash(material) != value["completion_sha256"]:
                        raise ValueError("COMPLETION_SEMANTIC_HASH_MISMATCH")
                if table == "validation_facts":
                    material = dict(value); material.pop("fact_sha256")
                    if row.get("fact_sha256") != value["fact_sha256"] or production_semantic_hash(material) != value["fact_sha256"]:
                        raise ValueError("FACT_SEMANTIC_HASH_MISMATCH")
                if table == "post_write_entries":
                    entry_ref = entry_ref_from_identity(decoded.identity).model_dump(mode="json")
                    for name in ("entry_schema_version", "entry_contract_version", "entry_sha256"):
                        if name in row and row[name] != entry_ref[name]:
                            raise ValueError("ENTRY_EXACT_HASH_MISMATCH:" + name)
                records[table].append({"location": location, "record": value,
                    "evidence": replace(source.evidence, pointer=f"/{table}/{ordinal}")})
            except (KeyError, ValueError, TypeError) as error:
                errors.append(location + ":" + str(error))
    return {"records": records, "errors": errors, "limitations": list(snapshot.limitations)}


def enumerate_required_items(completion=None, *, declaration=None):
    """Formal Completion wins only for accounting, not for erasing a mismatch."""
    completion, declaration = _plain(completion), _plain(declaration)
    if completion is not None:
        items = completion.get("items") if isinstance(completion, Mapping) else None
        provenance = "FORMAL_COMPLETION"
    elif declaration is not None:
        items = declaration.get("items") if isinstance(declaration, Mapping) else declaration
        provenance = "DERIVED_EXPECTED"
    else:
        return {"items": (), "provenance": "EXPECTED_UNKNOWN", "error": "REQUIRED_MANIFEST_MISSING"}
    if not isinstance(items, (list, tuple)):
        return {"items": (), "provenance": "EXPECTED_UNKNOWN", "error": "REQUIRED_MANIFEST_INVALID"}
    required = tuple(_plain(item) for item in items if _plain(item).get("required") is True)
    ids = [i.get("expected_validation_item_ref") for i in required]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        return {"items": required, "provenance": provenance, "error": "REQUIRED_IDENTITY_DUPLICATE_OR_MISSING"}
    return {"items": required, "provenance": provenance, "error": None}


def evaluate_quality_dimension(observed: Mapping[str, Any], reference: Mapping[str, Any] | None,
                               *, dimension: str, scope: str):
    """One pure evaluator, with independent denominators for every scope."""
    unit = QUALITY_UNITS[dimension]
    if observed.get("technical_error") or observed.get("parse_error"):
        return MetricResult(dimension + "_accuracy", unit_id=unit, scope=scope,
            measurement_state="TECHNICAL_FAILURE", reason="PRODUCER_SCHEMA_OR_TECHNICAL_FAILURE")
    if reference is None:
        return MetricResult(dimension + "_accuracy", unit_id=unit, scope=scope,
            measurement_state="UNMEASURED", reason="INDEPENDENT_REFERENCE_MISSING")
    if reference.get("scope") != scope:
        return MetricResult(dimension + "_accuracy", unit_id=unit, scope=scope,
            measurement_state="UNMEASURED", reason="INDEPENDENT_SCOPE_MISMATCH")
    expected = reference.get("expected_units")
    outputs = observed.get("units")
    if not isinstance(expected, list) or not isinstance(outputs, list):
        return MetricResult(dimension + "_accuracy", unit_id=unit, scope=scope,
            measurement_state="UNMEASURED", reason="INDEPENDENT_DENOMINATOR_MISSING")
    by_id = {str(i["id"]): i.get("verdict") for i in outputs}
    duplicate = len(by_id) != len(outputs)
    correct = sum(str(i["id"]) in by_id and by_id[str(i["id"])] == i.get("verdict") for i in expected)
    expected_ids = {str(i["id"]) for i in expected}
    extra = len(set(by_id) - expected_ids)
    return MetricResult(dimension + "_accuracy", unit_id=unit, scope=scope,
        numerator=correct, denominator=len(expected), value=correct / len(expected) if expected else None,
        measurement_state="MEASURED" if expected else "N/A",
        assertion_result=correct == len(expected) and not duplicate and not extra,
        reason="DUPLICATE_OR_UNEXPECTED_UNITS" if duplicate or extra else "INDEPENDENT_UNIT_VERDICTS",
        reference_ref=str(reference.get("reference_id", "synthetic-provisional")), sample_counts={"expected": len(expected), "observed": len(outputs), "extra": extra})


def check_control_and_assembly(gate, control, assembly=None, *, completion=None, subject_id="unknown"):
    gate, control, assembly, completion = map(_plain, (gate, control, assembly, completion))
    if not isinstance(gate, Mapping) or not isinstance(control, Mapping):
        return EdgeObservation("X18", subject_id, reason="GATE_CONTROL_BINDING_MISSING")
    action = control.get("enforcement_action", control.get("action"))
    expected_ref = gate.get("gate_ref", gate.get("delivery_gate_decision_ref"))
    observed_ref = control.get("delivery_gate_decision_ref", control.get("gate_ref"))
    if expected_ref is None or observed_ref is None:
        result, reason = None, "EXACT_GATE_REF_MISSING"
    else:
        if gate.get("schema_version") == "delivery_gate_decision_v1" and isinstance(expected_ref, Mapping) and isinstance(observed_ref, Mapping):
            required = ("schema_version", "delivery_gate_decision_id", "decision_hash")
            result = (_original_projection_equal(observed_ref, expected_ref) if all(k in observed_ref and k in expected_ref for k in required) else None)
        else:
            result = expected_ref == observed_ref
        reason = "GATE_CONTROL_EXACT_BINDING"
    allowed = control.get("allowed_actions")
    if allowed is not None and action not in allowed:
        result, reason = False, "CONTROL_POLICY_ACTION_MISMATCH"
    if assembly is not None and "selected_entries" in assembly and "allowed_entries" in control:
        if any(i not in control["allowed_entries"] for i in assembly["selected_entries"]):
            result, reason = False, "ASSEMBLY_SELECTION_NOT_AUTHORIZED"
    if completion is not None:
        bound = gate.get("gate_input_manifest", {}).get("validation_completion_ref")
        if bound is not None:
            keys = ("completion_id", "validation_run_id", "subject_hash", "completion_sha256")
            if any(completion.get(k) != bound.get(k) for k in keys):
                result, reason = False, "GATE_COMPLETION_MANIFEST_MISMATCH"
    return EdgeObservation("X18", subject_id, source_key=canonical_hash(gate), target_key=canonical_hash(control),
        allowed_transformation="FROZEN_CONTROL_POLICY", assertion_result=result,
        measurement_state="MEASURED" if result is not None else "UNMEASURED", reason=reason,
        availability="PERSISTED", completeness="COMPLETE" if result is not None else "PARTIAL")


def check_postwrite_edge(edge_id, source, target, *, subject_id="unknown"):
    """Exact endpoint checks for the six non-Control PostWrite edges."""
    if edge_id not in {"X16", "X17", "X19", "X20", "X21", "X27"}:
        raise ValueError("unsupported PostWrite edge")
    source, target = _plain(source), _plain(target)
    result, reason = None, "POSTWRITE_ENDPOINT_EVIDENCE_MISSING"
    transformation = {"X16": "EXACT_SECTION_INPUT_AND_INCREMENTAL_CHECK_SET", "X17": "ALL_REQUIRED_TO_EXACT_GATE_MANIFEST",
        "X19": "AUTHORIZED_FORMAL_ASSEMBLY_SELECTION", "X20": "DOCUMENT_SCOPE_VALIDATION_TO_ASSESSMENT",
        "X21": "TERMINAL_AND_ASSESSMENT_AUTHORIZED_PUBLICATION", "X27": "PUBLIC_DELIVERY_WITH_REVIEW_DISCLOSURE"}[edge_id]
    if isinstance(source, Mapping) and isinstance(target, Mapping):
        if edge_id == "X16":
            if isinstance(source.get("native_verifier_input"), Mapping) and isinstance(target.get("native_verifier_result"), Mapping):
                input_, output = source["native_verifier_input"], target["native_verifier_result"]
                sections, states = input_.get("sections"), input_.get("section_states")
                changed, checked = input_.get("changed_section_ids"), output.get("checked_section_ids")
                if isinstance(sections,list) and isinstance(states,Mapping) and isinstance(changed,list) and isinstance(checked,list):
                    ids = [section.get("section_id") for section in sections]
                    result = (all(isinstance(value,str) and value for value in ids) and len(ids)==len(set(ids))
                        and set(ids)==set(states) and set(changed)<=set(checked)<=set(ids)
                        and len(checked)==len(set(checked)) and input_.get("mode") in {"incremental_logic","full_logic"})
                    reason = "NATIVE_DOCUMENT_VERIFIER_INPUT_AND_INCREMENTAL_SET"
            elif "section_refs" in source and "input_section_refs" in target and "checked_sections" in target and "changed_sections" in target:
                result = (source["section_refs"] == target["input_section_refs"] and
                    set(target["changed_sections"]) <= set(target["checked_sections"]) and
                    set(target["checked_sections"]) <= set(source.get("section_ids", [])))
                reason = "SECTION_VERIFY_INPUT_OR_INCREMENTAL_SET_MISMATCH"
        elif edge_id == "X17":
            completion = source.get("completion")
            declaration = enumerate_required_items(declaration=source.get("declaration"))
            formal = enumerate_required_items(completion)
            manifest = target.get("gate_input_manifest", target)
            completion_ref = manifest.get("validation_completion_ref")
            if completion and completion_ref and not declaration["error"] and not formal["error"]:
                expected = sorted(i["expected_validation_item_ref"] for i in declaration["items"])
                actual = [i["expected_validation_item_ref"] for i in formal["items"]]
                result = expected == actual and all(completion.get(k) == completion_ref.get(k)
                    for k in ("completion_id", "completion_sha256", "validation_run_id", "subject_hash"))
                reason = "REQUIRED_COMPLETION_GATE_BINDING_MISMATCH"
        elif edge_id == "X19":
            if isinstance(source.get("controls"),list) and isinstance(target.get("assembly_manifest"),Mapping):
                manifest = target["assembly_manifest"]
                entries, required = manifest.get("ordered_section_entries"), manifest.get("required_section_keys")
                controls = source["controls"]
                if isinstance(entries,list) and isinstance(required,list) and controls:
                    result = not manifest.get("_native_schema_invalid") and [entry.get("section_key") for entry in entries] == required
                    for entry in entries:
                        if entry.get("selection_state") == "MISSING":
                            result = result and entry.get("selected_section_control_decision_ref") is None and entry.get("section_assembly_candidate") is None
                            continue
                        selected = entry.get("selected_section_control_decision_ref") or {}
                        matches = [control for control in controls if control.get("control_decision_id") == selected.get("control_decision_id")]
                        if len(matches) != 1:
                            result = None; break
                        control = matches[0]
                        expected = {key:control[key] for key in ("schema_version","control_decision_id","control_contract_version","control_decision_hash") if key in control}
                        result = result and not control.get("_native_schema_invalid") and selected == expected
                        candidate = entry.get("section_assembly_candidate")
                        if entry.get("selection_state") == "INCLUDED" and isinstance(candidate,Mapping):
                            result = result and (control.get("enforcement_action") in {"OUTPUT","OUTPUT_BEST_EFFORT"}
                                and candidate.get("section_control_decision_ref") == expected
                                and candidate.get("section_artifact_ref") == control.get("input_manifest", {}).get("artifact_state_ref")
                                and candidate.get("section_post_write_entry_ref") == control.get("input_manifest", {}).get("post_write_entry_ref")
                                and candidate.get("enforcement_action") == control.get("enforcement_action")
                                and candidate.get("quality_state") == control.get("quality_state_passthrough"))
                        elif entry.get("selection_state") == "WITHHELD":
                            result = result and candidate is None and control.get("enforcement_action") in {"BLOCK","CORRECT"}
                        else:
                            result = False
                    reason = "NATIVE_CONTROL_AUTHORIZED_ASSEMBLY_SELECTION"
            elif "allowed_entries" in source and "selected_entries" in target and "document_artifact_ref" in target:
                selected = target["selected_entries"]
                result = (len(selected) == len({canonical_hash(item) for item in selected})
                    and all(item in source["allowed_entries"] for item in selected)
                    and all(item in selected or item in source.get("policy_blocked_entries", []) for item in source.get("required_entries", [])))
                expected_version = source.get("document_artifact_ref")
                if expected_version is not None and target["document_artifact_ref"] != expected_version:
                    result = False
                reason = "ASSEMBLY_SELECTION_OR_VERSION_MISMATCH"
        elif edge_id == "X20":
            checks = target.get("document_checks")
            assessment = target.get("assessment")
            if isinstance(source.get("document_entry"), Mapping) and all(isinstance(target.get(key), Mapping) for key in ("assessment","control","gate","completion")):
                entry, manifest = source["document_entry"], source.get("assembly_manifest", {})
                identity = entry.get("identity", {})
                control, gate, completion = target["control"], target["gate"], target["completion"]
                artifact = identity.get("artifact_state_ref")
                expected_completion = gate.get("gate_input_manifest", {}).get("validation_completion_ref")
                if isinstance(artifact,Mapping) and isinstance(expected_completion,Mapping):
                    from apps.enterprise_document.schemas.post_write.entry import DocumentPostWriteEntrySchema, entry_ref_from_identity
                    typed = DocumentPostWriteEntrySchema.model_validate(entry)
                    entry_ref = entry_ref_from_identity(typed.identity).model_dump(mode="json")
                    control_ref = {key:control[key] for key in ("schema_version","control_decision_id","control_contract_version","control_decision_hash") if key in control}
                    result = (not assessment.get("_native_schema_invalid") and artifact.get("artifact_scope") == "DOCUMENT"
                        and gate.get("subject_key", {}).get("validation_scope") == "DOCUMENT"
                        and identity.get("assembly_input_manifest_ref", {}).get("manifest_id") == manifest.get("manifest_id")
                        and artifact == assessment.get("document_artifact_ref", {}).get("exact_ref_payload")
                        and entry_ref == assessment.get("document_post_write_entry_ref", {}).get("exact_ref_payload")
                        and control_ref == assessment.get("post_write_control_decision_ref", {}).get("exact_ref_payload")
                        and artifact == control.get("input_manifest", {}).get("artifact_state_ref") == gate.get("gate_input_manifest", {}).get("artifact_state_ref")
                        and all(expected_completion.get(key) == completion.get(key) for key in
                            ("completion_id","completion_sha256","validation_run_id","subject_hash")))
                    reason = "NATIVE_ASSEMBLY_DOCUMENT_COMPLETION_ASSESSMENT_BINDING"
            elif source.get("document_artifact_ref") and isinstance(checks, Mapping) and isinstance(assessment, Mapping):
                if "completion_ref" in checks and "document_completion_ref" in assessment:
                    result = (checks.get("scope") == "DOCUMENT" and checks.get("artifact_ref") == source["document_artifact_ref"]
                        and assessment.get("document_artifact_ref") == source["document_artifact_ref"]
                        and checks["completion_ref"] == assessment["document_completion_ref"])
                    reason = "DOCUMENT_SCOPE_ASSESSMENT_BINDING_MISMATCH"
        elif edge_id == "X21":
            if isinstance(source.get("terminal"), Mapping) and isinstance(source.get("assessment"), Mapping):
                terminal, assessment = source["terminal"], source["assessment"]
                request = target.get("request", {})
                expected = terminal.get("run_completion_assessment_ref", {})
                document = assessment.get("document_artifact_ref", {}).get("exact_ref_payload")
                control = assessment.get("post_write_control_decision_ref", {}).get("exact_ref_payload")
                actual = target.get("run_completion_assessment_ref")
                fields = ("assessment_id", "run_id", "aggregate_result", "assessment_contract_version", "semantic_fingerprint", "assessment_sha256")
                if document and control and isinstance(actual, Mapping) and all(k in expected and k in assessment and k in actual for k in fields):
                    result = (terminal.get("status") == "success" and assessment.get("aggregate_result") == "SUCCESS"
                        and all(expected[k] == assessment[k] == actual[k] for k in fields)
                        and terminal.get("identity", {}).get("run_id") == assessment.get("run_id") == request.get("run_id")
                        and terminal.get("lifecycle_revision") == target.get("run_lifecycle_revision")
                        and _original_projection_equal(request.get("document_artifact_ref"), document)
                        and _original_projection_equal(request.get("post_write_control_decision_ref"), control))
                    reason = "NATIVE_TERMINAL_ASSESSMENT_PUBLICATION_EXACT_BINDING"
            elif source.get("control_action") == "BLOCK" and source.get("policy_requires_publication") is False:
                result = target.get("publication_started") is False
                reason = "PUBLICATION_POLICY_BLOCK_EXPECTATION"
            elif all(k in source for k in ("terminal_ref", "assessment_ref", "document_artifact_ref")) and "request" in target:
                request = target["request"]
                required = ("terminal_ref", "assessment_ref", "document_artifact_ref")
                if all(k in request for k in required):
                    result = (source.get("terminal_valid") is True and source.get("assessment_valid") is True
                        and all(source[k] == request[k] for k in required))
                    reason = "PUBLICATION_TERMINAL_ASSESSMENT_BINDING_MISMATCH"
        elif edge_id == "X27":
            if isinstance(source.get("manifest"), Mapping) and isinstance(source.get("release_payload"), Mapping):
                manifest = source["manifest"]
                public_manifest = target.get("target_release_publication", {}).get("manifest", {})
                outcome = target.get("terminal_outcome", {})
                fields = ("release_id", "request_sha256", "run_id", "task_id", "document_artifact_ref",
                    "post_write_control_decision_ref", "run_completion_assessment_ref", "run_lifecycle_revision",
                    "release_mode", "enforcement_action_passthrough", "quality_state_passthrough",
                    "release_payload_artifact", "release_payload_sha256", "manifest_payload_sha256")
                payload = source["release_payload"]
                draft = target.get("deliverable_final_result", {}).get("draft")
                required_outcome = ("control_action", "quality_state", "publication_ref", "is_best_effort", "is_partial", "requires_human")
                if all(k in manifest and k in public_manifest for k in fields) and all(k in outcome for k in required_outcome) and isinstance(draft, Mapping):
                    # Exact JSON business-payload projection is applicable only
                    # when the public boundary exposes the same native shape.
                    if draft.get("payload_kind") == payload.get("content", {}).get("payload_kind"):
                        best = manifest["enforcement_action_passthrough"] == "OUTPUT_BEST_EFFORT"
                        result = (all(_original_projection_equal(manifest[k], public_manifest[k]) for k in fields)
                            and _original_projection_equal(manifest["release_payload_artifact"], payload["reference"])
                            and payload["reference"].get("content_hash") == "sha256:" + manifest["release_payload_sha256"]
                            and draft == payload["content"]
                            and outcome["control_action"] == manifest["enforcement_action_passthrough"]
                            and outcome["quality_state"] == manifest["quality_state_passthrough"]
                            and outcome["publication_ref"] == manifest["release_id"]
                            and outcome["is_best_effort"] == best
                            and (not best or outcome["is_partial"] is True)
                            and isinstance(outcome["requires_human"], bool))
                        reason = "NATIVE_RELEASE_PUBLIC_PAYLOAD_AND_DISCLOSURE_BINDING"
            else:
                required = ("artifact_ref", "content", "status", "human_review_required")
                if all(k in source and k in target for k in required):
                    result = all(source[k] == target[k] for k in required)
                    reason = "PUBLIC_PROJECTION_OR_REVIEW_DISCLOSURE_MISMATCH"
    if result is True and not reason.startswith("NATIVE_"):
        reason = "EXACT_EDGE_VALID"
    return EdgeObservation(edge_id, subject_id,
        source_key=canonical_hash(source) if source is not None else None,
        target_key=canonical_hash(target) if target is not None else None,
        allowed_transformation=transformation, assertion_result=result,
        measurement_state="MEASURED" if result is not None else "UNMEASURED", reason=reason,
        availability="PERSISTED" if result is not None else "NOT_RECORDED",
        completeness="COMPLETE" if result is not None else "PARTIAL")


def classify_publication(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """Several independent failures may coexist; no file absence inference."""
    findings = []
    if evidence.get("control_action") == "BLOCK" and evidence.get("policy_requires_publication") is False:
        findings.append("RELEASE_NOT_EXPECTED_POLICY_BLOCK")
    elif evidence.get("path_complete") is True and evidence.get("upstream_failure") is True and evidence.get("publication_started") is False:
        findings.append("RELEASE_NOT_REACHED_UPSTREAM_FAILURE")
    elif evidence.get("eligible") is True and evidence.get("path_complete") is True and evidence.get("publication_started") is False:
        findings.append("RELEASE_ELIGIBLE_NOT_STARTED")
    if evidence.get("terminal_valid") is True and evidence.get("assessment_valid") is True and evidence.get("publication_started") is True and evidence.get("publication_error"):
        findings.append("TERMINALIZED_PUBLICATION_FAILED")
    if evidence.get("manifest_present") is True and evidence.get("artifact_integrity_valid") is False:
        findings.append("PUBLICATION_ARTIFACT_INTEGRITY_FAILURE")
    if evidence.get("release_valid") is True and evidence.get("public_projection_valid") is False:
        findings.append("PUBLIC_PROJECTION_MISMATCH")
    if evidence.get("terminal_valid") is True and (evidence.get("resource_closed") is False or evidence.get("usage_finalized") is False):
        findings.append("RESOURCE_OR_USAGE_FINALIZATION_INCOMPLETE")
    known_success = (evidence.get("release_valid") is True and evidence.get("artifact_integrity_valid") is True
        and evidence.get("public_projection_valid") is True and evidence.get("resource_closed") is True
        and evidence.get("usage_finalized") is True)
    if not findings and not known_success:
        findings.append("PUBLICATION_OR_CLOSE_UNKNOWN")
    return tuple(findings)


def evaluate_postwrite(snapshot: SnapshotView, *, references: Mapping[str, Any] | None = None) -> ObservationBatch:
    references = references or {}
    batch = ObservationBatch("05", limitations=list(snapshot.limitations))
    if "postwrite" in snapshot.sources:
        decoded = read_postwrite_records(snapshot)
        for error in decoded["errors"]:
            batch.findings.append(Finding("POSTWRITE_EXACT_RECORD_INVALID", message=error, unit_id="E03"))
        batch.limitations.extend(decoded["limitations"])
        entries = {r["record"]["identity"]["entry_id"]: r["record"]["identity"] for r in decoded["records"].get("post_write_entries", [])}
        completion_by_id = {r["record"]["completion_id"]: r for r in decoded["records"].get("validation_completions", [])}
        gates = {r["record"]["delivery_gate_decision_id"]: r for r in decoded["records"].get("delivery_gate_decisions", [])}
        validation_scope = {}
        for row in gates.values():
            record = row["record"]
            manifest = record["gate_input_manifest"]
            entry = entries.get(manifest["post_write_entry_ref"]["entry_id"], {})
            validation_scope[record["validation_run_id"]] = (entry.get("run_id", "validation-subject:" + record["subject_key"]["subject_hash"]),
                record["subject_key"]["validation_scope"])
        for table, loaded_records in decoded["records"].items():
            for row in loaded_records:
                record = row["record"]
                unit = {"artifact_states": "E02", "post_write_entries": "E03", "validation_completions": "E03",
                    "delivery_gate_decisions": "E09", "post_write_control_decisions": "E09"}.get(table)
                if table == "validation_facts":
                    unit = FACT_UNITS.get(record.get("family"))
                if table == "validator_execution_records":
                    unit = next((u for producer, u in PRODUCER_UNITS.items() if producer.lower() in record.get("validator_id", "").lower()), "E03")
                if unit is None:
                    continue
                default_subject = record.get("identity", {}).get("run_id") or "validation-subject:" + str(record.get("subject_hash", "unknown"))
                subject, scope = validation_scope.get(record.get("validation_run_id"), (default_subject,
                    record.get("artifact_ref", {}).get("artifact_scope", record.get("identity", {}).get("entry_scope", "unknown"))))
                quality = record.get("semantic_state", record.get("overall_quality_state", record.get("quality_state_passthrough", "UNKNOWN")))
                batch.stages.append(StageObservation(unit, subject, operation_id=row["location"], scope=scope,
                    executed=True, observed_execution_status="PERSISTED_RECORD", observed_quality_status=quality,
                    observed_control_action=record.get("enforcement_action"), measurement_state="MEASURED",
                    evidence_refs=(row["evidence"],), identity_quality="ORIGINAL_OWNER_REF", payload={"table": table, "record": record}))
                if table == "validation_completions":
                    enum = enumerate_required_items(record)
                    batch.metrics.append(MetricResult("required_completion_accounting", unit_id="E03", scope=scope,
                        value={"expected": len(enum["items"]), "recorded": len(enum["items"]),
                            "technical_failed": sum(i.get("state") == "TECHNICAL_FAILURE" for i in enum["items"]),
                            "not_run": sum(i.get("state") == "NOT_RUN" for i in enum["items"])},
                        measurement_state="MEASURED" if not enum["error"] else "UNMEASURED", representation="FORMAL_COMPLETION"))
                elif table == "delivery_gate_decisions":
                    completion_ref = record["gate_input_manifest"]["validation_completion_ref"]
                    actual = completion_by_id.get(completion_ref["completion_id"])
                    if actual is None:
                        batch.findings.append(Finding("GATE_EXACT_COMPLETION_UNAVAILABLE", subject, unit_id="E03", edge_id="X17"))
                    elif any(actual["record"].get(k) != completion_ref.get(k) for k in
                             ("completion_id", "completion_sha256", "validation_run_id", "subject_hash")):
                        batch.findings.append(Finding("GATE_COMPLETION_MANIFEST_MISMATCH", subject, unit_id="E03", edge_id="X17",
                            evidence_refs=(row["evidence"], actual["evidence"])))
                elif table == "post_write_control_decisions":
                    # Exact native Gate/Control operands and evidence are joined
                    # once below, including captured consumers when available.
                    pass
    for key, unit in STAGES.items():
        rows = _rows(snapshot, key)
        if not rows:
            batch.stages.append(StageObservation(unit, "evaluation-local:unknown", operation_id=key,
                payload={"diagnostic_key": key}))
        for row in rows:
            if key == "S-ITEM":
                unit = QUALITY_UNITS.get(row.get("dimension"), PRODUCER_UNITS.get(row.get("producer"), "E03"))
            subject = str(row.get("subject_id") or row.get("run_id") or "evaluation-local:unknown")
            technical = bool(row.get("technical_error") or row.get("parse_error") or row.get("exception_type"))
            batch.stages.append(StageObservation(unit, subject, operation_id=str(row.get("operation_id") or row.get("operation_ref") or key) +
                (":" + str(row["producer"]) if row.get("producer") else ""), scope=str(row.get("scope", "unknown")),
                measurement_state="TECHNICAL_FAILURE" if technical else "MEASURED", executed=True,
                observed_quality_status=str(row.get("quality_state", "UNKNOWN")),
                observed_control_action=row.get("enforcement_action", row.get("action")), payload=row))
            if key == "S-PRECHECK":
                checked, changed = row.get("checked_sections"), row.get("changed_sections")
                batch.metrics.append(MetricResult("actual_document_checked_count", unit_id="E01",
                    value=len(checked) if isinstance(checked, list) else None,
                    measurement_state="MEASURED" if isinstance(checked, list) else "UNMEASURED"))
                if changed == []:
                    batch.metrics.append(MetricResult("actual_repair_count", unit_id="E01", value=0, measurement_state="MEASURED"))
                required = references.get("document_requirements")
                if required is not None:
                    missing = set(required) - set(row.get("covered_requirements", []))
                    for requirement in sorted(missing):
                        batch.findings.append(Finding("DOCUMENT_REQUIREMENT_MISSING", subject, requirement, unit_id="E01", evidence_level="INDEPENDENT_REFERENCE"))
            elif key == "S-PLAN":
                completion = row.get("completion")
                enumerated = enumerate_required_items(completion, declaration=row)
                if enumerated["error"]:
                    batch.findings.append(Finding(enumerated["error"], subject, unit_id="E03"))
                expected = {i["expected_validation_item_ref"] for i in enumerated["items"] if i.get("expected_validation_item_ref")}
                recorded = {i["expected_validation_item_ref"] for i in (completion or {}).get("items", [])}
                started = set(row.get("started_items", []))
                metric = {"expected": len(expected), "started": len(started), "recorded": len(expected & recorded),
                    "missing": len(expected - recorded), "technical_failed": sum(i.get("state") == "TECHNICAL_FAILURE" for i in (completion or {}).get("items", [])),
                    "unresolved": sum(i.get("state") == "INDETERMINATE" for i in (completion or {}).get("items", [])),
                    "not_run": sum(i.get("state") == "NOT_RUN" for i in (completion or {}).get("items", []))}
                batch.metrics.append(MetricResult("required_completion_accounting", unit_id="E03", value=metric,
                    measurement_state="MEASURED" if not enumerated["error"] else "UNMEASURED", representation=enumerated["provenance"],
                    assertion_result=not enumerated["error"] and metric["missing"] == 0 and completion is not None))
                declared = enumerate_required_items(declaration=row)["items"]
                declared_ids = {i["expected_validation_item_ref"] for i in declared if i.get("expected_validation_item_ref")}
                if completion and declared_ids and declared_ids != expected:
                    batch.findings.append(Finding("REQUIRED_DECLARATION_COMPLETION_MISMATCH", subject, unit_id="E03", edge_id="X17"))
            elif key == "S-ITEM":
                dimension = row.get("dimension")
                if dimension in {"faithfulness", "citation", "relevancy", "coverage", "consistency"}:
                    scope = row.get("scope", "SECTION")
                    batch.metrics.append(evaluate_quality_dimension(row, references.get((scope, dimension)), dimension=dimension, scope=scope))
                if row.get("parse_error"):
                    batch.findings.append(Finding("VALIDATOR_SCHEMA_TECHNICAL_FAILURE", subject, "Schema failure is separate from semantic conflict", unit_id="E08"))
            elif key == "S-CONTROL":
                gate = row.get("gate")
                if isinstance(gate, dict):
                    batch.edges.append(check_control_and_assembly(gate, row,
                        completion=row.get("completion"), subject_id=subject))
                if row.get("allowed_actions") is not None:
                    action = row.get("enforcement_action", row.get("action"))
                    batch.metrics.append(MetricResult("control_policy_conformance", unit_id="E09", value=action in row["allowed_actions"],
                        assertion_result=action in row["allowed_actions"], measurement_state="MEASURED",
                        reason="QUALITY_AND_ACTION_ARE_SEPARATE_AXES"))
            elif key in {"S-PUBLISH", "S-PUBLIC", "S-ASSESS"}:
                for code in classify_publication(row):
                    batch.findings.append(Finding(code, subject, unit_id="E11",
                        evidence_level="DIRECT_ERROR" if row.get("publication_error") else "OBSERVED_RELATION"))
            elif key == "S-REPLAY":
                counts = {k: row.get(k) for k in ("new_model_calls", "new_business_writes", "new_publications")}
                complete = all(isinstance(v, int) and v >= 0 for v in counts.values())
                healthy = row.get("healthy_exact_read") is True
                legal = row.get("authorized_missing_obligation") is True and row.get("failed_run_revived") is not True
                valid = complete and (all(v == 0 for v in counts.values()) if healthy else legal)
                batch.metrics.append(MetricResult("replay_side_effects", unit_id="E12", value=counts,
                    measurement_state="MEASURED" if complete else "UNMEASURED", assertion_result=valid if complete else None,
                    reason="HEALTHY_EXACT_READ" if healthy else "LEGAL_MISSING_OBLIGATION" if legal else "REPLAY_AUTHORITY_UNKNOWN"))
    endpoint_families = {"X16": ("S-SECTIONS", "S-PRECHECK"), "X17": ("S-REQUIRED", "S-GATE"), "X18": ("S-GATE", "S-CONTROL"),
        "X19": ("S-CONTROL", "S-ASSEMBLY"), "X20": ("S-ASSEMBLY", "S-DOCUMENT-ASSESSMENT"),
        "X21": ("S-TERMINAL", "S-PUBLISH"), "X27": ("S-RELEASE", "S-PUBLIC")}
    native_edges = _native_postwrite_edges(snapshot)
    for edge_id, (source_key, target_key) in endpoint_families.items():
        # Native records are selected only by their explicit original schema;
        # synthetic pure-checker fixtures retain their previous interface.
        batch.edges.extend(native_edges.get(edge_id) or _joined_edge_observations(snapshot, edge_id, source_key, target_key))
    if not any(edge.edge_id == "X18" for edge in batch.edges):
        batch.edges.append(check_control_and_assembly(snapshot.get("S-GATE"), snapshot.get("S-CONTROL")))
    for edge in batch.edges:
        if edge.assertion_result is False:
            batch.findings.append(Finding(edge.reason, edge.subject_id, edge_id=edge.edge_id, evidence_level="EDGE_IDENTITY"))
    for unit in QUALITY_UNITS.values():
        if not any(stage.unit_id == unit for stage in batch.stages):
            batch.stages.append(StageObservation(unit, "evaluation-local:unknown", operation_id="MISSING_QUALITY_PRODUCER"))
    batch.evidence.extend(source.evidence for source in snapshot.sources.values())
    return batch


evaluate_postwrite_release = evaluate_postwrite


def extract_postwrite(snapshot: SnapshotView, *, subject_id="unknown") -> ObservationBatch:
    batch = evaluate_postwrite(snapshot)
    batch.stages = [replace(stage, subject_id=subject_id) if stage.subject_id == "evaluation-local:unknown" else stage for stage in batch.stages]
    return batch
