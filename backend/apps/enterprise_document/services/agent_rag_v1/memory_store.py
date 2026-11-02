"""PostgreSQL persistence adapter for canonical SIF-09 Durable Memory.

The adapter owns only physical transaction/idempotency/index persistence. Memory
semantics, source currentness, projection policies, selector schemas, and
Context eligibility remain in DurableMemoryRuntime / Domain registries.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Callable, Iterator, Protocol

from apps.enterprise_document.schemas.agent_rag_v1.memory import (
    DurableMemoryRecordSchema,
    DurableMemoryRefSchema,
    MemorySelectorIndexRecordSchema,
)
from core.runtime.side_effects import (
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    LogicalOperationRef,
    validate_sif02_semantic_fingerprint,
)


class MemoryStoreError(RuntimeError):
    """Base M7 physical persistence failure."""


class MemoryStoreConflict(MemoryStoreError):
    """Immutable Memory identity/operation resolved conflicting payload."""


class MemoryStoreIntegrityError(MemoryStoreError):
    """Persisted Memory/Index bytes fail canonical contract validation."""


class MemoryStoreUnavailable(MemoryStoreError):
    """PostgreSQL connection/driver is unavailable."""


class _Cursor(Protocol):
    rowcount: int

    def execute(self, query: str, params: tuple[Any, ...] | None = None): ...
    def fetchone(self): ...
    def fetchall(self): ...
    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], _Connection]


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS durable_memory_record (
        memory_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        scope_ref TEXT NOT NULL,
        logical_operation_id TEXT NOT NULL UNIQUE,
        record_semantic_fingerprint TEXT NOT NULL,
        record_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS durable_memory_record_run_order_idx
    ON durable_memory_record (run_id, memory_type, scope_ref, memory_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_selector_index (
        memory_id TEXT PRIMARY KEY
            REFERENCES durable_memory_record(memory_id) ON DELETE CASCADE,
        run_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        scope_ref TEXT NOT NULL,
        source_memory_fingerprint TEXT NOT NULL,
        selector_schema_id TEXT NOT NULL,
        selector_schema_version TEXT NOT NULL,
        index_policy_id TEXT NOT NULL,
        index_policy_version TEXT NOT NULL,
        selector_values_json JSONB NOT NULL,
        index_record_json JSONB NOT NULL,
        built_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_selector_index_run_order_idx
    ON memory_selector_index (run_id, memory_type, scope_ref, memory_id)
    """,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, (bytes, bytearray)):
        decoded = json.loads(bytes(value).decode("utf-8"))
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise MemoryStoreIntegrityError("persisted JSON payload must be an object")
    return decoded


def _row_value(row: Any, index: int, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    try:
        return row[index]
    except Exception as exc:
        raise MemoryStoreIntegrityError(
            f"unexpected PostgreSQL row shape for {key}"
        ) from exc


class PostgresDurableMemoryStore:
    """Persistence-only PostgreSQL adapter frozen by SIF-09/207."""

    def __init__(
        self,
        *,
        database_url: str,
        connection_factory: ConnectionFactory | None = None,
        statement_timeout_ms: int = 5000,
        schema_version: str = "memory_v1",
        ensure_schema: bool = True,
    ) -> None:
        self.database_url = str(database_url or "").strip()
        self.connection_factory = connection_factory
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.schema_version = str(schema_version or "").strip()
        if self.statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms must be positive")
        if not self.schema_version:
            raise ValueError("schema_version must be non-empty")
        if self.schema_version != "memory_v1":
            raise ValueError(
                f"unsupported Durable Memory schema_version: {self.schema_version}"
            )
        if self.connection_factory is None and not self.database_url:
            raise MemoryStoreUnavailable(
                "Memory PostgreSQL DSN is required without injected connection_factory"
            )
        if ensure_schema:
            self.ensure_schema()

    def _new_connection(self) -> _Connection:
        if self.connection_factory is not None:
            return self.connection_factory()
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise MemoryStoreUnavailable(
                "psycopg is not installed; inject connection_factory or install "
                "the deployment PostgreSQL driver"
            ) from exc
        try:
            return psycopg.connect(self.database_url)
        except Exception as exc:
            raise MemoryStoreUnavailable(
                "unable to connect to Durable Memory PostgreSQL"
            ) from exc

    @contextmanager
    def _transaction(self) -> Iterator[_Cursor]:
        connection = self._new_connection()
        cursor: _Cursor | None = None
        try:
            cursor = connection.cursor()
            # Parameterized, transaction-local PostgreSQL timeout.
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self.statement_timeout_ms),),
            )
            yield cursor
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            finally:
                if cursor is not None:
                    cursor.close()
                connection.close()
            raise
        else:
            if cursor is not None:
                cursor.close()
            connection.close()

    def ensure_schema(self) -> None:
        try:
            with self._transaction() as cursor:
                for statement in _DDL:
                    cursor.execute(statement)
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable(
                "unable to initialize Durable Memory PostgreSQL schema"
            ) from exc

    def validate_schema(self) -> None:
        """Read-only required-capability probe; application startup is not DDL."""
        try:
            with self._transaction() as cursor:
                cursor.execute("SELECT memory_id, run_id, memory_type, scope_ref, "
                    "logical_operation_id, record_semantic_fingerprint, record_json, created_at "
                    "FROM durable_memory_record LIMIT 0")
                cursor.execute("SELECT memory_id, run_id, memory_type, scope_ref, source_memory_fingerprint, selector_schema_id, "
                    "selector_schema_version, index_policy_id, index_policy_version, "
                    "selector_values_json, index_record_json, built_at FROM memory_selector_index LIMIT 0")
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable('required Durable Memory schema is unavailable') from exc

    # ------------------------------------------------------------------
    # Row decoding / physical-key integrity
    # ------------------------------------------------------------------

    @staticmethod
    def _record_from_row(row: Any) -> DurableMemoryRecordSchema:
        payload = _decode_json(_row_value(row, 0, "record_json"))
        try:
            return DurableMemoryRecordSchema.model_validate(payload)
        except Exception as exc:
            raise MemoryStoreIntegrityError(
                "persisted DurableMemoryRecord is invalid"
            ) from exc

    @staticmethod
    def _index_from_row(row: Any) -> MemorySelectorIndexRecordSchema:
        payload = _decode_json(_row_value(row, 0, "index_record_json"))
        try:
            return MemorySelectorIndexRecordSchema.model_validate(payload)
        except Exception as exc:
            raise MemoryStoreIntegrityError(
                "persisted MemorySelectorIndex record is invalid"
            ) from exc

    @classmethod
    def _record_from_keyed_row(cls, row: Any) -> DurableMemoryRecordSchema:
        row_memory_id = str(_row_value(row, 0, "memory_id"))
        payload = _decode_json(_row_value(row, 1, "record_json"))
        try:
            record = DurableMemoryRecordSchema.model_validate(payload)
        except Exception as exc:
            raise MemoryStoreIntegrityError(
                "persisted DurableMemoryRecord is invalid"
            ) from exc
        if record.memory_ref.memory_id != row_memory_id:
            raise MemoryStoreIntegrityError(
                "DurableMemoryRecord row key does not match canonical memory_ref"
            )
        return record

    @classmethod
    def _index_from_keyed_row(cls, row: Any) -> MemorySelectorIndexRecordSchema:
        row_memory_id = str(_row_value(row, 0, "memory_id"))
        payload = _decode_json(_row_value(row, 1, "index_record_json"))
        try:
            record = MemorySelectorIndexRecordSchema.model_validate(payload)
        except Exception as exc:
            raise MemoryStoreIntegrityError(
                "persisted MemorySelectorIndex record is invalid"
            ) from exc
        if record.memory_ref.memory_id != row_memory_id:
            raise MemoryStoreIntegrityError(
                "MemorySelectorIndex row key does not match canonical memory_ref"
            )
        return record

    @staticmethod
    def _exact_filter_sql(
        *,
        run_id: str,
        scope_refs: tuple[str, ...],
        memory_types: tuple[str, ...],
    ) -> tuple[list[str], list[Any]]:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")
        clauses = ["run_id = %s"]
        params: list[Any] = [normalized]
        if scope_refs:
            clauses.append("scope_ref = ANY(%s)")
            params.append(list(scope_refs))
        if memory_types:
            clauses.append("memory_type = ANY(%s)")
            params.append(list(memory_types))
        return clauses, params

    # ------------------------------------------------------------------
    # DurableMemoryRecord
    # ------------------------------------------------------------------

    def _select_record_by_memory_id(
        self,
        cursor: _Cursor,
        memory_id: str,
    ) -> DurableMemoryRecordSchema | None:
        cursor.execute(
            """
            SELECT record_json
            FROM durable_memory_record
            WHERE memory_id = %s
            """,
            (memory_id,),
        )
        row = cursor.fetchone()
        return None if row is None else self._record_from_row(row)

    def _select_record_by_operation(
        self,
        cursor: _Cursor,
        logical_operation_id: str,
    ) -> DurableMemoryRecordSchema | None:
        cursor.execute(
            """
            SELECT record_json
            FROM durable_memory_record
            WHERE logical_operation_id = %s
            """,
            (logical_operation_id,),
        )
        row = cursor.fetchone()
        return None if row is None else self._record_from_row(row)

    @staticmethod
    def _same_record(
        existing: DurableMemoryRecordSchema,
        incoming: DurableMemoryRecordSchema,
    ) -> bool:
        # created_at is audit-only and intentionally excluded from the frozen
        # semantic fingerprint. Concurrent same-operation projectors can build
        # equivalent candidates at different wall-clock instants.
        return existing.model_dump(
            mode="json",
            exclude={"created_at"},
        ) == incoming.model_dump(
            mode="json",
            exclude={"created_at"},
        )

    def put_memory_record_if_absent_or_same(
        self,
        record: DurableMemoryRecordSchema,
    ) -> DurableMemoryRecordSchema:
        incoming = DurableMemoryRecordSchema.model_validate(record)
        operation_id = incoming.committed_operation_ref.logical_operation_id
        try:
            with self._transaction() as cursor:
                existing_id = self._select_record_by_memory_id(
                    cursor,
                    incoming.memory_ref.memory_id,
                )
                if existing_id is not None:
                    if not self._same_record(existing_id, incoming):
                        raise MemoryStoreConflict(
                            "memory_id already exists with different immutable payload"
                        )
                    return existing_id

                existing_op = self._select_record_by_operation(
                    cursor,
                    operation_id,
                )
                if existing_op is not None:
                    if not self._same_record(existing_op, incoming):
                        raise MemoryStoreConflict(
                            "logical Memory operation already committed different payload"
                        )
                    return existing_op

                cursor.execute(
                    """
                    INSERT INTO durable_memory_record (
                        memory_id,
                        run_id,
                        memory_type,
                        scope_ref,
                        logical_operation_id,
                        record_semantic_fingerprint,
                        record_json,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (memory_id) DO NOTHING
                    RETURNING memory_id
                    """,
                    (
                        incoming.memory_ref.memory_id,
                        incoming.memory_ref.run_id,
                        incoming.memory_ref.memory_type.value,
                        incoming.memory_ref.scope_ref,
                        operation_id,
                        incoming.record_semantic_fingerprint,
                        _json(incoming),
                        incoming.created_at,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is not None:
                    return incoming

                # A concurrent transaction may have won the memory_id race.
                existing_id = self._select_record_by_memory_id(
                    cursor,
                    incoming.memory_ref.memory_id,
                )
                if existing_id is not None and self._same_record(
                    existing_id,
                    incoming,
                ):
                    return existing_id
                raise MemoryStoreConflict(
                    "concurrent Memory commit resolved conflicting immutable payload"
                )
        except MemoryStoreError:
            raise
        except Exception as exc:
            # A UNIQUE(logical_operation_id) race can raise before ON CONFLICT
            # handles memory_id. Resolve the operation in a fresh transaction.
            try:
                existing = self.get_record_by_operation_id(operation_id)
            except MemoryStoreError:
                raise MemoryStoreUnavailable(
                    "PostgreSQL Memory commit failed"
                ) from exc
            if existing is not None and self._same_record(existing, incoming):
                return existing
            if existing is not None:
                raise MemoryStoreConflict(
                    "logical Memory operation committed conflicting payload"
                ) from exc
            raise MemoryStoreUnavailable(
                "PostgreSQL Memory commit failed"
            ) from exc

    def get_memory_record(
        self,
        memory_ref: DurableMemoryRefSchema,
    ) -> DurableMemoryRecordSchema | None:
        ref = DurableMemoryRefSchema.model_validate(memory_ref)
        try:
            with self._transaction() as cursor:
                record = self._select_record_by_memory_id(
                    cursor,
                    ref.memory_id,
                )
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("MemoryRecord lookup failed") from exc
        if record is None:
            return None
        if record.memory_ref != ref:
            raise MemoryStoreIntegrityError(
                "memory_id resolves a different DurableMemoryRef"
            )
        return record

    def get_record_by_operation_id(
        self,
        logical_operation_id: str,
    ) -> DurableMemoryRecordSchema | None:
        normalized = str(logical_operation_id or "").strip()
        if not normalized:
            raise ValueError("logical_operation_id must be non-empty")
        try:
            with self._transaction() as cursor:
                return self._select_record_by_operation(cursor, normalized)
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable(
                "Memory logical-operation lookup failed"
            ) from exc

    def resolve_memory_record(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        try:
            record = self.get_record_by_operation_id(
                operation_ref.logical_operation_id
            )
        except MemoryStoreIntegrityError:
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.INTEGRITY_INVALID,
            )
        if record is None:
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.ABSENT,
            )
        committed = record.committed_operation_ref
        if (
            committed.logical_operation_id != operation_ref.logical_operation_id
            or committed.operation_kind != operation_ref.operation_kind
            or committed.run_id != operation_ref.run_id
            or committed.operation_scope_ref != operation_ref.operation_scope_ref
            or committed.attempt_scope_ref != operation_ref.attempt_scope_ref
            or committed.attempt_id != operation_ref.attempt_id
        ):
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.INTEGRITY_INVALID,
            )
        persisted_fingerprint = record.record_semantic_fingerprint.removeprefix(
            "sha256:"
        )
        if persisted_fingerprint != fingerprint:
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.COMMITTED_CONFLICT,
            )
        return CommittedResultResolution(
            operation_ref.logical_operation_id,
            fingerprint,
            CommittedResultResolutionStatus.COMMITTED_SAME,
            (record.memory_ref.memory_id,),
        )

    # ------------------------------------------------------------------
    # Rebuildable MemorySelectorIndex
    # ------------------------------------------------------------------

    def upsert_selector_index(
        self,
        record: MemorySelectorIndexRecordSchema,
    ) -> MemorySelectorIndexRecordSchema:
        incoming = MemorySelectorIndexRecordSchema.model_validate(record)
        try:
            with self._transaction() as cursor:
                source = self._select_record_by_memory_id(
                    cursor,
                    incoming.memory_ref.memory_id,
                )
                if source is None:
                    raise MemoryStoreIntegrityError(
                        "SelectorIndex cannot outlive/create missing MemoryRecord"
                    )
                if (
                    source.memory_ref != incoming.memory_ref
                    or source.record_semantic_fingerprint
                    != incoming.source_memory_fingerprint
                ):
                    raise MemoryStoreIntegrityError(
                        "SelectorIndex source Memory fingerprint mismatch"
                    )
                cursor.execute(
                    """
                    INSERT INTO memory_selector_index (
                        memory_id,
                        run_id,
                        memory_type,
                        scope_ref,
                        source_memory_fingerprint,
                        selector_schema_id,
                        selector_schema_version,
                        index_policy_id,
                        index_policy_version,
                        selector_values_json,
                        index_record_json,
                        built_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        memory_type = EXCLUDED.memory_type,
                        scope_ref = EXCLUDED.scope_ref,
                        source_memory_fingerprint = EXCLUDED.source_memory_fingerprint,
                        selector_schema_id = EXCLUDED.selector_schema_id,
                        selector_schema_version = EXCLUDED.selector_schema_version,
                        index_policy_id = EXCLUDED.index_policy_id,
                        index_policy_version = EXCLUDED.index_policy_version,
                        selector_values_json = EXCLUDED.selector_values_json,
                        index_record_json = EXCLUDED.index_record_json,
                        built_at = EXCLUDED.built_at
                    """,
                    (
                        incoming.memory_ref.memory_id,
                        incoming.memory_ref.run_id,
                        incoming.memory_ref.memory_type.value,
                        incoming.memory_ref.scope_ref,
                        incoming.source_memory_fingerprint,
                        incoming.selector_schema_id,
                        incoming.selector_schema_version,
                        incoming.index_policy_id,
                        incoming.index_policy_version,
                        _json(incoming.selector_values),
                        _json(incoming),
                        incoming.built_at,
                    ),
                )
                return incoming
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("SelectorIndex upsert failed") from exc

    def get_selector_index(
        self,
        memory_ref: DurableMemoryRefSchema,
    ) -> MemorySelectorIndexRecordSchema | None:
        ref = DurableMemoryRefSchema.model_validate(memory_ref)
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    """
                    SELECT index_record_json
                    FROM memory_selector_index
                    WHERE memory_id = %s
                    """,
                    (ref.memory_id,),
                )
                row = cursor.fetchone()
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("SelectorIndex lookup failed") from exc
        if row is None:
            return None
        record = self._index_from_row(row)
        if record.memory_ref != ref:
            raise MemoryStoreIntegrityError(
                "SelectorIndex memory_id resolves different MemoryRef"
            )
        return record

    def delete_selector_index(
        self,
        memory_ref: DurableMemoryRefSchema,
    ) -> None:
        ref = DurableMemoryRefSchema.model_validate(memory_ref)
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "DELETE FROM memory_selector_index WHERE memory_id = %s",
                    (ref.memory_id,),
                )
        except Exception as exc:
            raise MemoryStoreUnavailable("SelectorIndex delete failed") from exc

    def delete_all_selector_index_for_run(self, run_id: str) -> None:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "DELETE FROM memory_selector_index WHERE run_id = %s",
                    (normalized,),
                )
        except Exception as exc:
            raise MemoryStoreUnavailable(
                "SelectorIndex run rebuild reset failed"
            ) from exc

    def count_selector_index(
        self,
        *,
        run_id: str,
        scope_refs: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
    ) -> int:
        clauses, params = self._exact_filter_sql(
            run_id=run_id,
            scope_refs=scope_refs,
            memory_types=memory_types,
        )
        query = (
            "SELECT COUNT(*) FROM memory_selector_index WHERE "
            + " AND ".join(clauses)
        )
        try:
            with self._transaction() as cursor:
                cursor.execute(query, tuple(params))
                row = cursor.fetchone()
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("SelectorIndex count failed") from exc
        if row is None:
            raise MemoryStoreIntegrityError("SelectorIndex count returned no row")
        return int(_row_value(row, 0, "count"))

    def query_selector_index(
        self,
        *,
        run_id: str,
        scope_refs: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
        after_key: tuple[str, str, str] | None = None,
        limit: int = 500,
    ) -> tuple[MemorySelectorIndexRecordSchema, ...]:
        if limit <= 0 or limit > 1000:
            raise ValueError("SelectorIndex query limit must be in [1, 1000]")
        clauses, params = self._exact_filter_sql(
            run_id=run_id,
            scope_refs=scope_refs,
            memory_types=memory_types,
        )
        if after_key is not None:
            if len(after_key) != 3 or any(not str(item).strip() for item in after_key):
                raise ValueError("after_key must be a non-empty structural Memory key")
            clauses.append("(memory_type, scope_ref, memory_id) > (%s, %s, %s)")
            params.extend(after_key)
        params.append(limit)
        query = (
            "SELECT memory_id, index_record_json FROM memory_selector_index WHERE "
            + " AND ".join(clauses)
            + " ORDER BY memory_type, scope_ref, memory_id LIMIT %s"
        )
        try:
            with self._transaction() as cursor:
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall() or []
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("SelectorIndex query failed") from exc
        return tuple(self._index_from_keyed_row(row) for row in rows)

    # ------------------------------------------------------------------
    # Bounded exact MemoryRecord scan / reconciliation enumeration
    # ------------------------------------------------------------------

    def count_memory_records_exact(
        self,
        *,
        run_id: str,
        scope_refs: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
    ) -> int:
        clauses, params = self._exact_filter_sql(
            run_id=run_id,
            scope_refs=scope_refs,
            memory_types=memory_types,
        )
        query = (
            "SELECT COUNT(*) FROM durable_memory_record WHERE "
            + " AND ".join(clauses)
        )
        try:
            with self._transaction() as cursor:
                cursor.execute(query, tuple(params))
                row = cursor.fetchone()
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("MemoryRecord count failed") from exc
        if row is None:
            raise MemoryStoreIntegrityError("MemoryRecord count returned no row")
        return int(_row_value(row, 0, "count"))

    def query_memory_records_exact(
        self,
        *,
        run_id: str,
        scope_refs: tuple[str, ...] = (),
        memory_types: tuple[str, ...] = (),
        after_key: tuple[str, str, str] | None = None,
        limit: int = 200,
    ) -> tuple[DurableMemoryRecordSchema, ...]:
        if limit <= 0 or limit > 1000:
            raise ValueError("Memory exact-query limit must be in [1, 1000]")
        clauses, params = self._exact_filter_sql(
            run_id=run_id,
            scope_refs=scope_refs,
            memory_types=memory_types,
        )
        if after_key is not None:
            if len(after_key) != 3 or any(not str(item).strip() for item in after_key):
                raise ValueError("after_key must be a non-empty structural Memory key")
            clauses.append("(memory_type, scope_ref, memory_id) > (%s, %s, %s)")
            params.extend(after_key)
        params.append(limit)

        query = (
            "SELECT memory_id, record_json FROM durable_memory_record WHERE "
            + " AND ".join(clauses)
            + " ORDER BY memory_type, scope_ref, memory_id LIMIT %s"
        )
        try:
            with self._transaction() as cursor:
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall() or []
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable("MemoryRecord exact query failed") from exc
        return tuple(self._record_from_keyed_row(row) for row in rows)

    def enumerate_memory_records_for_reconcile(
        self,
        *,
        run_id: str,
        limit: int = 200,
        after_memory_id: str | None = None,
    ) -> tuple[DurableMemoryRecordSchema, ...]:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")
        if limit <= 0 or limit > 1000:
            raise ValueError("reconcile limit must be in [1, 1000]")
        params: list[Any] = [normalized]
        predicate = "run_id = %s"
        if after_memory_id is not None:
            marker = str(after_memory_id).strip()
            if not marker:
                raise ValueError("after_memory_id cannot be blank")
            predicate += " AND memory_id > %s"
            params.append(marker)
        params.append(limit)
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    (
                        "SELECT memory_id, record_json FROM durable_memory_record "
                        f"WHERE {predicate} "
                        "ORDER BY memory_id LIMIT %s"
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall() or []
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable(
                "Memory reconciliation enumeration failed"
            ) from exc
        return tuple(self._record_from_keyed_row(row) for row in rows)


__all__ = [
    "ConnectionFactory",
    "MemoryStoreConflict",
    "MemoryStoreError",
    "MemoryStoreIntegrityError",
    "MemoryStoreUnavailable",
    "PostgresDurableMemoryStore",
]
