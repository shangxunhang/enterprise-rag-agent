"""Read-only planning, admission, Context and ordered citation fan-in checks."""
from __future__ import annotations

from dataclasses import replace
import base64
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from core.hashing import canonical_sha256 as production_semantic_hash

from ..contracts import (EdgeObservation, Finding, MetricResult, ObservationBatch,
                         StageObservation, canonical_hash)
from ..snapshot import LoadedSource, SnapshotView


STAGES = {"P-INGRESS": "B01", "P-ROUTE": "B02", "P-FACT": "B03",
    "P-PLAN": "B04", "P-DISPATCH": "B05", "P-PACKAGE": "C13",
    "P-ADMISSION": "D01", "P-ENFORCEMENT": "D02", "P-RECOVERY": "D03",
    "P-CONTEXT": "D04", "P-FANIN": "B05"}


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _rows(snapshot: SnapshotView, key: str):
    direct = snapshot.get(key)
    rows = list(direct) if isinstance(direct, (list, tuple)) else [direct] if isinstance(direct, dict) else []
    for row in snapshot.records("diagnostics"):
        if str(row.get("event_type", "")).startswith(key) or row.get("diagnostic_key") == key:
            rows.append(row)
    rows = [{**r, **r.get("payload", {})} if isinstance(r.get("payload"), dict) else r for r in rows if isinstance(r, dict)]
    return rows


def _native_phase_edges(snapshot, key, edge_id, before_phase, after_phase):
    """Join captured original operations, retaining ambiguous/missing frames."""
    groups = {}
    for source in snapshot.sources.values():
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, original in enumerate(values):
            if not isinstance(original, Mapping) or original.get("event_type") != key:
                continue
            row = {**original, **original.get("payload", {})} if isinstance(original.get("payload"), Mapping) else dict(original)
            if row.get("phase") not in {before_phase, after_phase}:
                continue
            operation = row.get("operation_ref") or row.get("operation_id")
            # A missing operation must never be joined by a shared subject.
            identity = (source.name, canonical_hash(operation)) if operation else (source.name, ordinal)
            groups.setdefault(identity, []).append((row, replace(source.evidence, pointer="/" + str(ordinal))))
    result = []
    for entries in groups.values():
        sources = [(row, ref) for row, ref in entries if row.get("phase") == before_phase]
        targets = [(row, ref) for row, ref in entries if row.get("phase") == after_phase]
        source = sources[0][0] if len(sources) == 1 else None
        target = targets[0][0] if len(targets) == 1 else None
        if source is not None and target is not None and len(entries) == 2:
            if edge_id == "X25":
                edge = check_planning_lineage(edge_id, source, target, subject_id=_subject(source))
                if not source.get("source_request_hash") or not target.get("source_request_hash"):
                    edge = replace(edge, assertion_result=None, measurement_state="UNMEASURED", completeness="PARTIAL",
                        reason="NATIVE_INGRESS_SOURCE_REQUEST_BINDING_MISSING")
                elif source["source_request_hash"] != target["source_request_hash"]:
                    edge = replace(edge, assertion_result=False, reason="NATIVE_INGRESS_SOURCE_REQUEST_MISMATCH")
            else:
                edge = check_fanin_remap({**source, **target}, subject_id=_subject(source))
                if any(source.get(field) != target.get(field) for field in ("section_id", "section_order")):
                    edge = replace(edge, assertion_result=False, measurement_state="MEASURED",
                        reason="NATIVE_FANIN_SECTION_LINEAGE_MISMATCH")
            if any(row.get("completeness") == "CAPTURE_INCOMPLETE" for row, _ in entries):
                edge = replace(edge, assertion_result=None, measurement_state="UNMEASURED", completeness="PARTIAL",
                    reason="NATIVE_PHASE_CAPTURE_INCOMPLETE")
        elif (edge_id == "X25" and snapshot.manifest.get("synthetic") is True and source is None
            and target is not None and len(entries) == 1 and not target.get("operation_ref")
            and not target.get("operation_id") and target.get("subject_id") and target.get("task_id") is None
            and target.get("production_task_created") is not True and target.get("completeness") != "CAPTURE_INCOMPLETE"
            and targets[0][1].locator.startswith("fixture:")):
            # The historical controlled fixture checks the evaluation's local
            # projection of its input subject. It is never native ingress proof.
            source, target = target, {"subject_id":_subject(target), "task_id":target.get("task_id"),
                "production_task_created":False}
            edge = replace(check_planning_lineage(edge_id, source, target, subject_id=_subject(source)),
                reason="SYNTHETIC_CAPTURE_EVALUATION_SUBJECT_BINDING", availability="SYNTHETIC_FIXTURE")
            sources, targets = entries, []
        else:
            edge = _edge(edge_id, _subject(source or target or entries[0][0]), None,
                "NATIVE_PHASE_OPERATION_JOIN_NOT_PROVEN", source, target)
        result.append(replace(edge, source_refs=tuple(ref for _, ref in sources), target_refs=tuple(ref for _, ref in targets),
            payload={"source_observation": source, "target_observation": target}))
    return result


def _request_rows(snapshot):
    """Resolve actual request material already verified by the snapshot reader."""
    rows = []
    for source in snapshot.sources.values():
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, original in enumerate(values):
            if not isinstance(original, Mapping):
                continue
            kind = original.get("representation_kind") or original.get("event_type") or source.family
            if kind != "MODEL_ATTEMPT_REQUEST":
                continue
            material = original.get("material", original.get("request", original))
            if not isinstance(material, Mapping):
                material = {}
            request = {**original, **material, "representation_kind": "MODEL_ATTEMPT_REQUEST"}
            rows.append((request, replace(source.evidence, pointer="/" + str(ordinal))))
    return rows


def _context_call(row):
    return (row.get("invocation_id") or row.get("invocation_subject", {}).get("invocation_id")
        or row.get("context_package_ref", row.get("package_ref", {})).get("invocation_id"))


def _context_package(row):
    return (row.get("context_package_id") or
        row.get("context_package_ref", row.get("package_ref", {})).get("context_package_id"))


def _request_package(row):
    return (row.get("extra", {}).get("llm_context_summary") or {}).get("package_id")


def _context_request_edges(snapshot, contexts):
    """Join by original invocation/package, retaining every unresolved endpoint."""
    requests = _request_rows(snapshot)
    context_refs = {}
    for source in snapshot.sources.values():
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, original in enumerate(values):
            if not isinstance(original, Mapping) or not (source.name == "P-CONTEXT" or source.family == "P-CONTEXT"
                or str(original.get("event_type", "")).startswith("P-CONTEXT")):
                continue
            row = {**original, **original.get("payload", {})} if isinstance(original.get("payload"), Mapping) else dict(original)
            context_refs.setdefault(canonical_hash(row), []).append(replace(source.evidence, pointer="/" + str(ordinal)))
    matched = set()
    result = []
    for context in contexts:
        call, package = _context_call(context), _context_package(context)
        source_refs = tuple(context_refs.get(canonical_hash(context), ()))
        embedded = context.get("actual_request")
        if isinstance(embedded, Mapping):
            candidates = [(dict(embedded), None, None)]
        else:
            candidates = [(request, evidence, index) for index, (request, evidence) in enumerate(requests)
                if (call and (request.get("model_call_id") or request.get("invocation_id")) == call)
                or (not call and package and _request_package(request) == package)]
            peers = [row for row in contexts if _context_call(row) == call] if call else []
            if len({(_context_package(row), row.get("final_input_hash") or
                canonical_hash(row.get("final_input_material", row.get("messages")))) for row in peers}) > 1:
                candidates = [(request, evidence, index) for request, evidence, index in candidates
                    if package and _request_package(request) == package]
        if not candidates:
            result.append(replace(_edge("X09", _subject(context), None,
                "CONTEXT_REQUEST_JOIN_NOT_PROVEN", context), source_refs=source_refs, payload={"source_observation": context,
                "target_observation": None}))
        for request, evidence, index in candidates:
            edge = check_context_edge(context, request, subject_id=_subject(context))
            if request.get("completeness") == "CAPTURE_INCOMPLETE" or context.get("completeness") == "CAPTURE_INCOMPLETE":
                edge = replace(edge, assertion_result=None, measurement_state="UNMEASURED",
                    completeness="PARTIAL", reason="CONTEXT_REQUEST_CAPTURE_INCOMPLETE")
            result.append(replace(edge, source_refs=source_refs, target_refs=(evidence,) if evidence else (),
                payload={"source_observation": context, "target_observation": request}))
            if index is not None:
                matched.add(index)
    for index, (request, evidence) in enumerate(requests):
        if index not in matched:
            result.append(replace(_edge("X09", _subject(request), None,
                "REQUEST_CONTEXT_JOIN_NOT_PROVEN", target=request), target_refs=(evidence,),
                payload={"source_observation": None, "target_observation": request}))
    return result


def _subject(row):
    return str(row.get("subject_id") or row.get("evaluation_subject_id") or row.get("run_id") or "evaluation-local:unknown")


def _edge(edge_id, subject_id, result, reason, source=None, target=None, transformation="IDENTITY"):
    return EdgeObservation(edge_id=edge_id, subject_id=subject_id,
        source_key=canonical_hash(source) if source is not None else None,
        target_key=canonical_hash(target) if target is not None else None,
        allowed_transformation=transformation,
        measurement_state="MEASURED" if result is not None else "UNMEASURED",
        assertion_result=result, availability="PERSISTED" if result is not None else "NOT_RECORDED",
        completeness="COMPLETE" if result is not None else "PARTIAL", reason=reason)


def _members(value):
    value = _plain(value)
    if not isinstance(value, Mapping):
        return None
    for key in ("selected_members", "members", "items", "selected_evidence"):
        if key in value:
            members = value[key]
            if not isinstance(members, (list, tuple)):
                return None
            return [_plain(item) for item in members]
    return None


def check_package_edge(bundle, package, *, subject_id="unknown") -> EdgeObservation:
    """Compare exact ordered selection identities, never just cardinality."""
    bundle, package = _plain(bundle), _plain(package)
    if isinstance(bundle, Mapping) and bundle.get("schema_version") == "rag_evidence_bundle_snapshot_v1" and isinstance(package, Mapping) and package.get("schema_version") == "evidence_package_content_v1":
        from contracts.evidence import evidence_bundle_sha256
        from schemas.rag import EvidenceBundleSchema
        from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageContentSchema
        try:
            original = EvidenceBundleSchema.model_validate(bundle.get("bundle"))
            # Only existing content fields are schema operands; physical reference
            # is independent immutable-envelope lineage, not serialized content.
            content = EvidencePackageContentSchema.model_validate({k:v for k,v in package.items() if k != "_physical_reference"})
        except ValueError:
            return _edge("X08", subject_id, None, "NATIVE_PACKAGE_SCHEMA_INVALID", bundle, package)
        binding = content.evidence_bundle_binding
        selections = [item.evidence_id for item in content.selections]
        physical = package.get("_physical_reference", {})
        run = content.section_plan_source.plan_version_ref.logical_ref.run_id
        valid = (bundle.get("handle") == binding.evidence_bundle_handle
            and bundle.get("evidence_bundle_sha256") == "sha256:" + evidence_bundle_sha256(original) == binding.evidence_bundle_sha256
            and len(selections) == len(set(selections))
            and set(selections) <= set(original.selected_evidence_ids)
            and not set(selections) & set(original.dropped_evidence_ids)
            and (original.run_id is None or original.run_id == run)
            and (not physical.get("run_id") or physical["run_id"] == run)
            and (not physical.get("section_id") or physical["section_id"] == content.section_plan_source.section_id))
        return _edge("X08", subject_id, valid, "NATIVE_BUNDLE_PACKAGE_EXACT_BINDING" if valid else "NATIVE_BUNDLE_PACKAGE_MISMATCH",
            bundle, package, "EXACT_BUNDLE_AUTHORIZED_PACKAGE_SELECTION")
    source, target = _members(bundle), _members(package)
    if source is None or target is None:
        return _edge("X08", subject_id, None, "PACKAGE_MEMBER_EVIDENCE_MISSING", source, target)
    identity_fields = ("ref", "hash", "scope", "run_id", "round_id")
    if any(not isinstance(item, Mapping) or any(key not in item for key in identity_fields)
           for item in (*source, *target)):
        return _edge("X08", subject_id, None, "EXACT_MEMBER_IDENTITY_INCOMPLETE", source, target)
    selected_ids = package.get("selected_ids")
    if selected_ids is not None:
        selected_ids = list(selected_ids)
        by_id = {item.get("id"): item for item in source}
        if len(selected_ids) != len(set(selected_ids)) or any(i not in by_id for i in selected_ids):
            return _edge("X08", subject_id, False, "BUNDLE_SELECTION_INVALID", source, target)
        source = [by_id[i] for i in selected_ids]
    identity = lambda item: {key: item[key] for key in identity_fields}
    valid = [identity(item) for item in source] == [identity(item) for item in target]
    for key in ("bundle_ref", "bundle_hash", "scope", "run_id", "round_id"):
        if key in bundle and key in package and bundle[key] != package[key]:
            valid = False
    return _edge("X08", subject_id, valid, "EXACT_PACKAGE_BINDING_VALID" if valid else "PACKAGE_IDENTITY_MISMATCH",
                 source, target, "AUTHORIZED_ORDERED_SELECTION")


def _native_package_edges(snapshot):
    bundles, packages = [], []
    for source in snapshot.sources.values():
        if source.family not in {"evidence_bundle", "evidence_package"}:
            continue
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, value in enumerate(values):
            if not isinstance(value, Mapping):
                continue
            ref = replace(source.evidence, pointer="/" + str(ordinal))
            if source.family == "evidence_bundle" and value.get("schema_version") == "rag_evidence_bundle_snapshot_v1":
                bundles.append((value, ref))
            elif source.family == "evidence_package":
                decoded = value
                if value.get("content_encoding") == "json-utf8" and value.get("reference", {}).get("schema_version") == "evidence_package_content_v1":
                    try:
                        encoded = value["content_base64"]
                        if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
                            raise ValueError("bounded native content exceeded")
                        raw = base64.b64decode(encoded, validate=True)
                        if "sha256:" + sha256(raw).hexdigest() != value["reference"].get("content_hash"):
                            raise ValueError("native content hash mismatch")
                        decoded = {**json.loads(raw), "_physical_reference": value["reference"]}
                    except (ValueError, KeyError, TypeError):
                        decoded = None
                if decoded is None or decoded.get("schema_version") == "evidence_package_content_v1":
                    packages.append((decoded, ref))
    by_handle = {}
    for index,(bundle,ref) in enumerate(bundles):
        by_handle.setdefault(bundle.get("handle"), []).append((index,bundle,ref))
    result, used = [], set()
    for package, ref in packages:
        handle = (package or {}).get("evidence_bundle_binding", {}).get("evidence_bundle_handle")
        candidates = by_handle.get(handle, ()) if handle else []
        # Multiple distinct consumers of one exact immutable Bundle are valid;
        # multiple source snapshots for its handle are ambiguous.
        if len(candidates) == 1:
            index, bundle, evidence = candidates[0]
            used.add(index)
            edge = check_package_edge(bundle, package, subject_id=str((bundle.get("bundle") or {}).get("run_id") or "evaluation-local:unknown"))
            result.append(replace(edge, source_refs=(evidence,), target_refs=(ref,), payload={"source_observation":bundle,"target_observation":package}))
        else:
            result.append(replace(_edge("X08", "evaluation-local:unknown", None, "NATIVE_BUNDLE_PACKAGE_JOIN_NOT_PROVEN", target=package),
                source_refs=tuple(e for _, _, e in candidates), target_refs=(ref,), payload={"source_observation":None,"target_observation":package}))
    for index, (bundle, ref) in enumerate(bundles):
        if index not in used:
            result.append(replace(_edge("X08", _subject(bundle.get("bundle") or {}), None, "NATIVE_PACKAGE_NOT_RECORDED", bundle), source_refs=(ref,)))
    return result


def check_context_edge(context, actual_request, *, subject_id="unknown") -> EdgeObservation:
    """A display prompt cannot substitute for an actual physical request."""
    context, request = _plain(context), _plain(actual_request)
    if not isinstance(context, Mapping) or not isinstance(request, Mapping):
        return _edge("X09", subject_id, None, "CONTEXT_OR_ACTUAL_REQUEST_MISSING", context, request)
    if request.get("representation_kind", request.get("representation")) != "MODEL_ATTEMPT_REQUEST":
        return _edge("X09", subject_id, None, "DISPLAY_PROJECTION_IS_NOT_ACTUAL_REQUEST", context, request)
    material = context.get("final_input_material")
    if material is None and context.get("representation_kind") == "WRITER_LOCKED_INPUT" and isinstance(context.get("messages"), list):
        material = {"input_kind": "MESSAGES", "messages": context["messages"]}
    if material is None:
        material = context.get("model_input", {}).get("final_input_material", {}).get("inline_final_input_payload")
    if not isinstance(material, Mapping) or material.get("input_kind") != "MESSAGES":
        return _edge("X09", subject_id, None, "LOCKED_INPUT_MATERIAL_MISSING", context, request)
    if not isinstance(material.get("messages"), list):
        return _edge("X09", subject_id, None, "LOCKED_INPUT_MESSAGES_MISSING", context, request)
    messages = request.get("messages")
    if messages is None:
        messages = request.get("request", {}).get("messages")
    if not isinstance(messages, list):
        return _edge("X09", subject_id, None, "PHYSICAL_REQUEST_MESSAGES_MISSING", material, request)
    valid = material["messages"] == messages
    bound_hash = context.get("final_input_hash") or context.get("model_input", {}).get("final_model_input_sha256")
    if bound_hash and production_semantic_hash(material) != bound_hash:
        valid = False
    lineage = []
    request_lineage = {**request.get("request", {}), **request, **request.get("extra", {})}
    summary = request_lineage.get("llm_context_summary") or {}
    package_ref = context.get("context_package_ref", context.get("package_ref", {}))
    package_id = package_ref.get("context_package_id") or context.get("context_package_id")
    exact_package_bound = bool(package_id and summary.get("package_id") == package_id)
    if package_id and summary.get("package_id") is not None and not exact_package_bound:
        valid = False
    if summary.get("final_model_input_sha256") is not None and bound_hash != summary["final_model_input_sha256"]:
        valid = False
    for field in ("run_id", "invocation_id", "attempt_id"):
        expected = context.get(field) or context.get("invocation_subject", {}).get(field) or package_ref.get(field)
        actual = request_lineage.get(field) or (request_lineage.get("model_call_id") if field == "invocation_id" else None)
        if expected is not None:
            if actual is None:
                if field != "attempt_id" or not exact_package_bound:
                    lineage.append(field)
            elif actual != expected:
                valid = False
    if lineage and valid:
        return _edge("X09", subject_id, None, "REQUEST_LINEAGE_MISSING:" + ",".join(lineage), material, request)
    return _edge("X09", subject_id, valid, "LOCKED_CONTEXT_MATCHES_REQUEST" if valid else "CONTEXT_REQUEST_MISMATCH",
                 material, request, "LOCKED_MESSAGES_TO_PHYSICAL_REQUEST")


_MARKERS = re.compile(r"\[([A-Za-z]*\d+)\]")


def check_fanin_remap(observation, *, subject_id="unknown") -> EdgeObservation:
    """Check each marker by namespace + source identity + occurrence index."""
    data = _plain(observation)
    required = ("section_id", "local_registry", "global_registry", "id_map", "before", "after")
    if not isinstance(data, Mapping) or any(key not in data for key in required):
        return _edge("X26", subject_id, None, "FANIN_ORIGINAL_MAPPING_MISSING", data)
    local = {str(i["citation_id"]): i.get("source_identity") for i in data["local_registry"]}
    global_ = {str(i["citation_id"]): i.get("source_identity") for i in data["global_registry"]}
    mapping = data["id_map"]
    before, after = data["before"], data["after"]
    if any(value is None for value in (*local.values(), *global_.values())):
        return _edge("X26", subject_id, None, "CANONICAL_SOURCE_IDENTITY_MISSING", before, after)
    allowed = {canonical_hash(i) for i in data.get("accepted_source_identities", list(local.values()))}
    errors = []
    for field in ("content", "model_output", "prompt"):
        if field not in before or field not in after:
            return _edge("X26", subject_id, None, "FANIN_REPRESENTATION_MISSING:" + field, before, after)
        def rename(match):
            alias = match[1]
            return "[" + str(mapping.get(alias, alias)) + "]"
        if _MARKERS.sub(rename, before[field]) != after[field]:
            errors.append("DISPLAY_TRANSFORM:" + field)
        old, new = _MARKERS.findall(before[field]), _MARKERS.findall(after[field])
        if len(old) != len(new):
            errors.append("OCCURRENCE_COUNT:" + field)
        for index, (left, right) in enumerate(zip(old, new)):
            if left not in local or right not in global_ or local[left] != global_[right] or canonical_hash(local[left]) not in allowed:
                errors.append(f"SOURCE_BINDING:{field}:{index}")
    bindings = data.get("after_bindings")
    if bindings is not None:
        aliases = _MARKERS.findall(after["content"])
        for index, alias in enumerate(aliases):
            bound = [b for b in bindings if b.get("target_marker_index") ==
                (index + 1 if b.get("schema_version") == "citation_binding_v1" else index)]
            if len(bound) != 1 or bound[0].get("citation_id") != alias:
                errors.append("REBIND_OCCURRENCE:" + str(index))
    return _edge("X26", subject_id, not errors, ";".join(errors) if errors else "LEGAL_ALIAS_RENAME_SOURCE_PRESERVED",
                 before, after, "NAMESPACE_SCOPED_ALIAS_RENAME_AND_BINDING_REBUILD")


def check_planning_lineage(edge_id, source, target, *, subject_id="unknown"):
    """Evaluate the early-subject and immutable planning lineage edges."""
    if edge_id not in {"X25", "X30"}:
        raise ValueError("unsupported planning lineage edge")
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        return _edge(edge_id, subject_id, None, "PLANNING_ENDPOINT_EVIDENCE_MISSING", source, target)
    if edge_id == "X25":
        if not source.get("subject_id") or not target.get("subject_id"):
            return _edge(edge_id, subject_id, None, "EARLY_FAILURE_SUBJECT_MISSING", source, target)
        valid = source["subject_id"] == target["subject_id"] and target.get("production_task_created") is not True
        if source.get("task_id") is None and target.get("task_id") is not None:
            valid = False
        return _edge(edge_id, subject_id, valid, "EARLY_FAILURE_SUBJECT_PRESERVED" if valid else "EARLY_FAILURE_SUBJECT_FABRICATED",
            source, target, "EVALUATION_LOCAL_ID_WITHOUT_PRODUCTION_TASK")
    if source.get("fact_version_ref") and target.get("plan_version_ref") and target.get("research_round"):
        fact_ref, fact = source["fact_version_ref"], source.get("fact_content", {})
        plan_ref, plan, round_ = target["plan_version_ref"], target.get("plan_content", {}), target["research_round"]
        bindings = source.get("source_bindings")
        if (isinstance(bindings, list) and bindings and isinstance(fact.get("source_bindings"), list)
            and isinstance(plan.get("fact_snapshot_version_ref"), Mapping) and isinstance(round_.get("source_plan_version_ref"), Mapping)
            and isinstance(round_.get("round_ref"), Mapping) and isinstance(plan.get("sections"), list)):
            run = fact_ref.get("logical_ref", {}).get("run_id")
            round_ref = round_["round_ref"]
            project_sources = [binding for binding in fact["source_bindings"] if binding.get("source_type") == "project_input"]
            valid = ({canonical_hash(binding) for binding in bindings} == {canonical_hash(binding) for binding in project_sources}
                and len(bindings) == len(project_sources) == len({canonical_hash(binding) for binding in bindings})
                and all(binding.get("source_type") == "project_input" and binding.get("source_version_ref") for binding in bindings)
                and plan["fact_snapshot_version_ref"] == fact_ref and round_["source_plan_version_ref"] == plan_ref
                and run == plan_ref.get("logical_ref", {}).get("run_id") == round_ref.get("run_id")
                and round_ref.get("section_id") in {section.get("section_id") for section in plan["sections"]}
                and round_ref.get("attempt_scope_ref") == "section:" + str(round_ref.get("section_id")))
            return _edge("X30", subject_id, valid, "NATIVE_PROJECT_SOURCE_FACT_PLAN_RESEARCH_SCOPE_BINDING",
                source, target, "ORIGINAL_FACT_PLAN_RESEARCH_STRUCTURAL_LINEAGE")
        return _edge("X30", subject_id, None, "NATIVE_FACT_PLAN_RESEARCH_OPERAND_MISSING", source, target)
    required_source = ("project_input_ref", "fact_snapshot_ref", "run_id", "scope")
    required_target = ("planner_input_refs", "plan_ref", "research_plan_ref", "route_run_id", "route_scope")
    if any(k not in source for k in required_source) or any(k not in target for k in required_target):
        return _edge(edge_id, subject_id, None, "PLANNING_EXACT_LINEAGE_MISSING", source, target)
    valid = (source["project_input_ref"] in target["planner_input_refs"] and source["fact_snapshot_ref"] in target["planner_input_refs"]
        and target["plan_ref"] == target["research_plan_ref"] and source["run_id"] == target["route_run_id"]
        and source["scope"] == target["route_scope"])
    return _edge(edge_id, subject_id, valid, "PLANNING_EXACT_LINEAGE_VALID" if valid else "PLANNING_LINEAGE_MISMATCH",
                 source, target, "EXACT_FACT_PLAN_RESEARCH_ROUTE_BINDING")


def _native_planning_lineage_edges(snapshot):
    contents, versions, rounds, bindings = {}, [], [], []
    for source in snapshot.sources.values():
        values = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, row in enumerate(values):
            if not isinstance(row, Mapping):
                continue
            evidence = replace(source.evidence, pointer="/" + str(ordinal))
            if source.family == "artifact_runtime" and row.get("event_type") == "version_committed":
                versions.append((row.get("version_ref", {}), evidence))
            elif source.family == "research_runtime" and row.get("event_type") == "round_committed":
                rounds.append((row.get("record", {}), evidence))
            elif source.family == "project_input_sources" and row.get("schema_version") == "fact_source_binding_v1":
                bindings.append((row, evidence))
            elif source.family in {"fact_snapshot", "section_plan"} and row.get("content_encoding") == "json-utf8":
                try:
                    encoded = row["content_base64"]
                    if not isinstance(encoded,str) or len(encoded) > 12 * 1024 * 1024:
                        raise ValueError("bounded content exceeded")
                    raw = base64.b64decode(encoded, validate=True)
                    if "sha256:" + sha256(raw).hexdigest() != row["reference"].get("content_hash"):
                        raise ValueError("physical content hash mismatch")
                    content = json.loads(raw)
                    contents.setdefault(row["reference"].get("artifact_ref"), []).append((content,evidence,row["reference"]))
                except (ValueError,KeyError,TypeError):
                    continue
    if not rounds and not contents:
        return []
    by_version, by_source = {}, {}
    for ref,evidence in versions:
        by_version.setdefault(ref.get("artifact_version_id"), []).append((ref,evidence))
    for binding,evidence in bindings:
        by_source.setdefault(binding.get("source_ref"), []).append((binding,evidence))
    result = []
    for round_, evidence in rounds:
        expected = round_.get("source_plan_version_ref", {})
        plan_versions = by_version.get(expected.get("artifact_version_id"), ()) if expected.get("artifact_version_id") else []
        source, target, source_refs, target_refs, material_valid = None, {"research_round":round_}, [], [evidence], True
        if len(plan_versions) == 1:
            plan_ref, ref_evidence = plan_versions[0]
            plan_contents = contents.get(plan_ref.get("content_ref"), ())
            target_refs.append(ref_evidence)
            if len(plan_contents) == 1:
                plan, content_evidence, physical = plan_contents[0]
                material_valid = plan_ref.get("content_hash") == physical.get("content_hash")
                target.update(plan_version_ref=plan_ref, plan_content=plan)
                target_refs.append(content_evidence)
                expected_fact = plan.get("fact_snapshot_version_ref", {})
                fact_versions = by_version.get(expected_fact.get("artifact_version_id"), ()) if expected_fact.get("artifact_version_id") else []
                if len(fact_versions) == 1:
                    fact_ref, fact_evidence = fact_versions[0]
                    fact_contents = contents.get(fact_ref.get("content_ref"), ())
                    source_refs.append(fact_evidence)
                    if len(fact_contents) == 1:
                        fact, content_evidence, fact_physical = fact_contents[0]
                        material_valid = material_valid and fact_ref.get("content_hash") == fact_physical.get("content_hash")
                        source_refs.append(content_evidence)
                        native = [pair for source_ref in {entry.get("source_ref") for entry in fact.get("source_bindings", ())}
                            for pair in by_source.get(source_ref, ())]
                        source_refs.extend(e for _,e in native)
                        if native:
                            source = {"fact_version_ref":fact_ref,"fact_content":fact,"source_bindings":[b for b,_ in native]}
        edge = check_planning_lineage("X30", source, target, subject_id=str(round_.get("round_ref", {}).get("run_id") or "evaluation-local:unknown"))
        if source is not None and not material_valid:
            edge = replace(edge, assertion_result=False, measurement_state="MEASURED", reason="NATIVE_IMMUTABLE_CONTENT_VERSION_MISMATCH")
        result.append(replace(edge, source_refs=tuple(source_refs), target_refs=tuple(target_refs),
            payload={"source_observation":source,"target_observation":target}))
    if not rounds:
        result.append(_edge("X30", "evaluation-local:unknown", None, "NATIVE_RESEARCH_ROUND_NOT_RECORDED"))
    return result


def _metric(batch, name, unit, value, valid=None, denominator=None, reason=""):
    batch.metrics.append(MetricResult(name, unit_id=unit, value=value,
        denominator=denominator, measurement_state="MEASURED", assertion_result=valid, reason=reason))


def _native_routing_observations(snapshot, batch):
    """Read original Trace routing; a bypass is not classification accuracy."""
    from schemas.trace import RunTraceEventSchema
    from agent.runtime.workflow_schema import WorkflowDefinitionSchema

    for source in snapshot.sources.values():
        if source.family != "runtime_trace":
            continue
        records = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, row in enumerate(records):
            if not isinstance(row, Mapping) or row.get("schema_version") != "run_trace_event_v2" or row.get("event_type") != "workflow_started":
                continue
            evidence = replace(source.evidence, pointer="/" + str(ordinal))
            payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
            routing = payload.get("routing") if isinstance(payload.get("routing"), Mapping) else {}
            workflow = payload.get("workflow") if isinstance(payload.get("workflow"), Mapping) else {}
            valid, reason = None, "ROUTING_EVIDENCE_MISSING"
            selected, allowed, mode = routing.get("selected_task_type"), routing.get("allowed_task_types"), routing.get("routing_mode")
            try:
                # Validate the existing schema without dumping, upgrading or
                # constructing any runtime/persistence owner.
                RunTraceEventSchema.model_validate(row)
                if workflow:
                    WorkflowDefinitionSchema.model_validate(workflow)
                if (isinstance(selected, str) and selected and isinstance(mode, str) and mode
                    and isinstance(allowed, list) and allowed
                    and all(isinstance(value, str) and value for value in allowed)
                    and workflow.get("task_type") and workflow.get("workflow_id") and row.get("workflow_id")):
                    valid = (selected in allowed and selected == workflow["task_type"]
                        and row["workflow_id"] == workflow["workflow_id"]
                        and row.get("workflow_version") == workflow.get("workflow_version")
                        and (mode != "single_workflow_direct" or len(allowed) == 1))
                    reason = "ROUTE_CONTRACT_VALID" if valid else "ROUTE_CONTRACT_MISMATCH"
            except ValueError:
                reason = "NATIVE_ROUTING_SCHEMA_INVALID"
            operation = str(row.get("event_id") or row.get("span_id") or source.name + ":" + str(ordinal))
            subject = _subject(row)
            scope = str(row.get("workflow_id") or "unknown")
            batch.stages.append(StageObservation("B02", subject, operation_id=operation,
                source_segment=source.name, implementation_version=row["schema_version"], scope=scope,
                executed=True if valid is not None else None, observed_execution_status="PERSISTED_NATIVE_ROUTING",
                measurement_state="MEASURED" if valid is not None else "UNMEASURED",
                evidence_refs=(evidence,), payload=row, identity_quality="ORIGINAL_TRACE_REF"))
            metric_scope = subject + ":" + operation
            batch.metrics.append(MetricResult("route_contract_conformance", unit_id="B02", scope=metric_scope,
                value=valid, measurement_state="MEASURED" if valid is not None else "UNMEASURED",
                assertion_result=valid, reason=reason, input_completeness="COMPLETE" if valid is not None else "PARTIAL"))
            classification_na = valid is True and mode == "single_workflow_direct"
            batch.metrics.append(MetricResult("task_classification_accuracy", unit_id="B02", scope=metric_scope,
                measurement_state="N/A" if classification_na else "UNMEASURED",
                reason="SINGLE_WORKFLOW_DIRECT_CLASSIFICATION_NOT_APPLICABLE" if classification_na
                    else "INDEPENDENT_TASK_CLASSIFICATION_REFERENCE_NOT_ESTABLISHED"))
            batch.metrics.append(MetricResult("router_llm_call_count", unit_id="B02", scope=metric_scope,
                measurement_state="UNMEASURED", reason="HISTORICAL_ROUTER_CALL_CAPTURE_NOT_PROVEN"))
            if valid is False:
                batch.findings.append(Finding("ROUTE_CONTRACT_MISMATCH", subject,
                    "Selected task type, allowed task types and actual workflow disagree", unit_id="B02",
                    evidence_refs=(evidence,), evidence_level="ORIGINAL_ROUTING_CONTRACT"))


def evaluate_planning_prewrite(snapshot: SnapshotView, *, references: Mapping[str, Any] | None = None) -> ObservationBatch:
    references = references or {}
    batch = ObservationBatch(domain_id="03", limitations=list(snapshot.limitations))
    _native_routing_observations(snapshot, batch)
    batch.edges.extend(_native_phase_edges(snapshot, "P-INGRESS", "X25", "begin", "error"))
    batch.edges.extend(_native_phase_edges(snapshot, "P-FANIN", "X26", "before", "after"))
    batch.edges.extend(_native_package_edges(snapshot))
    # Existing immutable owner bytes remain usable without constructing owners
    # or resolving today's active pointers. Explicit manifest families identify
    # the historical material; absent content refs are an evidence limitation.
    for source in snapshot.sources.values():
        raw_records = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        for ordinal, record in enumerate(raw_records):
            if not isinstance(record, Mapping):
                continue
            version = record.get("schema_version", "")
            payload, unit, scope, subject = record, None, "unknown", "evaluation-local:unknown"
            if source.family == "artifact_runtime" and record.get("event_type") == "version_committed":
                payload = record.get("version_ref", {})
                logical = payload.get("logical_ref", {})
                unit = {"fact_snapshot": "B03", "section_plan": "B04", "evidence_package": "C13"}.get(logical.get("artifact_type"))
                subject, scope = logical.get("run_id", subject), logical.get("domain_scope_ref", logical.get("scope_ref", scope))
            elif source.family == "research_runtime" and version == "research_runtime_event_v1" and record.get("event_type") == "round_committed":
                from apps.enterprise_document.schemas.agent_rag_v1.research import ResearchRoundRecordSchema, ResearchProgressStateSchema
                try:
                    ResearchRoundRecordSchema.model_validate(record.get("record"))
                    ResearchProgressStateSchema.model_validate(record.get("resulting_progress"))
                except ValueError as error:
                    batch.findings.append(Finding("RESEARCH_PERSISTED_SCHEMA_INVALID", message=str(error), unit_id="D03", evidence_refs=(source.evidence,)))
                    continue
                unit = "D03"
                payload = record["record"]
                subject = payload.get("round_ref", {}).get("run_id") or record.get("operation_ref", {}).get("run_id", subject)
            elif version.startswith("fact_snapshot_content_"):
                unit = "B03"
            elif version == "section_plan_content_v1":
                unit = "B04"
            elif version == "evidence_package_content_v1":
                unit = "C13"
            if unit is not None:
                batch.stages.append(StageObservation(unit, str(subject), operation_id=source.name + ":" + str(ordinal),
                    scope=str(scope), implementation_version=version, executed=True,
                    observed_execution_status="PERSISTED_OWNER_RECORD", measurement_state="MEASURED", payload=payload,
                    evidence_refs=(replace(source.evidence, pointer="/" + str(ordinal)),), identity_quality="ORIGINAL_OWNER_REF"))
    for key, unit in STAGES.items():
        rows = _rows(snapshot, key)
        if not rows:
            if key == "P-ROUTE" and any(stage.unit_id == "B02" for stage in batch.stages):
                continue
            batch.stages.append(StageObservation(unit, "evaluation-local:unknown", operation_id=key,
                payload={"diagnostic_key": key}, measurement_state="UNMEASURED"))
            continue
        for row in rows:
            subject = _subject(row)
            technical = bool(row.get("technical_failure") or row.get("exception_type") or row.get("error_type") or row.get("error"))
            stage = StageObservation(unit, subject, operation_id=str(row.get("operation_id") or key),
                scope=str(row.get("scope", "unknown")), attempt=row.get("attempt_id"),
                executed=row.get("executed", True), observed_execution_status="TECHNICAL_FAILURE" if technical else "OBSERVED",
                observed_quality_status=str(row.get("semantic_state", "UNKNOWN")),
                observed_control_action=row.get("action"), measurement_state="TECHNICAL_FAILURE" if technical else "MEASURED",
                payload=row, identity_quality="ORIGINAL_RUN" if row.get("run_id") else "EVALUATION_LOCAL")
            batch.stages.append(stage)
            if key == "P-INGRESS":
                if technical:
                    batch.findings.append(Finding("ENTRY_FAILURE", subject, "Input/assembly failed before downstream creation",
                        unit_id="B01", evidence_level="DIRECT_ERROR", unknowns=("Unrecorded downstream execution is not certified NOT_RUN",)))
                if not row.get("task_id"):
                    _metric(batch, "early_failure_subject", unit, subject, bool(subject != "evaluation-local:unknown"))
                if technical and row.get("phase") not in {"begin", "error"}:
                    evaluated_subject = snapshot.get("P-EVALUATION-SUBJECT") or {"subject_id": subject,
                        "task_id": row.get("task_id"), "production_task_created": False}
                    batch.edges.append(check_planning_lineage("X25", row, evaluated_subject, subject_id=subject))
            elif key == "P-FACT":
                facts = row.get("facts", [])
                expected = references.get("facts")
                if expected is None:
                    batch.limitations.append("B03:INDEPENDENT_FACT_REFERENCE_MISSING")
                else:
                    expected_by_key = {canonical_hash(f["key"]): f for f in expected}
                    contamination = [f for f in facts if canonical_hash(f.get("key")) in expected_by_key
                        and expected_by_key[canonical_hash(f.get("key"))].get("state") == "UNKNOWN" and f.get("state") == "KNOWN"]
                    for fact in contamination:
                        batch.findings.append(Finding("UNKNOWN_FACT_ASSERTED", subject, "An independently unknown fact became definite",
                            unit_id=unit, evidence_level="INDEPENDENT_REFERENCE", badcase_ref=str(fact.get("key"))))
                    correct = sum(f in expected for f in facts)
                    _metric(batch, "fact_precision", unit, correct / len(facts) if facts else None, denominator=len(facts))
                    _metric(batch, "fact_recall", unit, correct / len(expected) if expected else None, denominator=len(expected))
            elif key == "P-PLAN":
                required = set(references.get("required_requirements", []))
                if "required_requirements" not in references:
                    batch.limitations.append("B04:INDEPENDENT_REQUIREMENT_REFERENCE_MISSING")
                else:
                    observed = set(row.get("requirement_ids", []))
                    missing = sorted(required - observed)
                    _metric(batch, "requirement_recall", unit, (len(required) - len(missing)) / len(required) if required else None,
                        not missing, len(required))
                    for requirement in missing:
                        batch.findings.append(Finding("PLAN_REQUIREMENT_MISSING", subject, requirement, unit_id=unit,
                            evidence_level="INDEPENDENT_REFERENCE"))
                graph = row.get("dependencies", {})
                visiting, visited = set(), set()
                def visit(node):
                    if node in visiting:
                        return True
                    if node in visited:
                        return False
                    visiting.add(node)
                    cycle = any(visit(dep) for dep in graph.get(node, []))
                    visiting.remove(node); visited.add(node)
                    return cycle
                if any(visit(node) for node in graph):
                    batch.findings.append(Finding("PLAN_DEPENDENCY_CYCLE", subject, unit_id=unit))
            elif key == "P-DISPATCH":
                if row.get("dependencies_complete") is False:
                    batch.findings.append(Finding("UNSATISFIED_DEPENDENCY_DISPATCH", subject, unit_id=unit, edge_id="X25"))
                if row.get("authorized") is False or row.get("duplicate") is True:
                    batch.findings.append(Finding("DISPATCH_AUTHORIZATION_OR_DUPLICATION", subject, unit_id=unit))
            elif key == "P-PACKAGE":
                batch.edges.append(check_package_edge(row.get("bundle"), row.get("package"), subject_id=subject))
            elif key == "P-ADMISSION":
                if technical:
                    batch.findings.append(Finding("CRITERION_TECHNICAL_FAILURE", subject, "No semantic verdict inferred from failed candidate", unit_id=unit))
                elif row.get("semantic_state") in {"NOT_SUPPORTED", "INDETERMINATE", "SUPPORTED"}:
                    _metric(batch, "criterion_observed_state", unit, row["semantic_state"])
                    independent = references.get("criterion_state")
                    if independent is not None:
                        _metric(batch, "criterion_accuracy", unit, row["semantic_state"] == independent, row["semantic_state"] == independent)
            elif key in {"P-ENFORCEMENT", "P-RECOVERY"}:
                allowed = row.get("allowed_actions")
                if allowed is not None:
                    _metric(batch, "policy_conformance", "D02", row.get("action") in allowed, row.get("action") in allowed)
                if key == "P-RECOVERY":
                    for field, limit in (("rounds_used", "rounds_limit"), ("cost_used", "budget_limit")):
                        if field in row and limit in row and row[field] > row[limit]:
                            batch.findings.append(Finding("RECOVERY_QUOTA_OR_BUDGET_EXCEEDED", subject, field, unit_id="D03"))
                    if row.get("current_commit_valid") is False and row.get("as_of_commit_valid") is True:
                        _metric(batch, "historical_currentness", "D03", "AS_OF_VALID_CURRENT_STALE", True)
            elif key == "P-CONTEXT":
                text = str(row.get("final_input_material", row.get("model_input", {})))
                for field, value in references.get("protected_values", {}).items():
                    if str(value) not in text:
                        batch.findings.append(Finding("CONTEXT_PROTECTED_VALUE_LOST", subject, field, unit_id=unit, edge_id="X09"))
            elif key == "P-FANIN":
                if row.get("phase") not in {"before", "after"}:
                    batch.edges.append(check_fanin_remap(row, subject_id=subject))
    batch.edges.extend(_context_request_edges(snapshot, _rows(snapshot, "P-CONTEXT")))
    native_lineage = _native_planning_lineage_edges(snapshot)
    lineage = snapshot.get("P-LINEAGE")
    batch.edges.extend(native_lineage or [check_planning_lineage("X30", (lineage or {}).get("source"), (lineage or {}).get("target"),
        subject_id=_subject(lineage or {}))])
    for edge_id in ("X08", "X09", "X25", "X26"):
        if not any(edge.edge_id == edge_id for edge in batch.edges):
            batch.edges.append(_edge(edge_id, "evaluation-local:unknown", None, "EDGE_ENDPOINT_EVIDENCE_MISSING"))
    for edge in batch.edges:
        if edge.assertion_result is False:
            batch.findings.append(Finding(edge.reason.split(":")[0], edge.subject_id, edge.reason, edge_id=edge.edge_id, evidence_level="EDGE_IDENTITY"))
    batch.evidence.extend(source.evidence for source in snapshot.sources.values())
    return batch


def extract_planning_prewrite(snapshot: SnapshotView, *, subject_id="unknown") -> ObservationBatch:
    batch = evaluate_planning_prewrite(snapshot)
    batch.stages = [replace(stage, subject_id=subject_id) if stage.subject_id == "evaluation-local:unknown" else stage for stage in batch.stages]
    return batch


def capture_planning_postwrite_snapshot(rows, *, snapshot_id="synthetic-capture", evidence_ref=None):
    """Use captured finite rows with their original read evidence when supplied.

    Without a real reader evidence ref this is explicitly a synthetic fixture;
    it cannot turn in-memory observations into persisted historical evidence.
    """
    material = list(rows)
    if evidence_ref is None:
        return SnapshotView.from_records({"diagnostics": material}, snapshot_id=snapshot_id)
    return SnapshotView(snapshot_id, {"diagnostics": LoadedSource("diagnostics", "diagnostics", material, evidence_ref)})
