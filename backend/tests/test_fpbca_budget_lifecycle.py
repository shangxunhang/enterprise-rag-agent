# PREPARATION AMENDMENT02: actual native Run/Attempt status wire values are lowercase; original01 preserved.
"""Budget wait/resume lifecycle owner controls; preparation only.

Wait packet/grant strings here are test-owned immutable bindings. These owner
controls do not certify Gateway no-effect, accepted Budget grants, actual model
calls, Task/checkpoint or graph continuation. Integration campaign supplies them.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    AttemptLifecycleStatus, RunIdentitySchema, RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError, LifecycleIntegrityError, LifecycleRevisionConflict,
    RunAttemptLifecycleService,
)
from core.hashing import canonical_sha256


class _Clock:
    def now_iso(self):
        return "2026-10-08T00:00:00+00:00"


def _service(path):
    return RunAttemptLifecycleService(path, clock=_Clock())


def _fresh(tmp_path, count=8):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = service.claim_new_run(RunIdentitySchema(
        run_id="run-budget-pause", task_id="task-budget-pause", tenant_id="tenant-budget-pause",
        canonical_request_sha256=canonical_sha256("test-owned-budget-pause-request"),
    ))
    service.transition_run(run.identity.run_id, 0, RunLifecycleStatus.RUNNING)
    for index in range(count):
        row = service.open_attempt(run.identity.run_id, f"section:{index}", "snapshot-budget-pause",
                                   attempt_id=f"child-budget-pause-{index}")
        if index != 1:
            service.activate_attempt(row.identity.attempt_id, 0)
    return service, service.budget_preimage(run.identity.run_id)


def _pause(service, preimage, **changes):
    arguments = {
        "expected_current_children": preimage.current_children,
        "operation_key": "pause-test-owned-budget-binding",
        "wait_ref": "artifact://test-owned-budget-pause/wait",
        "wait_sha256": canonical_sha256("test-owned-wait-packet"),
        "semantic_operation_ref": "citation-association-call://sha256/" + canonical_sha256("citation-material"),
        "semantic_input_sha256": canonical_sha256("citation-material"),
        "artifact_version": "artifact-version-test-owned",
    }
    arguments.update(changes)
    return service.pause_for_budget(preimage.run.identity.run_id, preimage.run.lifecycle_revision, **arguments)


def _resume(service, wait, **changes):
    arguments = {
        "expected_current_children": wait.current_children,
        "operation_key": "resume-test-owned-budget-binding",
        "wait_event_ref": wait.event_ref, "wait_event_sha256": wait.event_sha256,
        "grant_ref": "budget_grant:" + canonical_sha256("test-owned-consumption") + ":" + canonical_sha256("test-owned-grant-fingerprint"),
        "grant_sha256": canonical_sha256("test-owned-grant"),
    }
    arguments.update(changes)
    return service.resume_budget_wait(wait.resulting_run.identity.run_id,
                                     wait.resulting_run.lifecycle_revision, **arguments)


def _fence(service, wait, **changes):
    arguments = {
        "task_id": wait.resulting_run.identity.task_id, "run_id": wait.resulting_run.identity.run_id,
        "expected_lifecycle_revision": wait.resulting_run.lifecycle_revision,
        "wait_event_ref": wait.event_ref, "wait_event_sha256": wait.event_sha256,
        "child_preimage_sha256": wait.child_preimage_sha256,
        "semantic_operation_ref": wait.semantic_operation_ref,
        "semantic_input_sha256": wait.semantic_input_sha256,
        "artifact_version": wait.artifact_version,
    }
    arguments.update(changes)
    return service.budget_authorization_fence(**arguments)


def _frames(service):
    return [json.loads(line) for line in service.path.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("schema_version") == "run_attempt_lifecycle_budget_event_v1"]


@pytest.mark.parametrize("count", [0, 1, 8])
def test_bt_pause_resume_preserves_full_children_and_nonterminal_run(tmp_path, count):
    service, preimage = _fresh(tmp_path, count)
    prefix = service.path.read_bytes()
    wait = _pause(service, preimage)
    assert service.path.read_bytes().startswith(prefix)
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
    assert wait.kind == "PAUSE"
    assert wait.resulting_run.status is RunLifecycleStatus.PAUSED
    assert wait.resulting_run.finished_at is None
    assert wait.resulting_run.terminal_reason_ref == preimage.run.terminal_reason_ref
    assert wait.current_children == preimage.current_children
    assert wait.child_preimage_sha256 == canonical_sha256([
        row.model_dump(mode="json", round_trip=True) for row in preimage.current_children])
    for row in preimage.current_children:
        assert service.load_attempt(row.identity.attempt_id) == row
    with pytest.raises(LifecycleCurrentnessError):
        service.assert_run_committable(preimage.run.identity.run_id)
    with pytest.raises(LifecycleCurrentnessError):
        service.open_attempt(preimage.run.identity.run_id, "section:paused-new", "paused-new")
    cold = _service(service.path)
    assert cold.get_budget_transition(wait.event_ref, wait.event_sha256) == wait
    resumed = _resume(cold, wait)
    assert resumed.kind == "RESUME"
    assert resumed.resume_occurrence_id == "budget-resume-occurrence://sha256/" + resumed.operation_key
    assert resumed.resulting_run.status is RunLifecycleStatus.RUNNING
    assert resumed.resulting_run.finished_at is None
    assert resumed.current_children == wait.current_children
    assert resumed.wait_event_ref == wait.event_ref
    assert cold.assert_run_committable(preimage.run.identity.run_id) == resumed.resulting_run
    for row in preimage.current_children:
        assert cold.load_attempt(row.identity.attempt_id) == row


@pytest.mark.parametrize("change", ["open", "activate", "retire", "supersede"])
def test_bt11_full_child_set_change_with_unchanged_run_revision_rejects(tmp_path, change):
    service, preimage = _fresh(tmp_path, 3)
    if change == "open":
        service.open_attempt(preimage.run.identity.run_id, "section:new", "new-input", attempt_id="new-child")
    elif change == "activate":
        service.activate_attempt("child-budget-pause-1", 0)
    elif change == "retire":
        service.transition_attempt("child-budget-pause-0", 1, AttemptLifecycleStatus.COMPLETED)
    else:
        service.open_attempt(preimage.run.identity.run_id, "section:0", "new-input", attempt_id="replacement-child")
    assert service.load_run(preimage.run.identity.run_id).lifecycle_revision == preimage.run.lifecycle_revision
    prefix = service.path.read_bytes()
    with pytest.raises(LifecycleRevisionConflict):
        _pause(service, preimage)
    assert service.path.read_bytes() == prefix


@pytest.mark.parametrize("change", ["identity", "status", "revision", "snapshot", "supersedes", "failure_ref"])
def test_bt_full_record_preimage_cannot_be_forged(tmp_path, change):
    service, preimage = _fresh(tmp_path, 1)
    rows = [row.model_dump(mode="json", round_trip=True) for row in preimage.current_children]
    if change == "identity":
        rows[0]["identity"]["attempt_id"] = "other-child"
    elif change == "snapshot":
        rows[0]["identity"]["input_snapshot_version"] = "other-snapshot"
    elif change == "status":
        rows[0]["status"] = "pending"
    elif change == "revision":
        rows[0]["lifecycle_revision"] += 1
    elif change == "supersedes":
        rows[0]["supersedes_attempt_id"] = "other-prior"
    else:
        rows[0]["failure_ref"] = "other-durable-failure"
    prefix = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _pause(service, preimage, expected_current_children=rows)
    assert service.path.read_bytes() == prefix


def test_bt_native_pause_resume_exact_replay_and_alias_conflict(tmp_path):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage)
    prefix = service.path.read_bytes()
    assert _pause(_service(service.path), preimage) == wait
    with pytest.raises(LifecycleRevisionConflict):
        _pause(service, preimage, wait_ref="artifact://changed/wait")
    assert service.path.read_bytes() == prefix
    resumed = _resume(service, wait)
    prefix = service.path.read_bytes()
    assert _resume(_service(service.path), wait) == resumed
    with pytest.raises(LifecycleIntegrityError):
        _resume(service, wait, grant_sha256=canonical_sha256("changed-grant"))
    with pytest.raises(LifecycleIntegrityError):
        _resume(service, wait, operation_key="another-client-resume-key")
    assert service.path.read_bytes() == prefix
    assert len(_frames(service)) == 2


@pytest.mark.parametrize("change", ["task_id", "run_id", "expected_lifecycle_revision", "wait_event_sha256",
                                  "child_preimage_sha256", "semantic_operation_ref", "semantic_input_sha256",
                                  "artifact_version"])
def test_bt_authorization_fence_rejects_wrong_exact_binding(tmp_path, change):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage)
    value = (wait.resulting_run.lifecycle_revision + 1 if change == "expected_lifecycle_revision"
             else canonical_sha256("changed-binding") if change.endswith("sha256")
             else "changed-binding")
    prefix = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        with _fence(service, wait, **{change: value}):
            pytest.fail("wrong authority must not enter held fence")
    assert service.path.read_bytes() == prefix


def test_bt10_held_fence_serializes_explicit_cancel_then_revokes_resume(tmp_path):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage)
    started, finished = threading.Event(), threading.Event()
    failures = []

    def cancel():
        started.set()
        try:
            _service(service.path).transition_run(wait.resulting_run.identity.run_id,
                wait.resulting_run.lifecycle_revision, RunLifecycleStatus.CANCELLED,
                reason_ref="explicit-user-cancel-test")
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    with _fence(service, wait) as held:
        assert held == wait
        worker = threading.Thread(target=cancel)
        worker.start()
        assert started.wait(5)
        assert not finished.wait(0.03)
        # Native currentness remains PAUSED while the actual owner fence is held.
        assert json.loads(service.path.read_bytes().splitlines()[-1])["resulting_run"]["status"] == "paused"
    worker.join(10)
    assert not worker.is_alive() and not failures
    assert service.load_run(preimage.run.identity.run_id).status is RunLifecycleStatus.CANCELLED
    prefix = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _resume(service, wait)
    with pytest.raises(LifecycleIntegrityError):
        with _fence(service, wait):
            pytest.fail("cancelled lineage must not regain approval fence")
    assert service.path.read_bytes() == prefix
    for row in preimage.current_children:
        assert service.load_attempt(row.identity.attempt_id) == row


@pytest.mark.parametrize("kind", ["pause", "resume"])
@pytest.mark.parametrize("cut", ["before", "after"])
def test_bt14_native_append_return_loss_has_zero_or_one_transition(tmp_path, monkeypatch, kind, cut):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage) if kind == "resume" else None
    prefix = service.path.read_bytes()
    original = service.log.append_serialized

    def interrupted(serialized):
        if cut == "after":
            original(serialized)
        raise OSError("local injected native append return loss")

    monkeypatch.setattr(service.log, "append_serialized", interrupted)
    with pytest.raises(OSError):
        _resume(service, wait) if wait is not None else _pause(service, preimage)
    cold = _service(service.path)
    appended = service.path.read_bytes()[len(prefix):]
    assert appended.count(b"\n") == (1 if cut == "after" else 0)
    result = _resume(cold, wait) if wait is not None else _pause(cold, preimage)
    assert result.kind == kind.upper()
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1


@pytest.mark.parametrize("tail", [b'{"schema_version":"run_attempt_lifecycle_budget_event_v1"', b"\xe4\xb8"])
def test_bt14_fresh_native_torn_tail_preserves_committed_prefix(tmp_path, tail):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage)
    prefix = service.path.read_bytes()
    with service.path.open("ab") as handle:
        handle.write(tail)
    cold = _service(service.path)
    assert cold.get_budget_transition(wait.event_ref, wait.event_sha256) == wait
    assert service.path.read_bytes() == prefix


@pytest.mark.parametrize("attack", ["hash", "unknown_field", "unknown_request_field", "child_record", "run_result", "occurrence"])
def test_bt_native_committed_budget_frame_tampering_fails_closed(tmp_path, attack):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage)
    _resume(service, wait)
    lines = service.path.read_text(encoding="utf-8").splitlines()
    frame = json.loads(lines[-1])
    if attack == "hash":
        frame["event_sha256"] = canonical_sha256("wrong-frame")
    elif attack == "unknown_field":
        frame["unknown_authority"] = "forged"
    elif attack == "unknown_request_field":
        frame["request"]["unknown_authority"] = "forged"
    elif attack == "child_record":
        frame["before_current_children"][0]["identity"]["input_snapshot_version"] = "forged"
    elif attack == "run_result":
        frame["resulting_run"]["terminal_reason_ref"] = "budget-must-not-mint-terminal-reason"
    else:
        frame["resume_occurrence_id"] = "forged-occurrence"
    # This is a fresh negative fixture, never an edit of protected history.
    negative = tmp_path / "corrupt" / "lifecycle.jsonl"
    negative.parent.mkdir()
    negative.write_text("\n".join([*lines[:-1], json.dumps(frame, ensure_ascii=False)]) + "\n", encoding="utf-8")
    before = negative.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _service(negative).load_run(preimage.run.identity.run_id)
    assert negative.read_bytes() == before


@pytest.mark.parametrize("kind", ["pause", "resume"])
def test_bt08_two_threads_exact_occurrence_replay_is_one_frame(tmp_path, kind):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage) if kind == "resume" else None
    prefix = service.path.read_bytes()
    barrier = threading.Barrier(2)

    def execute():
        barrier.wait(10)
        cold = _service(service.path)
        return _resume(cold, wait) if wait is not None else _pause(cold, preimage)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result(timeout=15) for future in [pool.submit(execute), pool.submit(execute)]]
    assert first == second
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1


_PROCESS_DRIVER = r'''import json, os, sys, time
from pathlib import Path
root, journal, mode, ready, release, output = sys.argv[1:]
sys.path.insert(0, str(Path(root) / "backend"))
import apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime as owner
from core.hashing import canonical_sha256
class Clock:
    def now_iso(self): return "2026-10-08T00:00:00+00:00"
service = owner.RunAttemptLifecycleService(journal, clock=Clock())
preimage = service.budget_preimage("run-budget-pause")
frames = [json.loads(line) for line in Path(journal).read_text(encoding="utf-8").splitlines()
          if json.loads(line).get("schema_version") == "run_attempt_lifecycle_budget_event_v1"]
wait = service.get_budget_transition(service._budget_frame_ref(frames[0])) if frames else None
original = service.log.append_serialized
if mode.endswith("_PRE") or mode.endswith("_POST"):
    def interrupted(serialized):
        if mode.endswith("_POST"): original(serialized)
        os._exit(82 if mode.endswith("_POST") else 81)
    service.log.append_serialized = interrupted
Path(ready).write_text("READY", encoding="utf-8")
deadline = time.monotonic() + 15
while not Path(release).is_file():
    if time.monotonic() >= deadline: raise RuntimeError("TEST_TIME_BUDGET_EXHAUSTED: no production mutation")
    time.sleep(0.01)
if mode.startswith("PAUSE"):
    result = service.pause_for_budget("run-budget-pause", 1,
        expected_current_children=preimage.current_children,
        operation_key="pause-test-owned-budget-binding",
        wait_ref="artifact://test-owned-budget-pause/wait", wait_sha256=canonical_sha256("test-owned-wait-packet"),
        semantic_operation_ref="citation-association-call://sha256/" + canonical_sha256("citation-material"),
        semantic_input_sha256=canonical_sha256("citation-material"), artifact_version="artifact-version-test-owned")
else:
    result = service.resume_budget_wait("run-budget-pause", 2,
        expected_current_children=wait.current_children,
        operation_key="resume-test-owned-budget-binding",
        wait_event_ref=wait.event_ref, wait_event_sha256=wait.event_sha256,
        grant_ref="budget_grant:" + canonical_sha256("test-owned-consumption") + ":" + canonical_sha256("test-owned-grant-fingerprint"),
        grant_sha256=canonical_sha256("test-owned-grant"))
Path(output).write_text(json.dumps({"event_ref":result.event_ref,"event_sha256":result.event_sha256,
    "occurrence":result.resume_occurrence_id,"owner_file":owner.__file__,"pid":os.getpid()}, sort_keys=True), encoding="utf-8")
'''


def _processes(tmp_path, service, modes):
    root = Path(__file__).resolve().parents[2]
    driver = tmp_path / "native-lifecycle-driver.py"
    driver.write_text(_PROCESS_DRIVER, encoding="utf-8")
    release = tmp_path / "release"
    children = []
    for index, mode in enumerate(modes):
        ready, output = tmp_path / f"ready-{index}", tmp_path / f"output-{index}.json"
        process = subprocess.Popen([sys.executable, "-I", "-B", str(driver), str(root),
            str(service.path), mode, str(ready), str(release), str(output)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append((process, ready, output))
    deadline = time.monotonic() + 15
    while not all(ready.is_file() for _, ready, _ in children):
        if time.monotonic() > deadline:
            for process, _, _ in children:
                process.kill()
            pytest.fail("TEST_TIME_BUDGET_EXHAUSTED; child closure failure is not production no-effect")
        time.sleep(0.01)
    release.write_text("GO", encoding="utf-8")
    outputs = []
    for process, _, output in children:
        stdout, stderr = process.communicate(timeout=20)
        outputs.append((process.returncode, stdout, stderr,
                        json.loads(output.read_text(encoding="utf-8")) if output.is_file() else None))
    return outputs


@pytest.mark.parametrize("kind", ["PAUSE", "RESUME"])
def test_bt08_two_native_processes_one_lifecycle_occurrence(tmp_path, kind):
    service, preimage = _fresh(tmp_path)
    if kind == "RESUME":
        _pause(service, preimage)
    prefix = service.path.read_bytes()
    observations = _processes(tmp_path, service, [kind, kind])
    for code, _, stderr, result in observations:
        assert code == 0, stderr
        assert Path(result["owner_file"]).resolve() == Path(__file__).resolve().parents[1] / "apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py"
    assert observations[0][3]["event_ref"] == observations[1][3]["event_ref"]
    assert observations[0][3]["pid"] != observations[1][3]["pid"]
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1


@pytest.mark.parametrize("kind", ["PAUSE", "RESUME"])
@pytest.mark.parametrize("cut", ["PRE", "POST"])
def test_bt14_native_process_exit_reopen_preserves_zero_or_one_event(tmp_path, kind, cut):
    service, preimage = _fresh(tmp_path)
    wait = _pause(service, preimage) if kind == "RESUME" else None
    prefix = service.path.read_bytes()
    [(code, _, stderr, result)] = _processes(tmp_path, service, [kind + "_" + cut])
    assert code == (82 if cut == "POST" else 81), stderr
    assert result is None
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == (1 if cut == "POST" else 0)
    cold = _service(service.path)
    recovered = _resume(cold, wait) if wait is not None else _pause(cold, preimage)
    assert recovered.kind == kind
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
