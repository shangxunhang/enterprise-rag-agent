"""Explicit, bounded, disposable diagnostics; never a business authority.

Importing this module and the default observer perform no I/O. Producers cannot
use an emission result to change execution: emission deliberately returns None.
Only a separately constructed and bound session enables capture.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4


@dataclass(frozen=True)
class DiagnosticConfig:
    output_root: str | Path
    allowed_root: str | Path
    session_id: str
    segment_id: str = "process-0"
    enabled: bool = True
    max_session_bytes: int = 16 * 1024 * 1024
    max_events: int = 10_000
    max_event_bytes: int = 64 * 1024
    max_queue_events: int = 128
    max_queue_bytes: int = 1024 * 1024
    max_representation_bytes: int = 256 * 1024
    max_representation_total_bytes: int = 4 * 1024 * 1024
    max_pool_items: int = 2_000
    max_directory_entries: int = 1_000
    max_error_message_chars: int = 1_024
    max_segments: int = 2
    worker_sample_interval_s: float = .2
    flush_records: int = 64
    flush_interval_s: float = .25
    close_timeout_s: float = 2.0
    window_s: float = 120.0
    max_depth: int = 16

    def __post_init__(self) -> None:
        for name in ("session_id", "segment_id"):
            value = getattr(self, name)
            if not value or len(value) > 120 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
                raise ValueError(f"invalid diagnostic {name}")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or not 1 <= self.max_depth <= 64:
            raise ValueError("diagnostic max_depth must be an integer from 1 to 64")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name.startswith("max_") or field.name in {"flush_records", "flush_interval_s", "close_timeout_s", "window_s", "worker_sample_interval_s"}:
                if isinstance(value, bool) or value <= 0:
                    raise ValueError(f"diagnostic {field.name} must be positive")
        if self.max_event_bytes > self.max_queue_bytes:
            raise ValueError("one diagnostic event must fit the byte queue")
        if self.max_representation_total_bytes > self.max_session_bytes:
            raise ValueError("representations must share the session budget")
        if self.close_timeout_s > 2:
            raise ValueError("diagnostic close cannot exceed two seconds")


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class _TooLarge(ValueError):
    pass


def _bounded_copy(value: Any, limit: int, *, max_items: int = 2000, max_depth: int = 16) -> Any:
    """Copy only JSON values, checking scalar size before encoding a container."""
    remaining = [limit]
    def walk(item: Any, depth: int = 0) -> Any:
        if depth > max_depth:
            raise _TooLarge("depth cap")
        remaining[0] -= 8
        if remaining[0] < 0:
            raise _TooLarge("byte cap")
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            if len(item) > remaining[0]:
                raise _TooLarge("text cap")
            remaining[0] -= len(item.encode("utf-8"))
            if remaining[0] < 0:
                raise _TooLarge("text cap")
            return item
        if isinstance(item, Mapping):
            if len(item) > max_items:
                raise _TooLarge("item cap")
            return {walk(k, depth + 1): walk(v, depth + 1) for k, v in item.items() if isinstance(k, str)}
        if isinstance(item, (list, tuple)):
            if len(item) > max_items:
                raise _TooLarge("item cap")
            return [walk(v, depth + 1) for v in item]
        raise TypeError("diagnostics accept JSON primitives, not arbitrary user objects")
    return walk(value)


class _NoDiagnostics:
    enabled = False
    def emit(self, event: Mapping[str, Any]) -> None:
        pass
    def capture_model(self, representation: str, request: Any, response: Any = None, exception: BaseException | None = None) -> None:
        pass
    def emit_pool(self, header: Mapping[str, Any], finite_items: Iterable[Mapping[str, Any]]) -> None:
        pass
    @contextmanager
    def span(self, operation_ref: str, category: str, **fields: Any):
        yield
    def close(self) -> None:
        pass


_NOOP = _NoDiagnostics()
_CURRENT: ContextVar[Any] = ContextVar("bounded_diagnostic_session", default=_NOOP)
_CURRENT_SPAN: ContextVar[Any] = ContextVar("bounded_diagnostic_parent_span", default=None)


def current_diagnostics():
    observer = _CURRENT.get()
    return observer if getattr(observer, "owner_pid", os.getpid()) == os.getpid() else _NOOP


@contextmanager
def activate_diagnostics(session):
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        _CURRENT.reset(token)


def emit(event: Mapping[str, Any]) -> None:
    current_diagnostics().emit(event)


def span(operation_ref: str, category: str, **fields: Any):
    return current_diagnostics().span(operation_ref, category, **fields)


def capture_event(evidence_key: str, payload: Mapping[str, Any], *, unit_id: str | None = None, operation_id: str | None = None, **identity: Any) -> None:
    """Optional callable for independent ingestion and other narrow producers."""
    current_diagnostics().emit({"event_type": evidence_key, "unit_id": unit_id, "operation_ref": operation_id, "payload": payload, **identity})


def capture_pool(evidence_key: str, items: Iterable[Mapping[str, Any]], *, unit_id: str | None = None, operation_id: str | None = None, **header: Any) -> None:
    if "declared_count" not in header and isinstance(items, Sequence):
        header["declared_count"] = len(items)
    current_diagnostics().emit_pool({"evidence_key": evidence_key, "unit_id": unit_id, "operation_ref": operation_id, **header}, items)


def diagnostic_operation(operation_ref: str, category: str):
    """Opt-in decorator for individually whitelisted hotspots, not a profiler."""
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            with current_diagnostics().span(operation_ref, category):
                return function(*args, **kwargs)
        return observed
    return decorate


_REQUEST_EXTRA = frozenset({"provider_attempt_id", "provider_attempt_ordinal", "selected_profile", "selected_model", "provider", "model_candidate_index", "availability_fallback_from", "call_purpose", "prompt_id", "prompt_version", "logical_operation_id", "input_snapshot_version", "model_request_fingerprint"})
_RESPONSE_META = _REQUEST_EXTRA | frozenset({"execution_cancelled", "usage_complete", "cancel_reason", "provider_response_success", "canonical_budget_token_reservation_id", "canonical_budget_logical_reservation_id", "candidate_count", "attempt_index"})


def _model_material(representation: str, request: Any, response: Any = None) -> dict[str, Any]:
    if representation == "MODEL_ATTEMPT_REQUEST":
        extra = {k: request.extra[k] for k in _REQUEST_EXTRA if k in request.extra}
        summary = request.extra.get("llm_context_summary")
        if isinstance(summary, Mapping):
            extra["llm_context_summary"] = {k:summary[k] for k in ("managed","schema_version","package_id","context_sha256","final_model_input_sha256","context_authority") if k in summary}
        params = request.extra.get("generation_params")
        if isinstance(params, Mapping):
            extra["generation_params"] = {k:params[k] for k in ("max_new_tokens","temperature","top_p","do_sample") if k in params}
        return {**{name: getattr(request, name, None) for name in ("schema_version", "model_call_id", "task_id", "run_id", "model_role", "model_name", "caller_agent", "prompt", "system_prompt", "messages", "temperature", "max_tokens", "created_at")}, "extra": extra}
    usage = response.token_usage
    origin = getattr(response.failure_origin, "value", response.failure_origin)
    error = response.error
    error_material = None if error is None else {name: getattr(error, name, None) for name in ("schema_version", "error_code", "error_type", "message", "retryable", "recoverable", "requires_human", "requires_new_input", "failed_node")}
    if error_material is not None:
        error_material["failure_category"] = getattr(error.failure_category, "value", error.failure_category)
    return {**{name: getattr(response, name, None) for name in ("schema_version", "model_call_id", "task_id", "run_id", "model_name", "success", "content", "error_message", "started_at", "finished_at", "latency_ms", "created_at", "finish_reason")}, "error": error_material, "failure_origin": origin, "token_usage": {name: getattr(usage, name, None) for name in ("prompt_tokens", "completion_tokens", "total_tokens")}, "metadata": {k: response.metadata[k] for k in _RESPONSE_META if k in response.metadata}}


class DiagnosticSession:
    """One explicit process segment, one nonblocking queue, one file writer.

    Caps include representation bytes. Prefixes survive business transaction
    failure; unsealed prefixes always remain incomplete. Segment allocations
    subtract from the parent's remaining budget and never reset quotas.
    """
    def __init__(self, config: DiagnosticConfig | Mapping[str, Any]):
        self.config = config if isinstance(config, DiagnosticConfig) else DiagnosticConfig(**config)
        self.enabled = self.config.enabled
        self.owner_pid = os.getpid()
        self._condition = threading.Condition()
        self._queue: deque = deque()
        self._queue_bytes = 0
        self._sequence = 0
        self._reserved_bytes = 0
        self._representation_bytes = 0
        self._representations: set[str] = set()
        self._start = time.monotonic()
        self._closing = False
        self._closed = False
        self._writer_error: str | None = None
        self._flushed_sequence = 0
        self._allocated_segments = 1
        self._allocated_events = 0
        self._active_workers: set[str] = set()
        self.lost_count = 0
        self.queue_high_water = 0
        self.written_bytes = 0
        self.path: Path | None = None
        self._writer: threading.Thread | None = None
        if not self.enabled:
            return
        root = Path(self.config.output_root).resolve()
        allowed = Path(self.config.allowed_root).resolve()
        if root == allowed or not root.is_relative_to(allowed):
            raise ValueError("diagnostic segment must be a new child of the authorized root")
        for parent in (root, *root.parents):
            if parent.exists() and (parent.is_symlink() or bool(getattr(parent.stat(), "st_file_attributes", 0) & 0x400)):
                raise ValueError("diagnostic roots cannot traverse reparse points")
            if parent == allowed:
                break
        root.mkdir(parents=True, exist_ok=False)
        self.path = root / f"{self.config.segment_id}.jsonl"
        self._blob_root = root / "representations"
        self._blob_root.mkdir()
        self._writer = threading.Thread(target=self._write_loop, name="bounded-diagnostics", daemon=True)
        self._writer.start()
        self.emit({"event_type": "SESSION_BEGIN", "category": "CAPTURE", "clock_domain": f"process:{self.owner_pid}", "flush_records": self.config.flush_records, "flush_interval_s": self.config.flush_interval_s, "clock_implementations": {name: {"implementation": time.get_clock_info(name).implementation, "declared_resolution_s": time.get_clock_info(name).resolution} for name in ("monotonic", "thread_time", "process_time")}, "caps": {f.name: getattr(self.config, f.name) for f in fields(self.config) if f.name.startswith("max_")}})

    def __enter__(self):
        self._binding = _CURRENT.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        _CURRENT.reset(self._binding)
        self.close()
        return False

    def allocate_segment(self, output_root: str | Path, segment_id: str, *, byte_quota: int, event_quota: int) -> DiagnosticConfig:
        """Explicit process handoff; child receives disjoint remaining quotas."""
        with self._condition:
            if self._allocated_segments >= self.config.max_segments or byte_quota <= 0 or event_quota <= 0 or byte_quota > self.config.max_session_bytes - self._reserved_bytes or event_quota > self.config.max_events - self._sequence - self._allocated_events:
                raise ValueError("diagnostic segment quota unavailable")
            self._reserved_bytes += byte_quota
            self._allocated_events += event_quota
            self._allocated_segments += 1
        return replace(self.config, output_root=output_root, segment_id=segment_id, max_session_bytes=byte_quota, max_events=event_quota, max_representation_total_bytes=min(byte_quota, self.config.max_representation_total_bytes), max_segments=1)

    def _drop(self) -> None:
        with self._condition:
            self.lost_count += 1

    def emit(self, event: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            payload = _bounded_copy(event, self.config.max_event_bytes, max_items=max(2000,self.config.max_pool_items), max_depth=self.config.max_depth)
            self._enqueue(payload)
        except Exception:
            self._drop()

    def _enqueue(self, payload: dict[str, Any], blob: tuple[str, bytes] | None = None) -> None:
        with self._condition:
            if self.owner_pid != os.getpid() or self._closing or self._writer_error or time.monotonic() - self._start > self.config.window_s:
                self.lost_count += 1
                return
            next_sequence = self._sequence + 1
            row = {**payload, "schema_version": "diagnostic_event_v1", "unit_id": payload.get("unit_id") or "F06", "session_id": self.config.session_id, "segment_id": self.config.segment_id, "process_id": self.owner_pid, "thread_id": threading.get_ident(), "worker_id": threading.current_thread().name, "clock_domain": f"process:{self.owner_pid}", "monotonic_ns": time.monotonic_ns(), "utc": datetime.now(timezone.utc).isoformat(), "sequence": next_sequence, "completeness": payload.get("completeness", "COMPLETE")}
            row["frame_sha256"] = hashlib.sha256(_encoded(row)).hexdigest()
            encoded = _encoded(row) + b"\n"
            blob_size = len(blob[1]) if blob is not None else 0
            size = len(encoded) + blob_size
            if len(encoded) > self.config.max_event_bytes or next_sequence + self._allocated_events > self.config.max_events or len(self._queue) >= self.config.max_queue_events or self._queue_bytes + size > self.config.max_queue_bytes or self._reserved_bytes + size + 4096 > self.config.max_session_bytes or self._representation_bytes + blob_size > self.config.max_representation_total_bytes:
                self.lost_count += 1
                return
            self._sequence = next_sequence
            self._reserved_bytes += size
            self._representation_bytes += blob_size
            if blob is not None:
                self._representations.add(blob[0])
            self._queue.append((next_sequence, encoded, blob, size))
            worker = payload.get("worker_identity")
            if isinstance(worker, str):
                if payload.get("event_type") == "WORKER_START":
                    self._active_workers.add(worker)
                elif payload.get("event_type") == "WORKER_FINISH":
                    self._active_workers.discard(worker)
            self._queue_bytes += size
            self.queue_high_water = max(self.queue_high_water, len(self._queue))
            self._condition.notify_all()

    def capture_model(self, representation: str, request: Any, response: Any = None, exception: BaseException | None = None) -> None:
        if not self.enabled:
            return
        try:
            extra = request.extra
            identity = {"model_call_id": request.model_call_id, "run_id": request.run_id, "task_id": request.task_id, **{k: extra.get(k) for k in _REQUEST_EXTRA if k.startswith("provider_") or k in {"selected_profile", "selected_model", "model_candidate_index", "availability_fallback_from"}}, "identity_quality": "ORIGINAL_PROVIDER_ATTEMPT_ID" if extra.get("provider_attempt_id") else "VERSION_COMPOSITE"}
            event = {"event_type": representation, "category": "PROVIDER", "unit_id": "F01", "operation_ref": request.model_call_id, **identity}
            if exception is not None:
                event.update(exception_type=type(exception).__name__, outcome="UNKNOWN")
                self.emit(event)
                return
            candidate = _model_material(representation, request, response)
            if response is not None:
                texts = [candidate.get("error_message"), (candidate.get("error") or {}).get("message")]
                if any(isinstance(t, str) and len(t) > self.config.max_error_message_chars for t in texts):
                    raise _TooLarge("error message cap")
            material = _bounded_copy(candidate, self.config.max_representation_bytes, max_items=max(2000,self.config.max_pool_items), max_depth=self.config.max_depth)
            encoded = _encoded(material)
            if len(encoded) > self.config.max_representation_bytes:
                raise _TooLarge("representation cap")
            digest = hashlib.sha256(representation.encode() + b"\0" + encoded).hexdigest()
            event["payload_ref"] = {"representation": representation, "relative_path": f"representations/{digest}.json", "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded), "completeness": "COMPLETE"}
            with self._condition:
                blob = None if digest in self._representations else (digest, encoded)
                self._enqueue(event, blob)
        except Exception:
            self._drop()

    @contextmanager
    def span(self, operation_ref: str, category: str, **fields: Any):
        if not self.enabled:
            yield
            return
        span_id = uuid4().hex
        began = time.monotonic_ns()
        thread_cpu = time.thread_time_ns()
        process_cpu = time.process_time_ns()
        parent = _CURRENT_SPAN.get()
        base = {**fields, "operation_ref": operation_ref, "category": category, "span_id": span_id, "parent_span_id": parent[1] if parent and parent[0] == self.config.session_id else None}
        binding = _CURRENT_SPAN.set((self.config.session_id, span_id))
        self.emit({**base, "event_type": "SPAN_BEGIN", "begin_ns": began})
        try:
            yield
        except BaseException as exc:
            self.emit({**base, "event_type": "SPAN_ERROR", "begin_ns": began, "end_ns": time.monotonic_ns(), "exception_type": type(exc).__name__, "thread_cpu_ns": time.thread_time_ns() - thread_cpu, "process_cpu_ns": time.process_time_ns() - process_cpu})
            raise
        else:
            self.emit({**base, "event_type": "SPAN_RESULT", "begin_ns": began, "end_ns": time.monotonic_ns(), "thread_cpu_ns": time.thread_time_ns() - thread_cpu, "process_cpu_ns": time.process_time_ns() - process_cpu})
        finally:
            _CURRENT_SPAN.reset(binding)

    def emit_pool(self, header: Mapping[str, Any], finite_items: Iterable[Mapping[str, Any]]) -> None:
        if not self.enabled:
            return
        try:
            pool_id = str(header.get("pool_id") or uuid4().hex)
            count = header.get("declared_count")
            self.emit({**dict(header), "event_type": "POOL_BEGIN", "pool_id": pool_id})
            digest = hashlib.sha256()
            actual = 0
            for ordinal, item in enumerate(finite_items):
                if ordinal >= self.config.max_pool_items:
                    self._drop()
                    return
                copied = _bounded_copy(item, self.config.max_event_bytes // 2, max_depth=self.config.max_depth)
                digest.update(_encoded(copied) + b"\n")
                self.emit({"event_type": "POOL_ITEM", "pool_id": pool_id, "ordinal": ordinal, "item": copied})
                actual += 1
            self.emit({"event_type": "POOL_END", "pool_id": pool_id, "declared_count": count, "actual_count": actual, "end_digest": digest.hexdigest(), "completeness": "COMPLETE" if count == actual else "CAPTURE_INCOMPLETE"})
        except Exception:
            self._drop()

    def _write_loop(self) -> None:
        try:
            with self.path.open("xb") as stream:
                last_flush = time.monotonic()
                last_sample = last_flush
                pending = 0
                last_sequence = 0
                while True:
                    with self._condition:
                        if not self._queue and not self._closing:
                            self._condition.wait(min(self.config.flush_interval_s, self.config.worker_sample_interval_s))
                        item = self._queue.popleft() if self._queue else None
                        if item:
                            self._queue_bytes -= item[3]
                        closing = self._closing and not self._queue and item is None
                    if item:
                        sequence, encoded, blob, _ = item
                        if blob is not None:
                            with (self._blob_root / (blob[0] + ".json")).open("xb") as material:
                                material.write(blob[1])
                                material.flush()
                                os.fsync(material.fileno())
                            self.written_bytes += len(blob[1])
                        stream.write(encoded)
                        self.written_bytes += len(encoded)
                        last_sequence = sequence
                        pending += 1
                    now = time.monotonic()
                    if not closing and now - self._start > self.config.window_s:
                        with self._condition:
                            self.lost_count += 1
                            self._closing = True
                    elif not closing and now - last_sample >= self.config.worker_sample_interval_s:
                        with self._condition:
                            active_count = len(self._active_workers)
                        self.emit({"event_type": "PROCESS_SAMPLE", "category": "CPU_SAMPLE", "active_worker_count": active_count, "process_cpu_ns": time.process_time_ns(), "sampling_interval_s": self.config.worker_sample_interval_s, "cpu_scope": "PROCESS", "thread_cpu_assignment": "UNMEASURED"})
                        last_sample = now
                    if pending and (closing or pending >= self.config.flush_records or time.monotonic() - last_flush >= self.config.flush_interval_s):
                        stream.flush()
                        os.fsync(stream.fileno())
                        with self._condition:
                            self._flushed_sequence = last_sequence
                            self._condition.notify_all()
                        pending = 0
                        last_flush = time.monotonic()
                    if closing:
                        seal = {"schema_version": "diagnostic_seal_v1", "event_type": "SESSION_SEAL", "session_id": self.config.session_id, "segment_id": self.config.segment_id, "flushed_sequence": self._flushed_sequence, "last_sequence": last_sequence, "lost_count": self.lost_count, "written_bytes": self.written_bytes, "queue_high_water": self.queue_high_water, "completeness": "CAPTURE_INCOMPLETE" if self.lost_count else "COMPLETE"}
                        seal["frame_sha256"] = hashlib.sha256(_encoded(seal)).hexdigest()
                        framed = _encoded(seal) + b"\n"
                        stream.write(framed)
                        stream.flush()
                        os.fsync(stream.fileno())
                        self.written_bytes += len(framed)
                        break
        except Exception as exc:
            with self._condition:
                self._writer_error = type(exc).__name__
                self.lost_count += len(self._queue) + 1
                self._queue.clear()
                self._queue_bytes = 0
                self._condition.notify_all()
        finally:
            self._closed = True

    def flush(self, timeout_s: float = 2.0) -> None:
        if not self.enabled:
            return
        deadline = time.monotonic() + min(timeout_s, self.config.close_timeout_s)
        with self._condition:
            target = self._sequence
            self._condition.notify_all()
            while self._flushed_sequence < target and not self._writer_error and time.monotonic() < deadline:
                self._condition.wait(min(.05, max(0, deadline - time.monotonic())))

    def close(self) -> None:
        if not self.enabled:
            return
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        if self._writer:
            self._writer.join(self.config.close_timeout_s)

    def health(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "lost_count": self.lost_count, "writer_error": self._writer_error, "flushed_sequence": self._flushed_sequence, "queue_high_water": self.queue_high_water, "written_bytes": self.written_bytes, "closed": self._closed, "completeness": "COMPLETE" if self._closed and not self.lost_count and not self._writer_error else "CAPTURE_INCOMPLETE"}
