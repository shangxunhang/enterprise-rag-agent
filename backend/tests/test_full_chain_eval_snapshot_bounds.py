"""Bounded historical reads, with fixed bytes and no source recovery writes."""
from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from eval.full_chain import snapshot
from eval.full_chain.domains import runtime_reliability
from schemas.model import ModelRequestSchema


def manifest(path, **fields):
    return {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "bounds",
            "sources": [{"name": "source", "path": str(path), **fields}]}


def test_manifest_byte_cap_precedes_read_and_counts_toward_session(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_bytes(b'{"value":1}')
    path = tmp_path / "manifest.json"
    raw = json.dumps(manifest(source, format="json")).encode()
    path.write_bytes(raw)
    original_open = Path.open
    opened = []

    def tracked_open(candidate, *args, **kwargs):
        opened.append(candidate)
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    with pytest.raises(snapshot.SnapshotError, match="MANIFEST_BYTE_CAP_EXCEEDED"):
        snapshot.load_source_snapshot(path, [tmp_path], max_bytes=len(raw) - 1)
    assert path not in opened
    exact = snapshot.load_source_snapshot(path, [tmp_path], max_bytes=len(raw) + source.stat().st_size)
    assert exact.get("source") == {"value": 1}
    capped = snapshot.load_source_snapshot(path, [tmp_path], max_bytes=len(raw) + source.stat().st_size - 1)
    assert capped.get("source") is None
    assert "source:SOURCE_BYTE_CAP_EXCEEDED" in capped.limitations
    assert path.read_bytes() == raw


def test_read_byte_cap_survives_growth_after_stat(tmp_path, monkeypatch):
    path = tmp_path / "growing.json"
    path.write_bytes(b"{}")
    original_open = Path.open
    reads = []

    class GrowingStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def opened(candidate, *args, **kwargs):
        if candidate == path and args and args[0] == "rb":
            return GrowingStream(b"{}" * 1024)
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    view = snapshot.load_source_snapshot(manifest(path, format="json"), [tmp_path], max_bytes=2)
    assert view.get("source") is None
    assert view.sources["source"].limitations == ("SOURCE_BYTE_CAP_EXCEEDED",)
    assert reads == [3]


def diagnostic_fixture(tmp_path):
    observer = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "bounds", flush_records=1))
    request = ModelRequestSchema(model_call_id="fixed-call", task_id="task", run_id="run",
        model_role="section_generation", prompt="actual fixed material", created_at="2026-10-05T00:00:00Z")
    with observer:
        for _ in range(3):
            observer.capture_model("MODEL_ATTEMPT_REQUEST", request)
    path = observer.path
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    blobs = sorted({row["payload_ref"]["relative_path"] for row in rows if row.get("payload_ref")})
    assert len(blobs) == 1
    declaration = manifest(path, format="diagnostic_jsonl", session_id="bounds", representation_files=blobs)
    return path, raw, blobs, declaration


def test_diagnostic_record_cap_preserves_only_prefix_without_fabricated_seal(tmp_path, monkeypatch):
    path, raw, blobs, declaration = diagnostic_fixture(tmp_path)
    original_read = runtime_reliability._read_bounded
    reads = []

    def tracked_read(candidate, *args, **kwargs):
        reads.append(candidate)
        return original_read(candidate, *args, **kwargs)

    monkeypatch.setattr(runtime_reliability, "_read_bounded", tracked_read)
    view = snapshot.load_source_snapshot(declaration, [tmp_path], max_records=1)
    assert len(view.get("source")) == 1
    assert view.get("source")[0]["event_type"] == "SESSION_BEGIN"
    assert all(row["event_type"] != "SESSION_SEAL" for row in view.get("source"))
    assert view.sources["source"].evidence.completeness == "PARTIAL"
    assert "source:RECORD_CAP_EXCEEDED" in view.limitations
    assert "source:NO_SEAL" in view.limitations
    assert not any(candidate == path.parent / blobs[0] for candidate in reads)
    assert path.read_bytes() == raw


def test_diagnostic_unique_blob_exact_byte_budget_is_not_deducted_twice(tmp_path):
    path, raw, blobs, declaration = diagnostic_fixture(tmp_path)
    total = len(raw) + sum((path.parent / blob).stat().st_size for blob in blobs)
    view = snapshot.load_source_snapshot(declaration, [tmp_path], max_bytes=total)
    assert view.limitations == ()
    assert view.sources["source"].evidence.completeness == "COMPLETE"
    requests = [row for row in view.get("source") if row["event_type"] == "MODEL_ATTEMPT_REQUEST"]
    assert len(requests) == 3
    assert all(row["material"]["prompt"] == "actual fixed material" for row in requests)
    capped = snapshot.load_source_snapshot(declaration, [tmp_path], max_bytes=total - 1)
    assert capped.get("source") is None
    assert "source:SOURCE_BYTE_CAP_EXCEEDED" in capped.limitations
    assert path.read_bytes() == raw


def test_diagnostic_cap_after_visible_seal_does_not_certify_hidden_tail(tmp_path):
    path, raw, _, declaration = diagnostic_fixture(tmp_path)
    rows = raw.splitlines()
    assert json.loads(rows[-1])["event_type"] == "SESSION_SEAL"
    # Preserve the original committed seal but hide a subsequent committed row.
    changed = raw + rows[0] + b"\n"
    path.write_bytes(changed)
    prefix = runtime_reliability.read_diagnostic_prefix(path, [tmp_path], max_records=len(rows))
    assert prefix["seal"] is None
    assert prefix["completeness"] == "CAPTURE_INCOMPLETE"
    assert "RECORD_CAP_EXCEEDED" in prefix["limitations"]
    view = snapshot.load_source_snapshot(declaration, [tmp_path], max_records=len(rows))
    assert all(row["event_type"] != "SESSION_SEAL" for row in view.get("source"))
    assert view.sources["source"].evidence.completeness == "PARTIAL"
    assert path.read_bytes() == changed


def test_diagnostic_prefix_reader_enforces_explicit_blob_declaration(tmp_path):
    path, raw, _, _ = diagnostic_fixture(tmp_path)
    prefix = runtime_reliability.read_diagnostic_prefix(path, [tmp_path], allowed_representation_files=[])
    assert prefix["completeness"] == "CAPTURE_INCOMPLETE"
    assert "DIAGNOSTIC_REPRESENTATION_NOT_DECLARED" in prefix["limitations"]
    assert all("material" not in event for event in prefix["events"])
    assert path.read_bytes() == raw


@pytest.mark.parametrize("shape,dtype,reason", [
    ((10**12,), "i8", "UNSAFE_OR_OVERSIZED_ARRAY"),
    ((0, 10**100), "i8", "UNSAFE_OR_OVERSIZED_ARRAY"),
    ((1,), "O", "UNSAFE_OR_OVERSIZED_ARRAY"),
    ((1,), "V1000000", "ARRAY_PAYLOAD_SIZE_MISMATCH"),
    ((2,), "i8", "ARRAY_PAYLOAD_SIZE_MISMATCH"),
])
def test_npy_header_refuses_allocation_before_load(tmp_path, monkeypatch, shape, dtype, reason):
    stream = io.BytesIO()
    np.lib.format.write_array_header_1_0(stream, {"descr": np.dtype(dtype).str,
        "fortran_order": False, "shape": shape})
    path = tmp_path / "unsafe.npy"
    raw = stream.getvalue()
    path.write_bytes(raw)

    def forbidden(*args, **kwargs):
        raise AssertionError("np.load must not precede header admission")

    monkeypatch.setattr(np, "load", forbidden)
    view = snapshot.load_source_snapshot(manifest(path, format="npy"), [tmp_path], max_records=10)
    assert view.get("source") is None
    assert view.sources["source"].limitations == (reason,)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("version", [(1, 0), (2, 0), (3, 0)])
def test_npy_valid_header_loads_the_same_bounded_hashed_bytes(tmp_path, monkeypatch, version):
    stream = io.BytesIO()
    np.lib.format.write_array(stream, np.array([[1, 2], [3, 4]], dtype=np.int32), version=version)
    path = tmp_path / "safe.npy"
    raw = stream.getvalue()
    path.write_bytes(raw)
    original_load = np.load
    loaded_bytes = []

    def tracked_load(source, **kwargs):
        assert isinstance(source, io.BytesIO)
        loaded_bytes.append(source.getvalue())
        return original_load(source, **kwargs)

    monkeypatch.setattr(np, "load", tracked_load)
    view = snapshot.load_source_snapshot(manifest(path, format="npy"), [tmp_path], max_records=4)
    assert view.limitations == ()
    assert view.get("source") == {"shape": [2, 2], "dtype": "int32", "values": [[1, 2], [3, 4]]}
    assert view.sources["source"].evidence.raw_sha256 == sha256(raw).hexdigest()
    assert loaded_bytes == [raw]
    assert path.read_bytes() == raw


def frozen_wal_fixture(tmp_path):
    active = tmp_path / "active.db"
    source = tmp_path / "source"
    source.mkdir()
    path = source / "state.db"
    with sqlite3.connect(active) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE facts (ref TEXT)")
        db.execute("INSERT INTO facts VALUES ('wal-visible')")
        db.commit()
        raw = active.read_bytes()
        wal_raw = Path(str(active) + "-wal").read_bytes()
        path.write_bytes(raw)
        Path(str(path) + "-wal").write_bytes(wal_raw)
    declaration = manifest(path, format="sqlite", tables=["facts"], schema_version="fixture_v1",
        frozen_wal={"stopped": True, "db_sha256": sha256(raw).hexdigest(), "wal_sha256": sha256(wal_raw).hexdigest()})
    return path, raw, wal_raw, declaration, source


def test_frozen_wal_exact_cap_reads_working_copy_and_preserves_source_bytes(tmp_path):
    path, raw, wal_raw, declaration, source = frozen_wal_fixture(tmp_path)
    output = tmp_path / "working"
    view = snapshot.load_source_snapshot(declaration, [source], output_root=output,
        max_bytes=len(raw) + len(wal_raw))
    assert view.limitations == ()
    assert view.get("source")["facts"] == [{"ref": "wal-visible"}]
    assert len(list(output.rglob("state.db"))) == 1
    assert path.read_bytes() == raw
    assert Path(str(path) + "-wal").read_bytes() == wal_raw
    assert not Path(str(path) + "-shm").exists()


def test_wal_byte_cap_refuses_before_read_or_sqlite_open(tmp_path, monkeypatch):
    path, raw, wal_raw, declaration, source = frozen_wal_fixture(tmp_path)
    wal = Path(str(path) + "-wal")
    original_open = Path.open
    opened = []

    def tracked_open(candidate, *args, **kwargs):
        opened.append(candidate)
        return original_open(candidate, *args, **kwargs)

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("SQLite must not open before WAL byte admission")

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    output = tmp_path / "working"
    view = snapshot.load_source_snapshot(declaration, [source], output_root=output,
        max_bytes=len(raw) + len(wal_raw) - 1)
    assert view.get("source") is None
    assert "source:SOURCE_BYTE_CAP_EXCEEDED" in view.limitations
    assert wal not in opened
    assert not output.exists()


def test_wal_windows_reparse_attribute_is_rejected_before_read(tmp_path, monkeypatch):
    path, _, _, declaration, source = frozen_wal_fixture(tmp_path)
    wal = Path(str(path) + "-wal")
    original_lstat = Path.lstat
    original_open = Path.open
    opened = []

    def lstat(candidate, *args, **kwargs):
        result = original_lstat(candidate, *args, **kwargs)
        if candidate == wal:
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    def tracked_open(candidate, *args, **kwargs):
        opened.append(candidate)
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "open", tracked_open)
    view = snapshot.load_source_snapshot(declaration, [source], output_root=tmp_path / "working")
    assert view.get("source") is None
    assert "source:REPARSE_POINT_REJECTED" in view.limitations
    assert wal not in opened
