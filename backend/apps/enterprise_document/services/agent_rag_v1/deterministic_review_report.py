"""Read-only, deterministic local view of committed Post-write results.

No quality, execution, human-approval or publication authority lives here.
Writer Copy owns the existing local file primitives; its protocol is unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import writer_review_copy as wc

RENDERER_VERSION = "deterministic-postwrite-review-v1"
_json_bytes = wc._json_bytes
_sha = wc._sha


class ReviewReportUnavailable(ValueError):
    """A derivative cannot be proved; never retry the business operation."""


_ADVICE = {
    "consistency": "核对是否同一项目、同一阶段、同一数量口径及属性；不自动修改正文。",
    "faithfulness": "对照正文与原始可信证据，人工核对正向依据。",
    "citation": "核对正文引用与来源绑定，检查缺引及错误引用。",
    "coverage": "对照已登记的必需章节和检查项，核对覆盖与完成情况。",
    "relevancy": "核对内容与项目需求范围是否匹配。",
}


def _advice(dimension):
    return next((text for family, text in _ADVICE.items() if family in dimension.lower()),
        "按原 RequiredCondition 与 FactRef 人工核对；不自动改写正文。")


def _results_sha(bundle):
    return _sha({"renderer_version": RENDERER_VERSION, "committed_results": bundle})


def _display(value):
    """One escaped display line; original payload remains in the source hashes."""
    if not isinstance(value, str):
        value = _json_bytes(value).decode("utf-8")
    import html
    return html.escape(value, quote=False).replace("\r", "&#13;").replace("\n", "&#10;")


def render_review_report(bundle):
    """Pure UTF-8 renderer. No wall clock, filesystem access, or model call."""
    source = bundle["source_receipt"]
    lines = ["# Post-write 确定性校审报告", "",
        "机器校审辅助意见，仅对指定原始 Writer 正文版本有效；报告不构成工程师签发。", "",
        "## 基本信息", ""]
    for key, value in (("TaskId", source["task_id"]), ("RunId", source["run_id"]),
            ("DocumentId", source["document_id"]), ("Writer Source Hash", source["source_snapshot_sha256"]),
            ("原始正文 SHA256", source["markdown_sha256"]), ("Renderer", RENDERER_VERSION),
            ("Results SHA256", _results_sha(bundle)), ("报告完整度", bundle["completeness"]),
            ("Assembly Manifest", {k: bundle["manifest"][k] for k in ("manifest_id", "full_manifest_sha256")}
                if bundle.get("manifest") else "NOT_ESTABLISHED"),
            ("原执行状态来源 SHA256", _sha(bundle.get("terminal", {}))),
            ("原 Post-write 执行状态", bundle.get("terminal", {}))):
        lines.append(f"- {key}: {_display(value)}")
    lines += ["", "原 Run FAILED/PAUSED、Section Quality、Document Quality 与报告完整度分别保留。",
        "缺少正式 Completion/Gate 的范围不会被记为 PASS；无明确持久执行证据不记 NOT_RUN。", "",
        "## 分章节校审结果", ""]

    def chain(value, heading):
        nonlocal lines
        lines.extend([heading, ""])
        if value is None or "gate" not in value:
            lines.extend(["COMPLETION_NOT_ESTABLISHED / UNVERIFIABLE。仅展示已提交且可精确归属的结果。", ""])
            return
        gate, control = value["gate"], value["control"]
        lines.extend([f"- Scope: {_display(value['entry']['identity']['entry_scope'])}",
            f"- Overall Quality: {_display(gate['overall_quality_state'])}",
            f"- Control: {_display(control['enforcement_action'])}",
            f"- Control 原因: {_display(control['control_reason_codes'])}",
            f"- Assembly selection: {_display(value.get('selection_state', 'DOCUMENT'))}",
            f"- ValidationRunRef: {_display(value['validation_run']['validation_run_id'])}",
            f"- CompletionRef: {_display(gate['gate_input_manifest']['validation_completion_ref'])}",
            f"- GateRef: {_display(control['delivery_gate_decision_ref'])}",
            f"- ControlRef: {_display({'control_decision_id': control['control_decision_id'], 'sha256': control['control_decision_hash']})}",
            f"- EntryRef: {_display(control['input_manifest']['post_write_entry_ref'])}",
            f"- ArtifactRef: {_display(control['input_manifest']['artifact_state_ref'])}",
            f"- 来源集合 SHA256: {_sha(value)}",
            "- 定位: LOCATOR_NOT_PROVEN；仅 Scope / Section / Dimension / FactRef，不提供页码或虚构原句。", ""])
        conditions = {c["condition_ref"]: c for c in gate["quality_policy_projection"]["condition_results"]}
        dimensions = sorted(gate["dimension_result_projections"], key=lambda d: d["dimension_id"])
        for dimension in dimensions:
            key = dimension["dimension_id"]
            lines.extend([f"#### {_display(key)}: {_display(dimension['state'])}", "",
                f"人工核对建议（固定模板）：{_advice(key)}", ""])
            for c in dimension.get("condition_results", []):
                conditions[c["condition_ref"]] = c
            if dimension.get("warning_fact_refs"):
                lines.append(f"- WARNING FactRefs: {_display(dimension['warning_fact_refs'])}")
            if dimension.get("unresolved_reason_refs"):
                lines.append(f"- UNRESOLVED 原因: {_display(dimension['unresolved_reason_refs'])}")
        lines.extend(["", "#### RequiredCondition（原状态）", ""])
        for ref in sorted(conditions):
            c = conditions[ref]
            lines.append(f"- {_display(c['dimension_id'])} / {_display(c['state'])}: {_display(c)}")
        for key in ("blocking_conditions", "fact_policy_effects"):
            if gate["quality_policy_projection"].get(key):
                lines.append(f"- 原 {key}: {_display(gate['quality_policy_projection'][key])}")
        lines += ["", "#### 已提交 Fact 与 Completion", "",
            "以下为原 Canonical Fact 的记录；其中模型原始理由标为 MODEL_DERIVED，不能作为独立证据。", ""]
        for fact in sorted(value["facts"], key=lambda f: (f["family"], f["fact_id"])):
            lines += [f"- {fact['family']} / {fact['semantic_state']} / FactRef: {_display(fact['fact_id'])}",
                f"  - SHA256: {fact['fact_sha256']}；TargetRef: {_display(fact['target_ref'])}",
                f"  - 原 payload（解释性文字 MODEL_DERIVED / 未独立核实）: {_display(fact['payload'])}"]
        for item in sorted(value["completion"]["items"], key=lambda i: i["expected_validation_item_ref"]):
            lines.append(f"- Completion item: {_display(item)}")
        lines.append("")

    for index, section in enumerate(bundle["sections"], 1):
        chain(section, f"### {index}. {_display(section['title'])} / SectionId: {_display(section['section_id'])}")
    lines.extend(["## 文档级校审结果", ""])
    chain(bundle["document"], "### DOCUMENT")
    lines.extend(["## 异常、不完整与发布", "",
        f"- Writer Copy: AVAILABLE；原始正文 SHA256: {source['markdown_sha256']}",
        f"- 报告覆盖完整度: {bundle['completeness']}",
        f"- Machine Release: {_display(bundle.get('publication', {'state': 'NOT_VERIFIED'}))}",
        "- Human approval: HUMAN_APPROVAL_NOT_ESTABLISHED", "",
        "技术中断：显示原错误和已提交范围；未建立验证结果的部分需要人工核对或原流程处理。",
        "STANDARD Machine Release 不等于工程师已审核签发。报告完成也不表示发现了所有错误。",
        "工程师编辑正文后，只要 SHA256 与本报告原始正文不同，本报告不适用于修改版；本模块不会发起重新校审。", ""])
    return "\n".join(lines).encode("utf-8")


def report_applies_to(receipt, markdown):
    """Compare an engineer's edited bytes with the original version only."""
    return hashlib.sha256(markdown).hexdigest() == receipt["writer_markdown_sha256"]


def seal_review_report(*, runs_dir, bundle):
    source = bundle["source_receipt"]
    # Re-open the earlier pair; never recover it by resealing or rewriting it.
    verified = wc.verify_sealed_pair(runs_dir=runs_dir, task_id=source["task_id"], run_id=source["run_id"],
        source_snapshot_sha256=source["source_snapshot_sha256"], expected_receipt=source)
    markdown = render_review_report(bundle)
    core = {"schema_version": "deterministic_review_report_receipt_v1", "renderer_version": RENDERER_VERSION,
        "task_id": source["task_id"], "run_id": source["run_id"], "document_id": source["document_id"],
        "writer_source_sha256": source["source_snapshot_sha256"], "writer_markdown_sha256": source["markdown_sha256"],
        "writer_receipt_sha256": verified["receipt"]["receipt_sha256"], "results_sha256": _results_sha(bundle),
        "report_sha256": hashlib.sha256(markdown).hexdigest(), "completeness": bundle["completeness"],
        "committed_scope_sha256": {**{s["section_id"]: _sha(s) for s in bundle["sections"]},
            "DOCUMENT": _sha(bundle["document"])},
        "human_approval": "HUMAN_APPROVAL_NOT_ESTABLISHED", "formal_release_authorized": False}
    receipt = {**core, "receipt_sha256": _sha(core)}
    with wc._wc_locked_paths(runs_dir, source["task_id"], source["run_id"], source["source_snapshot_sha256"],
            create=False) as (writer, _):
        stem = "review-report-" + source["source_snapshot_sha256"] + "-" + core["results_sha256"]
        md, record = writer.parent / (stem + ".md"), writer.parent / (stem + ".receipt.json")
        if not wc._wc_exists(record):
            wc._wc_publish(md, markdown)
            with wc._wc_read_file(md) as stream:
                if stream.read() != markdown:
                    raise ReviewReportUnavailable("REPORT_MARKDOWN_CHANGED")
            wc._wc_publish(record, _json_bytes(receipt))
        with wc._wc_read_file(md) as body, wc._wc_read_file(record) as saved:
            if body.read() != markdown or saved.read() != _json_bytes(receipt):
                raise ReviewReportUnavailable("REPORT_PAIR_CONFLICT")
    return {"report_path": str(md), "receipt_path": str(record), "receipt": receipt,
        "ready": True, "formal_release_authorized": False}


def _find_writer_pair(post, task_id, run_id):
    with wc._wc_locked_paths(post.runs_dir, task_id, run_id, "0" * 64, create=False) as (writer, _):
        paths = sorted(writer.parent.glob("writer-review-*.receipt.json"))
        if len(paths) != 1:
            raise ReviewReportUnavailable("UNAMBIGUOUS_WRITER_COPY_REQUIRED")
        source_hash = paths[0].name[len("writer-review-"):-len(".receipt.json")]
    return wc.verify_sealed_pair(runs_dir=post.runs_dir, task_id=task_id, run_id=run_id,
        source_snapshot_sha256=source_hash)["receipt"]


def try_export_after_workflow(*, engine, workflow, task, result):
    """Called after Graph return, outside its Node runner and commit authority."""
    post = getattr(getattr(engine, "stage_service", None), "target_post_write_composition", None)
    if post is None:
        return None
    source = _find_writer_pair(post, task.task_id, task.run_id)
    payload = engine._latest_checkpoint_payload(workflow, run_id=task.run_id)
    if payload is None or (payload.task_id, payload.run_id, payload.document_id) != (
            source["task_id"], source["run_id"], source["document_id"]):
        raise ReviewReportUnavailable("COMMITTED_WORKFLOW_SOURCE_MISMATCH")
    bundle = collect_review_results(post, source_receipt=source,
        document_control_ref=payload.target_post_write_control_decision_ref)
    outcome = payload.terminal_outcome
    canonical_run = post.lifecycle_service.load_run_lifecycle(task.run_id)
    if (canonical_run.identity.task_id, canonical_run.identity.run_id) != (task.task_id, task.run_id):
        raise ReviewReportUnavailable("CANONICAL_RUN_IDENTITY_MISMATCH")
    bundle.update(source_receipt=source, terminal={"execution_status": result.status.value,
        "canonical_run": canonical_run.model_dump(mode="json"),
        "canonical_outcome": outcome.model_dump(mode="json") if outcome is not None else None,
        "outer_error": {"error_code": result.error.error_code, "message": result.error.message}
            if result.error is not None else None,
        "technical_error": payload.terminal_error.model_dump(mode="json") if payload.terminal_error else None},
        publication={"state": "NOT_VERIFIED"})
    publication = payload.target_release_publication
    if publication is not None:
        from apps.enterprise_document.schemas.post_write.release import PostWriteReleaseManifestSchema
        ref = publication.manifest_artifact
        native_ref, raw = post.release_store.get_bytes_with_reference(ref.artifact_ref,
            task_id=task.task_id, run_id=task.run_id,
            input_snapshot_version=ref.input_snapshot_version)
        manifest = PostWriteReleaseManifestSchema.model_validate_json(raw)
        document = bundle["document"]
        if (native_ref != ref or manifest != publication.manifest or document is None
                or manifest.document_artifact_ref.model_dump(mode="json") != document["artifact"]["artifact_ref"]
                or manifest.run_completion_assessment_ref != payload.target_run_completion_assessment_ref
                or manifest.quality_state_passthrough.value != document["gate"]["overall_quality_state"]
                or manifest.enforcement_action_passthrough.value != document["control"]["enforcement_action"]):
            raise ReviewReportUnavailable("PUBLICATION_RESULT_MISMATCH")
        if (manifest.task_id, manifest.run_id, manifest.document_artifact_ref,
                manifest.post_write_control_decision_ref, manifest.assembly_input_manifest_ref.model_dump(mode="json")) != (
                task.task_id, task.run_id, payload.target_document_artifact_ref,
                payload.target_post_write_control_decision_ref,
                bundle["document"]["entry"]["identity"]["assembly_input_manifest_ref"]):
            raise ReviewReportUnavailable("PUBLICATION_SOURCE_MISMATCH")
        bundle["publication"] = {"state": "VERIFIED_COMMITTED_MANIFEST", "release_mode": manifest.release_mode.value,
            "release_id": manifest.release_id, "manifest_artifact_ref": ref.model_dump(mode="json"),
            "manifest_sha256": hashlib.sha256(raw).hexdigest()}
    return seal_review_report(runs_dir=post.runs_dir, bundle=bundle)


from pathlib import Path
import hashlib
import json
import sqlite3

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write import ArtifactScope, AssemblySelectionState
from apps.enterprise_document.schemas.post_write.control import PostWriteControlDecisionSchema
from apps.enterprise_document.schemas.post_write.foundation import (
    PostWriteControlDecisionRefSchema, ValidationFactRefSchema,
    ValidationIntermediateRefSchema,
)
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.binding import PostWriteEntryIdentityBuilder
from apps.enterprise_document.services.post_write.execution import ValidationSubjectKeyBuilder
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    DocumentCitationProjection, validate_document_citation_projection,
)


def _rr_require(condition, message):
    if not condition:
        raise ReviewReportUnavailable(message)


def _rr_refs(value, schema_version):
    if isinstance(value, dict):
        if value.get("schema_version") == schema_version:
            yield value
        else:
            for item in value.values():
                yield from _rr_refs(item, schema_version)
    elif isinstance(value, list):
        for item in value:
            yield from _rr_refs(item, schema_version)


def _rr_chain(post, control_ref, receipt, connection, *, source_row=None):
    """Read an immutable result, validating identity without evaluating quality."""
    control = post.control_store.resolve_exact(control_ref, connection=connection)
    entry_ref = control.input_manifest.post_write_entry_ref
    entry = post.entry_store.resolve_exact(entry_ref, connection=connection)
    identity = entry.identity
    scope = ArtifactScope.SECTION if source_row is not None else ArtifactScope.DOCUMENT
    _rr_require(identity.entry_scope is scope and control.input_manifest.artifact_scope is scope
        and identity.run_id == receipt["run_id"] and identity.document_id == receipt["document_id"]
        and control.input_manifest.artifact_state_ref == identity.artifact_state_ref,
        "Control/Entry Run, Document, Scope or Artifact differs")
    # The existing identity producer hashes typed operands; JSON conversion here
    # changes that historical preimage. Reuse its exact typed input contract.
    identity_payload = {key: getattr(identity, key) for key in type(identity).model_fields
        if key not in {"schema_version", "entry_id", "entry_sha256"}}
    _rr_require(PostWriteEntryIdentityBuilder._finalize(identity_payload) == identity,
        "Entry identity canonical hash differs")
    artifact = post.artifact_store.resolve_exact(identity.artifact_state_ref, connection=connection)
    _rr_require(post.artifact_store.resolve_current(artifact.artifact_ref.artifact_id,
        connection=connection).artifact_ref == artifact.artifact_ref, "Artifact head moved; report source is stale")
    _rr_require(ArtifactCanonicalPayloadMaterializer.artifact_hash(artifact_scope=scope,
        canonical_payload_ref=artifact.canonical_payload_ref) == artifact.artifact_ref.artifact_hash,
        "Artifact content hash differs")
    body = ArtifactCanonicalPayloadMaterializer.business_payload(artifact.canonical_payload_ref)
    if source_row is not None:
        _rr_require(identity.section_id == source_row["section_id"]
            and identity.writer_execution_ref.model_dump(mode="json") == source_row["writer_execution_ref"]
            and identity.writer_invocation_ref.model_dump(mode="json") == source_row["writer_invocation_ref"]
            and body.get("payload_kind") == "section" and body.get("section_id") == source_row["section_id"]
            and body.get("section_title") == source_row["section_title"]
            and isinstance(body.get("content"), str)
            and hashlib.sha256(body["content"].encode("utf-8")).hexdigest() == source_row["section_sha256"],
            "Section differs from sealed Writer Source")
    gate = post.gate_store.resolve_exact(control.delivery_gate_decision_ref, connection=connection)
    manifest = gate.gate_input_manifest
    _rr_require(gate.validation_run_id == control.validation_run_id
        and control.quality_state_passthrough == gate.overall_quality_state
        and manifest.post_write_entry_ref == entry_ref
        and manifest.artifact_state_ref == identity.artifact_state_ref
        and manifest.evidence_binding == identity.evidence_binding
        and manifest.requirement_ref == identity.requirement_ref
        and manifest.quality_delivery_contract_ref == identity.quality_delivery_contract_ref,
        "Gate/Control/Entry exact binding differs")
    for proof in (gate.currentness_proof, control.fresh_currentness_proof):
        value = proof.model_dump(mode="json")
        expected_hash = value.pop("proof_sha256")
        builder = PostWriteEntryIdentityBuilder()
        active_identity = (builder.from_active_section_projection(proof.active_projection) if source_row is not None
            else builder.from_active_document_projection(proof.active_projection))
        _rr_require(canonical_sha256(value) == expected_hash and proof.currentness_state.value == "CURRENT"
            and proof.historical_entry_ref == entry_ref and proof.expected_active_entry_identity == identity
            and active_identity == identity
            and proof.active_projection.artifact_state_ref == identity.artifact_state_ref
            and proof.active_projection.run_id == identity.run_id
            and proof.active_projection.document_id == identity.document_id,
            "Stored currentness proof differs from exact Entry")
    run = post.validation_store.resolve_run_exact(control.validation_run_id, connection=connection)
    subject = run.subject_key
    _rr_require(subject == gate.subject_key and subject.post_write_entry_ref == entry_ref
        and subject.validation_scope.value == scope.value
        and subject.target_selector.target_ref == (identity.section_id if source_row is not None else identity.document_id)
        and ValidationSubjectKeyBuilder.build(post_write_entry_ref=entry_ref,
            validation_scope=subject.validation_scope, target_selector=subject.target_selector,
            subject_contract_version=subject.subject_contract_version) == subject,
        "ValidationRun subject identity differs")
    completion = post.validation_store.resolve_completion(manifest.validation_completion_ref, connection=connection)
    _rr_require(run.execution_state.value == "COMPLETED"
        and completion.validation_run_id == run.validation_run_id
        and completion.subject_hash == subject.subject_hash
        and manifest.validation_completion_hash == completion.completion_sha256,
        "Completion is not established for exact ValidationRun")
    facts = post.validation_store.list_facts_for_run(run.validation_run_id, connection=connection)
    fact_refs = tuple(post.validation_store._fact_ref(fact) for fact in facts)
    _rr_require(fact_refs == manifest.validation_fact_refs
        and all(ref.subject_hash == subject.subject_hash for ref in fact_refs),
        "Gate Fact universe differs from committed ValidationRun")
    policy = gate.quality_policy_projection
    _rr_require(policy.validation_run_id == run.validation_run_id and policy.subject_hash == subject.subject_hash
        and policy.quality_delivery_contract_ref == identity.quality_delivery_contract_ref
        and policy.consumed_fact_refs == fact_refs,
        "Gate policy source identity differs")
    conditions = {item.condition_ref: item for item in policy.condition_results}
    for dimension in gate.dimension_result_projections:
        _rr_require(all(item.dimension_id == dimension.dimension_id
            and conditions.get(item.condition_ref) == item for item in dimension.condition_results),
            "Dimension/RequiredCondition exact relation differs")
    values = [value.model_dump(mode="json") for value in (control, gate, completion)]
    known_refs = {ref.fact_id: ref for ref in fact_refs}
    for raw in _rr_refs(values, "validation_fact_ref_v1"):
        ref = ValidationFactRefSchema.model_validate(raw)
        _rr_require(known_refs.get(ref.fact_id) == ref, "Fact Ref escapes exact ValidationRun")
        post.validation_store.resolve_fact(ref, connection=connection)
    for raw in _rr_refs(values, "validation_intermediate_ref_v1"):
        ref = ValidationIntermediateRefSchema.model_validate(raw)
        _rr_require(ref.validation_run_id == run.validation_run_id, "Condition proof belongs to another ValidationRun")
        post.validation_store.resolve_intermediate(ref, connection=connection)
    return {"entry": entry.model_dump(mode="json"), "artifact": artifact.model_dump(mode="json"),
        "control": control.model_dump(mode="json"), "gate": gate.model_dump(mode="json"),
        "validation_run": run.model_dump(mode="json"), "completion": completion.model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json") for fact in facts]}


def collect_review_results(post, *, source_receipt, document_control_ref=None):
    """No owner creation, live currentness, lifecycle, Gate evaluation or model call."""
    receipt = source_receipt
    rows = receipt["required_sections"]
    by_id = {row["section_id"]: row for row in rows}
    _rr_require(bool(rows) and len(by_id) == len(rows), "Sealed Source Section set differs")
    path = Path(post.database.path).absolute()
    _rr_require(path.is_file(), "Post-write database unavailable")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        sections, document, manifest = [], None, None
        if document_control_ref is not None:
            ref = PostWriteControlDecisionRefSchema.model_validate(document_control_ref)
            document = _rr_chain(post, ref, receipt, connection)
            control = PostWriteControlDecisionSchema.model_validate(document["control"])
            entry_manifest = document["entry"]["identity"]["assembly_input_manifest_ref"]
            _rr_require(control.input_manifest.assembly_input_manifest_ref.model_dump(mode="json") == entry_manifest,
                "Document Control/Entry Manifest differs")
            manifest = post.binding_store.resolve_assembly_manifest(control.input_manifest.assembly_input_manifest_ref,
                connection=connection)
            _rr_require(manifest.document_logical_id == f'document-logical:{receipt["run_id"]}:{receipt["document_id"]}'
                and tuple(item.section_key for item in manifest.ordered_section_entries) == tuple(by_id),
                "Assembly Manifest differs from sealed Writer Source set/order")
            source_payload = {"payload_kind": "document", "document_logical_id": manifest.document_logical_id,
                "included_sections": [], "missing_section_keys": [], "withheld_section_keys": [], "partial": False}
            for item in manifest.ordered_section_entries:
                row = by_id[item.section_key]
                header = {"section_id": item.section_key, "title": row["section_title"],
                    "selection_state": item.selection_state.value}
                if item.selection_state is AssemblySelectionState.MISSING:
                    sections.append({**header, "status": "COMPLETION_NOT_ESTABLISHED"})
                    source_payload["missing_section_keys"].append(item.section_key)
                    continue
                chain = _rr_chain(post, item.selected_section_control_decision_ref, receipt, connection, source_row=row)
                _rr_require(chain["entry"]["identity"]["requirement_ref"] == document["entry"]["identity"]["requirement_ref"]
                    and chain["entry"]["identity"]["quality_delivery_contract_ref"]
                        == document["entry"]["identity"]["quality_delivery_contract_ref"],
                    "Selected Section/Document requirement or delivery contract differs")
                sections.append({**header, **chain})
                candidate = item.section_assembly_candidate
                if candidate is None:
                    source_payload["withheld_section_keys"].append(item.section_key)
                    continue
                section_control = PostWriteControlDecisionSchema.model_validate(chain["control"])
                _rr_require(candidate.section_artifact_ref == section_control.input_manifest.artifact_state_ref
                    and candidate.section_post_write_entry_ref == section_control.input_manifest.post_write_entry_ref
                    and candidate.section_gate_decision_ref == section_control.delivery_gate_decision_ref
                    and candidate.section_evidence_snapshot_ref.model_dump(mode="json")
                        == chain["entry"]["identity"]["evidence_binding"]["section_writer_snapshot_ref"]
                    and candidate.generation_class == section_control.input_manifest.generation_class
                    and candidate.quality_state == section_control.quality_state_passthrough
                    and candidate.enforcement_action == section_control.enforcement_action
                    and candidate.availability_state == section_control.availability_projection.state,
                    "Assembly candidate differs from selected Section chain")
                source_payload["included_sections"].append({"section_key": item.section_key,
                    "visible_payload": dict(chain["artifact"]["canonical_payload_ref"])})
            source_payload["partial"] = bool(source_payload["missing_section_keys"] or source_payload["withheld_section_keys"])
            document_payload = ArtifactCanonicalPayloadMaterializer.business_payload(document["artifact"]["canonical_payload_ref"])
            if "citation_projection" in document_payload:
                projection = DocumentCitationProjection.model_validate(document_payload["citation_projection"])
                _rr_require(projection.source_payload_sha256 == canonical_sha256(source_payload),
                    "Document projection Source payload differs")
                validate_document_citation_projection(document_payload, source_payload, projection.source_catalogs)
            else:
                _rr_require(document_payload == source_payload, "Document content differs from Manifest Sections")
        else:
            # Enumeration discovers candidates only; every displayed value is re-read by exact Ref.
            candidates = {key: [] for key in by_id}
            for record in connection.execute("SELECT decision_json FROM post_write_control_decisions ORDER BY control_decision_id"):
                raw = json.loads(record["decision_json"])
                if raw.get("input_manifest", {}).get("artifact_scope") != "SECTION":
                    continue
                candidate = PostWriteControlDecisionSchema.model_validate(raw)
                entry = post.entry_store.resolve_exact(candidate.input_manifest.post_write_entry_ref, connection=connection)
                identity = entry.identity
                row = by_id.get(identity.section_id)
                if row is None or identity.run_id != receipt["run_id"] or identity.document_id != receipt["document_id"]:
                    continue
                if (identity.writer_execution_ref.model_dump(mode="json") != row["writer_execution_ref"]
                        or identity.writer_invocation_ref.model_dump(mode="json") != row["writer_invocation_ref"]):
                    continue
                candidates[identity.section_id].append(candidate.as_ref())
            for row in rows:
                refs = candidates[row["section_id"]]
                header = {"section_id": row["section_id"], "title": row["section_title"]}
                if len(refs) != 1:
                    sections.append({**header, "status": "COMPLETION_NOT_ESTABLISHED",
                        "evidence_limit": "AMBIGUOUS_COMMITTED_CONTROLS" if refs else "NO_EXACT_COMMITTED_CONTROL"})
                else:
                    chain = _rr_chain(post, refs[0], receipt, connection, source_row=row)
                    sections.append({**header, "selection_state": "NOT_ESTABLISHED_WITHOUT_ASSEMBLY_MANIFEST", **chain})
        count = sum("gate" in section for section in sections)
        return {"manifest": manifest.model_dump(mode="json") if manifest is not None else None,
            "sections": sections, "document": document,
            "completeness": "COMPLETE" if document is not None and count == len(rows)
                else "PARTIAL" if document is not None or count else "UNVERIFIABLE"}
    except ReviewReportUnavailable:
        raise
    except Exception as exc:
        raise ReviewReportUnavailable("Exact committed Post-write results could not be verified") from exc
    finally:
        connection.close()
