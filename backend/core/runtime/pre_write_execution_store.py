"""Durable technical execution control for Gate V2 pre-write operations.

The dedicated SQLite store owns only:

* one physical in-flight owner for an already-authorized Recovery occurrence;
* Writer logical execution at-most-once dispatch intent and normalized result
  durability.

It does not calculate Admission, Recovery eligibility/round counters/progress,
Route, Fallback, or Evidence truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Protocol

from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterExecutionResultSchema,
    WriterExecutionStatus,
    WriterGenerationInvocationRefSchema,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from core.hashing import canonical_sha256
from core.runtime.ids import IdGenerator, UuidIdGenerator


EXECUTION_CONTROL_DATABASE_FILENAME = "execution_control.sqlite3"
EXECUTION_STORE_SCHEMA_VERSION_V1 = "gate_v2_pre_write_execution_store_v1"
EXECUTION_STORE_SCHEMA_VERSION = "gate_v2_pre_write_execution_store_v2"


class PreWriteExecutionIntegrityError(ValueError):
    """Stable logical identity was presented with conflicting semantics."""


_METADATA_TABLE = "pre_write_execution_metadata"
_RECOVERY_TABLE = "recovery_dispatches"
_WRITER_TABLE = "writer_executions"
_ROUTE_TABLE = "pre_write_route_decisions"

_RECOVERY_TABLE_SQL = (
    "CREATE TABLE recovery_dispatches ("
    "occurrence_id TEXT PRIMARY KEY, "
    "semantic_fingerprint TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status IN ('in_progress', 'completed')), "
    "owner_id TEXT, "
    "claim_token TEXT, "
    "lease_expires_at REAL, "
    "result_ref TEXT, "
    "release_reason TEXT, "
    "created_at_epoch REAL NOT NULL, "
    "updated_at_epoch REAL NOT NULL)"
)
_WRITER_TABLE_SQL = (
    "CREATE TABLE writer_executions ("
    "logical_execution_key TEXT PRIMARY KEY, "
    "semantic_fingerprint TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN "
    "('RESERVED', 'DISPATCHED', 'SUCCEEDED', 'CONFIRMED_FAILED')), "
    "result_json TEXT, "
    "created_at_epoch REAL NOT NULL, "
    "updated_at_epoch REAL NOT NULL)"
)
_ROUTE_TABLE_SQL = (
    "CREATE TABLE pre_write_route_decisions ("
    "route_decision_id TEXT PRIMARY KEY, "
    "schema_version TEXT NOT NULL, "
    "contract_version TEXT NOT NULL, "
    "decision_json TEXT NOT NULL, "
    "decision_sha256 TEXT NOT NULL, "
    "authorization_snapshot_sha256 TEXT NOT NULL, "
    "created_at_epoch REAL NOT NULL)"
)

_EXPECTED_TABLE_SHAPES = {
    _RECOVERY_TABLE: (
        ("occurrence_id", 1, False),
        ("semantic_fingerprint", 0, True),
        ("status", 0, True),
        ("owner_id", 0, False),
        ("claim_token", 0, False),
        ("lease_expires_at", 0, False),
        ("result_ref", 0, False),
        ("release_reason", 0, False),
        ("created_at_epoch", 0, True),
        ("updated_at_epoch", 0, True),
    ),
    _WRITER_TABLE: (
        ("logical_execution_key", 1, False),
        ("semantic_fingerprint", 0, True),
        ("state", 0, True),
        ("result_json", 0, False),
        ("created_at_epoch", 0, True),
        ("updated_at_epoch", 0, True),
    ),
    _ROUTE_TABLE: (
        ("route_decision_id", 1, False),
        ("schema_version", 0, True),
        ("contract_version", 0, True),
        ("decision_json", 0, True),
        ("decision_sha256", 0, True),
        ("authorization_snapshot_sha256", 0, True),
        ("created_at_epoch", 0, True),
    ),
}


def _verify_table_shape(connection: sqlite3.Connection, table_name: str) -> None:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    expected = _EXPECTED_TABLE_SHAPES[table_name]
    actual_names = tuple(str(row[1]) for row in rows)
    expected_names = tuple(item[0] for item in expected)
    if actual_names != expected_names:
        raise ValueError(
            f"malformed {table_name} columns: {actual_names!r}; expected {expected_names!r}"
        )
    real_columns = {
        "lease_expires_at",
        "created_at_epoch",
        "updated_at_epoch",
    }
    for row, (column_name, expected_pk, expected_not_null) in zip(rows, expected):
        expected_type = "REAL" if column_name in real_columns else "TEXT"
        if str(row[2]).upper() != expected_type:
            raise ValueError(
                f"malformed {table_name}.{column_name} declared type: {row[2]!r}"
            )
        if int(row[5]) != expected_pk:
            raise ValueError(f"malformed {table_name}.{column_name} primary-key shape")
        if expected_not_null and int(row[3]) != 1:
            raise ValueError(f"malformed {table_name}.{column_name} NOT NULL shape")


def _establish_wal(
    connection: sqlite3.Connection,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            mode = "" if row is None else str(row[0]).lower()
            if mode != "wal":
                raise ValueError(
                    f"execution_control.sqlite3 failed to establish WAL mode: {mode!r}"
                )
            return
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if (
                ("locked" not in message and "busy" not in message)
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.01)


def ensure_pre_write_execution_schema_v2(
    database_path: str | Path,
    connect_timeout_seconds: float = 5.0,
) -> None:
    """Atomically establish or migrate the single execution-control DB to V2."""

    path = Path(database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(path), timeout=connect_timeout_seconds, check_same_thread=False
    )
    try:
        connection.execute(
            f"PRAGMA busy_timeout = {int(max(0.0, connect_timeout_seconds) * 1000)}"
        )
        _establish_wal(
            connection,
            timeout_seconds=connect_timeout_seconds,
        )
        connection.execute("BEGIN IMMEDIATE")
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pre_write_execution_metadata ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        version_row = connection.execute(
            "SELECT value FROM pre_write_execution_metadata WHERE key = 'schema_version'"
        ).fetchone()
        version = None if version_row is None else str(version_row[0])
        authority_tables = {_RECOVERY_TABLE, _WRITER_TABLE, _ROUTE_TABLE}

        if version is None:
            if existing_tables & authority_tables:
                raise ValueError(
                    "unversioned execution_control.sqlite3 contains authority tables"
                )
            connection.execute(_RECOVERY_TABLE_SQL)
            connection.execute(_WRITER_TABLE_SQL)
            connection.execute(_ROUTE_TABLE_SQL)
            connection.execute(
                "INSERT INTO pre_write_execution_metadata(key, value) VALUES (?, ?)",
                ("schema_version", EXECUTION_STORE_SCHEMA_VERSION),
            )
        elif version == EXECUTION_STORE_SCHEMA_VERSION_V1:
            if _ROUTE_TABLE in existing_tables:
                raise ValueError(
                    "V1 execution_control.sqlite3 already contains pre_write_route_decisions"
                )
            for table_name in (_RECOVERY_TABLE, _WRITER_TABLE):
                if table_name not in existing_tables:
                    raise ValueError(f"V1 execution_control.sqlite3 missing {table_name}")
                _verify_table_shape(connection, table_name)
            connection.execute(_ROUTE_TABLE_SQL)
            connection.execute(
                "UPDATE pre_write_execution_metadata SET value = ? "
                "WHERE key = 'schema_version' AND value = ?",
                (EXECUTION_STORE_SCHEMA_VERSION, EXECUTION_STORE_SCHEMA_VERSION_V1),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("execution-control V1→V2 version update did not commit exactly once")
        elif version == EXECUTION_STORE_SCHEMA_VERSION:
            for table_name in (_RECOVERY_TABLE, _WRITER_TABLE, _ROUTE_TABLE):
                if table_name not in existing_tables:
                    raise ValueError(f"V2 execution_control.sqlite3 missing {table_name}")
                _verify_table_shape(connection, table_name)
        else:
            raise ValueError(
                "unsupported execution_control.sqlite3 schema version: " f"{version!r}"
            )

        for table_name in (_RECOVERY_TABLE, _WRITER_TABLE, _ROUTE_TABLE):
            _verify_table_shape(connection, table_name)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_dispatch_status_lease "
            "ON recovery_dispatches(status, lease_expires_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_execution_state "
            "ON writer_executions(state)"
        )
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


class RecoveryDispatchStatus(str, Enum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class WriterExecutionState(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"


class WriterRestoreCondition(str, Enum):
    NEVER_DISPATCHED = "NEVER_DISPATCHED"
    INDETERMINATE_EXTERNAL_EXECUTION = "INDETERMINATE_EXTERNAL_EXECUTION"
    SUCCEEDED = "SUCCEEDED"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"


@dataclass(frozen=True)
class RecoveryDispatchClaim:
    """Technical ownership result for one already-reserved Recovery occurrence."""

    status: RecoveryDispatchStatus
    occurrence_id: str
    semantic_fingerprint: str
    owner_id: str
    claim_token: str | None = None
    result_ref: str | None = None

    @property
    def acquired(self) -> bool:
        return self.status is RecoveryDispatchStatus.ACQUIRED


@dataclass(frozen=True)
class RecoveryDispatchRecord:
    """Read-only physical evidence; never Recovery semantic authorization."""

    occurrence_id: str
    semantic_fingerprint: str
    status: str
    owner_id: str | None
    claim_token: str | None
    lease_expires_at: float | None
    result_ref: str | None
    release_reason: str | None
    created_at_epoch: float
    updated_at_epoch: float


@dataclass(frozen=True)
class WriterExecutionRecord:
    """Durable technical state for one frozen WriterLogicalExecutionKey."""

    logical_execution_key: WriterLogicalExecutionKey
    semantic_fingerprint: str
    state: WriterExecutionState
    result: WriterExecutionResultSchema | None
    created_at_epoch: float
    updated_at_epoch: float

    @property
    def restore_condition(self) -> WriterRestoreCondition:
        if self.state is WriterExecutionState.RESERVED:
            return WriterRestoreCondition.NEVER_DISPATCHED
        if self.state is WriterExecutionState.DISPATCHED:
            return WriterRestoreCondition.INDETERMINATE_EXTERNAL_EXECUTION
        if self.state is WriterExecutionState.SUCCEEDED:
            return WriterRestoreCondition.SUCCEEDED
        return WriterRestoreCondition.CONFIRMED_FAILED


class PreWriteExecutionStore(Protocol):
    """Narrow technical persistence port consumed by future P3 services."""

    def resolve_recovery_dispatch(
        self, occurrence_id: str,
    ) -> RecoveryDispatchRecord | None: ...

    def claim_recovery_dispatch(
        self,
        occurrence_id: str,
        *,
        semantic_fingerprint: str,
        owner_id: str,
    ) -> RecoveryDispatchClaim: ...

    def renew_recovery_claim(self, claim: RecoveryDispatchClaim) -> bool: ...

    def complete_recovery_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        result_ref: str | None = None,
    ) -> bool: ...

    def release_recovery_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        reason: str,
    ) -> bool: ...

    def reserve_writer_execution(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
    ) -> WriterExecutionRecord: ...

    def mark_writer_dispatched(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
    ) -> bool: ...

    def persist_writer_result(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
        result: WriterExecutionResultSchema,
    ) -> WriterExecutionRecord: ...

    def get_writer_execution(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
    ) -> WriterExecutionRecord | None: ...

    def resolve_writer_execution(
        self,
        ref: WriterExecutionRefSchema,
    ) -> WriterExecutionResultSchema: ...

    def resolve_writer_generation_invocation(
        self,
        ref: WriterGenerationInvocationRefSchema,
    ) -> WriterGenerationInvocationSchema: ...


class SQLitePreWriteExecutionStore:
    """Transactional SQLite implementation of the frozen execution boundary."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        recovery_claim_lease_seconds: float = 30.0,
        connect_timeout_seconds: float = 5.0,
        id_generator: IdGenerator | None = None,
        run_commit_guard=None,
        require_run_commit_guard: bool = False,
    ) -> None:
        if recovery_claim_lease_seconds <= 0:
            raise ValueError("recovery_claim_lease_seconds must be > 0")
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be >= 0")
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.recovery_claim_lease_seconds = float(recovery_claim_lease_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.id_generator = id_generator or UuidIdGenerator()
        if require_run_commit_guard and not callable(run_commit_guard):
            raise ValueError("Target Writer execution requires a Run commit guard")
        self.run_commit_guard = run_commit_guard
        self.require_run_commit_guard = require_run_commit_guard
        self._initialize_schema()

    @classmethod
    def for_data_root(
        cls,
        data_root: str | Path,
        **kwargs: Any,
    ) -> "SQLitePreWriteExecutionStore":
        """Construct the non-activating Target store at ``DATA_ROOT/runs``."""

        return cls(
            Path(data_root) / "runs" / EXECUTION_CONTROL_DATABASE_FILENAME,
            **kwargs,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.connect_timeout_seconds,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        ensure_pre_write_execution_schema_v2(
            self.database_path,
            self.connect_timeout_seconds,
        )

    def resolve_recovery_dispatch(
        self, occurrence_id: str,
    ) -> RecoveryDispatchRecord | None:
        """Read the exact physical row without claiming, renewing, or repairing it."""

        occurrence = self._required_text(occurrence_id, field_name="occurrence_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM recovery_dispatches WHERE occurrence_id = ?",
                (occurrence,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None

        def text_field(name: str, *, optional: bool = False) -> str | None:
            value = row[name]
            if value is None and optional:
                return None
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise PreWriteExecutionIntegrityError(f"malformed Recovery {name}")
            return value

        def epoch(name: str, *, optional: bool = False) -> float | None:
            value = row[name]
            if value is None and optional:
                return None
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise PreWriteExecutionIntegrityError(f"malformed Recovery {name}")
            return float(value)

        status = text_field("status")
        owner = text_field("owner_id", optional=True)
        token = text_field("claim_token", optional=True)
        lease = epoch("lease_expires_at", optional=True)
        result = row["result_ref"]
        # Existing complete permits an empty result_ref as well as None.
        if result is not None and not isinstance(result, str):
            raise PreWriteExecutionIntegrityError("malformed Recovery result_ref")
        reason = text_field("release_reason", optional=True)
        if status == "completed":
            if owner is not None or token is not None or lease is not None:
                raise PreWriteExecutionIntegrityError("completed Recovery row retains ownership")
        elif status == "in_progress":
            if result is not None or lease is None or (owner is None) != (token is None):
                raise PreWriteExecutionIntegrityError("malformed in-progress Recovery row")
            if owner is None and (lease != 0 or reason is None):
                raise PreWriteExecutionIntegrityError("malformed released Recovery row")
        else:
            raise PreWriteExecutionIntegrityError("malformed Recovery status")
        return RecoveryDispatchRecord(
            occurrence_id=text_field("occurrence_id"),
            semantic_fingerprint=text_field("semantic_fingerprint"),
            status=status,
            owner_id=owner,
            claim_token=token,
            lease_expires_at=lease,
            result_ref=result,
            release_reason=reason,
            created_at_epoch=epoch("created_at_epoch"),
            updated_at_epoch=epoch("updated_at_epoch"),
        )

    @staticmethod
    def _required_text(value: str, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} must be non-empty")
        return normalized

    @classmethod
    def _fingerprint(cls, value: str) -> str:
        return cls._required_text(value, field_name="semantic_fingerprint")

    @staticmethod
    def _writer_key_json(logical_execution_key: WriterLogicalExecutionKey) -> str:
        key = WriterLogicalExecutionKey.model_validate(logical_execution_key)
        return json.dumps(
            key.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _assert_fingerprint(
        *,
        logical_id: str,
        existing: str,
        incoming: str,
    ) -> None:
        if existing == incoming:
            return
        raise PreWriteExecutionIntegrityError(
            "pre-write execution identity collision with different semantic "
            f"fingerprint: {logical_id}"
        )

    def _new_claim_token(self) -> str:
        return self.id_generator.new_id("recovery_claim")

    def claim_recovery_dispatch(
        self,
        occurrence_id: str,
        *,
        semantic_fingerprint: str,
        owner_id: str,
    ) -> RecoveryDispatchClaim:
        occurrence_id = self._required_text(occurrence_id, field_name="occurrence_id")
        owner_id = self._required_text(owner_id, field_name="owner_id")
        fingerprint = self._fingerprint(semantic_fingerprint)
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_dispatches WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
            if row is not None:
                self._assert_fingerprint(
                    logical_id=occurrence_id,
                    existing=str(row["semantic_fingerprint"]),
                    incoming=fingerprint,
                )
                if row["status"] == "completed":
                    connection.commit()
                    return RecoveryDispatchClaim(
                        status=RecoveryDispatchStatus.COMPLETED,
                        occurrence_id=occurrence_id,
                        semantic_fingerprint=fingerprint,
                        owner_id=owner_id,
                        result_ref=row["result_ref"],
                    )
                lease_expires_at = row["lease_expires_at"]
                if lease_expires_at is not None and float(lease_expires_at) > now:
                    connection.commit()
                    return RecoveryDispatchClaim(
                        status=RecoveryDispatchStatus.IN_PROGRESS,
                        occurrence_id=occurrence_id,
                        semantic_fingerprint=fingerprint,
                        owner_id=owner_id,
                    )
                # Recovery is a read/retrieval execution. An expired technical
                # ownership lease may reclaim this *same* occurrence only.
                token = self._new_claim_token()
                connection.execute(
                    "UPDATE recovery_dispatches SET owner_id = ?, claim_token = ?, "
                    "lease_expires_at = ?, release_reason = NULL, updated_at_epoch = ? "
                    "WHERE occurrence_id = ?",
                    (
                        owner_id,
                        token,
                        now + self.recovery_claim_lease_seconds,
                        now,
                        occurrence_id,
                    ),
                )
                connection.commit()
                return RecoveryDispatchClaim(
                    status=RecoveryDispatchStatus.ACQUIRED,
                    occurrence_id=occurrence_id,
                    semantic_fingerprint=fingerprint,
                    owner_id=owner_id,
                    claim_token=token,
                )

            token = self._new_claim_token()
            connection.execute(
                "INSERT INTO recovery_dispatches("
                "occurrence_id, semantic_fingerprint, status, owner_id, claim_token, "
                "lease_expires_at, result_ref, release_reason, created_at_epoch, "
                "updated_at_epoch) VALUES (?, ?, 'in_progress', ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    occurrence_id,
                    fingerprint,
                    owner_id,
                    token,
                    now + self.recovery_claim_lease_seconds,
                    now,
                    now,
                ),
            )
            connection.commit()
            return RecoveryDispatchClaim(
                status=RecoveryDispatchStatus.ACQUIRED,
                occurrence_id=occurrence_id,
                semantic_fingerprint=fingerprint,
                owner_id=owner_id,
                claim_token=token,
            )
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _assert_active_recovery_owner(
        self,
        row: sqlite3.Row | None,
        claim: RecoveryDispatchClaim,
    ) -> None:
        if not claim.acquired or not claim.claim_token:
            raise ValueError("Recovery operation requires an acquired claim")
        if row is None or row["status"] != "in_progress":
            raise ValueError("Recovery claim is no longer in progress")
        self._assert_fingerprint(
            logical_id=claim.occurrence_id,
            existing=str(row["semantic_fingerprint"]),
            incoming=claim.semantic_fingerprint,
        )
        if row["claim_token"] != claim.claim_token or row["owner_id"] != claim.owner_id:
            raise ValueError("Recovery claim is not the active physical owner")

    def renew_recovery_claim(self, claim: RecoveryDispatchClaim) -> bool:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_dispatches WHERE occurrence_id = ?",
                (claim.occurrence_id,),
            ).fetchone()
            self._assert_active_recovery_owner(row, claim)
            connection.execute(
                "UPDATE recovery_dispatches SET lease_expires_at = ?, updated_at_epoch = ? "
                "WHERE occurrence_id = ? AND claim_token = ?",
                (
                    now + self.recovery_claim_lease_seconds,
                    now,
                    claim.occurrence_id,
                    claim.claim_token,
                ),
            )
            connection.commit()
            return True
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def complete_recovery_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        result_ref: str | None = None,
    ) -> bool:
        normalized_result_ref = str(result_ref).strip() if result_ref is not None else None
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_dispatches WHERE occurrence_id = ?",
                (claim.occurrence_id,),
            ).fetchone()
            if row is not None and row["status"] == "completed":
                self._assert_fingerprint(
                    logical_id=claim.occurrence_id,
                    existing=str(row["semantic_fingerprint"]),
                    incoming=claim.semantic_fingerprint,
                )
                if row["result_ref"] != normalized_result_ref:
                    raise PreWriteExecutionIntegrityError(
                        "completed Recovery occurrence cannot change its result_ref"
                    )
                connection.commit()
                return False
            self._assert_active_recovery_owner(row, claim)
            connection.execute(
                "UPDATE recovery_dispatches SET status = 'completed', owner_id = NULL, "
                "claim_token = NULL, lease_expires_at = NULL, result_ref = ?, "
                "updated_at_epoch = ? WHERE occurrence_id = ?",
                (normalized_result_ref, now, claim.occurrence_id),
            )
            connection.commit()
            return True
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def release_recovery_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        reason: str,
    ) -> bool:
        reason = self._required_text(reason, field_name="reason")
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_dispatches WHERE occurrence_id = ?",
                (claim.occurrence_id,),
            ).fetchone()
            if row is None or row["status"] == "completed":
                connection.commit()
                return False
            self._assert_active_recovery_owner(row, claim)
            # Preserve identity/fingerprint while making only this same
            # occurrence safely reclaimable. No business round/counter exists here.
            connection.execute(
                "UPDATE recovery_dispatches SET owner_id = NULL, claim_token = NULL, "
                "lease_expires_at = 0, release_reason = ?, updated_at_epoch = ? "
                "WHERE occurrence_id = ?",
                (reason, now, claim.occurrence_id),
            )
            connection.commit()
            return True
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_writer_record(row: sqlite3.Row) -> WriterExecutionRecord:
        key = WriterLogicalExecutionKey.model_validate_json(row["logical_execution_key"])
        result_json = row["result_json"]
        result = (
            WriterExecutionResultSchema.model_validate_json(result_json)
            if result_json is not None
            else None
        )
        state = WriterExecutionState(str(row["state"]))
        if state in {WriterExecutionState.SUCCEEDED, WriterExecutionState.CONFIRMED_FAILED}:
            if result is None:
                raise ValueError("terminal Writer execution row is missing durable result")
        elif result is not None:
            raise ValueError("non-terminal Writer execution row cannot contain result")
        return WriterExecutionRecord(
            logical_execution_key=key,
            semantic_fingerprint=str(row["semantic_fingerprint"]),
            state=state,
            result=result,
            created_at_epoch=float(row["created_at_epoch"]),
            updated_at_epoch=float(row["updated_at_epoch"]),
        )

    def reserve_writer_execution(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
    ) -> WriterExecutionRecord:
        key_json = self._writer_key_json(logical_execution_key)
        fingerprint = self._fingerprint(semantic_fingerprint)
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.run_commit_guard is not None:
                self.run_commit_guard(logical_execution_key.writer_subject_ref.run_id)
            row = connection.execute(
                "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                (key_json,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO writer_executions("
                    "logical_execution_key, semantic_fingerprint, state, result_json, "
                    "created_at_epoch, updated_at_epoch) VALUES (?, ?, 'RESERVED', NULL, ?, ?)",
                    (key_json, fingerprint, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                    (key_json,),
                ).fetchone()
            else:
                self._assert_fingerprint(
                    logical_id=key_json,
                    existing=str(row["semantic_fingerprint"]),
                    incoming=fingerprint,
                )
            connection.commit()
            assert row is not None
            return self._row_to_writer_record(row)
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def mark_writer_dispatched(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
    ) -> bool:
        """Persist irreversible dispatch intent; True alone permits first provider send."""

        key_json = self._writer_key_json(logical_execution_key)
        fingerprint = self._fingerprint(semantic_fingerprint)
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.run_commit_guard is not None:
                self.run_commit_guard(logical_execution_key.writer_subject_ref.run_id)
            row = connection.execute(
                "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                (key_json,),
            ).fetchone()
            if row is None:
                raise ValueError("Writer execution must be RESERVED before DISPATCHED")
            self._assert_fingerprint(
                logical_id=key_json,
                existing=str(row["semantic_fingerprint"]),
                incoming=fingerprint,
            )
            if row["state"] != WriterExecutionState.RESERVED.value:
                connection.commit()
                return False
            connection.execute(
                "UPDATE writer_executions SET state = 'DISPATCHED', updated_at_epoch = ? "
                "WHERE logical_execution_key = ? AND state = 'RESERVED'",
                (now, key_json),
            )
            changed = connection.execute("SELECT changes()").fetchone()[0]
            connection.commit()
            return int(changed) == 1
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def persist_writer_result(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
        *,
        semantic_fingerprint: str,
        result: WriterExecutionResultSchema,
    ) -> WriterExecutionRecord:
        key = WriterLogicalExecutionKey.model_validate(logical_execution_key)
        normalized_result = WriterExecutionResultSchema.model_validate(result)
        if normalized_result.logical_execution_key != key:
            raise PreWriteExecutionIntegrityError(
                "Writer result logical_execution_key does not match execution row"
            )
        key_json = self._writer_key_json(key)
        fingerprint = self._fingerprint(semantic_fingerprint)
        result_json = json.dumps(
            normalized_result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        target_state = (
            WriterExecutionState.SUCCEEDED
            if normalized_result.status is WriterExecutionStatus.SUCCESS
            else WriterExecutionState.CONFIRMED_FAILED
        )
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                (key_json,),
            ).fetchone()
            if row is None:
                raise ValueError("Writer execution row does not exist")
            self._assert_fingerprint(
                logical_id=key_json,
                existing=str(row["semantic_fingerprint"]),
                incoming=fingerprint,
            )
            if row["state"] in {
                WriterExecutionState.SUCCEEDED.value,
                WriterExecutionState.CONFIRMED_FAILED.value,
            }:
                if row["result_json"] != result_json or row["state"] != target_state.value:
                    raise PreWriteExecutionIntegrityError(
                        "terminal Writer execution cannot change durable result"
                    )
                connection.commit()
                return self._row_to_writer_record(row)
            if row["state"] != WriterExecutionState.DISPATCHED.value:
                raise ValueError("Writer result requires durable DISPATCHED intent first")

            connection.execute(
                "UPDATE writer_executions SET state = ?, result_json = ?, "
                "updated_at_epoch = ? WHERE logical_execution_key = ?",
                (target_state.value, result_json, now, key_json),
            )
            row = connection.execute(
                "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                (key_json,),
            ).fetchone()
            connection.commit()
            assert row is not None
            return self._row_to_writer_record(row)
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def get_writer_execution(
        self,
        logical_execution_key: WriterLogicalExecutionKey,
    ) -> WriterExecutionRecord | None:
        key_json = self._writer_key_json(logical_execution_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM writer_executions WHERE logical_execution_key = ?",
                (key_json,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_writer_record(row)

    def resolve_writer_execution(
        self,
        ref: WriterExecutionRefSchema,
    ) -> WriterExecutionResultSchema:
        normalized_ref = WriterExecutionRefSchema.model_validate(ref)
        record = self.get_writer_execution(normalized_ref.logical_execution_key)
        if record is None:
            raise PreWriteExecutionIntegrityError("Writer execution ref does not resolve")
        if record.state is not WriterExecutionState.SUCCEEDED or record.result is None:
            raise PreWriteExecutionIntegrityError(
                "Writer execution ref requires exact SUCCEEDED durable result"
            )
        result = record.result
        if result.status is not WriterExecutionStatus.SUCCESS:
            raise PreWriteExecutionIntegrityError(
                "Writer execution ref resolved to non-success result"
            )
        if result.logical_execution_key != normalized_ref.logical_execution_key:
            raise PreWriteExecutionIntegrityError(
                "Writer execution ref logical key does not match durable result"
            )
        payload_hash = canonical_sha256(result.model_dump(mode="json"))
        if payload_hash != normalized_ref.result_sha256:
            raise PreWriteExecutionIntegrityError(
                "Writer execution ref result_sha256 does not match durable result"
            )
        return result

    def resolve_writer_generation_invocation(
        self,
        ref: WriterGenerationInvocationRefSchema,
    ) -> WriterGenerationInvocationSchema:
        normalized_ref = WriterGenerationInvocationRefSchema.model_validate(ref)
        record = self.get_writer_execution(normalized_ref.logical_execution_key)
        if record is None:
            raise PreWriteExecutionIntegrityError(
                "Writer generation invocation ref does not resolve"
            )
        if record.state is not WriterExecutionState.SUCCEEDED or record.result is None:
            raise PreWriteExecutionIntegrityError(
                "Writer generation invocation ref requires SUCCEEDED durable result"
            )
        if record.semantic_fingerprint != normalized_ref.invocation_sha256:
            raise PreWriteExecutionIntegrityError(
                "Writer generation invocation ref hash does not match execution fingerprint"
            )
        # WriterExecutionResult owns recursively frozen JSON snapshots. Resolve
        # embedded invocation bytes through its canonical serializer rather than
        # feeding private frozen containers back into Pydantic as raw input.
        result_payload = record.result.model_dump(mode="json")
        artifact_payload = result_payload.get("artifact")
        artifact_input = (
            artifact_payload.get("input")
            if isinstance(artifact_payload, Mapping)
            else None
        )
        if not isinstance(artifact_input, Mapping):
            raise PreWriteExecutionIntegrityError(
                "successful Writer result has no exact invocation-bearing artifact input"
            )
        raw_invocation = artifact_input.get("writer_generation_invocation")
        if raw_invocation is None:
            raise PreWriteExecutionIntegrityError(
                "legacy Writer success is missing exact writer_generation_invocation"
            )
        invocation = WriterGenerationInvocationSchema.model_validate(raw_invocation)
        if invocation.writer_subject_ref != normalized_ref.logical_execution_key.writer_subject_ref:
            raise PreWriteExecutionIntegrityError(
                "Writer invocation ref subject does not match logical execution key"
            )
        if invocation.write_instance_id != normalized_ref.logical_execution_key.write_instance_id:
            raise PreWriteExecutionIntegrityError(
                "Writer invocation ref write_instance_id does not match logical execution key"
            )
        if invocation.route_decision_ref != normalized_ref.logical_execution_key.route_decision_ref:
            raise PreWriteExecutionIntegrityError(
                "Writer invocation ref route does not match logical execution key"
            )
        invocation_hash = canonical_sha256(invocation.model_dump(mode="json"))
        if invocation_hash != normalized_ref.invocation_sha256:
            raise PreWriteExecutionIntegrityError(
                "Writer generation invocation ref hash does not match embedded invocation"
            )
        return invocation


__all__ = [
    "EXECUTION_CONTROL_DATABASE_FILENAME",
    "EXECUTION_STORE_SCHEMA_VERSION_V1",
    "EXECUTION_STORE_SCHEMA_VERSION",
    "ensure_pre_write_execution_schema_v2",
    "PreWriteExecutionIntegrityError",
    "PreWriteExecutionStore",
    "RecoveryDispatchClaim",
    "RecoveryDispatchRecord",
    "RecoveryDispatchStatus",
    "SQLitePreWriteExecutionStore",
    "WriterExecutionRecord",
    "WriterExecutionState",
    "WriterRestoreCondition",
]
