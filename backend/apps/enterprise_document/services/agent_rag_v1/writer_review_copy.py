"""Local, non-authoritative Writer snapshot before the original Post-write stages.

Canonical source owners and the committed SQLite checkpoint retain authority.
The Markdown/receipt pair only preserves their exact bytes for local review.
"""
from __future__ import annotations

import hashlib
import json


class WriterReviewCopyUnavailable(ValueError):
    """A derived copy cannot be proved; the original workflow must continue."""


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


# CHECKPOINT_SOURCE_BEGIN
def _require_source(condition, code):
    if not condition:
        raise WriterReviewCopyUnavailable(code)


def _payload_comparable_json(snapshot_payload):
    """Normalize only the known Set in a fresh, comparison-only JSON view."""
    data = snapshot_payload.model_dump(mode="json")
    _require_source(isinstance(data, dict), "CHECKPOINT_INCOMING_SNAPSHOT_MISMATCH")
    assembly = snapshot_payload.assembly
    if assembly is None:
        return data
    original = assembly.known_chunk_ids
    view = data.get("assembly")
    serialized = view.get("known_chunk_ids") if isinstance(view, dict) else None
    _require_source(
        isinstance(original, set)
        and all(isinstance(item, str) for item in original)
        and isinstance(serialized, list)
        and all(isinstance(item, str) for item in serialized)
        and len(serialized) == len(original)
        and set(serialized) == original,
        "CHECKPOINT_INCOMING_SNAPSHOT_MISMATCH",
    )
    view["known_chunk_ids"] = sorted(serialized)
    return data


def resolve_committed_predecessor(engine, *, workflow, before_graph, before_payload):
    """Pin the actual committed validate predecessor, never pending writes."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity

    saver = engine.checkpointer
    _require_source(isinstance(getattr(saver, "delegate", saver), SqliteSaver), "SQLITE_SAVER_REQUIRED")
    namespace = engine.runtime_state_codec.workflow_namespace(workflow)
    config = phase2_checkpoint_identity(workflow, run_id=before_graph.run_id)
    latest = saver.get_tuple(config)
    _require_source(latest is not None, "COMMITTED_PREDECESSOR_MISSING")
    identity = latest.config.get("configurable", {})
    checkpoint_id = identity.get("checkpoint_id")
    _require_source(isinstance(checkpoint_id, str) and bool(checkpoint_id)
        and identity.get("thread_id") == before_graph.run_id
        and identity.get("checkpoint_ns") == namespace
        and identity.get("workflow_namespace", namespace) == namespace
        and latest.checkpoint.get("id") == checkpoint_id, "CHECKPOINT_IDENTITY_MISMATCH")
    exact_config = {**config, "configurable": {**config["configurable"], "checkpoint_id": checkpoint_id}}
    pinned = saver.get_tuple(exact_config)
    pinned_identity = pinned.config.get("configurable", {}) if pinned is not None else {}
    _require_source(pinned is not None and pinned.checkpoint == latest.checkpoint
        and pinned.metadata == latest.metadata
        and (pinned_identity.get("thread_id"), pinned_identity.get("checkpoint_ns"),
            pinned_identity.get("checkpoint_id")) == (before_graph.run_id, namespace, checkpoint_id)
        and pinned_identity.get("workflow_namespace", namespace) == namespace,
        "CHECKPOINT_PIN_MISMATCH")
    metadata = pinned.metadata
    _require_source(metadata.get("source") == "loop", "CHECKPOINT_NOT_LOOP_COMMIT")
    for key, expected in (("workflow_id", workflow.workflow_id),
            ("workflow_version", workflow.workflow_version), ("checkpoint_namespace", namespace),
            ("workflow_namespace", namespace)):
        _require_source(key not in metadata or metadata[key] == expected, "CHECKPOINT_METADATA_MISMATCH")
    values, payload, count, _ = engine._recovery_checkpoint_values(
        workflow, pinned, task_id=before_graph.task_id, run_id=before_graph.run_id)
    graph = engine.runtime_state_codec.restore_graph_state(values, workflow=workflow)
    outputs = values.get("node_outputs") or []
    output = outputs[-1] if outputs else {}
    _require_source(values.get("workflow_namespace", namespace) == namespace
        and values.get("current_business_node") == "validate_document_assembly"
        and output.get("node_id") == "validate_document_assembly"
        and output.get("status") == "success" and (output.get("result") or {}).get("status") == "success"
        and values.get("business_route") == "continue"
        and payload.assembly_validation is not None and payload.assembly_validation.passed is True,
        "VALIDATE_SUCCESS_PREDECESSOR_REQUIRED")
    _require_source(graph.model_dump(mode="json") == before_graph.model_dump(mode="json")
        and _payload_comparable_json(payload) == _payload_comparable_json(before_payload),
        "CHECKPOINT_INCOMING_SNAPSHOT_MISMATCH")
    return {"checkpoint_id": checkpoint_id, "committed_checkpoint_source_sha256": _sha(values),
        "payload": payload, "workflow_namespace": namespace, "task_id": graph.task_id,
        "run_id": graph.run_id, "business_revision": count}


def validate_exact_writer_document(snapshot, *, section_dependencies):
    """Reuse the source owner; add only document identity/completeness/bytes checks."""
    payload = snapshot["payload"]
    plan, assembly = payload.document_plan, payload.assembly
    _require_source(plan is not None and assembly is not None and payload.generated_draft is not None
        and payload.project_input is not None and payload.assembly_validation is not None
        and payload.assembly_validation.passed is True, "COMPLETE_ASSEMBLY_REQUIRED")
    draft = assembly.draft
    _require_source((payload.task_id, payload.run_id, payload.workflow_namespace)
        == (snapshot["task_id"], snapshot["run_id"], snapshot["workflow_namespace"])
        and (draft.task_id, draft.run_id, draft.document_id)
        == (payload.task_id, payload.run_id, payload.document_id)
        and plan.document_id == payload.document_id, "DOCUMENT_IDENTITY_MISMATCH")
    _require_source(section_dependencies.lifecycle.load_run_identity(payload.run_id).task_id == payload.task_id,
        "SOURCE_TASK_IDENTITY_MISMATCH")
    required = list(payload.required_sections)
    input_required = list(payload.project_input.generation_requirements.required_sections
        or payload.project_input.output_schema.required_sections)
    _require_source(bool(required) and len(required) == len(set(required)) and required == input_required
        and list(draft.required_sections) == required and not draft.missing_sections,
        "REQUIRED_SECTIONS_MISMATCH")
    ordered_plan = sorted(plan.sections, key=lambda item: (item.section_order, item.section_id))
    triples = [(item.section_id, item.section_title, item.section_order) for item in ordered_plan]
    _require_source(bool(triples) and len({row[0] for row in triples}) == len(triples)
        and len({row[1] for row in triples}) == len(triples)
        and len({row[2] for row in triples}) == len(triples)
        and set(required) <= {row[1] for row in triples}, "PLAN_MEMBERSHIP_INVALID")
    results = payload.section_execution_results
    sections = payload.sections
    _require_source(len(results) == len(sections) == len(draft.sections) == len(triples)
        and [(item.section_id, item.section_title, item.section_order) for item in sections] == triples
        and [item.section for item in results] == sections == draft.sections
        and payload.generated_draft.model_dump(mode="json") == draft.model_dump(mode="json")
        and not draft.truncation_detected, "SECTION_DRAFT_PROJECTION_MISMATCH")
    rows, states, exact_plan = [], set(), None
    placeholders = {"todo", "tbd", "待生成", "待补充", "正文待生成", "占位正文", "...", "…"}
    for result, section in zip(results, sections):
        source = result.canonical_section_source_binding
        lineage = result.active_execution_lineage
        checks = section.eval_result.checks if section.eval_result is not None else {}
        body = section.content.encode("utf-8")
        _require_source(section.status.value == "success" and result.error is None and section.error is None
            and bool(section.content.strip()) and section.content.strip().lower() not in placeholders
            and section.content.strip().lstrip("#").strip() != section.section_title.strip()
            and not section.truncation.truncated and not section.extra.get("generation_blocked")
            and not checks.get("normal_generation_blocked") and checks.get("not_truncated") is not False,
            "SECTION_NOT_PROVABLY_COMPLETE")
        _require_source(source is not None and lineage is not None and result.canonical_plan_binding is not None,
            "CANONICAL_SECTION_SOURCE_REQUIRED")
        _require_source(source.plan_binding == result.canonical_plan_binding
            and source.writer_execution_ref == result.writer_execution_ref
            and source.writer_invocation_ref == result.writer_generation_invocation_ref
            and source.paragraph_manifest == result.section_paragraph_manifest
            and source.coverage_proof_refs == tuple(result.canonical_claim_coverage_proof_refs)
            and source.verification_refs == tuple(result.canonical_claim_verification_refs)
            and source.producer_execution_status == "success"
            and (lineage.run_id, lineage.attempt_scope_ref, lineage.attempt_id, lineage.input_snapshot_version)
            == (payload.run_id, "section:" + section.section_id, source.producer_attempt_id, source.input_snapshot_version),
            "SECTION_SOURCE_PROJECTION_MISMATCH")
        key = source.writer_execution_ref.logical_execution_key
        _require_source((key.writer_subject_ref.run_id, key.writer_subject_ref.document_id,
            key.writer_subject_ref.section_id, key.write_instance_id)
            == (payload.run_id, payload.document_id, section.section_id, source.producer_attempt_id)
            and source.writer_invocation_ref.logical_execution_key == key
            and source.plan_binding.section_id == section.section_id
            and source.plan_binding.plan_version_ref.logical_ref.run_id == payload.run_id
            and source.plan_binding.plan_version_ref.logical_ref.domain_scope_ref == "document:" + payload.document_id
            and source.paragraph_manifest.generation_membership_state == "SEALED"
            and bool(source.paragraph_manifest.members), "SECTION_SOURCE_IDENTITY_MISMATCH")
        # This owner alone resolves Writer/Paragraph/Claim/Verification currentness.
        try:
            validated = section_dependencies.validate_source(source, require_accepted=False)
            _, canonical_plan, canonical_section = section_dependencies.plan(source.plan_binding)
            writer = section_dependencies.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
        except Exception as exc:
            raise WriterReviewCopyUnavailable("CANONICAL_SOURCE_OWNER_REJECTED") from exc
        _require_source(validated == source and (canonical_section.section_id, canonical_section.section_title,
            canonical_section.section_order) == (section.section_id, section.section_title, section.section_order)
            and [(item.section_id, item.section_title, item.section_order) for item in canonical_plan.sections] == triples
            and (exact_plan is None or exact_plan == source.plan_binding.plan_version_ref), "CURRENT_PLAN_MISMATCH")
        exact_plan = source.plan_binding.plan_version_ref
        _require_source(writer.status == "success" and writer.artifact is not None
            and writer.logical_execution_key == key
            and (writer.artifact.section_id, writer.artifact.section_title, writer.artifact.section_order)
            == (section.section_id, section.section_title, section.section_order)
            and writer.artifact.content.encode("utf-8") == body, "DURABLE_WRITER_BYTES_MISMATCH")
        states.add(source.workflow_status)
        rows.append({"section_id": section.section_id, "section_title": section.section_title,
            "section_order": section.section_order, "required": section.section_title in required,
            "section_sha256": hashlib.sha256(body).hexdigest(), "source_ref": source.source_ref,
            "source_binding_sha256": source.source_sha256, "source_binding_state": source.workflow_status,
            "plan_binding": source.plan_binding.model_dump(mode="json"),
            "producer_attempt_id": source.producer_attempt_id, "input_snapshot_version": source.input_snapshot_version,
            "writer_execution_ref": source.writer_execution_ref.model_dump(mode="json"),
            "writer_invocation_ref": source.writer_invocation_ref.model_dump(mode="json"),
            "paragraph_manifest_ref": source.paragraph_manifest.manifest_ref.model_dump(mode="json"),
            "paragraph_manifest_sha256": _sha(source.paragraph_manifest.model_dump(mode="json")),
            "coverage_proof_refs": [ref.model_dump(mode="json") for ref in source.coverage_proof_refs],
            "verification_refs": [ref.model_dump(mode="json") for ref in source.verification_refs]})
    # Preserve the existing DocumentAssembler rendering, including its exact newlines.
    assembled = "\n\n".join(f"{index}、{section.section_title}\n{section.content}"
        for index, section in enumerate(sections, start=1)).encode("utf-8")
    markdown = draft.full_text.encode("utf-8")
    _require_source(markdown == assembled and bool(markdown), "ASSEMBLED_FULL_TEXT_BYTES_MISMATCH")
    receipt = {"schema_version": "writer_review_copy_receipt_v1",
        "receipt_kind": "WRITER_REVIEW_COPY_NOT_FORMAL_RELEASE", "task_id": payload.task_id,
        "run_id": payload.run_id, "document_id": payload.document_id,
        "workflow_namespace": snapshot["workflow_namespace"], "checkpoint_id": snapshot["checkpoint_id"],
        "committed_checkpoint_source_sha256": snapshot["committed_checkpoint_source_sha256"],
        "business_revision": snapshot["business_revision"], "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
        "required_sections": rows, "required_section_titles": required, "source_binding_states": sorted(states),
        "source_review_status": "SOURCE_VERIFYING_NOT_ACCEPTED" if "VERIFYING" in states
            else "SOURCE_ACCEPTED_NOT_POSTWRITE_ASSESSED",
        "validation_scope": "SOURCE_AUTHENTICATED_NOT_POSTWRITE_VERIFIED",
        "post_write_quality": "POSTWRITE_NOT_ASSESSED", "human_approval": "NOT_ESTABLISHED",
        "formal_release_authorized": False}
    return {"markdown": markdown, "receipt": receipt}
# CHECKPOINT_SOURCE_END


# FILE_PROTOCOL_BEGIN
import os
from contextlib import contextmanager
from pathlib import Path
import stat
import uuid

from core.persistence.safe_paths import SafeExternalIdentifier, SafePathResolver


def _wc_digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise WriterReviewCopyUnavailable("invalid Writer Copy digest")
    return value


def _wc_validate_core(core):
    if not isinstance(core, dict):
        raise WriterReviewCopyUnavailable("invalid Writer Copy receipt")
    fixed = {"schema_version": "writer_review_copy_receipt_v1",
        "receipt_kind": "WRITER_REVIEW_COPY_NOT_FORMAL_RELEASE",
        "validation_scope": "SOURCE_AUTHENTICATED_NOT_POSTWRITE_VERIFIED",
        "post_write_quality": "POSTWRITE_NOT_ASSESSED", "human_approval": "NOT_ESTABLISHED"}
    if any(core.get(key) != value for key, value in fixed.items()) or core.get("formal_release_authorized") is not False:
        raise WriterReviewCopyUnavailable("invalid Writer Copy review restrictions")
    for key in ("task_id", "run_id", "document_id", "workflow_namespace", "checkpoint_id"):
        if not isinstance(core.get(key), str) or not core[key].strip():
            raise WriterReviewCopyUnavailable("missing Writer Copy identity")
    for key in ("task_id", "run_id"):
        SafeExternalIdentifier.validate(core[key], field_name=key)
    for key in ("committed_checkpoint_source_sha256", "markdown_sha256"):
        _wc_digest(core.get(key))
    if type(core.get("business_revision")) is not int or core["business_revision"] < 1:
        raise WriterReviewCopyUnavailable("invalid committed revision")
    rows, titles, states = core.get("required_sections"), core.get("required_section_titles"), core.get("source_binding_states")
    if not isinstance(rows, list) or not rows or not isinstance(titles, list) or not titles or any(
            not isinstance(title, str) or not title for title in titles) or len(set(titles)) != len(titles):
        raise WriterReviewCopyUnavailable("missing section receipt references")
    if not isinstance(states, list) or not states or any(s not in ("VERIFYING", "ACCEPTED") for s in states):
        raise WriterReviewCopyUnavailable("invalid source binding states")
    if states != sorted(set(states)) or any(not isinstance(row, dict) or type(row.get("required")) is not bool
            or row.get("source_binding_state") not in states for row in rows):
        raise WriterReviewCopyUnavailable("invalid section receipt states")
    if sorted({row["source_binding_state"] for row in rows}) != states:
        raise WriterReviewCopyUnavailable("inconsistent source binding states")
    required_titles = [row.get("section_title") for row in rows if row["required"]]
    if len(required_titles) != len(titles) or set(required_titles) != set(titles):
        raise WriterReviewCopyUnavailable("inconsistent required section references")
    expected = "SOURCE_VERIFYING_NOT_ACCEPTED" if "VERIFYING" in states else "SOURCE_ACCEPTED_NOT_POSTWRITE_ASSESSED"
    if core.get("source_review_status") != expected:
        raise WriterReviewCopyUnavailable("invalid source review restriction")


def _wc_receipt(core):
    _wc_validate_core(core)
    if "source_snapshot_sha256" in core or "receipt_sha256" in core:
        raise WriterReviewCopyUnavailable("receipt core already has seal hashes")
    result = {**core, "source_snapshot_sha256": _sha(core)}
    result["receipt_sha256"] = _sha(result)
    return result


def _wc_extended(path):
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _wc_kernel():
    if os.name != "nt":
        raise WriterReviewCopyUnavailable("Writer Copy requires verified Windows file primitives")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def _wc_open(path, *, directory):
    import ctypes
    flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT / BACKUP_SEMANTICS
    handle = _wc_kernel().CreateFileW(_wc_extended(path), 1 if directory else 0x80000000,
        1, None, 3, flags, None)  # SHARE_READ only; OPEN_EXISTING
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _wc_check_stat(info, *, directory):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_file_attributes & 0x400:
        raise WriterReviewCopyUnavailable("Writer Copy rejects reparse or nonregular paths")


def _wc_trusted_windows_sids():
    if os.name != "nt":
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: Windows is required")
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)
        sid = user[0] if isinstance(user, tuple) else user
        return {win32security.ConvertSidToStringSid(sid), "S-1-5-18", "S-1-5-32-544"}
    finally:
        token.Close()


def _wc_require_fixed_ntfs_root(path):
    """Exclude drive aliases before interpreting a physical volume root ACL."""
    import win32api
    import win32file
    root = str(path)
    volume = win32file.GetVolumeNameForVolumeMountPoint(root)
    device = win32file.QueryDosDevice(path.drive).rstrip("\0")
    if (win32file.GetDriveType(root) != 3 or win32api.GetVolumeInformation(root)[-1] != "NTFS"
            or volume.lower() != "\\\\?\\volume{" + str(uuid.UUID(volume[11:-2])) + "}\\"
            or device != win32file.QueryDosDevice(volume[4:-1]).rstrip("\0")
            or not device.startswith("\\Device\\HarddiskVolume")
            or not device[len("\\Device\\HarddiskVolume"):].isdigit()):
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: actual fixed NTFS volume root required")
    # Read-only root handle, all sharing: this is identity evidence, not a path lock.
    handle = win32file.CreateFile(_wc_extended(path), 0, 7, None, 3, 0x02200000, None)
    try:
        if win32file.GetFinalPathNameByHandle(handle, 1).lower() != volume.lower():
            raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: volume root identity mismatch")
    finally:
        handle.Close()


def _wc_require_trusted_directory(path, *, output_root, trusted_sids):
    """Conservative local-writer prerequisite, not an arbitrary-token AccessCheck."""
    import win32security
    before = os.lstat(_wc_extended(path))
    _wc_check_stat(before, directory=True)
    inside = path == output_root or output_root in path.parents
    volume_root = path == Path(path.anchor)
    if volume_root:
        _wc_require_fixed_ntfs_root(path)
    descriptor = win32security.GetNamedSecurityInfo(_wc_extended(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    owner = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
    allowed_owners = trusted_sids | ({
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    } if path == Path(path.anchor) else set())  # Volume-root TrustedInstaller owner.
    if owner not in allowed_owners:
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: untrusted directory owner")
    if path == output_root and not descriptor.GetSecurityDescriptorControl()[0] & 0x1000:
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: output root DACL is not protected")
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: absent or NULL DACL")
    # Write data/EA/attributes, delete child/object, write DACL/owner, generic write/all.
    dangerous = 0x00000156 | 0x00010000 | 0x00040000 | 0x00080000 | 0x40000000 | 0x10000000
    if not inside:
        dangerous &= ~0x4  # Creating a different child cannot replace this fixed ancestor.
        if volume_root:
            # The actual volume has no ordinary parent entry to rename. Its existing
            # trusted child cannot be cleared for a name-surrogate reparse without
            # DELETE_CHILD or child DELETE. Keep DC/DACL/owner/generic mutations denied.
            dangerous &= ~0x00010112  # Root self DELETE, add-file/data, EA, attributes.
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if len(ace) != 3 or ace[0][0] not in (0, 1):
            raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: unsupported directory ACE")
        (kind, flags), rights, sid = ace
        considered = inside or not flags & 0x08  # Inside: also reject future-child mutation grants.
        principal = win32security.ConvertSidToStringSid(sid)
        # OWNER RIGHTS denotes this already-validated object's owner, not a new writer.
        trusted = principal in trusted_sids or (principal == "S-1-3-4" and owner in allowed_owners)
        if kind == 0 and considered and rights & dangerous and not trusted:
            raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: nontrusted directory mutation grant")
    after = os.lstat(_wc_extended(path))
    _wc_check_stat(after, directory=True)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise WriterReviewCopyUnavailable("Writer Copy directory identity changed")


@contextmanager
def _wc_locked_paths(runs_dir, task_id, run_id, source_hash, *, create):
    """Use protected whole-Run ACLs; same/equivalent-identity malice is out of scope."""
    SafeExternalIdentifier.validate(task_id, field_name="task_id")
    SafeExternalIdentifier.validate(run_id, field_name="run_id")
    _wc_digest(source_hash)
    raw = Path(os.path.expanduser(os.fspath(runs_dir)))
    if (not raw.is_absolute() or len(raw.drive) != 2 or raw.drive[1] != ":"
            or ".." in raw.parts or str(raw).startswith("\\\\")):
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: ordinary local absolute path required")
    root = Path(os.path.abspath(raw))
    if root.name.lower() != "runs":
        raise WriterReviewCopyUnavailable("TRUSTED_ROOT_UNAVAILABLE: RunWorkspace runs directory required")
    output_root = root.parent  # Existing RunWorkspace derives runs_dir = output_root / 'runs'.
    target = root / "review_drafts" / task_id / run_id
    trusted_sids = _wc_trusted_windows_sids()
    # Validate the actual whole runtime root and its raw parents before any write.
    for directory in [*reversed(output_root.parents), output_root]:
        _wc_require_trusted_directory(directory, output_root=output_root, trusted_sids=trusted_sids)
    chain = [root, root / "review_drafts", root / "review_drafts" / task_id, target]
    for directory in chain:
        if create:
            try:
                os.mkdir(_wc_extended(directory))
            except FileExistsError:
                pass
        _wc_require_trusted_directory(directory, output_root=output_root, trusted_sids=trusted_sids)
    if SafePathResolver(root).resolve("review_drafts", task_id, run_id) != target:
        raise WriterReviewCopyUnavailable("Writer Copy directory identity changed")
    try:
        stem = "writer-review-" + source_hash
        yield target / (stem + ".md"), target / (stem + ".receipt.json")
    finally:
        for directory in [*reversed(output_root.parents), output_root, *chain]:
            _wc_require_trusted_directory(directory, output_root=output_root, trusted_sids=trusted_sids)


@contextmanager
def _wc_read_file(path):
    import msvcrt
    handle = _wc_open(path, directory=False)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _wc_kernel().CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        _wc_check_stat(os.fstat(stream.fileno()), directory=False)
        yield stream


def _wc_exists(path):
    try:
        os.lstat(_wc_extended(path))
        return True
    except FileNotFoundError:
        return False


def _wc_publish(path, content):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    created = False
    try:
        with open(_wc_extended(temporary), "xb") as stream:
            created = True
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Close the writer before publication so a concurrent winner is readable
        # through a SHARE_READ-only verification handle without any retries.
        try:
            os.link(_wc_extended(temporary), _wc_extended(path))
        except FileExistsError:
            with _wc_read_file(path) as existing:
                if existing.read() != content:
                    raise WriterReviewCopyUnavailable("Writer Copy target conflicts with sealed bytes")
    finally:
        if created:
            os.unlink(_wc_extended(temporary))  # Never remove or replace an existing target.


def _wc_verify_paths(markdown_path, receipt_path, *, task_id, run_id, source_hash, expected_receipt):
    # Both immutable read handles stay open through pair verification.
    with _wc_read_file(markdown_path) as markdown, _wc_read_file(receipt_path) as receipt_file:
        markdown_bytes, receipt_bytes = markdown.read(), receipt_file.read()
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        if not isinstance(receipt, dict) or _json_bytes(receipt) != receipt_bytes:
            raise WriterReviewCopyUnavailable("noncanonical Writer Copy receipt")
        core = {key: value for key, value in receipt.items() if key not in ("source_snapshot_sha256", "receipt_sha256")}
        sealed = _wc_receipt(core)
        if receipt != sealed or receipt["source_snapshot_sha256"] != source_hash:
            raise WriterReviewCopyUnavailable("Writer Copy receipt hash mismatch")
        if receipt["task_id"] != task_id or receipt["run_id"] != run_id:
            raise WriterReviewCopyUnavailable("Writer Copy Run identity mismatch")
        if hashlib.sha256(markdown_bytes).hexdigest() != receipt["markdown_sha256"]:
            raise WriterReviewCopyUnavailable("Writer Copy Markdown hash mismatch")
        if expected_receipt is not None:
            expected_core = {key: value for key, value in expected_receipt.items()
                if key not in ("source_snapshot_sha256", "receipt_sha256")}
            expected = _wc_receipt(expected_core)
            if any(key in expected_receipt and expected_receipt[key] != expected[key]
                    for key in ("source_snapshot_sha256", "receipt_sha256")) or receipt != expected:
                raise WriterReviewCopyUnavailable("Writer Copy differs from expected source")
    return {"markdown_path": str(markdown_path), "receipt_path": str(receipt_path), "receipt": receipt,
        "ready": True, "verification": "HISTORICAL_VERIFIED"}


def seal_local_writer_copy(*, runs_dir, source: dict) -> dict:
    """Publish exact local Markdown then a non-authoritative receipt, without replacement."""
    markdown = source.get("markdown")
    if not isinstance(markdown, bytes) or not markdown:
        raise WriterReviewCopyUnavailable("missing exact Writer Markdown bytes")
    markdown.decode("utf-8")
    receipt = _wc_receipt(source.get("receipt"))
    if hashlib.sha256(markdown).hexdigest() != receipt["markdown_sha256"]:
        raise WriterReviewCopyUnavailable("Writer Markdown differs from source receipt")
    task_id, run_id, source_hash = receipt["task_id"], receipt["run_id"], receipt["source_snapshot_sha256"]
    with _wc_locked_paths(runs_dir, task_id, run_id, source_hash, create=True) as (md, record):
        if _wc_exists(record):
            if not _wc_exists(md):
                raise WriterReviewCopyUnavailable("receipt exists without Writer Markdown")
        else:
            _wc_publish(md, markdown)
            # No receipt appears until the exact Markdown was read back successfully.
            with _wc_read_file(md) as stream:
                if stream.read() != markdown:
                    raise WriterReviewCopyUnavailable("published Writer Markdown changed")
            _wc_publish(record, _json_bytes(receipt))
        return _wc_verify_paths(md, record, task_id=task_id, run_id=run_id,
            source_hash=source_hash, expected_receipt=receipt)


def verify_sealed_pair(*, runs_dir, task_id, run_id, source_snapshot_sha256, expected_receipt=None) -> dict:
    """Verify the earlier seal without consulting a live Run or Source Owner."""
    with _wc_locked_paths(runs_dir, task_id, run_id, source_snapshot_sha256, create=False) as (md, record):
        return _wc_verify_paths(md, record, task_id=task_id, run_id=run_id,
            source_hash=source_snapshot_sha256, expected_receipt=expected_receipt)
# FILE_PROTOCOL_END
