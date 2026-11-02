"""SQLite physical persistence foundation for Target Post-write.

This module owns database initialization, schema-shape integrity, transaction
boundaries, structural Artifact/Requirement/Entry stores, Validation persistence,
and immutable Gate/Control policy-decision persistence. CorrectionAttempt rows in
this module are observability projections only; semantic repair authority and hard
repair Budget remain outside Post-write persistence.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import json
import sqlite3
import time
from core.diagnostics import current_diagnostics
from typing import Callable, Iterator

from pydantic import TypeAdapter

from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestRefSchema,
    AssemblyInputManifestSchema,
    DocumentEvidenceSnapshotProjectionSchema,
    DocumentEvidenceSnapshotRefSchema,
)
from apps.enterprise_document.schemas.post_write.entry import (
    PostWriteEntrySchema,
    entry_ref_from_identity,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRecordSchema,
    ArtifactStateRefSchema,
    CorrectionPolicyRefSchema,
    DeliveryGateDecisionRefSchema,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementSnapshotSchema,
    RequirementRefSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    InputEnvelopeSchema,
    QualityDeliveryContractRefSchema,
    StagePlanSchema,
    ValidationCompletionRefSchema,
    ValidationCompletionSchema,
    ValidationExecutionEventSchema,
    ValidationFactRefSchema,
    ValidationFactSchema,
    ValidationIntermediateCommittedPayloadSchema,
    ValidationIntermediateRefSchema,
    ValidationRunSchema,
    ValidationRunState,
    ValidatorExecutionRecordSchema,
)
from apps.enterprise_document.schemas.post_write.gate import (
    DeliveryGateDecisionSchema,
    QualityDeliveryContractSchema,
)
from apps.enterprise_document.schemas.post_write.control import (
    CorrectionAttemptRecordSchema,
    CorrectionPolicySchema,
    PostWriteControlDecisionSchema,
)
from core.hashing import canonical_sha256


POST_WRITE_SCHEMA_VERSION = "gate_v2_post_write_store_v4"
PREVIOUS_POST_WRITE_SCHEMA_VERSION = "gate_v2_post_write_store_v3"
POST_WRITE_V2_SCHEMA_VERSION = "gate_v2_post_write_store_v2"
LEGACY_POST_WRITE_SCHEMA_VERSION = "gate_v2_post_write_store_v1"

_SQLITE_BUSY_PRIMARY_CODE = 5
_SQLITE_LOCKED_PRIMARY_CODE = 6

_METADATA_DDL = (
    "CREATE TABLE post_write_metadata ("
    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
)


class PostWritePersistenceIntegrityError(RuntimeError):
    """The Post-write database metadata or required physical shape is invalid."""


class PostWriteConcurrencyError(RuntimeError):
    """A caller-owned Post-write write/CAS operation lost its concurrency race."""


class PostWriteReleaseCommitFence:
    """Physical BEGIN IMMEDIATE fence for NEW Target publication linearization."""

    def __init__(self, database: "PostWriteSqliteDatabase") -> None:
        self.database = database

    @contextmanager
    def hold(self) -> Iterator[sqlite3.Connection]:
        with self.database.write_transaction(immediate=True) as connection:
            yield connection


_TABLE_DDL: dict[str, str] = {
    "artifact_states": """
        CREATE TABLE artifact_states (
            artifact_id TEXT NOT NULL,
            artifact_version INTEGER NOT NULL,
            artifact_hash TEXT NOT NULL,
            artifact_scope TEXT NOT NULL,
            parent_artifact_id TEXT,
            parent_artifact_version INTEGER,
            parent_artifact_hash TEXT,
            canonical_payload_ref_json TEXT NOT NULL,
            rendered_output_ref_json TEXT,
            origin_json TEXT NOT NULL,
            record_json TEXT NOT NULL,
            record_hash TEXT NOT NULL,
            created_at_epoch REAL NOT NULL,
            PRIMARY KEY (artifact_id, artifact_version)
        )
    """,
    "requirement_snapshots": """
        CREATE TABLE requirement_snapshots (
            requirement_snapshot_id TEXT PRIMARY KEY,
            requirement_schema_version TEXT NOT NULL,
            requirement_semantic_contract_version TEXT NOT NULL,
            requirement_hash TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "quality_delivery_contracts": """
        CREATE TABLE quality_delivery_contracts (
            contract_id TEXT NOT NULL,
            contract_schema_version TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL,
            PRIMARY KEY (contract_id, contract_version)
        )
    """,
    "post_write_entries": """
        CREATE TABLE post_write_entries (
            entry_id TEXT PRIMARY KEY,
            entry_schema_version TEXT NOT NULL,
            entry_contract_version TEXT NOT NULL,
            entry_scope TEXT NOT NULL,
            entry_json TEXT NOT NULL,
            entry_sha256 TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "validation_runs": """
        CREATE TABLE validation_runs (
            validation_run_id TEXT PRIMARY KEY,
            run_schema_version TEXT NOT NULL,
            run_contract_version TEXT NOT NULL,
            subject_hash TEXT NOT NULL,
            run_binding_json TEXT NOT NULL,
            run_binding_sha256 TEXT NOT NULL,
            execution_state TEXT NOT NULL,
            state_version INTEGER NOT NULL,
            created_at_epoch REAL NOT NULL,
            started_at_epoch REAL,
            finished_at_epoch REAL,
            run_failure_kind TEXT,
            run_error_ref_json TEXT,
            updated_at_epoch REAL NOT NULL
        )
    """,
    "validation_execution_events": """
        CREATE TABLE validation_execution_events (
            validation_run_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            run_event_sequence INTEGER NOT NULL,
            event_kind TEXT NOT NULL,
            event_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            occurred_at_epoch REAL NOT NULL,
            PRIMARY KEY (validation_run_id, event_id),
            UNIQUE (validation_run_id, run_event_sequence)
        )
    """,
    "validation_facts": """
        CREATE TABLE validation_facts (
            fact_id TEXT PRIMARY KEY,
            validation_run_id TEXT NOT NULL,
            subject_hash TEXT NOT NULL,
            fact_schema_version TEXT NOT NULL,
            fact_contract_version TEXT NOT NULL,
            fact_json TEXT NOT NULL,
            fact_sha256 TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "validation_completions": """
        CREATE TABLE validation_completions (
            completion_id TEXT PRIMARY KEY,
            validation_run_id TEXT NOT NULL UNIQUE,
            subject_hash TEXT NOT NULL,
            completion_schema_version TEXT NOT NULL,
            completion_contract_version TEXT NOT NULL,
            completion_json TEXT NOT NULL,
            completion_sha256 TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "validator_execution_records": """
        CREATE TABLE validator_execution_records (
            validator_execution_ref TEXT PRIMARY KEY,
            validation_run_id TEXT NOT NULL,
            validator_id TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            record_json TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            terminal_event_id TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "delivery_gate_decisions": """
        CREATE TABLE delivery_gate_decisions (
            delivery_gate_decision_id TEXT PRIMARY KEY,
            decision_schema_version TEXT NOT NULL,
            gate_contract_version TEXT NOT NULL,
            validation_run_id TEXT NOT NULL,
            subject_hash TEXT NOT NULL,
            input_manifest_hash TEXT NOT NULL,
            decision_hash TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "correction_policies": """
        CREATE TABLE correction_policies (
            policy_id TEXT NOT NULL,
            policy_schema_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL,
            PRIMARY KEY (policy_id, policy_version)
        )
    """,
    "post_write_control_decisions": """
        CREATE TABLE post_write_control_decisions (
            control_decision_id TEXT PRIMARY KEY,
            decision_schema_version TEXT NOT NULL,
            control_contract_version TEXT NOT NULL,
            validation_run_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_version INTEGER NOT NULL,
            input_manifest_hash TEXT NOT NULL,
            control_decision_hash TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "correction_attempt_projections": """
        CREATE TABLE correction_attempt_projections (
            projection_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_version INTEGER NOT NULL,
            control_decision_id TEXT NOT NULL,
            embedded_authorization_id TEXT NOT NULL,
            embedded_authorization_hash TEXT NOT NULL,
            outcome TEXT NOT NULL,
            projection_hash TEXT NOT NULL,
            projection_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
    "document_binding_values": """
        CREATE TABLE document_binding_values (
            binding_kind TEXT NOT NULL,
            binding_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at_epoch REAL NOT NULL
        )
    """,
}

_INDEX_DDL: dict[str, str] = {
    "idx_validation_execution_events_run_event": (
        "CREATE INDEX idx_validation_execution_events_run_event "
        "ON validation_execution_events(validation_run_id, event_id)"
    ),
    "idx_validation_facts_run": (
        "CREATE INDEX idx_validation_facts_run ON validation_facts(validation_run_id)"
    ),
    "idx_validator_execution_records_run": (
        "CREATE INDEX idx_validator_execution_records_run "
        "ON validator_execution_records(validation_run_id)"
    ),
    "idx_delivery_gate_decisions_run": (
        "CREATE INDEX idx_delivery_gate_decisions_run "
        "ON delivery_gate_decisions(validation_run_id)"
    ),
    "idx_post_write_control_decisions_run": (
        "CREATE INDEX idx_post_write_control_decisions_run "
        "ON post_write_control_decisions(validation_run_id)"
    ),
    "idx_correction_attempt_projections_run": (
        "CREATE INDEX idx_correction_attempt_projections_run "
        "ON correction_attempt_projections(run_id)"
    ),
}

_EXPECTED_TABLE_DDL: dict[str, str] = {
    "post_write_metadata": _METADATA_DDL,
    **_TABLE_DDL,
}

_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "post_write_metadata": ("key", "value"),
    "artifact_states": (
        "artifact_id", "artifact_version", "artifact_hash", "artifact_scope",
        "parent_artifact_id", "parent_artifact_version", "parent_artifact_hash",
        "canonical_payload_ref_json", "rendered_output_ref_json", "origin_json",
        "record_json", "record_hash", "created_at_epoch",
    ),
    "requirement_snapshots": (
        "requirement_snapshot_id", "requirement_schema_version",
        "requirement_semantic_contract_version", "requirement_hash",
        "snapshot_json", "created_at_epoch",
    ),
    "quality_delivery_contracts": (
        "contract_id", "contract_schema_version", "contract_version",
        "contract_hash", "contract_json", "created_at_epoch",
    ),
    "post_write_entries": (
        "entry_id", "entry_schema_version", "entry_contract_version",
        "entry_scope", "entry_json", "entry_sha256", "created_at_epoch",
    ),
    "validation_runs": (
        "validation_run_id", "run_schema_version", "run_contract_version",
        "subject_hash", "run_binding_json", "run_binding_sha256",
        "execution_state", "state_version", "created_at_epoch",
        "started_at_epoch", "finished_at_epoch", "run_failure_kind",
        "run_error_ref_json", "updated_at_epoch",
    ),
    "validation_execution_events": (
        "validation_run_id", "event_id", "run_event_sequence", "event_kind",
        "event_json", "payload_hash", "event_hash", "occurred_at_epoch",
    ),
    "validation_facts": (
        "fact_id", "validation_run_id", "subject_hash", "fact_schema_version",
        "fact_contract_version", "fact_json", "fact_sha256", "created_at_epoch",
    ),
    "validation_completions": (
        "completion_id", "validation_run_id", "subject_hash",
        "completion_schema_version", "completion_contract_version",
        "completion_json", "completion_sha256", "created_at_epoch",
    ),
    "validator_execution_records": (
        "validator_execution_ref", "validation_run_id", "validator_id",
        "validator_version", "record_json", "record_sha256",
        "terminal_event_id", "created_at_epoch",
    ),
    "delivery_gate_decisions": (
        "delivery_gate_decision_id", "decision_schema_version",
        "gate_contract_version", "validation_run_id", "subject_hash",
        "input_manifest_hash", "decision_hash", "decision_json",
        "created_at_epoch",
    ),
    "correction_policies": (
        "policy_id", "policy_schema_version", "policy_version", "policy_hash",
        "policy_json", "created_at_epoch",
    ),
    "post_write_control_decisions": (
        "control_decision_id", "decision_schema_version",
        "control_contract_version", "validation_run_id", "artifact_id",
        "artifact_version", "input_manifest_hash", "control_decision_hash",
        "decision_json", "created_at_epoch",
    ),
    "correction_attempt_projections": (
        "projection_id", "run_id", "artifact_id", "artifact_version",
        "control_decision_id", "embedded_authorization_id",
        "embedded_authorization_hash", "outcome", "projection_hash",
        "projection_json", "created_at_epoch",
    ),
    "document_binding_values": (
        "binding_kind", "binding_id", "schema_version", "contract_version",
        "payload_sha256", "payload_json", "created_at_epoch",
    ),
}


class PostWriteSqliteDatabase:
    """One local SQLite database and transaction owner for Target Post-write."""

    def __init__(
        self,
        path: str | Path,
        *,
        connect_timeout_seconds: float = 5.0,
        busy_timeout_ms: int = 5000,
        run_commit_guard: Callable[[str], object] | None = None,
        require_run_commit_guard: bool = False,
    ) -> None:
        self.path = Path(path)
        if require_run_commit_guard and not callable(run_commit_guard):
            raise ValueError("Target PostWrite requires a Run commit guard")
        self.run_commit_guard = run_commit_guard
        self.require_run_commit_guard = require_run_commit_guard
        # Exact identity is passed by run-aware orchestration, never decoded
        # from Artifact/Validation identifiers. This is not durable authority.
        self._write_run_id: ContextVar[str | None] = ContextVar(
            "post_write_run_id", default=None
        )
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.busy_timeout_ms = int(busy_timeout_ms)
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if self.busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_or_verify()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.connect_timeout_seconds,
            check_same_thread=False,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        journal_mode = str(
            connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            connection.close()
            raise PostWritePersistenceIntegrityError(
                f"Post-write SQLite requires WAL mode, got {journal_mode}"
            )
        return connection

    @staticmethod
    def _user_tables(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _initialize_or_verify(self) -> None:
        connection = self._connect()
        try:
            tables = self._user_tables(connection)
            if not tables:
                self._initialize_fresh(connection)
                return
            if "post_write_metadata" not in tables:
                raise PostWritePersistenceIntegrityError(
                    "non-empty Post-write database has no schema metadata"
                )
            version_row = connection.execute(
                "SELECT value FROM post_write_metadata WHERE key='schema_version'"
            ).fetchone()
            if version_row is None:
                raise PostWritePersistenceIntegrityError(
                    "Post-write schema_version metadata row is missing"
                )
            version = str(version_row[0])
            if version == LEGACY_POST_WRITE_SCHEMA_VERSION:
                self._migrate_exact_v1_to_v2(connection)
                version = POST_WRITE_V2_SCHEMA_VERSION
            if version == POST_WRITE_V2_SCHEMA_VERSION:
                self._migrate_exact_v2_to_v3(connection)
                version = PREVIOUS_POST_WRITE_SCHEMA_VERSION
            if version == PREVIOUS_POST_WRITE_SCHEMA_VERSION:
                self._migrate_exact_v3_to_v4(connection)
                version = POST_WRITE_SCHEMA_VERSION
            if version != POST_WRITE_SCHEMA_VERSION:
                raise PostWritePersistenceIntegrityError(
                    "unknown or future Post-write schema version: "
                    f"{version_row[0]}"
                )
            self._verify_current_shape(connection)
        finally:
            connection.close()

    def _initialize_fresh(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_METADATA_DDL)
            for ddl in _TABLE_DDL.values():
                connection.execute(ddl)
            for ddl in _INDEX_DDL.values():
                connection.execute(ddl)
            connection.execute(
                "INSERT INTO post_write_metadata(key, value) VALUES(?, ?)",
                ("schema_version", POST_WRITE_SCHEMA_VERSION),
            )
            self._verify_current_shape(connection)
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _columns(
        connection: sqlite3.Connection,
        table: str,
    ) -> tuple[str, ...]:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        return tuple(str(row[1]) for row in rows)

    @staticmethod
    def _indexes(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _schema_sql(
        connection: sqlite3.Connection,
        *,
        object_type: str,
        name: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
            (object_type, name),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    @staticmethod
    def _normalize_schema_sql(value: str) -> str:
        return " ".join(value.split()).lower()

    def _verify_exact_version_shape(
        self,
        connection: sqlite3.Connection,
        *,
        version_label: str,
        expected_tables: set[str],
        expected_indexes: set[str],
    ) -> None:
        tables = self._user_tables(connection)
        if tables != expected_tables:
            raise PostWritePersistenceIntegrityError(
                f"Post-write {version_label} table set mismatch before migration: "
                f"expected={sorted(expected_tables)} actual={sorted(tables)}"
            )
        for table in sorted(expected_tables):
            expected_columns = _EXPECTED_COLUMNS[table]
            actual = self._columns(connection, table)
            if actual != expected_columns:
                raise PostWritePersistenceIntegrityError(
                    f"Post-write {version_label} column shape mismatch for {table}"
                )
            actual_ddl = self._schema_sql(
                connection,
                object_type="table",
                name=table,
            )
            expected_ddl = _EXPECTED_TABLE_DDL[table]
            if actual_ddl is None or self._normalize_schema_sql(
                actual_ddl
            ) != self._normalize_schema_sql(expected_ddl):
                raise PostWritePersistenceIntegrityError(
                    f"Post-write {version_label} table DDL shape mismatch for {table}"
                )
        indexes = self._indexes(connection)
        if indexes != expected_indexes:
            raise PostWritePersistenceIntegrityError(
                f"Post-write {version_label} index set mismatch before migration: "
                f"expected={sorted(expected_indexes)} actual={sorted(indexes)}"
            )
        for index_name in sorted(expected_indexes):
            actual_ddl = self._schema_sql(
                connection,
                object_type="index",
                name=index_name,
            )
            expected_ddl = _INDEX_DDL[index_name]
            if actual_ddl is None or self._normalize_schema_sql(
                actual_ddl
            ) != self._normalize_schema_sql(expected_ddl):
                raise PostWritePersistenceIntegrityError(
                    f"Post-write {version_label} index DDL shape mismatch for {index_name}"
                )

    def _verify_exact_v1_shape(self, connection: sqlite3.Connection) -> None:
        later_tables = {
            "correction_policies",
            "post_write_control_decisions",
            "correction_attempt_projections",
            "document_binding_values",
        }
        expected_tables = (
            set(_EXPECTED_COLUMNS)
            - later_tables
            - {"delivery_gate_decisions"}
        )
        expected_indexes = (
            set(_INDEX_DDL)
            - {
                "idx_delivery_gate_decisions_run",
                "idx_post_write_control_decisions_run",
                "idx_correction_attempt_projections_run",
            }
        )
        self._verify_exact_version_shape(
            connection,
            version_label="V1",
            expected_tables=expected_tables,
            expected_indexes=expected_indexes,
        )

    def _verify_exact_v2_shape(self, connection: sqlite3.Connection) -> None:
        later_tables = {
            "correction_policies",
            "post_write_control_decisions",
            "correction_attempt_projections",
            "document_binding_values",
        }
        expected_tables = set(_EXPECTED_COLUMNS) - later_tables
        expected_indexes = set(_INDEX_DDL) - {
            "idx_post_write_control_decisions_run",
            "idx_correction_attempt_projections_run",
        }
        self._verify_exact_version_shape(
            connection,
            version_label="V2",
            expected_tables=expected_tables,
            expected_indexes=expected_indexes,
        )

    def _verify_exact_v3_shape(self, connection: sqlite3.Connection) -> None:
        expected_tables = set(_EXPECTED_COLUMNS) - {"document_binding_values"}
        expected_indexes = set(_INDEX_DDL)
        self._verify_exact_version_shape(
            connection,
            version_label="V3",
            expected_tables=expected_tables,
            expected_indexes=expected_indexes,
        )

    def _migrate_exact_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        self._verify_exact_v1_shape(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_TABLE_DDL["delivery_gate_decisions"])
            connection.execute(_INDEX_DDL["idx_delivery_gate_decisions_run"])
            connection.execute(
                "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
                (POST_WRITE_V2_SCHEMA_VERSION,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _migrate_exact_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        self._verify_exact_v2_shape(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "correction_policies",
                "post_write_control_decisions",
                "correction_attempt_projections",
            ):
                connection.execute(_TABLE_DDL[table])
            for index in (
                "idx_post_write_control_decisions_run",
                "idx_correction_attempt_projections_run",
            ):
                connection.execute(_INDEX_DDL[index])
            connection.execute(
                "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
                (PREVIOUS_POST_WRITE_SCHEMA_VERSION,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _migrate_exact_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        self._verify_exact_v3_shape(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_TABLE_DDL["document_binding_values"])
            connection.execute(
                "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
                (POST_WRITE_SCHEMA_VERSION,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _verify_current_shape(self, connection: sqlite3.Connection) -> None:
        tables = self._user_tables(connection)
        expected_tables = set(_EXPECTED_COLUMNS)
        if tables != expected_tables:
            raise PostWritePersistenceIntegrityError(
                "Post-write V1 table set mismatch: "
                f"expected={sorted(expected_tables)} actual={sorted(tables)}"
            )
        for table, expected_columns in _EXPECTED_COLUMNS.items():
            actual = self._columns(connection, table)
            if actual != expected_columns:
                raise PostWritePersistenceIntegrityError(
                    f"Post-write V1 column shape mismatch for {table}: "
                    f"expected={expected_columns} actual={actual}"
                )

            actual_ddl = self._schema_sql(
                connection,
                object_type="table",
                name=table,
            )
            expected_ddl = _EXPECTED_TABLE_DDL[table]
            if actual_ddl is None or self._normalize_schema_sql(
                actual_ddl
            ) != self._normalize_schema_sql(expected_ddl):
                raise PostWritePersistenceIntegrityError(
                    f"Post-write V1 table DDL shape mismatch for {table}"
                )

        indexes = self._indexes(connection)
        missing_indexes = set(_INDEX_DDL) - indexes
        if missing_indexes:
            raise PostWritePersistenceIntegrityError(
                "Post-write V1 required indexes missing: "
                f"{sorted(missing_indexes)}"
            )
        for index_name, expected_ddl in _INDEX_DDL.items():
            actual_ddl = self._schema_sql(
                connection,
                object_type="index",
                name=index_name,
            )
            if actual_ddl is None or self._normalize_schema_sql(
                actual_ddl
            ) != self._normalize_schema_sql(expected_ddl):
                raise PostWritePersistenceIntegrityError(
                    f"Post-write V1 index DDL shape mismatch for {index_name}"
                )

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            with current_diagnostics().span("post_write.read_transaction", "DB_TRANSACTION_SCOPE"):
                yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def finalization_read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Drain this SQLite owner and read exact state on the yielded connection.

        Caller must already have durable FINALIZING and release lifecycle locks.
        This is a physical fence only: no lifecycle CAS or currentness verdict.
        BEGIN IMMEDIATE shares normal writers' reservation; query_only prevents
        business SQL mutation after acquisition. All nested exact resolvers must
        receive this connection. Ordinary reads retain their existing behavior.
        """
        with self.write_transaction(immediate=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            yield connection

    @contextmanager
    def _exact_read_connection(
        self, connection: sqlite3.Connection | None,
    ) -> Iterator[sqlite3.Connection]:
        """Borrow a caller's transaction without opening or closing another one."""
        if connection is None:
            with self.read_transaction() as owned:
                yield owned
        else:
            if not connection.in_transaction:
                raise PostWritePersistenceIntegrityError(
                    "exact read requires an active caller-owned transaction"
                )
            yield connection

    @contextmanager
    def run_write_guard_context(self, run_id: str) -> Iterator[None]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("PostWrite normal writes require exact run_id")
        prior = self._write_run_id.get()
        if prior is not None and prior != run_id:
            raise PostWritePersistenceIntegrityError("PostWrite write context crossed Run identity")
        token = self._write_run_id.set(run_id)
        try:
            yield
        finally:
            self._write_run_id.reset(token)

    @contextmanager
    def normal_write_transaction(self, *, run_id: str | None = None) -> Iterator[sqlite3.Connection]:
        # Physical reservation FIRST. Release fencing continues to use the
        # existing write_transaction directly and is not a normal I3 write.
        with self.write_transaction(immediate=True) as connection:
            if self.run_commit_guard is not None:
                context_run_id = self._write_run_id.get()
                if run_id is not None and context_run_id is not None and run_id != context_run_id:
                    raise PostWritePersistenceIntegrityError("PostWrite DTO differs from exact Run write context")
                run_id = run_id if run_id is not None else context_run_id
                if run_id is None:
                    raise PostWritePersistenceIntegrityError("missing exact PostWrite Run write context")
                self.run_commit_guard(run_id)
            with current_diagnostics().span("post_write.normal_write_transaction", "DB_TRANSACTION_SCOPE", run_id=run_id):
                yield connection

    @contextmanager
    def write_transaction(
        self,
        *,
        immediate: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            error_code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(error_code, int) and (error_code & 0xFF) in {
                _SQLITE_BUSY_PRIMARY_CODE,
                _SQLITE_LOCKED_PRIMARY_CODE,
            }:
                raise PostWriteConcurrencyError(str(exc)) from exc
            message = str(exc).strip().lower()
            if (
                message == "database is locked"
                or message.startswith("database table is locked")
                or message.startswith("database schema is locked")
                or message == "database is busy"
            ):
                raise PostWriteConcurrencyError(str(exc)) from exc
            raise
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def verify_integrity(self) -> None:
        connection = self._connect()
        try:
            self._verify_current_shape(connection)
            version = connection.execute(
                "SELECT value FROM post_write_metadata WHERE key='schema_version'"
            ).fetchone()
            if version is None or str(version[0]) != POST_WRITE_SCHEMA_VERSION:
                raise PostWritePersistenceIntegrityError(
                    "Post-write V1 metadata version is invalid"
                )
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or str(result[0]).lower() != "ok":
                raise PostWritePersistenceIntegrityError(
                    f"SQLite integrity_check failed: {result[0] if result else None}"
                )
        finally:
            connection.close()


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _record_hash_without_self(
    record: ArtifactStateRecordSchema | dict,
) -> str:
    payload = (
        record.model_dump(mode="json")
        if isinstance(record, ArtifactStateRecordSchema)
        else dict(record)
    )
    payload.pop("record_hash", None)
    return canonical_sha256(payload)


class ArtifactStateStore:
    """SECTION/DOCUMENT Artifact persistence + expected-parent CAS foundation."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ArtifactStateRecordSchema:
        try:
            raw_record = json.loads(row["record_json"])
            if not isinstance(raw_record, dict):
                raise TypeError("ArtifactState record_json must be an object")
            record = ArtifactStateRecordSchema.model_validate(raw_record)
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ArtifactState record_json"
            ) from exc
        if record.record_hash != row["record_hash"]:
            raise PostWritePersistenceIntegrityError(
                "ArtifactState row record_hash mismatch"
            )
        if _record_hash_without_self(raw_record) != record.record_hash:
            raise PostWritePersistenceIntegrityError(
                "ArtifactState canonical record hash mismatch"
            )
        ref = record.artifact_ref
        parent = record.parent_artifact_ref
        if (
            ref.artifact_id != row["artifact_id"]
            or ref.artifact_version != row["artifact_version"]
            or ref.artifact_hash != row["artifact_hash"]
            or ref.artifact_scope.value != row["artifact_scope"]
            or row["parent_artifact_id"]
            != (parent.artifact_id if parent is not None else None)
            or row["parent_artifact_version"]
            != (parent.artifact_version if parent is not None else None)
            or row["parent_artifact_hash"]
            != (parent.artifact_hash if parent is not None else None)
            or row["canonical_payload_ref_json"]
            != _canonical_json(record.canonical_payload_ref)
            or row["rendered_output_ref_json"]
            != (
                _canonical_json(record.rendered_output_ref)
                if record.rendered_output_ref is not None
                else None
            )
            or row["origin_json"]
            != _canonical_json(record.origin.model_dump(mode="json"))
        ):
            raise PostWritePersistenceIntegrityError(
                "ArtifactState row metadata contradicts canonical record"
            )
        return record

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: ArtifactStateRecordSchema,
    ) -> None:
        ref = record.artifact_ref
        parent = record.parent_artifact_ref
        connection.execute(
            """
            INSERT INTO artifact_states(
                artifact_id, artifact_version, artifact_hash, artifact_scope,
                parent_artifact_id, parent_artifact_version, parent_artifact_hash,
                canonical_payload_ref_json, rendered_output_ref_json, origin_json,
                record_json, record_hash, created_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ref.artifact_id,
                ref.artifact_version,
                ref.artifact_hash,
                ref.artifact_scope.value,
                parent.artifact_id if parent else None,
                parent.artifact_version if parent else None,
                parent.artifact_hash if parent else None,
                _canonical_json(record.canonical_payload_ref),
                (
                    _canonical_json(record.rendered_output_ref)
                    if record.rendered_output_ref is not None
                    else None
                ),
                _canonical_json(record.origin.model_dump(mode="json")),
                record.model_dump_json(),
                record.record_hash,
                time.time(),
            ),
        )

    def create_initial_if_absent_or_same(
        self,
        record: ArtifactStateRecordSchema,
    ) -> ArtifactStateRefSchema:
        if record.artifact_ref.artifact_version != 1:
            raise PostWritePersistenceIntegrityError(
                "initial ArtifactState must use artifact_version=1"
            )
        if record.parent_artifact_ref is not None:
            raise PostWritePersistenceIntegrityError(
                "initial ArtifactState cannot have parent"
            )
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_states WHERE artifact_id=? AND artifact_version=1",
                (record.artifact_ref.artifact_id,),
            ).fetchone()
            if row is not None:
                existing = self._row_to_record(row)
                if existing != record:
                    raise PostWritePersistenceIntegrityError(
                        "initial Artifact identity reused with conflicting payload"
                    )
                return existing.artifact_ref
            any_row = connection.execute(
                "SELECT artifact_version FROM artifact_states WHERE artifact_id=? LIMIT 1",
                (record.artifact_ref.artifact_id,),
            ).fetchone()
            if any_row is not None:
                raise PostWritePersistenceIntegrityError(
                    "Artifact lineage exists without version 1"
                )
            self._insert_record(connection, record)
            return record.artifact_ref

    def append_next_version(
        self,
        record: ArtifactStateRecordSchema,
        *,
        expected_parent_ref: ArtifactStateRefSchema,
    ) -> ArtifactStateRefSchema:
        if record.artifact_ref.artifact_id != expected_parent_ref.artifact_id:
            raise PostWritePersistenceIntegrityError(
                "candidate Artifact id differs from expected parent lineage"
            )
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_states WHERE artifact_id=? "
                "ORDER BY artifact_version DESC LIMIT 1",
                (expected_parent_ref.artifact_id,),
            ).fetchone()
            if row is None:
                raise PostWriteConcurrencyError("expected Artifact parent does not exist")
            current = self._row_to_record(row)
            if current.artifact_ref != expected_parent_ref:
                raise PostWriteConcurrencyError("stale expected Artifact parent")
            if record.artifact_ref.artifact_hash == current.artifact_ref.artifact_hash:
                return current.artifact_ref
            if record.parent_artifact_ref != expected_parent_ref:
                raise PostWritePersistenceIntegrityError(
                    "candidate parent ref does not equal expected parent"
                )
            if (
                record.artifact_ref.artifact_version
                != expected_parent_ref.artifact_version + 1
            ):
                raise PostWritePersistenceIntegrityError(
                    "candidate Artifact version is not parent+1"
                )
            existing = connection.execute(
                "SELECT * FROM artifact_states WHERE artifact_id=? AND artifact_version=?",
                (
                    record.artifact_ref.artifact_id,
                    record.artifact_ref.artifact_version,
                ),
            ).fetchone()
            if existing is not None:
                parsed = self._row_to_record(existing)
                if parsed != record:
                    raise PostWritePersistenceIntegrityError(
                        "Artifact version identity collision"
                    )
                return parsed.artifact_ref
            self._insert_record(connection, record)
            return record.artifact_ref

    def resolve_exact(
        self,
        ref: ArtifactStateRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ArtifactStateRecordSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            with current_diagnostics().span("post_write.resolve_exact.artifact", "DB_API_ELAPSED"):
                row = read_connection.execute(
                    "SELECT * FROM artifact_states WHERE artifact_id=? AND artifact_version=?",
                    (ref.artifact_id, ref.artifact_version),
                ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact ArtifactState ref is missing"
            )
        record = self._row_to_record(row)
        if record.artifact_ref != ref:
            raise PostWritePersistenceIntegrityError(
                "resolved ArtifactState does not match exact ref"
            )
        return record


    def resolve_current(self, artifact_id: str, *, connection=None) -> ArtifactStateRecordSchema:
        """Read the same version head used by append_next_version under an owner fence."""
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM artifact_states WHERE artifact_id=? ORDER BY artifact_version DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError("required ArtifactState head is missing")
        return self._row_to_record(row)


class DocumentBindingStore:
    """Typed immutable Document assembly/evidence binding persistence."""

    ASSEMBLY_INPUT_MANIFEST = "ASSEMBLY_INPUT_MANIFEST"
    DOCUMENT_EVIDENCE_SNAPSHOT = "DOCUMENT_EVIDENCE_SNAPSHOT"

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    def _put(
        self,
        *,
        binding_kind: str,
        binding_id: str,
        schema_version: str,
        contract_version: str,
        payload_sha256: str,
        payload_json: str,
    ) -> None:
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM document_binding_values WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["binding_kind"] != binding_kind
                    or row["schema_version"] != schema_version
                    or row["contract_version"] != contract_version
                    or row["payload_sha256"] != payload_sha256
                    or row["payload_json"] != payload_json
                ):
                    raise PostWritePersistenceIntegrityError(
                        "Document binding identity collision"
                    )
                return
            connection.execute(
                """
                INSERT INTO document_binding_values(
                    binding_kind,binding_id,schema_version,contract_version,
                    payload_sha256,payload_json,created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding_kind,
                    binding_id,
                    schema_version,
                    contract_version,
                    payload_sha256,
                    payload_json,
                    time.time(),
                ),
            )

    def put_assembly_manifest_if_absent_or_same(
        self,
        manifest: AssemblyInputManifestSchema,
    ) -> AssemblyInputManifestRefSchema:
        candidate = AssemblyInputManifestSchema.model_validate(manifest)
        ref = candidate.as_ref()
        self._put(
            binding_kind=self.ASSEMBLY_INPUT_MANIFEST,
            binding_id=ref.manifest_id,
            schema_version=candidate.schema_version,
            contract_version=candidate.assembly_contract_version,
            payload_sha256=candidate.full_manifest_sha256,
            payload_json=candidate.model_dump_json(),
        )
        return ref

    def resolve_assembly_manifest(
        self,
        ref: AssemblyInputManifestRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> AssemblyInputManifestSchema:
        exact = AssemblyInputManifestRefSchema.model_validate(ref)
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM document_binding_values WHERE binding_id=?",
                (exact.manifest_id,),
            ).fetchone()
        if row is None or row["binding_kind"] != self.ASSEMBLY_INPUT_MANIFEST:
            raise PostWritePersistenceIntegrityError(
                "required exact AssemblyInputManifest is missing"
            )
        try:
            manifest = AssemblyInputManifestSchema.model_validate_json(
                row["payload_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt AssemblyInputManifest"
            ) from exc
        if (
            row["schema_version"] != manifest.schema_version
            or row["contract_version"] != manifest.assembly_contract_version
            or row["payload_sha256"] != manifest.full_manifest_sha256
            or manifest.as_ref() != exact
        ):
            raise PostWritePersistenceIntegrityError(
                "AssemblyInputManifest exact ref/hash mismatch"
            )
        return manifest

    def put_document_evidence_snapshot_if_absent_or_same(
        self,
        projection: DocumentEvidenceSnapshotProjectionSchema,
    ) -> DocumentEvidenceSnapshotRefSchema:
        candidate = DocumentEvidenceSnapshotProjectionSchema.model_validate(projection)
        ref = candidate.as_ref()
        self._put(
            binding_kind=self.DOCUMENT_EVIDENCE_SNAPSHOT,
            binding_id=ref.projection_id,
            schema_version=candidate.schema_version,
            contract_version=candidate.projection_contract_version,
            payload_sha256=candidate.projection_sha256,
            payload_json=candidate.model_dump_json(),
        )
        return ref

    def resolve_document_evidence_snapshot(
        self,
        ref: DocumentEvidenceSnapshotRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> DocumentEvidenceSnapshotProjectionSchema:
        exact = DocumentEvidenceSnapshotRefSchema.model_validate(ref)
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM document_binding_values WHERE binding_id=?",
                (exact.projection_id,),
            ).fetchone()
        if row is None or row["binding_kind"] != self.DOCUMENT_EVIDENCE_SNAPSHOT:
            raise PostWritePersistenceIntegrityError(
                "required exact DocumentEvidenceSnapshot is missing"
            )
        try:
            projection = DocumentEvidenceSnapshotProjectionSchema.model_validate_json(
                row["payload_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt DocumentEvidenceSnapshot"
            ) from exc
        if (
            row["schema_version"] != projection.schema_version
            or row["contract_version"] != projection.projection_contract_version
            or row["payload_sha256"] != projection.projection_sha256
            or projection.as_ref() != exact
        ):
            raise PostWritePersistenceIntegrityError(
                "DocumentEvidenceSnapshot exact ref/hash mismatch"
            )
        return projection


class RequirementStore:
    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _expected_hash(snapshot: PostWriteRequirementSnapshotSchema) -> str:
        payload = snapshot.model_dump(mode="json")
        payload.pop("requirement_hash", None)
        return canonical_sha256(payload)

    def put_if_absent_or_same(
        self,
        snapshot: PostWriteRequirementSnapshotSchema,
    ) -> RequirementRefSchema:
        if self._expected_hash(snapshot) != snapshot.requirement_hash:
            raise PostWritePersistenceIntegrityError(
                "Post-write Requirement canonical hash mismatch"
            )
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM requirement_snapshots WHERE requirement_snapshot_id=?",
                (snapshot.requirement_snapshot_id,),
            ).fetchone()
            if row is not None:
                try:
                    existing = PostWriteRequirementSnapshotSchema.model_validate_json(
                        row["snapshot_json"]
                    )
                except Exception as exc:
                    raise PostWritePersistenceIntegrityError(
                        "corrupt Requirement snapshot"
                    ) from exc
                if existing != snapshot:
                    raise PostWritePersistenceIntegrityError(
                        "Requirement snapshot identity collision"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO requirement_snapshots(
                        requirement_snapshot_id, requirement_schema_version,
                        requirement_semantic_contract_version, requirement_hash,
                        snapshot_json, created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.requirement_snapshot_id,
                        snapshot.schema_version,
                        snapshot.semantic_contract_version,
                        snapshot.requirement_hash,
                        snapshot.model_dump_json(),
                        time.time(),
                    ),
                )
        return RequirementRefSchema(
            requirement_snapshot_id=snapshot.requirement_snapshot_id,
            requirement_semantic_contract_version=snapshot.semantic_contract_version,
            requirement_hash=snapshot.requirement_hash,
        )

    def resolve_exact(
        self,
        ref: RequirementRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PostWriteRequirementSnapshotSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            with current_diagnostics().span("post_write.resolve_exact.requirement", "DB_API_ELAPSED"):
                row = read_connection.execute(
                    "SELECT * FROM requirement_snapshots WHERE requirement_snapshot_id=?",
                    (ref.requirement_snapshot_id,),
                ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact Requirement ref is missing"
            )
        try:
            snapshot = PostWriteRequirementSnapshotSchema.model_validate_json(
                row["snapshot_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt Requirement snapshot"
            ) from exc
        if (
            row["requirement_schema_version"] != snapshot.schema_version
            or row["requirement_semantic_contract_version"]
            != snapshot.semantic_contract_version
            or row["requirement_hash"] != snapshot.requirement_hash
            or snapshot.schema_version != ref.requirement_schema_version
            or snapshot.semantic_contract_version
            != ref.requirement_semantic_contract_version
            or snapshot.requirement_hash != ref.requirement_hash
            or self._expected_hash(snapshot) != snapshot.requirement_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "Requirement exact ref/hash mismatch"
            )
        return snapshot


_ENTRY_ADAPTER = TypeAdapter(PostWriteEntrySchema)


class PostWriteEntryStore:
    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    def put_if_absent_or_same(self, entry: PostWriteEntrySchema) -> PostWriteEntryRefSchema:
        ref = entry_ref_from_identity(entry.identity)
        with self.database.normal_write_transaction(run_id=entry.identity.run_id) as connection:
            row = connection.execute(
                "SELECT * FROM post_write_entries WHERE entry_id=?",
                (ref.entry_id,),
            ).fetchone()
            if row is not None:
                try:
                    existing = _ENTRY_ADAPTER.validate_json(row["entry_json"])
                except Exception as exc:
                    raise PostWritePersistenceIntegrityError(
                        "corrupt PostWriteEntry"
                    ) from exc
                if existing != entry:
                    raise PostWritePersistenceIntegrityError(
                        "PostWriteEntry identity collision"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO post_write_entries(
                        entry_id, entry_schema_version, entry_contract_version,
                        entry_scope, entry_json, entry_sha256, created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ref.entry_id,
                        ref.entry_schema_version,
                        ref.entry_contract_version,
                        entry.identity.entry_scope.value,
                        entry.model_dump_json(),
                        ref.entry_sha256,
                        time.time(),
                    ),
                )
        return ref

    def resolve_exact(self, ref: PostWriteEntryRefSchema, *, connection: sqlite3.Connection | None = None) -> PostWriteEntrySchema:
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM post_write_entries WHERE entry_id=?",
                (ref.entry_id,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact PostWriteEntry ref is missing"
            )
        try:
            entry = _ENTRY_ADAPTER.validate_json(row["entry_json"])
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt PostWriteEntry"
            ) from exc
        actual_ref = entry_ref_from_identity(entry.identity)
        if (
            row["entry_schema_version"] != actual_ref.entry_schema_version
            or row["entry_contract_version"] != actual_ref.entry_contract_version
            or row["entry_scope"] != entry.identity.entry_scope.value
            or row["entry_sha256"] != actual_ref.entry_sha256
            or actual_ref != ref
        ):
            raise PostWritePersistenceIntegrityError(
                "PostWriteEntry exact ref/hash mismatch"
            )
        return entry


def _canonical_payload_hash(model, self_hash_field: str) -> str:
    payload = (
        model.model_dump(mode="json")
        if hasattr(model, "model_dump")
        else dict(model)
    )
    payload.pop(self_hash_field, None)
    return canonical_sha256(payload)


def _validate_stage_plan_input_conformance(
    stage_plan: StagePlanSchema,
    input_envelope: InputEnvelopeSchema,
) -> None:
    stage_plan_preimage = stage_plan.model_dump(mode="json")
    stage_plan_preimage.pop("stage_plan_hash", None)
    if canonical_sha256(stage_plan_preimage) != stage_plan.stage_plan_hash:
        raise PostWritePersistenceIntegrityError(
            "Validator StagePlan canonical hash mismatch"
        )
    if (
        input_envelope.validator_id != stage_plan.validator_id
        or input_envelope.validator_version != stage_plan.validator_version
        or input_envelope.stage_plan_ref != stage_plan.stage_plan_ref
        or input_envelope.stage_plan_version != stage_plan.stage_plan_version
        or input_envelope.stage_plan_hash != stage_plan.stage_plan_hash
    ):
        raise PostWritePersistenceIntegrityError(
            "Validator input envelope does not conform to exact StagePlan"
        )
    expected_hash = canonical_sha256(
        {
            "validation_run_id": input_envelope.validation_run_id,
            "subject_hash": input_envelope.subject_hash,
            "artifact_state_ref": input_envelope.artifact_state_ref.model_dump(
                mode="json"
            ),
            "artifact_visible_projection_ref": (
                input_envelope.artifact_visible_projection_ref.model_dump(
                    mode="json"
                )
            ),
            "artifact_visible_projection_sha256": (
                input_envelope.artifact_visible_projection_sha256
            ),
            "validator_id": input_envelope.validator_id,
            "validator_version": input_envelope.validator_version,
            "stage_plan_ref": input_envelope.stage_plan_ref,
            "stage_plan_version": input_envelope.stage_plan_version,
            "stage_plan_hash": input_envelope.stage_plan_hash,
            "applicable_expected_validation_item_refs": list(
                input_envelope.applicable_expected_validation_item_refs
            ),
            "exact_authority_refs": list(input_envelope.exact_authority_refs),
            "execution_config_refs": list(input_envelope.execution_config_refs),
            "execution_contract_version": input_envelope.execution_contract_version,
        }
    )
    if expected_hash != input_envelope.input_hash:
        raise PostWritePersistenceIntegrityError(
            "Validator input envelope canonical hash mismatch"
        )


class QualityDeliveryContractStore:
    """Immutable historical QualityDeliveryContract persistence."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _contract_from_row(row: sqlite3.Row) -> QualityDeliveryContractSchema:
        try:
            contract = QualityDeliveryContractSchema.model_validate_json(
                row["contract_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt QualityDeliveryContract"
            ) from exc
        if (
            row["contract_id"] != contract.contract_id
            or row["contract_schema_version"] != contract.schema_version
            or row["contract_version"] != contract.contract_version
            or row["contract_hash"] != contract.contract_hash
            or _canonical_payload_hash(contract, "contract_hash")
            != contract.contract_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "QualityDeliveryContract row/hash mismatch"
            )
        return contract

    def put_if_absent_or_same(
        self,
        contract: QualityDeliveryContractSchema,
    ) -> QualityDeliveryContractRefSchema:
        if _canonical_payload_hash(contract, "contract_hash") != contract.contract_hash:
            raise PostWritePersistenceIntegrityError(
                "QualityDeliveryContract canonical hash mismatch"
            )
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM quality_delivery_contracts "
                "WHERE contract_id=? AND contract_version=?",
                (contract.contract_id, contract.contract_version),
            ).fetchone()
            if row is not None:
                existing = self._contract_from_row(row)
                if existing != contract:
                    raise PostWritePersistenceIntegrityError(
                        "QualityDeliveryContract identity collision"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO quality_delivery_contracts(
                        contract_id, contract_schema_version, contract_version,
                        contract_hash, contract_json, created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract.contract_id,
                        contract.schema_version,
                        contract.contract_version,
                        contract.contract_hash,
                        contract.model_dump_json(),
                        time.time(),
                    ),
                )
        return contract.as_ref()

    def resolve_exact(
        self,
        ref: QualityDeliveryContractRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> QualityDeliveryContractSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM quality_delivery_contracts "
                "WHERE contract_id=? AND contract_version=?",
                (ref.contract_id, ref.contract_version),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact QualityDeliveryContract ref is missing"
            )
        contract = self._contract_from_row(row)
        if contract.as_ref() != ref:
            raise PostWritePersistenceIntegrityError(
                "QualityDeliveryContract exact ref/hash mismatch"
            )
        return contract


class ValidationStore:
    """Canonical Validation Run/Fact/Completion/technical-journal store."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ValidationExecutionEventSchema:
        try:
            event = ValidationExecutionEventSchema.model_validate_json(
                row["event_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidationExecutionEvent"
            ) from exc
        if (
            event.validation_run_id != row["validation_run_id"]
            or event.event_id != row["event_id"]
            or event.run_event_sequence != row["run_event_sequence"]
            or event.event_kind != row["event_kind"]
            or event.payload_hash != row["payload_hash"]
            or event.event_hash != row["event_hash"]
        ):
            raise PostWritePersistenceIntegrityError(
                "Validation event row contradicts canonical event"
            )
        if canonical_sha256(event.payload) != event.payload_hash:
            raise PostWritePersistenceIntegrityError(
                "Validation event payload hash mismatch"
            )
        event_preimage = event.model_dump(mode="json")
        event_preimage.pop("event_hash", None)
        if canonical_sha256(event_preimage) != event.event_hash:
            raise PostWritePersistenceIntegrityError(
                "Validation event hash mismatch"
            )
        return event

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> ValidationRunSchema:
        try:
            subject_key = json.loads(row["run_binding_json"])
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidationRun subject binding"
            ) from exc
        if canonical_sha256(subject_key) != row["run_binding_sha256"]:
            raise PostWritePersistenceIntegrityError(
                "ValidationRun binding hash mismatch"
            )
        try:
            run = ValidationRunSchema(
                validation_run_id=row["validation_run_id"],
                subject_key=subject_key["subject_key"],
                execution_state=row["execution_state"],
                state_version=row["state_version"],
                creation_command_id=subject_key["creation_command_id"],
                created_at_epoch=row["created_at_epoch"],
                started_at_epoch=row["started_at_epoch"],
                finished_at_epoch=row["finished_at_epoch"],
                run_failure_kind=row["run_failure_kind"],
                run_error_ref=(
                    json.loads(row["run_error_ref_json"])
                    if row["run_error_ref_json"] is not None
                    else None
                ),
            )
            if (
                row["run_schema_version"] != run.schema_version
                or row["run_contract_version"] != run.contract_version
                or row["subject_hash"] != run.subject_key.subject_hash
            ):
                raise ValueError("ValidationRun row metadata mismatch")
            return run
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "invalid persisted ValidationRun"
            ) from exc

    @staticmethod
    def _fact_ref(fact: ValidationFactSchema) -> ValidationFactRefSchema:
        return ValidationFactRefSchema(
            fact_id=fact.fact_id,
            validation_run_id=fact.validation_run_id,
            subject_hash=fact.subject_hash,
            fact_contract_version=fact.contract_version,
            fact_sha256=fact.fact_sha256,
        )

    @staticmethod
    def _fact_from_row(row: sqlite3.Row) -> ValidationFactSchema:
        try:
            fact = ValidationFactSchema.model_validate_json(row["fact_json"])
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidationFact"
            ) from exc
        if (
            row["fact_id"] != fact.fact_id
            or row["validation_run_id"] != fact.validation_run_id
            or row["subject_hash"] != fact.subject_hash
            or row["fact_schema_version"] != fact.schema_version
            or row["fact_contract_version"] != fact.contract_version
            or row["fact_sha256"] != fact.fact_sha256
            or _canonical_payload_hash(fact, "fact_sha256") != fact.fact_sha256
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidationFact row/hash mismatch"
            )
        return fact

    @staticmethod
    def _completion_ref(
        completion: ValidationCompletionSchema,
    ) -> ValidationCompletionRefSchema:
        return ValidationCompletionRefSchema(
            completion_id=completion.completion_id,
            validation_run_id=completion.validation_run_id,
            subject_hash=completion.subject_hash,
            completion_sha256=completion.completion_sha256,
        )

    @staticmethod
    def _completion_from_row(row: sqlite3.Row) -> ValidationCompletionSchema:
        try:
            completion = ValidationCompletionSchema.model_validate_json(
                row["completion_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidationCompletion"
            ) from exc
        if (
            row["completion_id"] != completion.completion_id
            or row["validation_run_id"] != completion.validation_run_id
            or row["subject_hash"] != completion.subject_hash
            or row["completion_schema_version"] != completion.schema_version
            or row["completion_contract_version"] != completion.contract_version
            or row["completion_sha256"] != completion.completion_sha256
            or _canonical_payload_hash(completion, "completion_sha256")
            != completion.completion_sha256
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidationCompletion row/hash mismatch"
            )
        return completion

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ValidatorExecutionRecordSchema:
        try:
            raw_record = json.loads(row["record_json"])
            if not isinstance(raw_record, dict):
                raise TypeError(
                    "ValidatorExecutionRecord record_json must be object"
                )
            record = ValidatorExecutionRecordSchema.model_validate(raw_record)
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidatorExecutionRecord"
            ) from exc
        if (
            row["validator_execution_ref"] != record.validator_execution_ref
            or row["validation_run_id"] != record.validation_run_id
            or row["validator_id"] != record.validator_id
            or row["validator_version"] != record.validator_version
            or row["record_sha256"] != record.record_sha256
            or row["terminal_event_id"] != record.terminal_event_id
            or _canonical_payload_hash(raw_record, "record_sha256")
            != record.record_sha256
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord row/hash mismatch"
            )
        return record

    @staticmethod
    def _run_binding(run: ValidationRunSchema) -> dict:
        return {
            "subject_key": run.subject_key.model_dump(mode="json"),
            "creation_command_id": run.creation_command_id,
        }

    @staticmethod
    def _next_event_sequence(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            "SELECT MAX(run_event_sequence) FROM validation_execution_events "
            "WHERE validation_run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0] or 0) + 1

    @staticmethod
    def _event(
        *,
        validation_run_id: str,
        event_id: str,
        sequence: int,
        event_kind: str,
        payload: dict,
        occurred_at_epoch: float,
    ) -> ValidationExecutionEventSchema:
        payload_hash = canonical_sha256(payload)
        base = {
            "schema_version": "validation_execution_event_v1",
            "validation_run_id": validation_run_id,
            "event_id": event_id,
            "run_event_sequence": sequence,
            "event_kind": event_kind,
            "payload": payload,
            "payload_hash": payload_hash,
            "occurred_at_epoch": occurred_at_epoch,
        }
        return ValidationExecutionEventSchema(
            **base,
            event_hash=canonical_sha256(base),
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event: ValidationExecutionEventSchema,
    ) -> None:
        connection.execute(
            """
            INSERT INTO validation_execution_events(
                validation_run_id,event_id,run_event_sequence,event_kind,
                event_json,payload_hash,event_hash,occurred_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.validation_run_id,
                event.event_id,
                event.run_event_sequence,
                event.event_kind,
                event.model_dump_json(),
                event.payload_hash,
                event.event_hash,
                event.occurred_at_epoch,
            ),
        )

    def create_run_with_event_if_absent_or_same(
        self,
        run: ValidationRunSchema,
        creation_command_id: str,
    ) -> ValidationRunSchema:
        if run.execution_state is not ValidationRunState.CREATED:
            raise PostWritePersistenceIntegrityError(
                "ValidationRun creation requires CREATED state"
            )
        if run.creation_command_id != creation_command_id:
            raise PostWritePersistenceIntegrityError(
                "creation command id differs from ValidationRun"
            )
        binding = self._run_binding(run)
        binding_hash = canonical_sha256(binding)
        event_id = "run-created:" + canonical_sha256(
            {
                "validation_run_id": run.validation_run_id,
                "creation_command_id": creation_command_id,
            }
        )
        event_payload = {
            "command_id": creation_command_id,
            "subject_hash": run.subject_key.subject_hash,
            "state_version": 1,
        }
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM validation_runs WHERE validation_run_id=?",
                (run.validation_run_id,),
            ).fetchone()
            if row is not None:
                existing = self._run_from_row(row)
                event_row = connection.execute(
                    "SELECT * FROM validation_execution_events "
                    "WHERE validation_run_id=? AND event_id=?",
                    (run.validation_run_id, event_id),
                ).fetchone()
                same_creation = (
                    existing.validation_run_id == run.validation_run_id
                    and existing.subject_key == run.subject_key
                    and existing.creation_command_id == run.creation_command_id
                    and existing.created_at_epoch == run.created_at_epoch
                )
                if not same_creation or event_row is None:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun identity reused with conflicting creation"
                    )
                event = self._event_from_row(event_row)
                if event.payload != event_payload or event.event_kind != "RUN_CREATED":
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun creation command collision"
                    )
                return existing

            connection.execute(
                """
                INSERT INTO validation_runs(
                    validation_run_id,run_schema_version,run_contract_version,
                    subject_hash,run_binding_json,run_binding_sha256,
                    execution_state,state_version,created_at_epoch,
                    started_at_epoch,finished_at_epoch,run_failure_kind,
                    run_error_ref_json,updated_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.validation_run_id,
                    run.schema_version,
                    run.contract_version,
                    run.subject_key.subject_hash,
                    json.dumps(binding, sort_keys=True, separators=(",", ":")),
                    binding_hash,
                    run.execution_state.value,
                    run.state_version,
                    run.created_at_epoch,
                    run.started_at_epoch,
                    run.finished_at_epoch,
                    run.run_failure_kind,
                    (
                        json.dumps(run.run_error_ref, sort_keys=True, separators=(",", ":"))
                        if run.run_error_ref is not None
                        else None
                    ),
                    run.created_at_epoch,
                ),
            )
            event = self._event(
                validation_run_id=run.validation_run_id,
                event_id=event_id,
                sequence=1,
                event_kind="RUN_CREATED",
                payload=event_payload,
                occurred_at_epoch=run.created_at_epoch,
            )
            self._insert_event(connection, event)
        return run

    def resolve_run_exact(self, validation_run_id: str, *, connection: sqlite3.Connection | None = None) -> ValidationRunSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            with current_diagnostics().span("post_write.resolve_run_exact", "DB_API_ELAPSED"):
                row = read_connection.execute(
                    "SELECT * FROM validation_runs WHERE validation_run_id=?",
                    (validation_run_id,),
                ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact ValidationRun is missing"
            )
        return self._run_from_row(row)

    def _find_command_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        command_id: str,
    ) -> ValidationExecutionEventSchema | None:
        rows = connection.execute(
            "SELECT * FROM validation_execution_events WHERE validation_run_id=?",
            (run_id,),
        ).fetchall()
        for row in rows:
            event = self._event_from_row(row)
            if event.payload.get("command_id") == command_id:
                return event
        return None

    def transition_run(
        self,
        validation_run_id: str,
        expected_state_version: int,
        transition_command_id: str,
        target_state: ValidationRunState,
        failure_ref: dict | None = None,
        *,
        occurred_at_epoch: float | None = None,
    ) -> ValidationRunSchema:
        now = float(time.time() if occurred_at_epoch is None else occurred_at_epoch)
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM validation_runs WHERE validation_run_id=?",
                (validation_run_id,),
            ).fetchone()
            if row is None:
                raise PostWritePersistenceIntegrityError(
                    "required exact ValidationRun is missing"
                )
            current = self._run_from_row(row)
            prior_event = self._find_command_event(
                connection,
                run_id=validation_run_id,
                command_id=transition_command_id,
            )
            if prior_event is not None:
                completion_ref_payload = None
                if target_state is ValidationRunState.COMPLETED:
                    completion_row = connection.execute(
                        "SELECT * FROM validation_completions "
                        "WHERE validation_run_id=?",
                        (validation_run_id,),
                    ).fetchone()
                    if completion_row is None:
                        raise PostWritePersistenceIntegrityError(
                            "ValidationRun COMPLETED requires canonical Completion"
                        )
                    completion = self._completion_from_row(completion_row)
                    completion_ref_payload = self._completion_ref(
                        completion
                    ).model_dump(mode="json")
                if (
                    prior_event.event_kind != f"RUN_{target_state.value}"
                    or prior_event.payload.get("target_state") != target_state.value
                    or prior_event.payload.get("expected_state_version")
                    != expected_state_version
                    or prior_event.payload.get("failure_ref") != failure_ref
                    or prior_event.payload.get("completion_ref")
                    != completion_ref_payload
                ):
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun transition command collision"
                    )
                return current

            if current.state_version != expected_state_version:
                raise PostWriteConcurrencyError(
                    "stale ValidationRun expected_state_version"
                )
            legal = {
                ValidationRunState.CREATED: {
                    ValidationRunState.RUNNING,
                    ValidationRunState.TECHNICAL_FAILURE,
                },
                ValidationRunState.RUNNING: {
                    ValidationRunState.COMPLETED,
                    ValidationRunState.TECHNICAL_FAILURE,
                },
            }
            if target_state not in legal.get(current.execution_state, set()):
                raise PostWritePersistenceIntegrityError(
                    "illegal ValidationRun lifecycle transition"
                )

            completion_ref_payload = None
            if target_state is ValidationRunState.COMPLETED:
                completion_row = connection.execute(
                    "SELECT * FROM validation_completions WHERE validation_run_id=?",
                    (validation_run_id,),
                ).fetchone()
                if completion_row is None:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun COMPLETED requires canonical Completion"
                    )
                completion = self._completion_from_row(completion_row)
                if completion.subject_hash != current.subject_key.subject_hash:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun Completion subject binding mismatch"
                    )
                completion_ref_payload = self._completion_ref(completion).model_dump(
                    mode="json"
                )
                if failure_ref is not None:
                    raise PostWritePersistenceIntegrityError(
                        "COMPLETED transition cannot carry run failure"
                    )
            if target_state is ValidationRunState.TECHNICAL_FAILURE and not failure_ref:
                raise PostWritePersistenceIntegrityError(
                    "TECHNICAL_FAILURE transition requires typed failure ref"
                )

            desired_payload = {
                "command_id": transition_command_id,
                "from_state": current.execution_state.value,
                "target_state": target_state.value,
                "expected_state_version": expected_state_version,
                "failure_ref": failure_ref,
                "completion_ref": completion_ref_payload,
            }

            started = current.started_at_epoch
            finished = current.finished_at_epoch
            failure_kind = None
            error_ref = None
            if target_state is ValidationRunState.RUNNING:
                started = now
            elif target_state is ValidationRunState.COMPLETED:
                finished = now
            else:
                finished = now
                failure_kind = str(failure_ref.get("failure_kind") or "technical_failure")
                error_ref = failure_ref

            next_version = current.state_version + 1
            cursor = connection.execute(
                """
                UPDATE validation_runs
                SET execution_state=?,state_version=?,started_at_epoch=?,
                    finished_at_epoch=?,run_failure_kind=?,run_error_ref_json=?,
                    updated_at_epoch=?
                WHERE validation_run_id=? AND state_version=?
                """,
                (
                    target_state.value,
                    next_version,
                    started,
                    finished,
                    failure_kind,
                    (
                        json.dumps(error_ref, sort_keys=True, separators=(",", ":"))
                        if error_ref is not None
                        else None
                    ),
                    now,
                    validation_run_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise PostWriteConcurrencyError(
                    "ValidationRun transition CAS lost"
                )
            sequence = self._next_event_sequence(connection, validation_run_id)
            event = self._event(
                validation_run_id=validation_run_id,
                event_id="run-transition:" + canonical_sha256(
                    {
                        "validation_run_id": validation_run_id,
                        "transition_command_id": transition_command_id,
                    }
                ),
                sequence=sequence,
                event_kind=f"RUN_{target_state.value}",
                payload=desired_payload,
                occurred_at_epoch=now,
            )
            self._insert_event(connection, event)
            updated_row = connection.execute(
                "SELECT * FROM validation_runs WHERE validation_run_id=?",
                (validation_run_id,),
            ).fetchone()
            return self._run_from_row(updated_row)

    def append_execution_event_if_absent_or_same(
        self,
        event: ValidationExecutionEventSchema,
    ) -> ValidationExecutionEventSchema:
        if canonical_sha256(event.payload) != event.payload_hash:
            raise PostWritePersistenceIntegrityError(
                "Validation event payload hash mismatch"
            )
        preimage = event.model_dump(mode="json")
        preimage.pop("event_hash", None)
        if canonical_sha256(preimage) != event.event_hash:
            raise PostWritePersistenceIntegrityError(
                "Validation event hash mismatch"
            )
        with self.database.normal_write_transaction() as connection:
            existing_row = connection.execute(
                "SELECT * FROM validation_execution_events "
                "WHERE validation_run_id=? AND event_id=?",
                (event.validation_run_id, event.event_id),
            ).fetchone()
            if existing_row is not None:
                existing = self._event_from_row(existing_row)
                if existing != event:
                    raise PostWritePersistenceIntegrityError(
                        "Validation event identity collision"
                    )
                return existing
            expected_sequence = self._next_event_sequence(
                connection, event.validation_run_id
            )
            if event.run_event_sequence != expected_sequence:
                raise PostWriteConcurrencyError(
                    "Validation event sequence is not next append position"
                )
            self._insert_event(connection, event)
            return event

    def put_fact_if_absent_or_same(
        self,
        fact: ValidationFactSchema,
    ) -> ValidationFactRefSchema:
        if _canonical_payload_hash(fact, "fact_sha256") != fact.fact_sha256:
            raise PostWritePersistenceIntegrityError(
                "ValidationFact canonical hash mismatch"
            )
        with self.database.normal_write_transaction() as connection:
            run_row = connection.execute(
                "SELECT subject_hash FROM validation_runs WHERE validation_run_id=?",
                (fact.validation_run_id,),
            ).fetchone()
            if run_row is None or run_row["subject_hash"] != fact.subject_hash:
                raise PostWritePersistenceIntegrityError(
                    "ValidationFact run/subject binding is invalid"
                )
            row = connection.execute(
                "SELECT * FROM validation_facts WHERE fact_id=?",
                (fact.fact_id,),
            ).fetchone()
            if row is not None:
                existing = self._fact_from_row(row)
                if existing != fact:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationFact identity collision"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO validation_facts(
                        fact_id,validation_run_id,subject_hash,
                        fact_schema_version,fact_contract_version,
                        fact_json,fact_sha256,created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact.fact_id,
                        fact.validation_run_id,
                        fact.subject_hash,
                        fact.schema_version,
                        fact.contract_version,
                        fact.model_dump_json(),
                        fact.fact_sha256,
                        time.time(),
                    ),
                )
        return self._fact_ref(fact)

    def resolve_fact(
        self,
        ref: ValidationFactRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ValidationFactSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM validation_facts WHERE fact_id=?",
                (ref.fact_id,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact ValidationFact ref is missing"
            )
        fact = self._fact_from_row(row)
        if self._fact_ref(fact) != ref:
            raise PostWritePersistenceIntegrityError(
                "ValidationFact exact ref/hash mismatch"
            )
        return fact

    def list_facts_for_run(
        self,
        validation_run_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[ValidationFactSchema, ...]:
        with self.database._exact_read_connection(connection) as read_connection:
            with current_diagnostics().span("post_write.list_facts_for_run", "DB_API_ELAPSED"):
                rows = read_connection.execute(
                    "SELECT * FROM validation_facts "
                    "WHERE validation_run_id=? ORDER BY fact_id",
                    (validation_run_id,),
                ).fetchall()
        facts = tuple(self._fact_from_row(row) for row in rows)
        for fact in facts:
            if fact.validation_run_id != validation_run_id:
                raise PostWritePersistenceIntegrityError(
                    "ValidationFact run enumeration mismatch"
                )
        return facts

    def put_completion_if_absent_or_same(
        self,
        completion: ValidationCompletionSchema,
    ) -> ValidationCompletionRefSchema:
        if (
            _canonical_payload_hash(completion, "completion_sha256")
            != completion.completion_sha256
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidationCompletion canonical hash mismatch"
            )
        self.validate_completion_proof_accounting(completion)
        with self.database.normal_write_transaction() as connection:
            run_row = connection.execute(
                "SELECT subject_hash FROM validation_runs WHERE validation_run_id=?",
                (completion.validation_run_id,),
            ).fetchone()
            if run_row is None or run_row["subject_hash"] != completion.subject_hash:
                raise PostWritePersistenceIntegrityError(
                    "ValidationCompletion run/subject binding is invalid"
                )
            row = connection.execute(
                "SELECT * FROM validation_completions WHERE validation_run_id=?",
                (completion.validation_run_id,),
            ).fetchone()
            if row is not None:
                existing = self._completion_from_row(row)
                if existing != completion:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationRun already has conflicting Completion"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO validation_completions(
                        completion_id,validation_run_id,subject_hash,
                        completion_schema_version,completion_contract_version,
                        completion_json,completion_sha256,created_at_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        completion.completion_id,
                        completion.validation_run_id,
                        completion.subject_hash,
                        completion.schema_version,
                        completion.contract_version,
                        completion.model_dump_json(),
                        completion.completion_sha256,
                        time.time(),
                    ),
                )
        return self._completion_ref(completion)

    def resolve_completion(
        self,
        ref: ValidationCompletionRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ValidationCompletionSchema:
        completion = self.resolve_completion_for_run(ref.validation_run_id, connection=connection)
        if completion is None:
            raise PostWritePersistenceIntegrityError(
                "required exact ValidationCompletion ref is missing"
            )
        if self._completion_ref(completion) != ref:
            raise PostWritePersistenceIntegrityError(
                "ValidationCompletion exact ref/hash mismatch"
            )
        return completion

    def resolve_completion_for_run(
        self,
        validation_run_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ValidationCompletionSchema | None:
        with self.database._exact_read_connection(connection) as read_connection:
            with current_diagnostics().span("post_write.resolve_completion", "DB_API_ELAPSED"):
                row = read_connection.execute(
                    "SELECT * FROM validation_completions WHERE validation_run_id=?",
                    (validation_run_id,),
                ).fetchone()
        if row is None:
            return None
        completion = self._completion_from_row(row)
        if completion.validation_run_id != validation_run_id:
            raise PostWritePersistenceIntegrityError(
                "ValidationCompletion run lookup mismatch"
            )
        self.validate_completion_proof_accounting(completion, connection=connection)
        return completion

    def validate_completion_proof_accounting(self, completion: ValidationCompletionSchema, *, connection: sqlite3.Connection | None = None) -> None:
        """Resolve proof transport against its exact terminal expected-item owner."""
        for item in completion.items:
            proofs = (*item.positive_proof_refs, *item.indeterminate_proof_refs)
            if not proofs:
                continue
            record = self.find_validator_execution_for_item(
                validation_run_id=completion.validation_run_id,
                expected_item_ref=item.expected_validation_item_ref,
                connection=connection,
            )
            if record is None or record.subject_hash != completion.subject_hash:
                raise PostWritePersistenceIntegrityError("Completion proof lacks exact ValidatorExecution owner")
            accounting = next(value for value in record.expected_item_accounting
                              if value.expected_validation_item_ref == item.expected_validation_item_ref)
            if (accounting.positive_proof_refs != item.positive_proof_refs
                    or accounting.indeterminate_proof_refs != item.indeterminate_proof_refs
                    or accounting.fact_refs != item.fact_refs
                    or accounting.execution_state.value != "COMPLETED"
                    or record.terminal_execution_state.value != "COMPLETED"):
                raise PostWritePersistenceIntegrityError("Completion proof differs from exact execution accounting")
            for proof in proofs:
                if proof.validation_run_id != completion.validation_run_id or proof not in record.intermediate_refs:
                    raise PostWritePersistenceIntegrityError("Completion proof escapes committed execution intermediates")
                self.resolve_intermediate(proof, connection=connection)


    def append_intermediate_if_absent_or_same(
        self,
        validation_run_id: str,
        event_id: str,
        typed_intermediate: dict,
        intermediate_ref: ValidationIntermediateRefSchema,
        causation_event_id: str | None = None,
        *,
        producer_ref: str,
        target_ref: str,
        occurred_at_epoch: float | None = None,
    ) -> ValidationIntermediateRefSchema:
        if intermediate_ref.validation_run_id != validation_run_id:
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate ref/run mismatch"
            )
        if intermediate_ref.event_id != event_id:
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate ref/event mismatch"
            )
        if canonical_sha256(typed_intermediate) != intermediate_ref.intermediate_sha256:
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate typed payload hash mismatch"
            )
        payload = ValidationIntermediateCommittedPayloadSchema(
            intermediate_ref=intermediate_ref,
            producer_ref=producer_ref,
            target_ref=target_ref,
            typed_payload=typed_intermediate,
            causation_event_id=causation_event_id,
        )
        now = float(time.time() if occurred_at_epoch is None else occurred_at_epoch)
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM validation_execution_events "
                "WHERE validation_run_id=? AND event_id=?",
                (validation_run_id, event_id),
            ).fetchone()
            if row is not None:
                existing = self._event_from_row(row)
                if existing.event_kind != "INTERMEDIATE_COMMITTED":
                    raise PostWritePersistenceIntegrityError(
                        "intermediate event id collides with another event kind"
                    )
                try:
                    existing_payload = ValidationIntermediateCommittedPayloadSchema.model_validate(
                        existing.payload
                    )
                except Exception as exc:
                    raise PostWritePersistenceIntegrityError(
                        "corrupt committed ValidationIntermediate payload"
                    ) from exc
                if existing_payload != payload:
                    raise PostWritePersistenceIntegrityError(
                        "ValidationIntermediate identity collision"
                    )
                return intermediate_ref
            sequence = self._next_event_sequence(connection, validation_run_id)
            event = self._event(
                validation_run_id=validation_run_id,
                event_id=event_id,
                sequence=sequence,
                event_kind="INTERMEDIATE_COMMITTED",
                payload=payload.model_dump(mode="json"),
                occurred_at_epoch=now,
            )
            self._insert_event(connection, event)
        return intermediate_ref

    def resolve_intermediate(
        self,
        ref: ValidationIntermediateRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM validation_execution_events "
                "WHERE validation_run_id=? AND event_id=?",
                (ref.validation_run_id, ref.event_id),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required ValidationIntermediate event is missing"
            )
        event = self._event_from_row(row)
        if event.event_kind != "INTERMEDIATE_COMMITTED":
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate ref resolves wrong event kind"
            )
        try:
            payload = ValidationIntermediateCommittedPayloadSchema.model_validate(
                event.payload
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt ValidationIntermediate payload"
            ) from exc
        if payload.intermediate_ref != ref:
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate exact ref mismatch"
            )
        if canonical_sha256(payload.typed_payload) != ref.intermediate_sha256:
            raise PostWritePersistenceIntegrityError(
                "ValidationIntermediate canonical hash mismatch"
            )
        return dict(payload.typed_payload)

    def finalize_validator_execution(
        self,
        terminal_event: ValidationExecutionEventSchema,
        validator_execution_record: ValidatorExecutionRecordSchema,
        *,
        stage_plan: StagePlanSchema,
        input_envelope: InputEnvelopeSchema,
    ) -> ValidatorExecutionRecordSchema:
        record = validator_execution_record
        _validate_stage_plan_input_conformance(stage_plan, input_envelope)
        if (
            input_envelope.validation_run_id != record.validation_run_id
            or input_envelope.subject_hash != record.subject_hash
            or input_envelope.validator_id != record.validator_id
            or input_envelope.validator_version != record.validator_version
            or input_envelope.stage_plan_ref != record.stage_plan_ref
            or input_envelope.stage_plan_version != record.stage_plan_version
            or input_envelope.input_hash != record.input_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord does not bind exact StagePlan/InputEnvelope"
            )
        expected_manifest = tuple(
            input_envelope.applicable_expected_validation_item_refs
        )
        accounted_manifest = tuple(
            item.expected_validation_item_ref
            for item in record.expected_item_accounting
        )
        if (
            len(expected_manifest) != len(accounted_manifest)
            or set(expected_manifest) != set(accounted_manifest)
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord expected-item accounting is incomplete"
            )
        if (
            record.terminal_execution_state.value == "COMPLETED"
            and any(
                item.execution_state.value == "TECHNICAL_FAILURE"
                for item in record.expected_item_accounting
            )
        ):
            raise PostWritePersistenceIntegrityError(
                "completed ValidatorExecution cannot contain technical-failure accounting"
            )
        if canonical_sha256(terminal_event.payload) != terminal_event.payload_hash:
            raise PostWritePersistenceIntegrityError(
                "validator terminal event payload hash mismatch"
            )
        terminal_preimage = terminal_event.model_dump(mode="json")
        terminal_preimage.pop("event_hash", None)
        if canonical_sha256(terminal_preimage) != terminal_event.event_hash:
            raise PostWritePersistenceIntegrityError(
                "validator terminal event hash mismatch"
            )
        expected_event_kind = (
            "VALIDATOR_COMPLETED"
            if record.terminal_execution_state.value == "COMPLETED"
            else "VALIDATOR_TECHNICAL_FAILURE"
        )
        if terminal_event.event_kind != expected_event_kind:
            raise PostWritePersistenceIntegrityError(
                "validator terminal event kind does not match terminal state"
            )
        if record.validation_run_id != terminal_event.validation_run_id:
            raise PostWritePersistenceIntegrityError(
                "validator terminal event/run mismatch"
            )
        if record.terminal_event_id != terminal_event.event_id:
            raise PostWritePersistenceIntegrityError(
                "validator record terminal_event_id mismatch"
            )
        if (
            terminal_event.payload.get("validator_execution_ref")
            != record.validator_execution_ref
        ):
            raise PostWritePersistenceIntegrityError(
                "validator terminal event does not bind exact execution ref"
            )
        if _canonical_payload_hash(record, "record_sha256") != record.record_sha256:
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord canonical hash mismatch"
            )
        with self.database.normal_write_transaction() as connection:
            run_row = connection.execute(
                "SELECT * FROM validation_runs WHERE validation_run_id=?",
                (record.validation_run_id,),
            ).fetchone()
            if run_row is None:
                raise PostWritePersistenceIntegrityError(
                    "ValidatorExecutionRecord requires exact ValidationRun"
                )
            run = self._run_from_row(run_row)
            if run.subject_key.subject_hash != record.subject_hash:
                raise PostWritePersistenceIntegrityError(
                    "ValidatorExecutionRecord run/subject binding mismatch"
                )

            for intermediate_ref in record.intermediate_refs:
                intermediate_row = connection.execute(
                    "SELECT * FROM validation_execution_events "
                    "WHERE validation_run_id=? AND event_id=?",
                    (
                        intermediate_ref.validation_run_id,
                        intermediate_ref.event_id,
                    ),
                ).fetchone()
                if intermediate_row is None:
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord references missing ValidationIntermediate"
                    )
                intermediate_event = self._event_from_row(intermediate_row)
                if intermediate_event.event_kind != "INTERMEDIATE_COMMITTED":
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord intermediate ref resolves wrong event kind"
                    )
                try:
                    intermediate_payload = (
                        ValidationIntermediateCommittedPayloadSchema.model_validate(
                            intermediate_event.payload
                        )
                    )
                except Exception as exc:
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord references corrupt ValidationIntermediate"
                    ) from exc
                if (
                    intermediate_payload.intermediate_ref != intermediate_ref
                    or canonical_sha256(
                        intermediate_payload.typed_payload
                    )
                    != intermediate_ref.intermediate_sha256
                ):
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord ValidationIntermediate binding mismatch"
                    )

            referenced_facts = list(record.produced_fact_refs)
            for item in record.expected_item_accounting:
                referenced_facts.extend(item.fact_refs)
            for fact_ref in referenced_facts:
                fact_row = connection.execute(
                    "SELECT * FROM validation_facts WHERE fact_id=?",
                    (fact_ref.fact_id,),
                ).fetchone()
                if fact_row is None:
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord references missing ValidationFact"
                    )
                fact = self._fact_from_row(fact_row)
                if (
                    self._fact_ref(fact) != fact_ref
                    or fact.validation_run_id != record.validation_run_id
                    or fact.subject_hash != record.subject_hash
                ):
                    raise PostWritePersistenceIntegrityError(
                        "ValidatorExecutionRecord ValidationFact binding mismatch"
                    )

            existing = connection.execute(
                "SELECT * FROM validator_execution_records "
                "WHERE validator_execution_ref=?",
                (record.validator_execution_ref,),
            ).fetchone()
            event_existing = connection.execute(
                "SELECT * FROM validation_execution_events "
                "WHERE validation_run_id=? AND event_id=?",
                (terminal_event.validation_run_id, terminal_event.event_id),
            ).fetchone()
            if existing is not None or event_existing is not None:
                if existing is None or event_existing is None:
                    raise PostWritePersistenceIntegrityError(
                        "validator terminal authority is partially committed"
                    )
                stored_event = self._event_from_row(event_existing)
                stored_record = self._record_from_row(existing)
                if stored_event != terminal_event or stored_record != record:
                    raise PostWritePersistenceIntegrityError(
                        "validator terminal identity collision"
                    )
                return stored_record
            expected_sequence = self._next_event_sequence(
                connection, terminal_event.validation_run_id
            )
            if terminal_event.run_event_sequence != expected_sequence:
                raise PostWriteConcurrencyError(
                    "validator terminal event is not next run event"
                )
            self._insert_event(connection, terminal_event)
            connection.execute(
                """
                INSERT INTO validator_execution_records(
                    validator_execution_ref,validation_run_id,validator_id,
                    validator_version,record_json,record_sha256,
                    terminal_event_id,created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.validator_execution_ref,
                    record.validation_run_id,
                    record.validator_id,
                    record.validator_version,
                    record.model_dump_json(),
                    record.record_sha256,
                    record.terminal_event_id,
                    time.time(),
                ),
            )
        return record

    def find_validator_execution_for_item(
        self, *, validation_run_id: str, expected_item_ref: str, validator_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> ValidatorExecutionRecordSchema | None:
        """Read committed identities without rematerializing validator input."""
        with self.database._exact_read_connection(connection) as read_connection:
            rows = read_connection.execute(
                "SELECT validator_execution_ref FROM validator_execution_records "
                "WHERE validation_run_id=?" + (" AND validator_id=?" if validator_id is not None else ""),
                (validation_run_id, validator_id) if validator_id is not None else (validation_run_id,),
            ).fetchall()
        matches = []
        for row in rows:
            record = self.resolve_validator_execution_record(row["validator_execution_ref"], connection=connection)
            if any(item.expected_validation_item_ref == expected_item_ref
                   for item in record.expected_item_accounting):
                matches.append(record)
        if len(matches) > 1:
            raise PostWritePersistenceIntegrityError("ambiguous committed ValidatorExecution item")
        return matches[0] if matches else None

    def resolve_validator_execution_record(
        self,
        validator_execution_ref: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ValidatorExecutionRecordSchema:
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM validator_execution_records "
                "WHERE validator_execution_ref=?",
                (validator_execution_ref,),
            ).fetchone()
            if row is None:
                raise PostWritePersistenceIntegrityError(
                    "required ValidatorExecutionRecord is missing"
                )
            record = self._record_from_row(row)
            event_row = read_connection.execute(
                "SELECT * FROM validation_execution_events "
                "WHERE validation_run_id=? AND event_id=?",
                (record.validation_run_id, record.terminal_event_id),
            ).fetchone()
        if event_row is None:
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord terminal event is missing"
            )
        event = self._event_from_row(event_row)
        expected_event_kind = (
            "VALIDATOR_COMPLETED"
            if record.terminal_execution_state.value == "COMPLETED"
            else "VALIDATOR_TECHNICAL_FAILURE"
        )
        if (
            event.event_kind != expected_event_kind
            or event.payload.get("validator_execution_ref")
            != record.validator_execution_ref
        ):
            raise PostWritePersistenceIntegrityError(
                "ValidatorExecutionRecord terminal event binding mismatch"
            )
        for intermediate_ref in record.intermediate_refs:
            self.resolve_intermediate(intermediate_ref, connection=connection)
        return record


class GateDecisionStore:
    """Immutable Target DeliveryGateDecision persistence."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> DeliveryGateDecisionSchema:
        try:
            decision = DeliveryGateDecisionSchema.model_validate_json(
                row["decision_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt DeliveryGateDecision"
            ) from exc
        if (
            row["delivery_gate_decision_id"]
            != decision.delivery_gate_decision_id
            or row["decision_schema_version"] != decision.schema_version
            or row["gate_contract_version"] != decision.gate_contract_version
            or row["validation_run_id"] != decision.validation_run_id
            or row["subject_hash"] != decision.subject_key.subject_hash
            or row["input_manifest_hash"]
            != decision.gate_input_manifest.input_manifest_hash
            or row["decision_hash"] != decision.decision_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "DeliveryGateDecision row/hash mismatch"
            )
        return decision

    def put_if_absent_or_same(
        self,
        decision: DeliveryGateDecisionSchema,
    ) -> DeliveryGateDecisionRefSchema:
        candidate = DeliveryGateDecisionSchema.model_validate(decision)
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM delivery_gate_decisions "
                "WHERE delivery_gate_decision_id=?",
                (candidate.delivery_gate_decision_id,),
            ).fetchone()
            if row is not None:
                existing = self._decision_from_row(row)
                if existing.decision_hash != candidate.decision_hash:
                    raise PostWritePersistenceIntegrityError(
                        "DeliveryGateDecision identity collision"
                    )
                return existing.as_ref()
            connection.execute(
                """
                INSERT INTO delivery_gate_decisions(
                    delivery_gate_decision_id,decision_schema_version,
                    gate_contract_version,validation_run_id,subject_hash,
                    input_manifest_hash,decision_hash,decision_json,
                    created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.delivery_gate_decision_id,
                    candidate.schema_version,
                    candidate.gate_contract_version,
                    candidate.validation_run_id,
                    candidate.subject_key.subject_hash,
                    candidate.gate_input_manifest.input_manifest_hash,
                    candidate.decision_hash,
                    candidate.model_dump_json(),
                    candidate.created_at_epoch,
                ),
            )
        return candidate.as_ref()

    def resolve_exact(
        self,
        ref: DeliveryGateDecisionRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> DeliveryGateDecisionSchema:
        exact_ref = DeliveryGateDecisionRefSchema.model_validate(ref)
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM delivery_gate_decisions "
                "WHERE delivery_gate_decision_id=?",
                (exact_ref.delivery_gate_decision_id,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact DeliveryGateDecision ref is missing"
            )
        decision = self._decision_from_row(row)
        if decision.as_ref() != exact_ref:
            raise PostWritePersistenceIntegrityError(
                "DeliveryGateDecision exact ref/hash mismatch"
            )
        return decision


class CorrectionPolicyStore:
    """Immutable exact historical CorrectionPolicy persistence."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _policy_from_row(row: sqlite3.Row) -> CorrectionPolicySchema:
        try:
            policy = CorrectionPolicySchema.model_validate_json(row["policy_json"])
        except Exception as exc:
            raise PostWritePersistenceIntegrityError("corrupt CorrectionPolicy") from exc
        if (
            row["policy_id"] != policy.policy_id
            or row["policy_schema_version"] != policy.schema_version
            or row["policy_version"] != policy.policy_version
            or row["policy_hash"] != policy.policy_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "CorrectionPolicy row/hash mismatch"
            )
        return policy

    def put_if_absent_or_same(
        self,
        policy: CorrectionPolicySchema,
    ) -> CorrectionPolicyRefSchema:
        candidate = CorrectionPolicySchema.model_validate(policy)
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM correction_policies WHERE policy_id=? AND policy_version=?",
                (candidate.policy_id, candidate.policy_version),
            ).fetchone()
            if row is not None:
                existing = self._policy_from_row(row)
                if existing.policy_hash != candidate.policy_hash:
                    raise PostWritePersistenceIntegrityError(
                        "CorrectionPolicy identity collision"
                    )
                return existing.as_ref()
            connection.execute(
                """
                INSERT INTO correction_policies(
                    policy_id,policy_schema_version,policy_version,policy_hash,
                    policy_json,created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.policy_id,
                    candidate.schema_version,
                    candidate.policy_version,
                    candidate.policy_hash,
                    candidate.model_dump_json(),
                    time.time(),
                ),
            )
        return candidate.as_ref()

    def resolve_exact(
        self,
        ref: CorrectionPolicyRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CorrectionPolicySchema:
        exact_ref = CorrectionPolicyRefSchema.model_validate(ref)
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM correction_policies WHERE policy_id=? AND policy_version=?",
                (exact_ref.policy_id, exact_ref.policy_version),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact CorrectionPolicy ref is missing"
            )
        policy = self._policy_from_row(row)
        if policy.as_ref() != exact_ref:
            raise PostWritePersistenceIntegrityError(
                "CorrectionPolicy exact ref/hash mismatch"
            )
        return policy


class ControlDecisionStore:
    """Immutable Target PostWriteControlDecision persistence."""

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> PostWriteControlDecisionSchema:
        try:
            decision = PostWriteControlDecisionSchema.model_validate_json(
                row["decision_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt PostWriteControlDecision"
            ) from exc
        artifact_ref = decision.input_manifest.artifact_state_ref
        if (
            row["control_decision_id"] != decision.control_decision_id
            or row["decision_schema_version"] != decision.schema_version
            or row["control_contract_version"] != decision.control_contract_version
            or row["validation_run_id"] != decision.validation_run_id
            or row["artifact_id"] != artifact_ref.artifact_id
            or int(row["artifact_version"]) != artifact_ref.artifact_version
            or row["input_manifest_hash"]
            != decision.input_manifest.input_manifest_hash
            or row["control_decision_hash"] != decision.control_decision_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "PostWriteControlDecision row/hash mismatch"
            )
        return decision

    def put_if_absent_or_same(
        self,
        decision: PostWriteControlDecisionSchema,
    ) -> PostWriteControlDecisionRefSchema:
        candidate = PostWriteControlDecisionSchema.model_validate(decision)
        artifact_ref = candidate.input_manifest.artifact_state_ref
        with self.database.normal_write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM post_write_control_decisions "
                "WHERE control_decision_id=?",
                (candidate.control_decision_id,),
            ).fetchone()
            if row is not None:
                existing = self._decision_from_row(row)
                if existing.control_decision_hash != candidate.control_decision_hash:
                    raise PostWritePersistenceIntegrityError(
                        "PostWriteControlDecision identity collision"
                    )
                return existing.as_ref()
            connection.execute(
                """
                INSERT INTO post_write_control_decisions(
                    control_decision_id,decision_schema_version,
                    control_contract_version,validation_run_id,artifact_id,
                    artifact_version,input_manifest_hash,control_decision_hash,
                    decision_json,created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.control_decision_id,
                    candidate.schema_version,
                    candidate.control_contract_version,
                    candidate.validation_run_id,
                    artifact_ref.artifact_id,
                    artifact_ref.artifact_version,
                    candidate.input_manifest.input_manifest_hash,
                    candidate.control_decision_hash,
                    candidate.model_dump_json(),
                    candidate.created_at_epoch,
                ),
            )
        return candidate.as_ref()

    def resolve_exact(
        self,
        ref: PostWriteControlDecisionRefSchema,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PostWriteControlDecisionSchema:
        exact_ref = PostWriteControlDecisionRefSchema.model_validate(ref)
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM post_write_control_decisions "
                "WHERE control_decision_id=?",
                (exact_ref.control_decision_id,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required exact PostWriteControlDecision ref is missing"
            )
        decision = self._decision_from_row(row)
        if decision.as_ref() != exact_ref:
            raise PostWritePersistenceIntegrityError(
                "PostWriteControlDecision exact ref/hash mismatch"
            )
        return decision


class CorrectionAttemptStore:
    """Projection-only semantic-repair observability store.

    Deliberately exposes no reserve/dispatch/budget mutation API. Canonical semantic
    repair Budget is SIF-12 and canonical committed-result recovery is MA-owned.
    """

    def __init__(self, database: PostWriteSqliteDatabase) -> None:
        self.database = database

    @staticmethod
    def _projection_from_row(row: sqlite3.Row) -> CorrectionAttemptRecordSchema:
        try:
            projection = CorrectionAttemptRecordSchema.model_validate_json(
                row["projection_json"]
            )
        except Exception as exc:
            raise PostWritePersistenceIntegrityError(
                "corrupt CorrectionAttempt projection"
            ) from exc
        artifact_ref = projection.target_artifact_ref
        if (
            row["projection_id"] != projection.projection_id
            or row["run_id"] != projection.run_id
            or row["artifact_id"] != artifact_ref.artifact_id
            or int(row["artifact_version"]) != artifact_ref.artifact_version
            or row["control_decision_id"]
            != projection.control_decision_ref.control_decision_id
            or row["embedded_authorization_id"]
            != projection.embedded_authorization_id
            or row["embedded_authorization_hash"]
            != projection.embedded_authorization_hash
            or row["outcome"] != projection.outcome.value
            or row["projection_hash"] != projection.projection_hash
        ):
            raise PostWritePersistenceIntegrityError(
                "CorrectionAttempt projection row/hash mismatch"
            )
        return projection

    def put_if_absent_or_same(
        self,
        projection: CorrectionAttemptRecordSchema,
    ) -> str:
        candidate = CorrectionAttemptRecordSchema.model_validate(projection)
        artifact_ref = candidate.target_artifact_ref
        with self.database.normal_write_transaction(run_id=candidate.run_id) as connection:
            row = connection.execute(
                "SELECT * FROM correction_attempt_projections WHERE projection_id=?",
                (candidate.projection_id,),
            ).fetchone()
            if row is not None:
                existing = self._projection_from_row(row)
                if existing.projection_hash != candidate.projection_hash:
                    raise PostWritePersistenceIntegrityError(
                        "CorrectionAttempt projection identity collision"
                    )
                return existing.projection_id
            connection.execute(
                """
                INSERT INTO correction_attempt_projections(
                    projection_id,run_id,artifact_id,artifact_version,
                    control_decision_id,embedded_authorization_id,
                    embedded_authorization_hash,outcome,projection_hash,
                    projection_json,created_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.projection_id,
                    candidate.run_id,
                    artifact_ref.artifact_id,
                    artifact_ref.artifact_version,
                    candidate.control_decision_ref.control_decision_id,
                    candidate.embedded_authorization_id,
                    candidate.embedded_authorization_hash,
                    candidate.outcome.value,
                    candidate.projection_hash,
                    candidate.model_dump_json(),
                    candidate.created_at_epoch,
                ),
            )
        return candidate.projection_id

    def resolve_exact(self, projection_id: str, *, connection: sqlite3.Connection | None = None) -> CorrectionAttemptRecordSchema:
        normalized = str(projection_id or "").strip()
        if not normalized:
            raise ValueError("projection_id is required")
        with self.database._exact_read_connection(connection) as read_connection:
            row = read_connection.execute(
                "SELECT * FROM correction_attempt_projections WHERE projection_id=?",
                (normalized,),
            ).fetchone()
        if row is None:
            raise PostWritePersistenceIntegrityError(
                "required CorrectionAttempt projection is missing"
            )
        return self._projection_from_row(row)

    def list_for_artifact_lineage(
        self,
        artifact_ref: ArtifactStateRefSchema,
    ) -> tuple[CorrectionAttemptRecordSchema, ...]:
        exact_ref = ArtifactStateRefSchema.model_validate(artifact_ref)
        with self.database.read_transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM correction_attempt_projections "
                "WHERE artifact_id=? ORDER BY created_at_epoch, projection_id",
                (exact_ref.artifact_id,),
            ).fetchall()
        return tuple(self._projection_from_row(row) for row in rows)


__all__ = [
    "ArtifactStateStore",
    "ControlDecisionStore",
    "CorrectionAttemptStore",
    "CorrectionPolicyStore",
    "DocumentBindingStore",
    "GateDecisionStore",
    "LEGACY_POST_WRITE_SCHEMA_VERSION",
    "POST_WRITE_SCHEMA_VERSION",
    "POST_WRITE_V2_SCHEMA_VERSION",
    "PREVIOUS_POST_WRITE_SCHEMA_VERSION",
    "PostWriteEntryStore",
    "RequirementStore",
    "PostWriteConcurrencyError",
    "PostWritePersistenceIntegrityError",
    "PostWriteReleaseCommitFence",
    "PostWriteSqliteDatabase",
    "QualityDeliveryContractStore",
    "ValidationStore",
]
