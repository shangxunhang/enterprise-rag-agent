"""W0-A SIF-01/SIF-02 static authority fences.

These tests prove negative architecture constraints that are easy to regress
during later workflow cutover. They intentionally inspect production source
without creating a second runtime authority.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_SCHEMA = (
    "backend/apps/enterprise_document/schemas/agent_rag_v1/lifecycle.py"
)
LIFECYCLE_RUNTIME = (
    "backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py"
)
SIDE_EFFECTS = "backend/core/runtime/side_effects.py"
MAINLINE_RECOVERY = (
    "backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py"
)
W0A_PRODUCTION = (
    LIFECYCLE_SCHEMA,
    LIFECYCLE_RUNTIME,
    SIDE_EFFECTS,
    MAINLINE_RECOVERY,
)


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _imports(relative: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            imported.update(alias.name for alias in node.names)
    return imported


def _class_node(relative: str, class_name: str) -> ast.ClassDef:
    for node in _tree(relative).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"missing class {class_name} in {relative}")


def _function_node(relative: str, function_name: str) -> ast.FunctionDef:
    for node in _tree(relative).body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    raise AssertionError(f"missing function {function_name} in {relative}")


def _method_node(relative: str, class_name: str, method_name: str) -> ast.FunctionDef:
    owner = _class_node(relative, class_name)
    for node in owner.body:
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return node
    raise AssertionError(f"missing method {class_name}.{method_name} in {relative}")


def _segment(relative: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(_source(relative), node)
    assert segment is not None
    return segment


def _field_names(relative: str, class_name: str) -> set[str]:
    names: set[str] = set()
    for node in _class_node(relative, class_name).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _identifier_names(relative: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_static_s01_lifecycle_schema_does_not_depend_on_workflow_state_truth() -> None:
    imported = {name.lower() for name in _imports(LIFECYCLE_SCHEMA)}
    forbidden = ("workflow", "state_projection", "section_execution", "checkpoint", "trace")
    assert not {
        name
        for name in imported
        if any(token in name for token in forbidden)
    }


def test_static_s02_s03_lifecycle_runtime_has_no_checkpoint_or_trace_authority_dependency() -> None:
    imported = {name.lower() for name in _imports(LIFECYCLE_RUNTIME)}
    assert not {name for name in imported if "checkpoint" in name}
    assert not {name for name in imported if "trace" in name}

    identifiers = {name.lower() for name in _identifier_names(LIFECYCLE_RUNTIME)}
    assert not {name for name in identifiers if "checkpoint" in name}
    assert not {name for name in identifiers if "trace" in name}


def test_static_s04_run_attempt_currentness_has_one_canonical_service_owner() -> None:
    owners: list[str] = []
    backend = REPO_ROOT / "backend"
    for path in backend.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "class RunAttemptLifecycleService" in text:
            owners.append(path.relative_to(REPO_ROOT).as_posix())
    assert owners == [LIFECYCLE_RUNTIME]

    service = _class_node(LIFECYCLE_RUNTIME, "RunAttemptLifecycleService")
    methods = {
        node.name
        for node in service.body
        if isinstance(node, ast.FunctionDef)
    }
    assert {
        "load_active_attempt",
        "resolve_active_lineage",
        "assert_attempt_committable",
    } <= methods


def test_static_s05_state_attempt_is_projection_when_lifecycle_service_is_injected() -> None:
    node = _method_node(MAINLINE_RECOVERY, "MainlineAttemptGuard", "active_identity")
    source = _segment(MAINLINE_RECOVERY, node)
    lifecycle_branch = source.index("if lifecycle_service is not None:")
    fallback_branch = source.index("else:", lifecycle_branch)
    state_projection = source.index("section_state.attempt", fallback_branch)

    assert lifecycle_branch < fallback_branch < state_projection
    assert "lifecycle_service.resolve_active_lineage" in source
    assert "lifecycle_service.load_active_attempt" in source


def test_static_true_successor_lowering_uses_canonical_open_attempt_before_state_projection() -> None:
    node = _method_node(MAINLINE_RECOVERY, "MainlineReplanAttempt", "prepare")
    source = _segment(MAINLINE_RECOVERY, node)

    assert "if lifecycle_service is not None:" in source
    assert "lifecycle_service.open_attempt(" in source
    assert "opened.identity.attempt_ordinal" in source
    assert "opened.identity.attempt_id" in source
    assert "canonical_attempt_id" in source
    assert "or f\"{payload.run_id}:{section_id}:attempt:{next_attempt}\"" in source


def test_static_s07_s08_physical_execution_identity_is_not_logical_operation_or_fingerprint_contract() -> None:
    fields = _field_names(SIDE_EFFECTS, "LogicalOperationRef")
    assert {
        "logical_operation_id",
        "operation_kind",
        "run_id",
        "operation_scope_ref",
        "attempt_scope_ref",
        "attempt_id",
    } == fields
    assert not {"execution_id", "invocation_id", "trace_id", "lease_id"} & fields

    validator = _segment(
        SIDE_EFFECTS,
        _function_node(SIDE_EFFECTS, "validate_sif02_semantic_fingerprint"),
    )
    lowered = validator.lower()
    for token in ("execution_id", "invocation_id", "trace_id", "lease_id", "timestamp"):
        assert token not in lowered


def test_static_s09_stale_attempt_commit_guard_checks_canonical_lineage_fields() -> None:
    node = _method_node(
        LIFECYCLE_RUNTIME,
        "RunAttemptLifecycleService",
        "assert_attempt_committable",
    )
    source = _segment(LIFECYCLE_RUNTIME, node)
    for required in (
        "resolve_active_lineage",
        "RunLifecycleStatus.RUNNING",
        "AttemptLifecycleStatus.RUNNING",
        "lineage.attempt_id",
        "lineage.input_snapshot_version",
        "LifecycleCurrentnessError",
    ):
        assert required in source


def test_static_s10_s11_canonical_result_resolver_is_separate_from_coordination_and_checkpoint() -> None:
    imports = {name.lower() for name in _imports(SIDE_EFFECTS)}
    assert not {name for name in imports if "checkpoint" in name}
    assert not {name for name in imports if name.startswith("apps.enterprise_document")}

    resolver = _class_node(SIDE_EFFECTS, "CanonicalCommittedResultResolver")
    resolver_methods = {
        node.name
        for node in resolver.body
        if isinstance(node, ast.FunctionDef)
    }
    assert resolver_methods == {"resolve_committed_result"}

    coordination = _class_node(SIDE_EFFECTS, "SideEffectCommitStore")
    coordination_methods = {
        node.name
        for node in coordination.body
        if isinstance(node, ast.FunctionDef)
    }
    assert coordination_methods == {
        "claim",
        "resolve",
        "mark_committed",
        "release",
        "wait_for_resolution",
        "finish_execution",
    }

    resolver_subclasses = [
        node.name
        for node in _tree(SIDE_EFFECTS).body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name)
            and base.id == "CanonicalCommittedResultResolver"
            for base in node.bases
        )
    ]
    assert resolver_subclasses == []


def test_static_s12_canonical_resolution_helper_resolves_source_before_any_retry_decision() -> None:
    node = _function_node(SIDE_EFFECTS, "resolve_canonical_committed_result")
    calls = [
        _segment(SIDE_EFFECTS, child)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    ]
    joined = "\n".join(calls)
    assert "validate_sif02_semantic_fingerprint" in joined
    assert "resolver.resolve_committed_result" in joined
    assert "checkpoint" not in joined.lower()
    assert "retry" not in joined.lower()


def test_static_s13_fingerprint_collision_has_explicit_fail_closed_exception() -> None:
    node = _function_node(SIDE_EFFECTS, "_assert_semantic_identity")
    source = _segment(SIDE_EFFECTS, node)
    assert "SideEffectIdentityConflict" in source
    assert "existing == incoming" in source
    assert "raise SideEffectIdentityConflict" in source


def test_static_s15_run_completion_lifecycle_core_does_not_import_post_write() -> None:
    for relative in (LIFECYCLE_SCHEMA, LIFECYCLE_RUNTIME):
        imported = {name.lower() for name in _imports(relative)}
        assert not {
            name
            for name in imported
            if "post_write" in name
        }

    runtime_source = _source(LIFECYCLE_RUNTIME)
    assert "RunCompletionAssessmentStore" not in runtime_source
    assert "DeliveryGateDecision" not in runtime_source
    assert "PostWriteControlDecision" not in runtime_source


def test_static_s14_w0a_does_not_introduce_transaction_or_event_sourcing_platform() -> None:
    forbidden_import_fragments = {
        "sqlalchemy",
        "eventstore",
        "kafka",
        "pika",
        "celery",
        "distributed_transaction",
        "twophase",
        "two_phase",
        "xa_transaction",
    }
    forbidden_classes = {
        "EventStore",
        "TransactionCoordinator",
        "DistributedTransactionCoordinator",
        "TwoPhaseCommitCoordinator",
        "XATransactionCoordinator",
    }
    for relative in W0A_PRODUCTION:
        imported = {name.lower() for name in _imports(relative)}
        assert not {
            name
            for name in imported
            if any(fragment in name for fragment in forbidden_import_fragments)
        }
        class_names = {
            node.name
            for node in _tree(relative).body
            if isinstance(node, ast.ClassDef)
        }
        assert not (forbidden_classes & class_names)
