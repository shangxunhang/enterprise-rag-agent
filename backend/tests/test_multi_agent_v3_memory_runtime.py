from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1.memory import (
    CanonicalMemorySourceRefEnvelopeSchema,
    DomainMemoryType,
    MemoryFreshnessEligibility,
    MemoryProjectionRequestSchema,
    MemoryQueryPurpose,
    MemoryQueryResultSchema,
    MemoryQuerySchema,
    MemoryRecallMode,
    MemorySelectorOperator,
    TypedMemorySelectorClauseSchema,
)
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import (
    DurableMemoryRuntime,
    DurableMemoryRuntimeError,
    MemoryCursorInvalid,
    MemoryIndexProjectionError,
    MemoryOperationConflict,
    MemoryProjectionRegistration,
    MemoryRecordIntegrityInvalid,
    MemorySelectorRegistration,
    MemorySelectorSchemaInvalid,
    MemorySourceRefInvalid,
    MemorySourceResolution,
    MemoryTypeUnregistered,
)
from apps.enterprise_document.services.agent_rag_v1.memory_store import (
    MemoryStoreConflict,
    MemoryStoreIntegrityError,
    MemoryStoreUnavailable,
    PostgresDurableMemoryStore,
)
from core.config import get_settings
from core.hashing import canonical_sha256


NOW = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)


def _sha256_ref(value) -> str:
    return "sha256:" + canonical_sha256(value)


@dataclass
class _FakePgDatabase:
    memory: dict[str, dict] = field(default_factory=dict)
    operation_to_memory: dict[str, str] = field(default_factory=dict)
    selector_index: dict[str, dict] = field(default_factory=dict)
    fail_commit_once: bool = False
    fail_index_upsert_once: bool = False
    commits: int = 0
    rollbacks: int = 0
    reconcile_rows_scanned: int = 0


class _FakePgConnection:
    def __init__(self, database: _FakePgDatabase) -> None:
        self.database = database
        self.memory = copy.deepcopy(database.memory)
        self.operation_to_memory = copy.deepcopy(database.operation_to_memory)
        self.selector_index = copy.deepcopy(database.selector_index)
        self.closed = False

    def cursor(self):
        return _FakePgCursor(self)

    def commit(self) -> None:
        if self.database.fail_commit_once:
            self.database.fail_commit_once = False
            raise RuntimeError("simulated PostgreSQL commit failure")
        self.database.memory = copy.deepcopy(self.memory)
        self.database.operation_to_memory = copy.deepcopy(self.operation_to_memory)
        self.database.selector_index = copy.deepcopy(self.selector_index)
        self.database.commits += 1

    def rollback(self) -> None:
        self.database.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _FakePgCursor:
    def __init__(self, connection: _FakePgConnection) -> None:
        self.connection = connection
        self.rows: list[tuple] = []
        self.rowcount = 0
        self.closed = False

    @staticmethod
    def _normalized(query: str) -> str:
        return " ".join(str(query).split()).lower()

    def execute(self, query: str, params=None):
        sql = self._normalized(query)
        params = tuple(params or ())
        self.rows = []
        self.rowcount = 0

        if sql.startswith("select set_config("):
            self.rows = [(params[0] if params else None,)]
            self.rowcount = 1
            return self

        if sql.startswith("create table") or sql.startswith("create index"):
            return self

        if (
            "select record_json from durable_memory_record" in sql
            and "where memory_id = %s" in sql
            and "order by" not in sql
        ):
            payload = self.connection.memory.get(str(params[0]))
            if payload is not None:
                self.rows = [(copy.deepcopy(payload),)]
                self.rowcount = 1
            return self

        if (
            "select record_json from durable_memory_record" in sql
            and "where logical_operation_id = %s" in sql
        ):
            memory_id = self.connection.operation_to_memory.get(str(params[0]))
            if memory_id is not None:
                self.rows = [(copy.deepcopy(self.connection.memory[memory_id]),)]
                self.rowcount = 1
            return self

        if sql.startswith("insert into durable_memory_record"):
            (
                memory_id,
                _run_id,
                _memory_type,
                _scope_ref,
                operation_id,
                _fingerprint,
                record_json,
                _created_at,
            ) = params
            memory_id = str(memory_id)
            operation_id = str(operation_id)
            if memory_id in self.connection.memory:
                return self
            if operation_id in self.connection.operation_to_memory:
                raise RuntimeError("duplicate logical_operation_id")
            payload = json.loads(record_json)
            self.connection.memory[memory_id] = payload
            self.connection.operation_to_memory[operation_id] = memory_id
            self.rows = [(memory_id,)]
            self.rowcount = 1
            return self

        if sql.startswith("insert into memory_selector_index"):
            if self.connection.database.fail_index_upsert_once:
                self.connection.database.fail_index_upsert_once = False
                raise RuntimeError("simulated selector index write failure")
            memory_id = str(params[0])
            index_json = params[10]
            self.connection.selector_index[memory_id] = json.loads(index_json)
            self.rowcount = 1
            return self

        if (
            "select index_record_json from memory_selector_index" in sql
            and "where memory_id = %s" in sql
        ):
            payload = self.connection.selector_index.get(str(params[0]))
            if payload is not None:
                self.rows = [(copy.deepcopy(payload),)]
                self.rowcount = 1
            return self

        if sql.startswith("delete from memory_selector_index where memory_id = %s"):
            self.connection.selector_index.pop(str(params[0]), None)
            return self

        if sql.startswith("delete from memory_selector_index where run_id = %s"):
            run_id = str(params[0])
            doomed = [
                memory_id
                for memory_id, payload in self.connection.selector_index.items()
                if payload["memory_ref"]["run_id"] == run_id
            ]
            for memory_id in doomed:
                self.connection.selector_index.pop(memory_id, None)
            return self

        if sql.startswith("select count(*) from memory_selector_index where"):
            pos = 0
            run_id = str(params[pos])
            pos += 1
            scope_refs = None
            memory_types = None
            if "scope_ref = any(%s)" in sql:
                scope_refs = set(params[pos])
                pos += 1
            if "memory_type = any(%s)" in sql:
                memory_types = set(params[pos])
                pos += 1
            count = sum(
                1
                for payload in self.connection.selector_index.values()
                if payload.get("memory_ref", {}).get("run_id") == run_id
                and (
                    scope_refs is None
                    or payload.get("memory_ref", {}).get("scope_ref") in scope_refs
                )
                and (
                    memory_types is None
                    or payload.get("memory_ref", {}).get("memory_type") in memory_types
                )
            )
            self.rows = [(count,)]
            self.rowcount = 1
            return self

        if sql.startswith("select count(*) from durable_memory_record where"):
            pos = 0
            run_id = str(params[pos])
            pos += 1
            scope_refs = None
            memory_types = None
            if "scope_ref = any(%s)" in sql:
                scope_refs = set(params[pos])
                pos += 1
            if "memory_type = any(%s)" in sql:
                memory_types = set(params[pos])
                pos += 1
            count = sum(
                1
                for payload in self.connection.memory.values()
                if payload["memory_ref"]["run_id"] == run_id
                and (
                    scope_refs is None
                    or payload["memory_ref"]["scope_ref"] in scope_refs
                )
                and (
                    memory_types is None
                    or payload["memory_ref"]["memory_type"] in memory_types
                )
            )
            self.rows = [(count,)]
            self.rowcount = 1
            return self

        if (
            "select memory_id, index_record_json from memory_selector_index" in sql
            and "order by memory_type, scope_ref, memory_id limit %s" in sql
        ):
            pos = 0
            run_id = str(params[pos])
            pos += 1
            scope_refs = None
            memory_types = None
            if "scope_ref = any(%s)" in sql:
                scope_refs = set(params[pos])
                pos += 1
            if "memory_type = any(%s)" in sql:
                memory_types = set(params[pos])
                pos += 1
            after_key = None
            if "(memory_type, scope_ref, memory_id) > (%s, %s, %s)" in sql:
                after_key = (
                    str(params[pos]),
                    str(params[pos + 1]),
                    str(params[pos + 2]),
                )
                pos += 3
            limit = int(params[pos])
            payloads = []
            for physical_memory_id, payload in self.connection.selector_index.items():
                ref = payload.get("memory_ref", {})
                key = (
                    str(ref.get("memory_type", "")),
                    str(ref.get("scope_ref", "")),
                    physical_memory_id,
                )
                if ref.get("run_id") != run_id:
                    continue
                if scope_refs is not None and ref.get("scope_ref") not in scope_refs:
                    continue
                if memory_types is not None and ref.get("memory_type") not in memory_types:
                    continue
                if after_key is not None and key <= after_key:
                    continue
                payloads.append((physical_memory_id, copy.deepcopy(payload)))
            payloads.sort(
                key=lambda item: (
                    item[1].get("memory_ref", {}).get("memory_type", ""),
                    item[1].get("memory_ref", {}).get("scope_ref", ""),
                    item[0],
                )
            )
            self.rows = [(memory_id, payload) for memory_id, payload in payloads[:limit]]
            self.rowcount = len(self.rows)
            return self

        if (
            "select memory_id, record_json from durable_memory_record" in sql
            and "order by memory_type, scope_ref, memory_id limit %s" in sql
        ):
            pos = 0
            run_id = str(params[pos])
            pos += 1
            scope_refs = None
            memory_types = None
            after_key = None
            if "scope_ref = any(%s)" in sql:
                scope_refs = set(params[pos])
                pos += 1
            if "memory_type = any(%s)" in sql:
                memory_types = set(params[pos])
                pos += 1
            if "(memory_type, scope_ref, memory_id) > (%s, %s, %s)" in sql:
                after_key = (
                    str(params[pos]),
                    str(params[pos + 1]),
                    str(params[pos + 2]),
                )
                pos += 3
            limit = int(params[pos])
            payloads = []
            for physical_memory_id, payload in self.connection.memory.items():
                ref = payload["memory_ref"]
                key = (
                    ref["memory_type"],
                    ref["scope_ref"],
                    physical_memory_id,
                )
                if ref["run_id"] != run_id:
                    continue
                if scope_refs is not None and ref["scope_ref"] not in scope_refs:
                    continue
                if memory_types is not None and ref["memory_type"] not in memory_types:
                    continue
                if after_key is not None and key <= after_key:
                    continue
                payloads.append((physical_memory_id, copy.deepcopy(payload)))
            payloads.sort(
                key=lambda item: (
                    item[1]["memory_ref"]["memory_type"],
                    item[1]["memory_ref"]["scope_ref"],
                    item[0],
                )
            )
            self.rows = [
                (memory_id, payload)
                for memory_id, payload in payloads[:limit]
            ]
            self.rowcount = len(self.rows)
            return self

        if (
            "select memory_id, record_json from durable_memory_record" in sql
            and "order by memory_id limit %s" in sql
        ):
            run_id = str(params[0])
            pos = 1
            after_memory_id = None
            if "memory_id > %s" in sql:
                after_memory_id = str(params[pos])
                pos += 1
            limit = int(params[pos])
            payloads = [
                (physical_memory_id, copy.deepcopy(payload))
                for physical_memory_id, payload in self.connection.memory.items()
                if payload["memory_ref"]["run_id"] == run_id
                and (
                    after_memory_id is None
                    or physical_memory_id > after_memory_id
                )
            ]
            payloads.sort(key=lambda item: item[0])
            self.rows = [
                (memory_id, payload)
                for memory_id, payload in payloads[:limit]
            ]
            self.rowcount = len(self.rows)
            self.connection.database.reconcile_rows_scanned += self.rowcount
            return self

        raise AssertionError(f"fake PostgreSQL does not recognize SQL: {sql}")

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)

    def fetchall(self):
        rows = list(self.rows)
        self.rows.clear()
        return rows

    def close(self) -> None:
        self.closed = True


@dataclass
class _SourceState:
    topic: str
    tags: tuple[str, ...] = ()
    current: bool = True
    resolvable: bool = True
    integrity_valid: bool = True
    lifecycle_active: bool | None = True
    historical_eligible: bool = True
    reason_codes: tuple[str, ...] = ("SOURCE_CURRENT",)


class _SourceRegistry:
    def __init__(self) -> None:
        self.states: dict[str, _SourceState] = {}
        self.binding_allowed = True

    def add(self, source_id: str, *, topic: str, tags=(), **kwargs) -> None:
        self.states[source_id] = _SourceState(
            topic=topic,
            tags=tuple(tags),
            **kwargs,
        )

    def resolver(
        self,
        source: CanonicalMemorySourceRefEnvelopeSchema,
    ) -> MemorySourceResolution:
        source_id = str(source.exact_source_ref["source_id"])
        state = self.states[source_id]
        if not state.resolvable:
            return MemorySourceResolution(
                exact_source_payload=None,
                source_semantic_fingerprint=None,
                source_resolvable=False,
                integrity_valid=False,
                current=False,
                lifecycle_active=state.lifecycle_active,
                historical_eligible=False,
                reason_codes=("SOURCE_UNRESOLVABLE",),
            )
        if not state.integrity_valid:
            return MemorySourceResolution(
                exact_source_payload=source.exact_source_ref,
                source_semantic_fingerprint=source.source_semantic_fingerprint,
                source_resolvable=True,
                integrity_valid=False,
                current=False,
                lifecycle_active=state.lifecycle_active,
                historical_eligible=False,
                reason_codes=("SOURCE_INTEGRITY_INVALID",),
            )
        return MemorySourceResolution(
            exact_source_payload=source.exact_source_ref,
            source_semantic_fingerprint=source.source_semantic_fingerprint,
            source_resolvable=True,
            integrity_valid=True,
            current=state.current,
            lifecycle_active=state.lifecycle_active,
            historical_eligible=state.historical_eligible,
            reason_codes=state.reason_codes,
        )


def _source(
    source_id: str,
    *,
    scope_ref: str = "section:s1",
    run_id: str = "run-1",
) -> CanonicalMemorySourceRefEnvelopeSchema:
    exact = {
        "run_id": run_id,
        "scope_ref": scope_ref,
        "source_id": source_id,
        "version": 1,
    }
    return CanonicalMemorySourceRefEnvelopeSchema(
        source_authority="fact_authority",
        source_kind="fact_snapshot",
        source_scope_ref=scope_ref,
        logical_source_ref=f"fact-snapshot:{source_id}",
        exact_source_ref=exact,
        exact_source_ref_schema_id="fact_snapshot_ref_v1",
        exact_source_ref_sha256=_sha256_ref(exact),
        source_semantic_fingerprint=_sha256_ref(
            {
                "source_id": source_id,
                "version": 1,
            }
        ),
    )


def _store(database: _FakePgDatabase) -> PostgresDurableMemoryStore:
    return PostgresDurableMemoryStore(
        database_url="postgresql://memory.test/db",
        connection_factory=lambda: _FakePgConnection(database),
        statement_timeout_ms=2500,
        schema_version="memory_v1",
    )


def _runtime(
    database: _FakePgDatabase,
    registry: _SourceRegistry,
    *,
    selector_policy_version: str = "v1",
    currentness_policy_version: str = "v1",
) -> DurableMemoryRuntime:
    runtime = DurableMemoryRuntime(
        store=_store(database),
        clock=lambda: NOW,
    )
    runtime.register_source_resolver(
        source_authority="fact_authority",
        source_kind="fact_snapshot",
        exact_source_ref_schema_id="fact_snapshot_ref_v1",
        resolver=registry.resolver,
    )

    def _source_binding_validator(request, primary, dependencies) -> None:
        if not registry.binding_allowed:
            raise ValueError("DOMAIN_SOURCE_CHAIN_COHERENT failed")
        if dependencies:
            raise ValueError("fact projection does not accept dependencies in this test")
        if primary.exact_source_payload is None:
            raise ValueError("source is unresolved")
        if primary.exact_source_payload["run_id"] != request.run_id:
            raise ValueError("RUN_MATCH failed")
        if primary.exact_source_payload["scope_ref"] != request.scope_ref:
            raise ValueError("SCOPE_MATCH failed")

    def _projection_builder(request, primary, dependencies):
        source_id = str(primary.exact_source_payload["source_id"])
        state = registry.states[source_id]
        return {
            "source_id": source_id,
            "topic": state.topic,
            "tags": list(state.tags),
        }

    def _projection_validator(payload) -> None:
        if set(payload) != {"source_id", "topic", "tags"}:
            raise ValueError("unexpected projection fields")
        if not isinstance(payload["source_id"], str) or not payload["source_id"]:
            raise ValueError("source_id")
        if not isinstance(payload["topic"], str) or not payload["topic"]:
            raise ValueError("topic")
        if not isinstance(payload["tags"], list):
            raise ValueError("tags")

    runtime.register_projection(
        MemoryProjectionRegistration(
            memory_type=DomainMemoryType.FACT,
            projection_schema_id="fact_memory_projection_v1",
            projection_schema_version="v1",
            projection_policy_id="fact_memory_policy",
            projection_policy_version="v1",
            currentness_resolver_id="fact_currentness_resolver",
            currentness_policy_version=currentness_policy_version,
            projection_validator=_projection_validator,
            projection_builder=_projection_builder,
            source_binding_validator=_source_binding_validator,
        )
    )

    def _selector_projector(record):
        payload = record.projection.projection_payload
        return {
            "topic": payload["topic"],
            "tags": payload["tags"],
        }

    def _selector_validator(values) -> None:
        if set(values) != {"topic", "tags"}:
            raise ValueError("selector fields")
        if not isinstance(values["topic"], str):
            raise ValueError("topic")
        if not isinstance(values["tags"], list):
            raise ValueError("tags")

    def _selector_clause_validator(clause) -> None:
        if clause.key == "topic":
            if clause.operator is MemorySelectorOperator.EQ:
                if not isinstance(clause.value, str):
                    raise ValueError("topic EQ requires string")
                return
            if clause.operator is MemorySelectorOperator.IN:
                if not isinstance(clause.value, (list, tuple)) or not all(
                    isinstance(item, str) for item in clause.value
                ):
                    raise ValueError("topic IN requires string sequence")
                return
            raise ValueError("topic does not support CONTAINS_SET_MEMBER")
        if clause.key == "tags":
            if clause.operator is MemorySelectorOperator.CONTAINS_SET_MEMBER:
                if not isinstance(clause.value, str):
                    raise ValueError("tags member requires string")
                return
            if clause.operator is MemorySelectorOperator.EQ:
                if not isinstance(clause.value, (list, tuple)) or not all(
                    isinstance(item, str) for item in clause.value
                ):
                    raise ValueError("tags EQ requires string sequence")
                return
            raise ValueError("tags does not support IN")
        raise ValueError("unknown selector key")

    runtime.register_selector(
        MemorySelectorRegistration(
            memory_type=DomainMemoryType.FACT,
            selector_schema_id="fact_selector_v1",
            selector_schema_version="v1",
            allowed_keys=frozenset({"topic", "tags"}),
            selector_validator=_selector_validator,
            selector_projector=_selector_projector,
            selector_clause_validator=_selector_clause_validator,
            index_policy_id="fact_selector_index",
            index_policy_version=selector_policy_version,
        )
    )
    return runtime


def _request(
    source: CanonicalMemorySourceRefEnvelopeSchema,
    *,
    run_id: str = "run-1",
    scope_ref: str | None = None,
) -> MemoryProjectionRequestSchema:
    return MemoryProjectionRequestSchema(
        run_id=run_id,
        memory_type=DomainMemoryType.FACT,
        scope_ref=scope_ref or source.source_scope_ref,
        primary_source_ref=source,
        projection_schema_id="fact_memory_projection_v1",
        projection_schema_version="v1",
        projection_policy_id="fact_memory_policy",
        projection_policy_version="v1",
    )


def _selector_clause(topic: str) -> TypedMemorySelectorClauseSchema:
    return TypedMemorySelectorClauseSchema(
        selector_schema_id="fact_selector_v1",
        selector_schema_version="v1",
        key="topic",
        operator=MemorySelectorOperator.EQ,
        value=topic,
    )


def test_contract_hashes_and_memory_identity_are_deterministic() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    source = _source("f1")
    request = _request(source)

    first = runtime.project_source(request)
    replay = runtime.project_source(request)

    assert replay == first
    assert first.memory_ref.memory_id.startswith("mem_")
    assert first.source_binding.source_set_sha256 == runtime.source_set_sha256(
        source,
        (),
    )
    assert first.source_binding.source_binding_semantic_fingerprint == (
        runtime.source_binding_fingerprint(
            primary=source,
            dependencies=(),
            source_set_sha256=first.source_binding.source_set_sha256,
            currentness_resolver_id="fact_currentness_resolver",
            currentness_policy_version="v1",
        )
    )
    assert first.projection.projection_payload_sha256 == _sha256_ref(
        first.projection.projection_payload
    )
    assert first.record_semantic_fingerprint == runtime.record_semantic_fingerprint(
        memory_ref=first.memory_ref,
        source_binding=first.source_binding,
        projection=first.projection,
        projection_policy_id="fact_memory_policy",
        projection_policy_version="v1",
    )
    assert first.committed_operation_ref.operation_kind == "MEMORY_PROJECTION_COMMIT"


def test_same_operation_changed_semantics_fails_closed() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    request = _request(_source("f1"))
    runtime.project_source(request)

    registry.states["f1"].topic = "changed-without-version-bump"
    with pytest.raises(MemoryOperationConflict):
        runtime.project_source(request)


def test_put_if_absent_or_same_ignores_audit_timestamp_but_not_semantics() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))
    store = runtime.store

    later = record.model_copy(update={"created_at": record.created_at + timedelta(seconds=5)})
    assert store.put_memory_record_if_absent_or_same(later) == record

    changed = record.model_copy(
        update={"projection_policy_version": "v2"},
    )
    with pytest.raises(MemoryStoreConflict):
        store.put_memory_record_if_absent_or_same(changed)


def test_postgres_transaction_commit_failure_rolls_back_canonical_record() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    db.fail_commit_once = True

    with pytest.raises(MemoryStoreUnavailable):
        runtime.project_source(_request(_source("f1")))

    assert db.memory == {}
    assert db.selector_index == {}
    assert db.rollbacks >= 1


def test_delayed_projection_from_historical_but_resolvable_source_is_allowed() -> None:
    registry = _SourceRegistry()
    registry.add(
        "f1",
        topic="network",
        current=False,
        historical_eligible=True,
        reason_codes=("SOURCE_SUPERSEDED",),
    )
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)

    record = runtime.project_source(_request(_source("f1")))
    assessment = runtime.assess_freshness(record)

    assert assessment.eligibility is MemoryFreshnessEligibility.HISTORICAL_ONLY
    assert runtime.resolve_record(record.memory_ref) == record


def test_unresolvable_source_is_rejected_before_memory_commit() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network", resolvable=False)
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)

    with pytest.raises(MemorySourceRefInvalid):
        runtime.project_source(_request(_source("f1")))

    assert db.memory == {}


def test_freshness_re_resolves_active_historical_and_ineligible() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))

    active = runtime.assess_freshness(record)
    assert active.eligibility is MemoryFreshnessEligibility.ACTIVE_RECALL

    registry.states["f1"].current = False
    registry.states["f1"].reason_codes = ("SOURCE_SUPERSEDED",)
    historical = runtime.assess_freshness(record)
    assert historical.eligibility is MemoryFreshnessEligibility.HISTORICAL_ONLY

    registry.states["f1"].resolvable = False
    ineligible = runtime.assess_freshness(record)
    assert ineligible.eligibility is MemoryFreshnessEligibility.INELIGIBLE


def test_domain_source_chain_coherence_is_rechecked_at_read_time() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))

    registry.binding_allowed = False
    assessment = runtime.assess_freshness(record)

    assert assessment.eligibility is MemoryFreshnessEligibility.INELIGIBLE
    assert "DOMAIN_SOURCE_CHAIN_INCOHERENT" in assessment.assessment_reason_codes


def test_index_failure_after_memory_commit_does_not_roll_back_memory() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    db.fail_index_upsert_once = True

    with pytest.raises(MemoryIndexProjectionError) as exc_info:
        runtime.project_source(_request(_source("f1")))

    memory_ref = exc_info.value.memory_ref
    assert runtime.store.get_memory_record(memory_ref) is not None
    assert memory_ref.memory_id in db.memory
    assert memory_ref.memory_id not in db.selector_index


def test_missing_or_stale_index_is_rebuildable_and_never_becomes_truth() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))

    runtime.store.delete_selector_index(record.memory_ref)
    reconciled = runtime.reconcile_source(record.memory_ref)
    assert reconciled.selector_rebuilt is True
    assert record.memory_ref.memory_id in db.selector_index

    db.selector_index[record.memory_ref.memory_id]["source_memory_fingerprint"] = (
        "sha256:" + "0" * 64
    )
    query = MemoryQuerySchema(
        run_id="run-1",
        memory_types=(DomainMemoryType.FACT,),
        selector_clauses=(_selector_clause("network"),),
    )
    result = runtime.query(query)

    assert tuple(item.memory_record.memory_ref for item in result.items) == (
        record.memory_ref,
    )
    assert (
        db.selector_index[record.memory_ref.memory_id]["source_memory_fingerprint"]
        == record.record_semantic_fingerprint
    )

    db.selector_index[record.memory_ref.memory_id] = {"corrupt": True}
    recovered = runtime.query(query)
    assert recovered.items[0].memory_record.memory_ref == record.memory_ref
    assert (
        db.selector_index[record.memory_ref.memory_id]["source_memory_fingerprint"]
        == record.record_semantic_fingerprint
    )


def test_query_uses_memory_record_truth_even_when_index_is_missing() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))
    runtime.store.delete_selector_index(record.memory_ref)

    result = runtime.query(
        MemoryQuerySchema(
            run_id="run-1",
            memory_types=(DomainMemoryType.FACT,),
        )
    )

    assert len(result.items) == 1
    assert result.items[0].memory_record == record
    assert record.memory_ref.memory_id not in db.selector_index


def test_typed_query_exact_source_filter_and_selector_schema_validation() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network", tags=("a", "b"))
    registry.add("f2", topic="compute", tags=("b",))
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    source1 = _source("f1", scope_ref="section:a")
    source2 = _source("f2", scope_ref="section:b")
    record1 = runtime.project_source(_request(source1))
    runtime.project_source(_request(source2))

    query = MemoryQuerySchema(
        run_id="run-1",
        memory_types=(DomainMemoryType.FACT,),
        source_kinds=("fact_snapshot",),
        exact_source_refs=(source1.exact_source_ref,),
        selector_clauses=(_selector_clause("network"),),
    )
    result = runtime.query(query)
    assert tuple(item.memory_record.memory_ref for item in result.items) == (
        record1.memory_ref,
    )

    unknown = query.model_copy(
        update={
            "selector_clauses": (
                _selector_clause("network").model_copy(
                    update={"selector_schema_id": "unknown_selector"}
                ),
            )
        }
    )
    with pytest.raises(MemorySelectorSchemaInvalid):
        runtime.query(unknown)

    with pytest.raises(MemoryTypeUnregistered, match="unregistered type"):
        runtime.query(
            MemoryQuerySchema(
                run_id="run-1",
                memory_types=(DomainMemoryType.EVIDENCE,),
            )
        )


def test_selector_clause_value_type_fails_closed() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    runtime.project_source(_request(_source("f1")))

    bad = _selector_clause("network").model_copy(update={"value": 123})
    with pytest.raises(
        MemorySelectorSchemaInvalid,
        match="type/canonicalization",
    ):
        runtime.query(
            MemoryQuerySchema(
                run_id="run-1",
                memory_types=(DomainMemoryType.FACT,),
                selector_clauses=(bad,),
            )
        )


def test_selector_fallback_scan_is_bounded_and_requires_rebuild() -> None:
    registry = _SourceRegistry()
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    for index in range(3):
        source_id = f"f{index}"
        registry.add(source_id, topic="network")
        runtime.project_source(
            _request(_source(source_id, scope_ref=f"section:{index}"))
        )

    runtime.store.delete_all_selector_index_for_run("run-1")
    runtime.SELECTOR_FALLBACK_SCAN_LIMIT = 2

    with pytest.raises(
        MemorySelectorSchemaInvalid,
        match="INDEX_REBUILD_REQUIRED",
    ):
        runtime.query(
            MemoryQuerySchema(
                run_id="run-1",
                memory_types=(DomainMemoryType.FACT,),
                selector_clauses=(_selector_clause("not-present"),),
            )
        )


def test_physical_memory_row_key_mismatch_fails_closed() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))

    payload = copy.deepcopy(db.memory[record.memory_ref.memory_id])
    payload["memory_ref"]["memory_id"] = "mem_corrupt_embedded_identity"
    payload["committed_operation_ref"]["operation_scope_ref"] = (
        "mem_corrupt_embedded_identity"
    )
    db.memory[record.memory_ref.memory_id] = payload

    with pytest.raises(
        MemoryStoreIntegrityError,
        match="row key",
    ):
        runtime.query(
            MemoryQuerySchema(
                run_id="run-1",
                memory_types=(DomainMemoryType.FACT,),
            )
        )


def test_query_order_pagination_cursor_and_query_fingerprint_are_stable() -> None:
    registry = _SourceRegistry()
    registry.add("fa", topic="network")
    registry.add("fb", topic="network")
    registry.add("fc", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    records = [
        runtime.project_source(_request(_source("fb", scope_ref="section:b"))),
        runtime.project_source(_request(_source("fa", scope_ref="section:a"))),
        runtime.project_source(_request(_source("fc", scope_ref="section:c"))),
    ]
    expected = sorted(
        (record.memory_ref for record in records),
        key=lambda ref: (ref.memory_type.value, ref.scope_ref, ref.memory_id),
    )

    first_query = MemoryQuerySchema(
        run_id="run-1",
        memory_types=(DomainMemoryType.FACT,),
        selector_clauses=(_selector_clause("network"),),
        limit=1,
    )
    first = runtime.query(first_query)
    assert first.items[0].memory_record.memory_ref == expected[0]
    assert first.next_cursor is not None

    second = runtime.query(
        first_query.model_copy(
            update={"cursor": first.next_cursor, "limit": 2},
        )
    )
    assert tuple(item.memory_record.memory_ref for item in second.items) == tuple(
        expected[1:]
    )
    assert second.normalized_query_fingerprint == first.normalized_query_fingerprint

    changed_query = first_query.model_copy(
        update={
            "cursor": first.next_cursor,
            "selector_clauses": (_selector_clause("compute"),),
        }
    )
    with pytest.raises(MemoryCursorInvalid, match="query fingerprint"):
        runtime.query(changed_query)

    cursor_payload = runtime._decode_cursor(first.next_cursor)
    assert cursor_payload["run_id"] == "run-1"
    wrong_run_cursor = runtime._encode_cursor(
        run_id="run-other",
        query_fingerprint=cursor_payload["normalized_query_fingerprint"],
        last_key=tuple(cursor_payload["last_key"]),
        index_contracts=tuple(
            tuple(item) for item in cursor_payload["index_contracts"]
        ),
    )
    with pytest.raises(MemoryCursorInvalid, match="run_id mismatch"):
        runtime.query(
            first_query.model_copy(update={"cursor": wrong_run_cursor})
        )

    v2_runtime = _runtime(db, registry, selector_policy_version="v2")
    with pytest.raises(MemoryCursorInvalid, match="contract version"):
        v2_runtime.query(
            first_query.model_copy(update={"cursor": first.next_cursor})
        )


def test_currentness_contract_upgrade_creates_new_identity_and_old_record_fails_closed() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    source = _source("f1")

    runtime_v1 = _runtime(db, registry, currentness_policy_version="v1")
    old = runtime_v1.project_source(_request(source))

    runtime_v2 = _runtime(db, registry, currentness_policy_version="v2")
    with pytest.raises(
        MemoryRecordIntegrityInvalid,
        match="currentness contract version",
    ):
        runtime_v2.resolve_record(old.memory_ref)

    new = runtime_v2.project_source(_request(source))
    assert new.memory_ref != old.memory_ref
    assert new.source_binding.currentness_policy_version == "v2"


def test_active_query_never_uses_stale_memory_as_checkpoint_like_fallback() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    record = runtime.project_source(_request(_source("f1")))

    registry.states["f1"].current = False
    registry.states["f1"].reason_codes = ("SOURCE_SUPERSEDED",)

    active = runtime.query(
        MemoryQuerySchema(
            run_id="run-1",
            memory_types=(DomainMemoryType.FACT,),
            recall_mode=MemoryRecallMode.ACTIVE_ONLY,
        )
    )
    historical = runtime.query(
        MemoryQuerySchema(
            run_id="run-1",
            memory_types=(DomainMemoryType.FACT,),
            recall_mode=MemoryRecallMode.HISTORICAL_ONLY,
            query_purpose=MemoryQueryPurpose.HISTORICAL_RECALL,
        )
    )

    assert active.items == ()
    assert historical.items[0].memory_record.memory_ref == record.memory_ref
    assert (
        historical.items[0].freshness_assessment.eligibility
        is MemoryFreshnessEligibility.HISTORICAL_ONLY
    )


def test_selector_rebuild_scan_is_bounded_by_records_checked() -> None:
    registry = _SourceRegistry()
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    for index in range(5):
        source_id = f"f{index}"
        registry.add(source_id, topic="network")
        runtime.project_source(
            _request(
                _source(source_id, scope_ref=f"section:{index}")
            )
        )

    runtime._selector_registrations.clear()
    db.reconcile_rows_scanned = 0
    rebuilt = runtime.rebuild_selector_index(run_id="run-1", limit=2)

    assert rebuilt == ()
    assert db.reconcile_rows_scanned == 2


def test_reconcile_cannot_reconstruct_missing_canonical_source_from_memory_ref() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    source = _source("f1")
    request = _request(source)
    binding, _, _ = runtime._resolve_projection_sources(
        request,
        runtime._projection_registration(request),
        require_resolvable=True,
    )
    memory_ref = runtime.derive_memory_ref(request, binding)

    with pytest.raises(DurableMemoryRuntimeError, match="cannot reconstruct source"):
        runtime.reconcile_source(memory_ref)

    assert db.memory == {}


def test_raw_agent_output_is_not_a_registered_canonical_memory_source() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    exact = {
        "run_id": "run-1",
        "scope_ref": "section:s1",
        "source_id": "raw-agent-output",
        "version": 1,
    }
    raw_source = CanonicalMemorySourceRefEnvelopeSchema(
        source_authority="agent_runtime",
        source_kind="raw_agent_output",
        source_scope_ref="section:s1",
        logical_source_ref="agent-output:1",
        exact_source_ref=exact,
        exact_source_ref_schema_id="agent_output_v1",
        exact_source_ref_sha256=_sha256_ref(exact),
        source_semantic_fingerprint=_sha256_ref({"raw": 1}),
    )

    with pytest.raises(MemorySourceRefInvalid):
        runtime.project_source(_request(raw_source))


def test_query_and_source_contracts_fail_closed_on_noncanonical_inputs() -> None:
    exact = {
        "run_id": "run-1",
        "scope_ref": "section:s1",
        "source_id": "f1",
        "version": 1,
    }
    with pytest.raises(ValidationError):
        CanonicalMemorySourceRefEnvelopeSchema(
            source_authority="fact_authority",
            source_kind="fact_snapshot",
            source_scope_ref="section:s1",
            logical_source_ref="fact-snapshot:f1",
            exact_source_ref={},
            exact_source_ref_schema_id="fact_snapshot_ref_v1",
            exact_source_ref_sha256=_sha256_ref({}),
            source_semantic_fingerprint=_sha256_ref({"source_id": "f1"}),
        )

    with pytest.raises(ValidationError, match="canonically sorted"):
        MemoryQuerySchema(
            run_id="run-1",
            scope_refs=("section:b", "section:a"),
        )

    with pytest.raises(ValidationError, match="canonical ordered-unique"):
        MemoryQuerySchema(
            run_id="run-1",
            exact_source_refs=(
                {"z": 1},
                {"a": 1},
            ),
        )


def test_memory_query_result_is_not_context_package() -> None:
    registry = _SourceRegistry()
    registry.add("f1", topic="network")
    db = _FakePgDatabase()
    runtime = _runtime(db, registry)
    runtime.project_source(_request(_source("f1")))

    result = runtime.query(MemoryQuerySchema(run_id="run-1"))
    assert isinstance(result, MemoryQueryResultSchema)
    assert result.__class__.__name__ == "MemoryQueryResultSchema"
    assert "context" not in result.__class__.__name__.lower()


def test_store_rejects_unsupported_physical_schema_version() -> None:
    db = _FakePgDatabase()
    with pytest.raises(ValueError, match="unsupported Durable Memory schema_version"):
        PostgresDurableMemoryStore(
            database_url="postgresql://memory.test/db",
            connection_factory=lambda: _FakePgConnection(db),
            schema_version="memory_v2",
            ensure_schema=False,
        )


def test_memory_config_defaults_fail_fast_and_redact_dsn(monkeypatch) -> None:
    monkeypatch.setenv(
        "MEMORY_DATABASE_URL",
        "postgresql://memory_user:super-secret@localhost/memory",
    )
    settings = get_settings(reload=True)

    assert settings.memory_database_pool_min_size == 1
    assert settings.memory_database_pool_max_size == 5
    assert settings.memory_database_statement_timeout_ms == 5000
    assert settings.memory_database_schema_version == "memory_v1"
    assert settings.raw_model_retention_days == 7
    assert settings.raw_model_diagnostic_retention_days == 14
    assert settings.raw_tool_retention_days == 3
    assert settings.raw_tool_diagnostic_retention_days == 14
    assert settings.old_artifact_retention_days == 30
    assert settings.claim_verification_history_retention_days == 90
    assert settings.memory_historical_retention_days == 30
    assert settings.memory_ineligible_quarantine_days == 7
    assert settings.context_replay_retention_days == 7
    assert settings.context_replay_diagnostic_retention_days == 14
    assert (
        settings.disk_soft_free_ratio,
        settings.disk_hard_free_ratio,
        settings.disk_critical_free_ratio,
    ) == (0.15, 0.10, 0.05)
    assert (
        settings.disk_soft_free_gib,
        settings.disk_hard_free_gib,
        settings.disk_critical_free_gib,
    ) == (20, 10, 5)
    rendered = repr(settings.as_dict())
    assert "super-secret" not in rendered
    assert "postgresql://" not in rendered

    monkeypatch.setenv("MEMORY_DATABASE_POOL_MIN_SIZE", "6")
    monkeypatch.setenv("MEMORY_DATABASE_POOL_MAX_SIZE", "5")
    with pytest.raises(ValueError, match="POOL_MIN_SIZE"):
        get_settings(reload=True)
