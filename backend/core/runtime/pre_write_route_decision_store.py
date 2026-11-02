"""Canonical immutable persistence for Gate V2 pre-write RouteDecision."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Protocol

from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
    PreWriteRouteDecisionSchema,
)
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import (
    EXECUTION_CONTROL_DATABASE_FILENAME,
    ensure_pre_write_execution_schema_v2,
)


class PreWriteRouteDecisionPersistenceIntegrityError(ValueError):
    """Canonical RouteDecision persistence or exact-ref integrity failure."""


class PreWriteRouteDecisionStore(Protocol):
    def put_if_absent_or_same(
        self,
        decision: PreWriteRouteDecisionSchema,
        *,
        authorization_snapshot_sha256: str,
    ) -> PreWriteRouteDecisionRefSchema: ...

    def resolve_exact(
        self,
        ref: PreWriteRouteDecisionRefSchema,
    ) -> PreWriteRouteDecisionSchema: ...

    def resolve_exact_for_snapshot(
        self,
        ref: PreWriteRouteDecisionRefSchema,
        *,
        authorization_snapshot_sha256: str,
    ) -> PreWriteRouteDecisionSchema: ...


class SQLitePreWriteRouteDecisionStore:
    """Logical RouteDecision store sharing execution_control.sqlite3 V2."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be >= 0")
        self.database_path = Path(database_path).expanduser().resolve()
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        ensure_pre_write_execution_schema_v2(
            self.database_path,
            self.connect_timeout_seconds,
        )

    @classmethod
    def for_data_root(
        cls,
        data_root: str | Path,
        **kwargs: object,
    ) -> "SQLitePreWriteRouteDecisionStore":
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

    @staticmethod
    def _snapshot_hash(value: str) -> str:
        normalized = str(value or "").strip()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("authorization_snapshot_sha256 must be lowercase sha256 hex")
        return normalized

    @staticmethod
    def _decision_payload(
        decision: PreWriteRouteDecisionSchema,
    ) -> tuple[PreWriteRouteDecisionSchema, dict[str, object], str, str]:
        normalized = PreWriteRouteDecisionSchema.model_validate(decision)
        payload = normalized.model_dump(mode="json", round_trip=True)
        decision_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decision_sha256 = canonical_sha256(payload)
        return normalized, payload, decision_json, decision_sha256

    @staticmethod
    def _ref(decision: PreWriteRouteDecisionSchema) -> PreWriteRouteDecisionRefSchema:
        return PreWriteRouteDecisionRefSchema(
            route_decision_id=decision.route_decision_id,
            route_decision_schema_version=decision.schema_version,
            route_decision_contract_version=decision.contract_version,
        )

    def put_if_absent_or_same(
        self,
        decision: PreWriteRouteDecisionSchema,
        *,
        authorization_snapshot_sha256: str,
    ) -> PreWriteRouteDecisionRefSchema:
        normalized, _, decision_json, decision_sha256 = self._decision_payload(decision)
        snapshot_hash = self._snapshot_hash(authorization_snapshot_sha256)
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pre_write_route_decisions WHERE route_decision_id = ?",
                (normalized.route_decision_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO pre_write_route_decisions("
                    "route_decision_id, schema_version, contract_version, decision_json, "
                    "decision_sha256, authorization_snapshot_sha256, created_at_epoch"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        normalized.route_decision_id,
                        normalized.schema_version,
                        normalized.contract_version,
                        decision_json,
                        decision_sha256,
                        snapshot_hash,
                        now,
                    ),
                )
            else:
                expected = (
                    normalized.schema_version,
                    normalized.contract_version,
                    decision_json,
                    decision_sha256,
                    snapshot_hash,
                )
                actual = (
                    str(row["schema_version"]),
                    str(row["contract_version"]),
                    str(row["decision_json"]),
                    str(row["decision_sha256"]),
                    str(row["authorization_snapshot_sha256"]),
                )
                if actual != expected:
                    raise PreWriteRouteDecisionPersistenceIntegrityError(
                        "immutable RouteDecision identity collision or authorization snapshot conflict"
                    )
            connection.commit()
            return self._ref(normalized)
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _resolve_row(
        self,
        ref: PreWriteRouteDecisionRefSchema,
    ) -> tuple[PreWriteRouteDecisionSchema, sqlite3.Row]:
        normalized_ref = PreWriteRouteDecisionRefSchema.model_validate(ref)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pre_write_route_decisions WHERE route_decision_id = ?",
                (normalized_ref.route_decision_id,),
            ).fetchone()
        if row is None:
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "PreWriteRouteDecisionRef does not resolve"
            )
        if (
            str(row["schema_version"]) != normalized_ref.route_decision_schema_version
            or str(row["contract_version"]) != normalized_ref.route_decision_contract_version
        ):
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "PreWriteRouteDecisionRef schema/contract mismatch"
            )
        try:
            decision = PreWriteRouteDecisionSchema.model_validate_json(
                str(row["decision_json"])
            )
        except Exception as exc:
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "stored RouteDecision JSON is invalid"
            ) from exc
        if decision.route_decision_id != normalized_ref.route_decision_id:
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "stored RouteDecision identity mismatches ref"
            )
        payload = decision.model_dump(mode="json", round_trip=True)
        canonical_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical_json != str(row["decision_json"]):
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "stored RouteDecision JSON is not canonical"
            )
        if canonical_sha256(payload) != str(row["decision_sha256"]):
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "stored RouteDecision hash mismatch"
            )
        return decision, row

    def resolve_exact(
        self,
        ref: PreWriteRouteDecisionRefSchema,
    ) -> PreWriteRouteDecisionSchema:
        decision, _ = self._resolve_row(ref)
        return decision

    def resolve_exact_for_snapshot(
        self,
        ref: PreWriteRouteDecisionRefSchema,
        *,
        authorization_snapshot_sha256: str,
    ) -> PreWriteRouteDecisionSchema:
        snapshot_hash = self._snapshot_hash(authorization_snapshot_sha256)
        decision, row = self._resolve_row(ref)
        if str(row["authorization_snapshot_sha256"]) != snapshot_hash:
            raise PreWriteRouteDecisionPersistenceIntegrityError(
                "RouteDecision authorization snapshot mismatch"
            )
        return decision


__all__ = [
    "PreWriteRouteDecisionPersistenceIntegrityError",
    "PreWriteRouteDecisionStore",
    "SQLitePreWriteRouteDecisionStore",
]
