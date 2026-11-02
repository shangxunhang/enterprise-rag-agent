"""I3 preflight characterization; this is not I3 acceptance evidence.

The existing frozen contract drains a pre-barrier writer at final read,
whereas the 2026-10-02 request requires it to precede the lifecycle CAS.
Only existing production methods are exercised; no finalizer is added.
"""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    RunAttemptLifecycleService,
)
from backend.tests.test_bca014_finalizing_lifecycle import _finalize
from backend.tests.test_multi_agent_v3_artifact_runtime import (
    _commit, _lifecycle, _logical, _runtime,
)


def test_finalizing_first_rejects_artifact_without_canonical_append(tmp_path):
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    _finalize(lifecycle, lifecycle.load_run("run-1"))
    before = runtime.ledger_path.read_bytes() if runtime.ledger_path.exists() else b""
    with pytest.raises(LifecycleCurrentnessError):
        _commit(runtime, _logical(), operation_id="probe-a", payload={"text": "late"})
    after = runtime.ledger_path.read_bytes() if runtime.ledger_path.exists() else b""
    assert before == after


def test_existing_artifact_owner_can_commit_after_finalizing_cas(tmp_path):
    lifecycle = _lifecycle(tmp_path)
    finalizer_lifecycle = RunAttemptLifecycleService(lifecycle.path)
    runtime = _runtime(tmp_path, lifecycle)
    before_append = threading.Event()
    release_append = threading.Event()
    trace = []
    append = runtime.log.append_serialized

    def held_append(serialized):
        # Real Artifact owner RLock and file lock are still held here.
        # All existing production lifecycle checks have already returned.
        before_append.set()
        assert release_append.wait(10), "probe failed to release Artifact writer"
        append(serialized)
        trace.append("artifact_committed")

    runtime.log.append_serialized = held_append
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(
            _commit, runtime, _logical(), operation_id="probe-b", payload={"text": "in-flight"},
        )
        try:
            assert before_append.wait(10), "Artifact did not reach canonical append"
            running = finalizer_lifecycle.load_run("run-1")
            finalizing = pool.submit(_finalize, finalizer_lifecycle, running).result(timeout=10)
            assert finalizing.status is RunLifecycleStatus.FINALIZING
            # Exact durable replay proves this is not an in-memory status.
            assert RunAttemptLifecycleService(lifecycle.path).load_run("run-1") == finalizing
            trace.append("finalizing_durable")
            assert not writer.done()
        finally:
            release_append.set()
        ref = writer.result(timeout=10)

    assert runtime.read_version(ref)[1] == ref
    assert trace == ["finalizing_durable", "artifact_committed"]
    print("OBSERVED_ORDER=" + " -> ".join(trace))
