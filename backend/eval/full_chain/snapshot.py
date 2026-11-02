"""Explicitly scoped, bounded, non-repairing historical readers."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterable, Mapping

from .contracts import EvaluationEvidenceRef, canonical_hash


class SnapshotError(ValueError):
    pass


class DiagnosticEvent(dict):
    """Hydrated reader view with a separately retained encoded-frame projection.

    The projection is data, not an asserted verification flag. Consumers still
    verify its hash. Keeping it outside dict keys also keeps it out of reports
    and permits dict/asdict reconstruction without a special serialized schema.
    """
    def __init__(self, frame=()):
        super().__init__(frame)
        self.__original_frame = deepcopy(dict(self))
        self.__material_digest = None
        self.reader_limitations = ()

    @property
    def original_frame(self):
        return deepcopy(self.__original_frame)

    def hydrate_material(self, raw: bytes):
        ref = self.__original_frame.get("payload_ref")
        if not isinstance(ref, Mapping) or len(raw) != ref.get("bytes") or sha256(raw).hexdigest() != ref.get("sha256"):
            raise SnapshotError("REPRESENTATION_HASH_MISMATCH")
        material = json.loads(raw)
        self["material"] = material
        self.__material_digest = canonical_hash(material)

    @property
    def material_valid(self):
        if not self.__original_frame.get("payload_ref"):
            return True
        try:
            return self.__material_digest is not None and canonical_hash(self.get("material")) == self.__material_digest
        except (TypeError, ValueError):
            return False


def original_diagnostic_frame(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row.original_frame if isinstance(row, DiagnosticEvent) else row


def diagnostic_frame_valid(row: Mapping[str, Any]) -> bool:
    """Never treat caller fields such as source_frame_verified as a proof."""
    frame = original_diagnostic_frame(row)
    expected = frame.get("frame_sha256")
    try:
        return isinstance(expected, str) and expected == canonical_hash(
            {key: value for key, value in frame.items() if key != "frame_sha256"})
    except (ValueError, TypeError):
        return False


def diagnostic_capture_complete(snapshot: "SnapshotView") -> bool:
    """Prove each original capture segment, separately from hydrated material."""
    groups = {}
    for source in snapshot.sources.values():
        rows = source.data if isinstance(source.data, (list, tuple)) else [source.data]
        native = source.family == "diagnostics" and not snapshot.manifest.get("synthetic")
        if native and (source.limitations or source.evidence.completeness != "COMPLETE"
                       or source.evidence.integrity != "VALID"):
            return False
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            frame = original_diagnostic_frame(row)
            if not frame.get("session_id"):
                continue
            if (not diagnostic_frame_valid(row) or getattr(row, "reader_limitations", ())
                    or frame.get("completeness") != "COMPLETE"
                    or (frame.get("payload_ref") and not (isinstance(row, DiagnosticEvent) and row.material_valid))):
                return False
            key = (frame.get("session_id"), frame.get("segment_id"))
            groups.setdefault(key, []).append(frame)
    for (session, segment), frames in groups.items():
        events = [row for row in frames if row.get("event_type") != "SESSION_SEAL"]
        seals = [row for row in frames if row.get("event_type") == "SESSION_SEAL"]
        if len(seals) != 1 or not events or frames[-1].get("event_type") != "SESSION_SEAL":
            return False
        if [row.get("sequence") for row in events] != list(range(1, len(events) + 1)):
            return False
        if len({row.get("process_id") for row in events}) != 1:
            return False
        seal = seals[0]
        if (seal.get("session_id"), seal.get("segment_id")) != (session, segment):
            return False
        if (seal.get("last_sequence") != len(events) or seal.get("flushed_sequence") != len(events)
                or seal.get("lost_count") != 0):
            return False
    return True


def _read_bounded(path: Path, limit: int, reason: str = "SOURCE_BYTE_CAP_EXCEEDED") -> bytes:
    """Enforce the limit on the read itself, including growth after stat()."""
    if path.stat().st_size > limit:
        raise SnapshotError(reason)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SnapshotError(reason)
    return raw


def safe_path(path: str | Path, allowed_roots: Iterable[str | Path]) -> Path:
    candidate = Path(path).absolute()
    roots = tuple(Path(r).resolve(strict=True) for r in allowed_roots)
    # Reject links/reparse points throughout the lexical path before resolving.
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SnapshotError("REPARSE_POINT_REJECTED")
    resolved = candidate.resolve(strict=True)
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise SnapshotError("OUTSIDE_ALLOWED_ROOT")
    if not resolved.is_file():
        raise SnapshotError("NOT_REGULAR_FILE")
    return resolved


@dataclass(frozen=True)
class LoadedSource:
    name: str
    family: str
    data: Any
    evidence: EvaluationEvidenceRef
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotView:
    snapshot_id: str
    sources: Mapping[str, LoadedSource] = field(default_factory=dict)
    manifest: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def records(self, family: str | None = None) -> list[dict[str, Any]]:
        result = []
        for source in self.sources.values():
            if family is not None and source.family != family:
                continue
            data = source.data
            if isinstance(data, dict):
                result.append(data)
            elif isinstance(data, (list, tuple)):
                result.extend(row for row in data if isinstance(row, dict))
        return result

    def get(self, name: str, default=None):
        source = self.sources.get(name)
        return source.data if source else default

    @classmethod
    def from_records(cls, records: Mapping[str, Any], snapshot_id="synthetic"):
        return cls(snapshot_id, {name: LoadedSource(name, name, data,
            EvaluationEvidenceRef(locator="fixture:" + name, family=name,
                raw_sha256=canonical_hash(data), snapshot_id=snapshot_id,
                integrity="VALID", completeness="COMPLETE"))
            for name, data in records.items()}, {"synthetic": True})


def read_jsonl_prefix(raw: bytes, *, max_records=100000) -> tuple[list[Any], tuple[str, ...]]:
    end = raw.rfind(b"\n") + 1
    limitations = [] if end == len(raw) else ["TORN_TAIL_UNCOMMITTED"]
    rows, identities = [], {}
    for ordinal, line in enumerate(raw[:end].splitlines(), 1):
        if ordinal > max_records:
            limitations.append("RECORD_CAP_EXCEEDED")
            break
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            limitations.append(f"INVALID_COMMITTED_RECORD:{ordinal}")
            break
        if isinstance(row, dict):
            key = row.get("record_id")
            if key is not None and key in identities and identities[key] != row:
                limitations.append(f"CONFLICTING_RECORD_ID:{ordinal}")
                break
            if key is not None:
                identities[key] = row
        rows.append(row)
    return rows, tuple(limitations)


def _sqlite_rows(path: Path, declaration: Mapping[str, Any], output_root: Path | None,
                 max_rows: int, raw: bytes, wal: Path | None,
                 wal_raw: bytes | None) -> tuple[dict[str, list[dict]], tuple[str, ...]]:
    reading_path = path
    limitations = []
    if wal_raw:
        # A caller assertion alone is insufficient: bind both frozen bytes.
        proof = declaration.get("frozen_wal") or {}
        if not proof.get("stopped") or not output_root:
            raise SnapshotError("WAL_CONSISTENT_SNAPSHOT_NOT_PROVEN")
        db_hash = sha256(raw).hexdigest()
        wal_hash = sha256(wal_raw).hexdigest()
        if proof.get("db_sha256") != db_hash or proof.get("wal_sha256") != wal_hash:
            raise SnapshotError("WAL_SNAPSHOT_HASH_MISMATCH")
        # Working copies may recover WAL; original files are never opened by SQLite.
        dest = output_root / ("sqlite_" + canonical_hash([str(path), db_hash, wal_hash])[:20])
        dest.mkdir(parents=True, exist_ok=False)
        reading_path = dest / path.name
        reading_path.write_bytes(raw)
        Path(str(reading_path) + "-wal").write_bytes(wal_raw)
        if sha256(_read_bounded(path, len(raw))).hexdigest() != db_hash or sha256(_read_bounded(wal, len(wal_raw))).hexdigest() != wal_hash:
            raise SnapshotError("SOURCE_CHANGED_DURING_SNAPSHOT")
        uri = reading_path.as_uri() + "?mode=ro"
    else:
        if not declaration.get("frozen"):
            raise SnapshotError("SQLITE_FREEZE_NOT_PROVEN")
        uri = reading_path.as_uri() + "?mode=ro&immutable=1"
    tables = declaration.get("tables", [])
    if not tables or any(not isinstance(t, str) or not t.replace("_", "").isalnum() for t in tables):
        raise SnapshotError("EXPLICIT_TABLE_ALLOWLIST_REQUIRED")
    result = {}
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        actual_schema = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        schema_hash = canonical_hash([list(r) for r in actual_schema])
        if declaration.get("schema_sha256") and declaration["schema_sha256"] != schema_hash:
            raise SnapshotError("UNSUPPORTED_SQLITE_SCHEMA")
        if not declaration.get("schema_version"):
            raise SnapshotError("SCHEMA_VERSION_REQUIRED")
        for table in tables:
            rows = db.execute('SELECT * FROM "' + table + '" LIMIT ?', (max_rows + 1,)).fetchall()
            if len(rows) > max_rows:
                limitations.append("ROW_CAP_EXCEEDED:" + table)
            result[table] = [dict(row) for row in rows[:max_rows]]
    return result, tuple(limitations)


def _npy_data(raw: bytes, max_records: int) -> dict[str, Any]:
    import numpy as np
    stream = io.BytesIO(raw)
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
    elif version == (2, 0):
        shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
    elif version == (3, 0):
        # NumPy's v3 parser uses UTF-8; the public v2 reader uses Latin-1.
        shape, _, dtype = np.lib.format._read_array_header(stream, version)
    else:
        raise SnapshotError("UNSUPPORTED_ARRAY_VERSION")
    count = 1
    for dimension in shape:
        if not isinstance(dimension, int) or dimension < 0 or dimension > np.iinfo(np.intp).max:
            raise SnapshotError("UNSAFE_OR_OVERSIZED_ARRAY")
        count *= dimension
    if dtype.hasobject or count > max_records:
        raise SnapshotError("UNSAFE_OR_OVERSIZED_ARRAY")
    payload_bytes = count * dtype.itemsize
    if payload_bytes != len(raw) - stream.tell():
        raise SnapshotError("ARRAY_PAYLOAD_SIZE_MISMATCH")
    # Load the same bounded, hashed bytes only after validating allocation size.
    array = np.load(io.BytesIO(raw), allow_pickle=False)
    return {"shape": list(array.shape), "dtype": str(array.dtype), "values": array.tolist()}


def load_source_snapshot(source_manifest: Mapping[str, Any] | str | Path,
                         allowed_roots: Iterable[str | Path], *,
                         output_root: str | Path | None = None,
                         max_bytes=32 * 1024 * 1024, max_records=100000) -> SnapshotView:
    roots = tuple(allowed_roots)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0 or not isinstance(max_records, int) or isinstance(max_records, bool) or max_records < 0:
        raise SnapshotError("INVALID_SNAPSHOT_READ_CAP")
    manifest_path, manifest_raw = None, b""
    if not isinstance(source_manifest, Mapping):
        manifest_path = safe_path(source_manifest, roots)
        manifest_raw = _read_bounded(manifest_path, max_bytes, "MANIFEST_BYTE_CAP_EXCEEDED")
        source_manifest = json.loads(manifest_raw)
    if not isinstance(source_manifest, Mapping):
        raise SnapshotError("INVALID_SOURCE_MANIFEST")
    if source_manifest.get("schema_version") != "full_chain_source_manifest_v1":
        raise SnapshotError("UNSUPPORTED_SOURCE_MANIFEST_SCHEMA")
    snapshot_id = str(source_manifest["snapshot_id"])
    sources, limitations, total_bytes = {}, [], len(manifest_raw)
    target = Path(output_root).resolve() if output_root else None
    for declaration in source_manifest.get("sources", []):
        name = declaration["name"]
        if name in sources:
            raise SnapshotError("DUPLICATE_SOURCE_NAME")
        try:
            path = safe_path(declaration["path"], roots)
            if target and (target == path.parent or path.is_relative_to(target) or target.is_relative_to(path.parent)):
                raise SnapshotError("OUTPUT_SOURCE_OVERLAP")
            size = path.stat().st_size
            if total_bytes + size > max_bytes:
                raise SnapshotError("SOURCE_BYTE_CAP_EXCEEDED")
            total_bytes += size
            raw = _read_bounded(path, size)
            if len(raw) != size:
                raise SnapshotError("SOURCE_CHANGED_DURING_READ")
            digest = sha256(raw).hexdigest()
            if declaration.get("sha256") and declaration["sha256"] != digest:
                raise SnapshotError("SOURCE_HASH_MISMATCH")
            format_name = declaration.get("format", path.suffix.lstrip("."))
            local_limits = ()
            if format_name == "json":
                data = json.loads(raw)
            elif format_name == "jsonl":
                cutoff = declaration.get("committed_bytes", len(raw))
                if not isinstance(cutoff, int) or cutoff < 0 or cutoff > len(raw) or ("committed_bytes" in declaration and cutoff and raw[cutoff-1:cutoff] != b"\n"):
                    raise SnapshotError("INVALID_COMMITTED_CUTOFF")
                data, local_limits = read_jsonl_prefix(raw[:cutoff], max_records=max_records)
                if cutoff < len(raw):
                    local_limits += ("TAIL_OUTSIDE_SNAPSHOT",)
            elif format_name == "diagnostic_jsonl":
                remaining_before_blobs=max_bytes-total_bytes+size
                rows, prefix_limits = read_jsonl_prefix(raw,max_records=max_records)
                declared_blobs = set(declaration.get('representation_files',[]))
                actual_blobs = {r['payload_ref']['relative_path'] for r in rows
                    if isinstance(r,dict) and isinstance(r.get('payload_ref'),dict)}
                if not actual_blobs <= declared_blobs:
                    raise SnapshotError('DIAGNOSTIC_REPRESENTATION_NOT_DECLARED')
                for blob in declared_blobs:
                    blob_path=safe_path(path.parent/blob,roots)
                    total_bytes+=blob_path.stat().st_size
                    if total_bytes>max_bytes:
                        raise SnapshotError('SOURCE_BYTE_CAP_EXCEEDED')
                from .domains.runtime_reliability import read_diagnostic_prefix
                prefix = read_diagnostic_prefix(path,roots,max_bytes=remaining_before_blobs,
                    max_records=max_records, allowed_representation_files=declared_blobs,
                    expected_session_id=declaration.get('session_id'))
                data = prefix['events'] + ([prefix['seal']] if prefix['seal'] else [])
                local_limits = tuple(dict.fromkeys((*prefix_limits, *prefix['limitations'])))
            elif format_name in ("sqlite", "db"):
                wal=Path(str(path)+'-wal')
                wal_raw = None
                if wal.exists() or wal.is_symlink():
                    wal=safe_path(wal,roots)
                    wal_size=wal.stat().st_size
                    total_bytes+=wal_size
                    if total_bytes>max_bytes: raise SnapshotError('SOURCE_BYTE_CAP_EXCEEDED')
                    wal_raw = _read_bounded(wal, wal_size)
                    if len(wal_raw) != wal_size:
                        raise SnapshotError('SOURCE_CHANGED_DURING_READ')
                else:
                    wal = None
                data, local_limits = _sqlite_rows(path, declaration, target, max_records,raw,wal,wal_raw)
            elif format_name == "npy":
                data = _npy_data(raw, max_records)
            else:
                raise SnapshotError("UNSUPPORTED_SOURCE_FORMAT")
            if sha256(_read_bounded(path, size)).hexdigest() != digest:
                raise SnapshotError("SOURCE_CHANGED_DURING_READ")
            evidence = EvaluationEvidenceRef(str(path), declaration.get("owner", "unknown"),
                declaration.get("family", name), declaration.get("schema_version", "unknown"),
                raw_sha256=digest, snapshot_id=snapshot_id, integrity="VALID",
                completeness="PARTIAL" if local_limits else "COMPLETE", as_of=source_manifest.get("as_of"))
            sources[name] = LoadedSource(name, evidence.family, data, evidence, local_limits)
            limitations.extend(name + ":" + value for value in local_limits)
        except (OSError, ValueError, sqlite3.Error) as error:
            limitations.append(name + ":" + str(error))
            sources[name] = LoadedSource(name, declaration.get("family", name), None,
                EvaluationEvidenceRef(declaration["path"], family=declaration.get("family", name),
                    snapshot_id=snapshot_id, access="UNREADABLE", integrity="UNKNOWN", completeness="UNKNOWN"), (str(error),))
    if manifest_path and _read_bounded(manifest_path, len(manifest_raw), "MANIFEST_BYTE_CAP_EXCEEDED") != manifest_raw:
        raise SnapshotError("SOURCE_MANIFEST_CHANGED_DURING_READ")
    return SnapshotView(snapshot_id, sources, dict(source_manifest), tuple(limitations))
