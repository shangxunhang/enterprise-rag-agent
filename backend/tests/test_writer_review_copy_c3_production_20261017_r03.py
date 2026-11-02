"""Formal C3 Scheme-A tests: the production guard is never replaced.

Fixtures contain file-only source references, not accepted Writer provenance.
Distinct nonadministrator-token attacks are an explicitly accepted NOT_RUN gap.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import threading
import traceback

import pytest
import pywintypes
import win32api
import win32con
import win32file
import win32security as security

from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc


def protected_directory(path: Path) -> Path:
    """Create one new directory with protected DACL atomically; never retune one."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or os.path.lexists(path):
        raise ValueError("protected_directory requires a new absolute local path")
    trusted = wc._wc_trusted_windows_sids()
    for parent in reversed(path.parents):
        wc._wc_require_trusted_directory(parent, output_root=path, trusted_sids=trusted)
    current = next(sid for sid in trusted if sid not in ("S-1-5-18", "S-1-5-32-544"))
    sddl = f"O:{current}G:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{current})"
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    attributes.bInheritHandle = False
    win32file.CreateDirectory(wc._wc_extended(path), attributes)
    wc._wc_require_trusted_directory(path, output_root=path, trusted_sids=trusted)
    return path


def _source(*, run="native-files", content=None):
    body = content if content is not None else "第一章\r\n完整原始正文\n\n附录\n非必需计划章节".encode("utf-8")
    core = {"schema_version": "writer_review_copy_receipt_v1",
        "receipt_kind": "WRITER_REVIEW_COPY_NOT_FORMAL_RELEASE", "task_id": "task-native-files", "run_id": run,
        "document_id": "document-native-files", "workflow_namespace": "file-fixture-only:v1",
        "checkpoint_id": "file-fixture-not-a-real-committed-checkpoint", "committed_checkpoint_source_sha256": "1" * 64,
        "business_revision": 10, "markdown_sha256": hashlib.sha256(body).hexdigest(),
        "required_sections": [{"section_id": "one", "section_title": "第一章", "required": True,
            "source_binding_state": "VERIFYING", "writer_execution_ref": "fixture://writer-one"},
            {"section_id": "extra", "section_title": "附录", "required": False,
            "source_binding_state": "VERIFYING", "writer_execution_ref": "fixture://writer-extra"}],
        "required_section_titles": ["第一章"], "source_binding_states": ["VERIFYING"],
        "source_review_status": "SOURCE_VERIFYING_NOT_ACCEPTED", "validation_scope": "SOURCE_AUTHENTICATED_NOT_POSTWRITE_VERIFIED",
        "post_write_quality": "POSTWRITE_NOT_ASSESSED", "human_approval": "NOT_ESTABLISHED", "formal_release_authorized": False}
    return {"markdown": body, "receipt": core}


def _verify(runs, sealed, **overrides):
    receipt = sealed["receipt"]
    return wc.verify_sealed_pair(runs_dir=runs, **{"task_id": receipt["task_id"], "run_id": receipt["run_id"],
        "source_snapshot_sha256": receipt["source_snapshot_sha256"], **overrides})


def _evidence(name, value):
    folder = os.environ.get("WC_C3_EVIDENCE_DIR")
    if folder:
        Path(folder, name + ".json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _acl(path):
    info = security.DACL_SECURITY_INFORMATION | security.OWNER_SECURITY_INFORMATION
    descriptor = security.GetFileSecurity(str(path), info)
    return descriptor, {"owner": security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()),
        "control": descriptor.GetSecurityDescriptorControl(), "sddl": security.ConvertSecurityDescriptorToStringSecurityDescriptor(descriptor, 1, info)}


@contextmanager
def _changed_dacl(path, acl, evidence_name):
    original, before = _acl(path)
    result = {"path": str(path), "before": before}; _evidence(evidence_name, result)
    protected = bool(original.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
    try:
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        result["changed"] = _acl(path)[1]; _evidence(evidence_name, result)
        yield result
    finally:
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION |
            (security.PROTECTED_DACL_SECURITY_INFORMATION if protected else security.UNPROTECTED_DACL_SECURITY_INFORMATION),
            None, None, original.GetSecurityDescriptorDacl(), None)
        result["restored"] = _acl(path)[1]; _evidence(evidence_name, result)


def _allowed_copy(original):
    result = security.ACL()
    for index in range(original.GetAceCount()):
        (kind, flags), mask, sid = original.GetAce(index)
        assert kind == security.ACCESS_ALLOWED_ACE_TYPE
        result.AddAccessAllowedAceEx(security.ACL_REVISION, flags, mask, sid)
    return result


def _native_child(request, event, pipe, log):
    with open(log, "x", encoding="utf-8") as stream:
        try:
            pipe.send({"event": "ready", "pid": os.getpid()})
            assert event.wait(60), "single Native start barrier timed out"
            if request["operation"] == "publish":
                wc._wc_publish(Path(request["path"]), request["body"])
                result = {"sha256": hashlib.sha256(request["body"]).hexdigest()}
            else:
                if request["operation"] == "crash":
                    original = wc.os.fsync
                    def crash(descriptor):
                        original(descriptor); os._exit(73)
                    wc.os.fsync = crash
                result = wc.seal_local_writer_copy(runs_dir=request["runs"], source=request["source"])
            pipe.send({"status": "SUCCEEDED", "result": result})
        except BaseException as exc:
            trace = traceback.format_exc(); stream.write(trace); stream.flush()
            pipe.send({"status": "REJECTED", "exception_type": type(exc).__name__,
                "winerror": getattr(exc, "winerror", None), "traceback": trace})
        finally:
            pipe.close()


def _processes(requests, logs, label):
    context = multiprocessing.get_context("spawn"); event = context.Event(); children = []
    for index, request in enumerate(requests):
        parent, child = context.Pipe()
        log = logs / f"{label}-{index}.stderr.log"
        process = context.Process(target=_native_child, args=(request, event, child, str(log)))
        process.start(); child.close(); children.append((process, parent, log))
    for process, pipe, log in children:
        assert pipe.poll(60), "child readiness missing"
        assert pipe.recv()["event"] == "ready"
    event.set()
    rows = []
    for process, pipe, log in children:
        process.join(60)
        if process.is_alive():
            process.terminate(); process.join(); raise TimeoutError("bounded Native child timeout")
        try:
            message = pipe.recv() if pipe.poll() else {"status": "EXITED_WITHOUT_RESULT"}
        except (EOFError, BrokenPipeError):
            message = {"status": "EXITED_WITHOUT_RESULT"}
        rows.append({"pid": process.pid, "exit_code": process.exitcode, "message": message, "stderr_path": str(log)})
        pipe.close()
    _evidence(label, rows)
    return rows


def test_c3_1_production_exact_seal_and_no_replace(tmp_path):
    output = protected_directory(tmp_path / "runtime"); runs = output / "runs"; source = _source()
    sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    md, record = Path(sealed["markdown_path"]), Path(sealed["receipt_path"])
    before = (md.read_bytes(), record.read_bytes(), md.stat().st_ino, record.stat().st_ino)
    assert before[0] == source["markdown"]
    assert _verify(runs, sealed, expected_receipt=source["receipt"])["ready"] is True
    assert wc.seal_local_writer_copy(runs_dir=runs, source=source) == sealed
    assert (md.read_bytes(), record.read_bytes(), md.stat().st_ino, record.stat().st_ino) == before
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc._wc_publish(md, b"different bytes")
    assert md.read_bytes() == before[0]
    _evidence("C3_1_SEALED_PAIR", {"output_root": str(output), "sealed": sealed, "exact_bytes": True,
        "direct_production_guard": True, "source_fixture_is_not_writer_acceptance": True})


def test_c3_2_production_four_processes_and_edits(tmp_path):
    output = protected_directory(tmp_path / "runtime"); runs = output / "runs"
    logs = output / "child-logs"; logs.mkdir()
    source = _source(run="same-source")
    rows = _processes([{"operation": "seal", "runs": str(runs), "source": source}] * 4, logs, "C3_2_SAME")
    assert all(row["message"]["status"] == "SUCCEEDED" for row in rows), rows
    assert len({row["message"]["result"]["receipt"]["source_snapshot_sha256"] for row in rows}) == 1
    rows = _processes([{"operation": "seal", "runs": str(runs), "source": _source(content=f"distinct-{i}".encode())}
        for i in range(4)], logs, "C3_2_DISTINCT")
    assert all(row["message"]["status"] == "SUCCEEDED" for row in rows), rows
    assert len({row["message"]["result"]["markdown_path"] for row in rows}) == 4
    target = output / "collision.md"
    rows = _processes([{"operation": "publish", "path": str(target), "body": f"conflict-{i}".encode()}
        for i in range(4)], logs, "C3_2_COLLISION")
    assert sum(row["message"]["status"] == "SUCCEEDED" for row in rows) == 1
    assert sum(row["message"].get("exception_type") == "WriterReviewCopyUnavailable" for row in rows) == 3
    source = _source(run="edited"); sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    md = Path(sealed["markdown_path"]); edited = md.read_bytes() + b"human edit"; md.write_bytes(edited)
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    assert md.read_bytes() == edited


def test_c3_3_production_acl_owner_and_raw_paths(tmp_path, monkeypatch):
    output = protected_directory(tmp_path / "runtime"); runs = output / "runs"; source = _source()
    for bad in ("../escape", "%252e%252e", "run:stream", "CON"):
        value = copy.deepcopy(source); value["receipt"]["run_id"] = bad
        with pytest.raises(ValueError): wc.seal_local_writer_copy(runs_dir=runs, source=value)
    for bad in (str(runs / ".." / "runs"), r"\\server\share\runs", "relative/runs", output / "wrong-name"):
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=bad, source=source)
    weak_output = tmp_path / "unprotected"; weak_output.mkdir()
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=weak_output / "runs", source=source)
    outside = output / "outside"; outside.mkdir(); runs.mkdir()
    junction = runs / "review_drafts"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    assert made.returncode == 0, made.stderr
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    assert list(outside.iterdir()) == []
    junction.rmdir()  # Remove only this new fixture's junction object.
    with wc._wc_locked_paths(runs, "task-native-files", "native-files", wc._sha(source["receipt"]), create=True) as (md, _):
        original = outside / "original.md"; original.write_bytes(source["markdown"]); os.symlink(original, md)
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    assert original.read_bytes() == source["markdown"]
    weak_acl = _allowed_copy(_acl(output)[0].GetSecurityDescriptorDacl())
    weak_acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3, 0x00130156, security.ConvertStringSidToSid("S-1-5-11"))
    with _changed_dacl(output, weak_acl, "C3_3_WEAK_ACL"):
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    with _changed_dacl(output, None, "C3_3_NULL_DACL"):
        with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    # Negative fault injection covers an unknown owner without changing Windows
    # users/privileges or replacing the production protection function.
    class UnknownOwner:
        def GetSecurityDescriptorOwner(self): return security.ConvertStringSidToSid("S-1-5-11")
        def GetSecurityDescriptorControl(self): return (0x1000, 1)
        def GetSecurityDescriptorDacl(self):
            acl = security.ACL()
            acl.AddAccessAllowedAce(security.ACL_REVISION, 0x001F01FF, security.ConvertStringSidToSid("S-1-3-4"))
            return acl
    with monkeypatch.context() as fault:
        fault.setattr(security, "GetNamedSecurityInfo", lambda *args: UnknownOwner())
        with pytest.raises(wc.WriterReviewCopyUnavailable, match="owner"):
            wc._wc_require_trusted_directory(output, output_root=output, trusted_sids=wc._wc_trusted_windows_sids())
    class UnsupportedAcl:
        def GetAceCount(self): return 1
        def GetAce(self, index): return ((5, 0), 0, None, security.ConvertStringSidToSid("S-1-5-18"))
    class UnsupportedDescriptor(UnknownOwner):
        def GetSecurityDescriptorOwner(self): return security.ConvertStringSidToSid("S-1-5-18")
        def GetSecurityDescriptorDacl(self): return UnsupportedAcl()
    with monkeypatch.context() as fault:
        fault.setattr(security, "GetNamedSecurityInfo", lambda *args: UnsupportedDescriptor())
        with pytest.raises(wc.WriterReviewCopyUnavailable, match="unsupported"):
            wc._wc_require_trusted_directory(output, output_root=output, trusted_sids=wc._wc_trusted_windows_sids())
    _evidence("C3_3_SCOPE", {"different_nonadministrator_token_attack": "NOT_RUN_ACCEPTED_GAP",
        "same_equivalent_or_admin_malicious_writer": "OUT_OF_SCOPE", "owner_unknown_negative": "DETERMINISTIC_API_FAULT_ONLY"})


def test_c3_4_production_incomplete_crash_and_permissions(tmp_path):
    output = protected_directory(tmp_path / "runtime"); runs = output / "runs"
    source = _source(run="md-only"); sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    md, record = Path(sealed["markdown_path"]), Path(sealed["receipt_path"])
    inode = md.stat().st_ino; record.unlink()
    with pytest.raises(FileNotFoundError): _verify(runs, sealed)
    assert wc.seal_local_writer_copy(runs_dir=runs, source=source) == sealed and md.stat().st_ino == inode
    source = _source(run="receipt-only"); sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    Path(sealed["markdown_path"]).unlink()
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs, source=source)
    source = _source(run="hash-mismatch"); sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    Path(sealed["markdown_path"]).write_bytes(b"edited")
    with pytest.raises(wc.WriterReviewCopyUnavailable): _verify(runs, sealed)
    source = _source(run="permission"); sealed = wc.seal_local_writer_copy(runs_dir=runs, source=source)
    wrong = copy.deepcopy(source["receipt"]); wrong["checkpoint_id"] = "wrong-source-occurrence"
    with pytest.raises(wc.WriterReviewCopyUnavailable): _verify(runs, sealed, expected_receipt=wrong)
    with pytest.raises(wc.WriterReviewCopyUnavailable): wc.seal_local_writer_copy(runs_dir=runs,
        source={**source, "markdown": b"bytes do not match source"})
    path = Path(sealed["markdown_path"]); original, _ = _acl(path)
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = security.GetTokenInformation(token, security.TokenUser)[0]; token.Close()
    denied = security.ACL(); denied.AddAccessDeniedAce(security.ACL_REVISION, 1, sid)
    copied = original.GetSecurityDescriptorDacl()
    for index in range(copied.GetAceCount()):
        (kind, flags), mask, ace_sid = copied.GetAce(index)
        assert kind == 0
        denied.AddAccessAllowedAceEx(security.ACL_REVISION, flags, mask, ace_sid)
    with _changed_dacl(path, denied, "C3_4_FILE_DENIED") as metadata:
        with pytest.raises(OSError) as rejected: _verify(runs, sealed)
        assert rejected.value.winerror == 5
        metadata["actual_winerror"] = rejected.value.winerror
    assert _verify(runs, sealed)["ready"] is True
    logs = output / "child-logs"; logs.mkdir()
    crashed = _processes([{"operation": "crash", "runs": str(runs), "source": _source(run="crashed")}], logs, "C3_4_CRASH")
    assert crashed[0]["exit_code"] == 73
    directory = runs / "review_drafts" / "task-native-files" / "crashed"
    assert list(directory.glob("*.md")) == [] and list(directory.glob("*.receipt.json")) == []
    assert list(directory.glob("*.tmp"))
    _evidence("C3_4_CRASH_SCOPE", {"abnormal_exit_after_real_fsync_before_link": True,
        "ready": False, "power_loss_guarantee": "NOT_CLAIMED", "temporary_files": [str(p) for p in directory.glob("*.tmp")]})


@pytest.mark.skip(reason="Approved Scheme-A gap: no authorized distinct nonadministrator token; no account creation.")
def test_c3_different_nonadministrator_dynamic_attack_not_run():
    pass
