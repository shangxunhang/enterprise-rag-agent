from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CanonicalFactRecordSchema,
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
    FactSourceBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1 import claims as claims_schema_module
from apps.enterprise_document.schemas.agent_rag_v1 import facts as facts_schema_module
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ArtifactIdentityCollision,
    ArtifactRuntime,
    FactSnapshotCurrentnessError,
    FactSnapshotError,
    FactSnapshotRuntime,
    FactSnapshotSourceBindingInvalid,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from core.runtime.side_effects import LogicalOperationRef


REQUEST_HASH = hashlib.sha256(b"fact-snapshot-request").hexdigest()


class _FixedClock:
    def now_iso(self) -> str:
        return "2026-09-03T15:00:00+00:00"


class _SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}_{self.value:03d}"


def _lifecycle(tmp_path) -> RunAttemptLifecycleService:
    service = RunAttemptLifecycleService(
        tmp_path / "lifecycle.jsonl",
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id="run-fact-1",
            task_id="task-fact-1",
            tenant_id="tenant-a",
            canonical_request_sha256=REQUEST_HASH,
        )
    )
    service.transition_run(
        "run-fact-1",
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )
    return service


def _runtime(tmp_path) -> FactSnapshotRuntime:
    artifact = ArtifactRuntime(
        tmp_path / "artifact-runtime.jsonl",
        lifecycle_service=_lifecycle(tmp_path),
    )
    return FactSnapshotRuntime(
        artifact_runtime=artifact,
        source_binding_resolver=lambda binding: bool(
            binding.source_version_ref
            and binding.source_version_ref.startswith("source-version://")
        ),
    )


def _source(version: str = "v1") -> FactSourceBindingSchema:
    return FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project-input://task-fact-1",
        source_version_ref=f"source-version://project-input/{version}",
    )


def _fact(
    *,
    fact_id: str = "fact-1",
    value: object = 10,
    source: FactSourceBindingSchema | None = None,
) -> CanonicalFactRecordSchema:
    return CanonicalFactRecordSchema(
        fact_id=fact_id,
        fact_type="quantity",
        normalized_value=value,
        unit="core",
        source_bindings=[source or _source()],
        confidence=1.0,
    )


def _op(operation_id: str, kind: str) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=operation_id,
        operation_kind=kind,
        run_id="run-fact-1",
        operation_scope_ref="fact-snapshot:project",
    )


def test_fact_content_canonicalizes_unordered_sources_and_facts() -> None:
    s1 = _source("v1")
    s2 = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project-input://secondary",
        source_version_ref="source-version://secondary/v1",
    )
    first = FactSnapshotContentSchema(
        normalization_contract_version="facts-v1",
        source_bindings=[s2, s1, s1],
        facts=[
            _fact(fact_id="fact-b", value=2, source=s2),
            _fact(fact_id="fact-a", value=1, source=s1),
        ],
    )
    second = FactSnapshotContentSchema(
        normalization_contract_version="facts-v1",
        source_bindings=[s1, s2],
        facts=[
            _fact(fact_id="fact-a", value=1, source=s1),
            _fact(fact_id="fact-b", value=2, source=s2),
        ],
    )
    assert first == second
    assert [item.fact_id for item in first.facts] == ["fact-a", "fact-b"]
    assert len(first.source_bindings) == 2


def test_empty_fact_snapshot_is_valid_and_preserves_material_source_set() -> None:
    content = FactSnapshotContentSchema(
        normalization_contract_version="facts-v1",
        source_bindings=[_source()],
        facts=[],
    )

    assert content.facts == []
    assert content.source_bindings == [_source()]


def test_fact_content_rejects_duplicate_fact_ids_and_foreign_fact_source() -> None:
    source = _source()
    with pytest.raises(ValueError, match="unique"):
        FactSnapshotContentSchema(
            normalization_contract_version="facts-v1",
            source_bindings=[source],
            facts=[_fact(fact_id="dup"), _fact(fact_id="dup")],
        )

    foreign = _source("v2")
    with pytest.raises(ValueError, match="snapshot-level"):
        FactSnapshotContentSchema(
            normalization_contract_version="facts-v1",
            source_bindings=[source],
            facts=[_fact(source=foreign)],
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        float("nan"),
        float("inf"),
        (1, 2),
        {1: "not-a-json-object-key"},
    ],
)
def test_normalized_fact_value_rejects_non_json_native_values(invalid_value: object) -> None:
    with pytest.raises(ValueError, match="normalized_value"):
        _fact(value=invalid_value)


def test_source_binding_resolvability_fails_closed(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    unresolved = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project-input://task-fact-1",
    )
    with pytest.raises(FactSnapshotSourceBindingInvalid):
        runtime.build_content_from_authoritative_sources(
            normalization_contract_version="facts-v1",
            source_bindings=[unresolved],
            facts=[
                CanonicalFactRecordSchema(
                    fact_id="fact-1",
                    fact_type="quantity",
                    normalized_value=1,
                    source_bindings=[unresolved],
                )
            ],
        )

    artifact = ArtifactRuntime(
        tmp_path / "strict-source-artifact-runtime.jsonl",
        lifecycle_service=_lifecycle(tmp_path / "strict-source"),
    )
    rejecting_runtime = FactSnapshotRuntime(
        artifact_runtime=artifact,
        source_binding_resolver=lambda binding: False,
    )
    version_looking_but_unresolved = _source("looks-versioned")
    with pytest.raises(FactSnapshotSourceBindingInvalid):
        rejecting_runtime.build_content_from_authoritative_sources(
            normalization_contract_version="facts-v1",
            source_bindings=[version_looking_but_unresolved],
            facts=[_fact(source=version_looking_but_unresolved)],
        )


def test_scope_allocates_one_canonical_logical_artifact_id() -> None:
    first = FactSnapshotRuntime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )
    replay = FactSnapshotRuntime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
        logical_artifact_id=first.logical_artifact_id,
    )
    assert replay == first

    with pytest.raises(FactSnapshotError, match="competing logical_artifact_id"):
        FactSnapshotRuntime.logical_ref_for_scope(
            run_id="run-fact-1",
            domain_scope_ref="project:task-fact-1",
            logical_artifact_id="fact-snapshot:competing",
        )


def test_commit_is_artifact_backed_and_not_current_until_promotion(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    logical = runtime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )
    content = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source()],
        facts=[_fact()],
    )

    version = runtime.commit_snapshot(
        logical_ref=logical,
        content=content,
        operation_ref=_op("fact-version-1", "ARTIFACT_VERSION_COMMIT"),
    )

    assert version.logical_ref == logical
    assert version.content_schema_version == "fact_snapshot_content_v1"
    assert runtime.resolve_current_binding(logical) is None

    binding = runtime.promote_snapshot(
        logical_ref=logical,
        target_version_ref=version,
        expected_head_revision=0,
        expected_active_version_id=None,
        operation_ref=_op("fact-promote-1", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    assert isinstance(binding, FactSnapshotBindingSchema)
    assert binding.resolved_head_revision == 1
    assert runtime.resolve_current_binding(logical) == binding
    assert runtime.read_binding(binding, require_current=True) == content


def test_same_operation_same_semantics_converges_and_changed_source_collides(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    logical = runtime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )
    content_v1 = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v1")],
        facts=[_fact(source=_source("v1"))],
    )
    operation = _op("fact-version-replay", "ARTIFACT_VERSION_COMMIT")

    first = runtime.commit_snapshot(
        logical_ref=logical,
        content=content_v1,
        operation_ref=operation,
    )
    replay = runtime.commit_snapshot(
        logical_ref=logical,
        content=content_v1,
        operation_ref=operation,
    )
    assert replay == first

    content_v2 = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v2")],
        facts=[_fact(source=_source("v2"))],
    )
    with pytest.raises(ArtifactIdentityCollision):
        runtime.commit_snapshot(
            logical_ref=logical,
            content=content_v2,
            operation_ref=operation,
        )


def test_normalization_contract_version_is_material_semantic_input(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    logical = runtime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )
    source = _source()
    facts = [_fact(source=source)]
    first = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[source],
        facts=facts,
    )
    second = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v2",
        source_bindings=[source],
        facts=facts,
    )

    assert runtime.semantic_fingerprint(logical, first) != runtime.semantic_fingerprint(
        logical, second
    )


def test_source_version_change_creates_distinct_semantic_version_even_same_fact_value(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    logical = runtime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )
    v1_content = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v1")],
        facts=[_fact(value=10, source=_source("v1"))],
    )
    v2_content = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v2")],
        facts=[_fact(value=10, source=_source("v2"))],
    )

    assert runtime.semantic_fingerprint(logical, v1_content) != runtime.semantic_fingerprint(
        logical, v2_content
    )
    version1 = runtime.commit_snapshot(
        logical_ref=logical,
        content=v1_content,
        operation_ref=_op("fact-version-v1", "ARTIFACT_VERSION_COMMIT"),
    )
    version2 = runtime.commit_snapshot(
        logical_ref=logical,
        content=v2_content,
        operation_ref=_op("fact-version-v2", "ARTIFACT_VERSION_COMMIT"),
    )
    assert version1.artifact_version_id != version2.artifact_version_id


def test_binding_becomes_stale_when_artifact_head_advances(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    logical = runtime.logical_ref_for_scope(
        run_id="run-fact-1",
        domain_scope_ref="project:task-fact-1",
    )

    c1 = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v1")],
        facts=[_fact(value=1, source=_source("v1"))],
    )
    v1 = runtime.commit_snapshot(
        logical_ref=logical,
        content=c1,
        operation_ref=_op("fact-version-1", "ARTIFACT_VERSION_COMMIT"),
    )
    b1 = runtime.promote_snapshot(
        logical_ref=logical,
        target_version_ref=v1,
        expected_head_revision=0,
        expected_active_version_id=None,
        operation_ref=_op("fact-promote-1", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    c2 = runtime.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[_source("v2")],
        facts=[_fact(value=2, source=_source("v2"))],
    )
    v2 = runtime.commit_snapshot(
        logical_ref=logical,
        content=c2,
        operation_ref=_op("fact-version-2", "ARTIFACT_VERSION_COMMIT"),
    )
    b2 = runtime.promote_snapshot(
        logical_ref=logical,
        target_version_ref=v2,
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
        operation_ref=_op("fact-promote-2", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    with pytest.raises(FactSnapshotCurrentnessError):
        runtime.validate_binding_current(b1)
    assert runtime.validate_binding_current(b2) == b2


def test_claims_module_is_compatibility_reexport_not_canonical_fact_owner() -> None:
    canonical_symbols = (
        "FactSourceBindingSchema",
        "CanonicalFactRecordSchema",
        "FactSnapshotContentSchema",
        "FactSnapshotBindingSchema",
    )
    for symbol in canonical_symbols:
        assert getattr(claims_schema_module, symbol) is getattr(facts_schema_module, symbol)

    claims_tree = ast.parse(Path(claims_schema_module.__file__).read_text(encoding="utf-8"))
    class_names = {
        node.name for node in ast.walk(claims_tree) if isinstance(node, ast.ClassDef)
    }
    assert class_names.isdisjoint(canonical_symbols)


def test_fact_runtime_does_not_import_state_checkpoint_memory_trace_or_rag() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "agent_rag_v1"
        / "fact_snapshot_runtime.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)

    forbidden = (
        "apps.enterprise_document.schemas.agent_rag_v1.state",
        "apps.enterprise_document.services.agent_rag_v1.checkpoint",
        "apps.enterprise_document.services.agent_rag_v1.memory",
        "schemas.trace",
        "rag.",
    )
    assert not any(
        module == prefix.rstrip(".") or module.startswith(prefix)
        for module in modules
        for prefix in forbidden
    )
