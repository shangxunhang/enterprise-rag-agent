"""Actual Windows range-lock controls; no production Run fixture or effects."""
from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time
import traceback

import pytest

from core.persistence.interprocess_lock import InterProcessFileLock

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows range-lock controls")


def _native_lock_holder(connection, path, exit_without_unlock):
    try:
        with InterProcessFileLock(path):
            connection.send({"phase":"HELD", "pid":os.getpid(), "monotonic_ns":time.monotonic_ns()})
            command=connection.recv()
            if command != "RELEASE":
                raise ValueError("test holder requires its exact release command")
            connection.send({"phase":"RELEASE_REQUEST_RECEIVED", "pid":os.getpid(),
                "monotonic_ns":time.monotonic_ns(), "os_exit_without_unlock":exit_without_unlock})
            if exit_without_unlock:
                os._exit(83)
        connection.send({"phase":"RELEASED", "pid":os.getpid(), "monotonic_ns":time.monotonic_ns()})
    except BaseException as error:
        connection.send({"phase":"ERROR", "pid":os.getpid(), "error_type":type(error).__name__,
            "errno":getattr(error,"errno",None), "message":str(error), "traceback":traceback.format_exc()})
    finally:
        connection.close()


def _native_lock_contender(connection, path):
    try:
        connection.send({"phase":"CONTENDER_STARTED", "pid":os.getpid(), "monotonic_ns":time.monotonic_ns()})
        with InterProcessFileLock(path):
            connection.send({"phase":"ACQUIRED", "pid":os.getpid(), "monotonic_ns":time.monotonic_ns()})
        connection.send({"phase":"CONTENDER_RELEASED", "pid":os.getpid(), "monotonic_ns":time.monotonic_ns()})
    except BaseException as error:
        connection.send({"phase":"ERROR", "pid":os.getpid(), "error_type":type(error).__name__,
            "errno":getattr(error,"errno",None), "message":str(error), "traceback":traceback.format_exc()})
    finally:
        connection.close()


def _native_pipe_observation(connection, events, seconds=20):
    if not connection.poll(seconds):
        events.append({"phase":"TEST_TIME_BUDGET_EXHAUSTED", "seconds":seconds})
        return None
    try:
        value=connection.recv()
    except EOFError:
        value={"phase":"PIPE_CLOSED"}
    events.append(value)
    return value


@pytest.mark.parametrize("exit_without_unlock", [False, True])
def test_windows_native_contender_survives_ten_second_retry_limit_and_actual_owner_release(tmp_path, exit_without_unlock):
    """Two actual processes contend on one stable range for more than ten seconds."""
    context=multiprocessing.get_context("spawn")
    path=tmp_path/"stable_native_range.lock"
    holder_connection, holder_child=context.Pipe()
    contender_connection, contender_child=context.Pipe()
    holder=context.Process(target=_native_lock_holder,args=(holder_child,str(path),exit_without_unlock))
    contender=context.Process(target=_native_lock_contender,args=(contender_child,str(path)))
    holder_events=[];contender_events=[];cleanup=[]
    held=None;started=None;release_received=None;acquired=None
    try:
        holder.start();holder_child.close()
        held=_native_pipe_observation(holder_connection,holder_events)
        if held is not None and held.get("phase")=="HELD":
            contender.start();contender_child.close()
            started=_native_pipe_observation(contender_connection,contender_events)
            if started is not None and started.get("phase")=="CONTENDER_STARTED":
                # Test-only scheduling. It cannot mutate any canonical Run/Attempt.
                time.sleep(11.25)
                holder_connection.send("RELEASE")
                release_received=_native_pipe_observation(holder_connection,holder_events)
                if not exit_without_unlock:
                    _native_pipe_observation(holder_connection,holder_events)
                acquired=_native_pipe_observation(contender_connection,contender_events)
                if acquired is not None and acquired.get("phase")=="ACQUIRED":
                    _native_pipe_observation(contender_connection,contender_events)
        for process in (holder,contender):
            if process.pid is not None:
                process.join(20)
    finally:
        for process in (holder,contender):
            if process.pid is not None and process.is_alive():
                cleanup.append({"test_owned_pid":process.pid,"classification":"TEST_TIME_BUDGET_EXHAUSTED",
                    "production_failure":"NOT_APPLICABLE_NO_RUN_FIXTURE"})
                process.terminate();process.join(10)
        for connection in (holder_connection,holder_child,contender_connection,contender_child):
            connection.close()
        from core.persistence import interprocess_lock as native_module
        body={"scope":"OWNER_LOCK_NATIVE_WINDOWS_ONLY", "exit_without_unlock":exit_without_unlock,
            "native_source_path":str(Path(native_module.__file__).resolve()),
            "native_source_sha256":hashlib.sha256(Path(native_module.__file__).read_bytes()).hexdigest(),
            "stable_lock_path":str(path), "holder_events":holder_events, "contender_events":contender_events,
            "holder_exitcode":holder.exitcode, "contender_exitcode":contender.exitcode, "test_worker_cleanup":cleanup,
            "canonical_production_records":"NOT_APPLICABLE_NO_RUN_FIXTURE"}
        receipt=tmp_path/("native_windows_lock_exit" if exit_without_unlock else "native_windows_lock_release")
        with receipt.with_suffix(".json").open("x",encoding="utf-8") as file:
            file.write(json.dumps(body,ensure_ascii=False,indent=2))
    assert not cleanup
    assert held is not None and held["phase"]=="HELD"
    assert started is not None and started["phase"]=="CONTENDER_STARTED"
    assert release_received is not None and release_received["phase"]=="RELEASE_REQUEST_RECEIVED"
    assert release_received["monotonic_ns"]-started["monotonic_ns"]>10_000_000_000
    assert acquired is not None and acquired["phase"]=="ACQUIRED"
    assert acquired["monotonic_ns"]>=release_received["monotonic_ns"]
    assert holder.exitcode==(83 if exit_without_unlock else 0)
    assert contender.exitcode==0
    assert contender_events[-1]["phase"]=="CONTENDER_RELEASED"


def test_windows_unrelated_native_lock_error_propagates_without_contention_retry(tmp_path, monkeypatch):
    """Authored EIO control of the real msvcrt entry; it is not an OS crash proof."""
    from core.persistence import interprocess_lock as native_module
    calls=[];observed=None
    def fail(fd,mode,count):
        calls.append({"native_fd":fd,"mode":mode,"count":count})
        raise OSError(errno.EIO,"TEST_AUTHORED_UNRELATED_NATIVE_IO_FAILURE")
    monkeypatch.setattr(native_module.msvcrt,"locking",fail)
    path=tmp_path/"native_error.lock"
    with path.open("a+b",buffering=0) as handle:
        handle.write(b"\0")
        try:native_module._lock(handle)
        except OSError as error:
            observed={"type":type(error).__name__,"errno":error.errno,"message":str(error)}
    receipt={"scope":"AUTHORED_ERROR_PROPAGATION_ONLY","actual_open_native_file":str(path),
        "native_calls":calls,"observed_error":observed,"canonical_production_records":"NOT_APPLICABLE_NO_RUN_FIXTURE"}
    with (tmp_path/"native_windows_unrelated_io.json").open("x",encoding="utf-8") as file:
        file.write(json.dumps(receipt,indent=2))
    assert observed is not None and observed["errno"]==errno.EIO
    assert len(calls)==1
    assert calls[0]["mode"]==native_module.msvcrt.LK_LOCK and calls[0]["count"]==1
