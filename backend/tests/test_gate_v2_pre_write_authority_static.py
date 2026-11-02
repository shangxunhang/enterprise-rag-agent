"""P2 integrated T12-02 static authority proofs for Gate V2 Pre-write."""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from unittest.mock import Mock

from apps.enterprise_document.services.scheme_writer.writer_admission import (
    WriterAdmissionPolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _imported_names(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            imported.update(alias.name for alias in node.names)
    return imported


def _symbol_source(path: str, class_name: str, method_name: str | None = None) -> str:
    source = _source(path)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            target: ast.AST = node
            if method_name is not None:
                for child in node.body:
                    if (
                        isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and child.name == method_name
                    ):
                        target = child
                        break
                else:
                    raise AssertionError(f"missing {class_name}.{method_name} in {path}")
            segment = ast.get_source_segment(source, target)
            assert segment is not None
            return segment
    raise AssertionError(f"missing {class_name} in {path}")


def _has_true_marker(path: str, marker: str) -> bool:
    tree = ast.parse(_source(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if any(isinstance(target, ast.Name) and target.id == marker for target in targets):
            return isinstance(value, ast.Constant) and value.value is True
    return False


def test_static_01_binder_has_no_policy_semantic_assessment_or_raw_evidence_dependency() -> None:
    relative = "backend/apps/enterprise_document/services/scheme_writer/writer_gate_binding.py"
    imported = _imported_names(relative)
    source = _source(relative)
    forbidden = {
        "EvidenceGatePolicy",
        "WriterAdmissionPolicy",
        "WriterAdmissionFactAssessor",
        "RAGEvidenceContractSchema",
        "EvidenceBundleSchema",
        "ClaimType",
    }
    assert not (forbidden & imported)
    for token in forbidden:
        assert token not in source


def test_static_02_target_schema_validators_are_structural_only() -> None:
    schema_paths = (
        "backend/apps/enterprise_document/schemas/pre_write_admission.py",
        "backend/apps/enterprise_document/schemas/pre_write_binding.py",
        "backend/apps/enterprise_document/schemas/pre_write_control.py",
        "backend/apps/enterprise_document/schemas/pre_write_writer.py",
        "backend/apps/enterprise_document/schemas/section_execution_graph.py",
        "backend/apps/enterprise_document/schemas/scheme_generation_graph.py",
    )
    forbidden = (
        "WriterAdmissionPolicy",
        "WriterAdmissionFactAssessor",
        "WriterGateBinder",
        "WriterAdmissionEnforcement",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "PreWriteWriterInvocationService",
        "ResearchRecommendedAction",
        "_project_section_lifecycle",
        "_project_canonical_business_state",
    )
    for path in schema_paths:
        source = _source(path)
        for token in forbidden:
            assert token not in source, (path, token)


def test_step12_shared_fixture_contract_is_explicit_and_non_authoritative() -> None:
    from test_support.gate_v2_pre_write_fixtures import (
        CountingRetrievalProvider,
        CountingWriterModel,
        GateV2IdentityScenario,
        sqlite_temp_paths,
        stdlib_sha256_json,
    )

    scenario = GateV2IdentityScenario(
        task_id="task-fixture",
        run_id="run-fixture",
        document_id="document-fixture",
        section_id="section-fixture",
        write_instance_id="write-fixture",
    )
    assert scenario.writer_subject_ref.model_dump(mode="json") == {
        "run_id": "run-fixture",
        "document_id": "document-fixture",
        "section_id": "section-fixture",
    }
    assert stdlib_sha256_json({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )
    paths = sqlite_temp_paths(Path("fixture-root"))
    assert {path.name for path in paths.values()} == {
        "langgraph_checkpoints.sqlite3",
        "execution_control.sqlite3",
        "run_trace.sqlite3",
    }
    retrieval = CountingRetrievalProvider("evidence")
    writer = CountingWriterModel("artifact")
    assert retrieval("query") == "evidence"
    assert writer("prompt") == "artifact"
    assert retrieval.call_count == writer.call_count == 1

    fixture_source = _source(
        "backend/test_support/gate_v2_pre_write_fixtures.py"
    )
    for forbidden in (
        "WriterAdmissionPolicy",
        "WriterGateBinder",
        "WriterAdmissionEnforcement",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "uuid.uuid",
        "datetime.now",
        "time.time",
    ):
        assert forbidden not in fixture_source


def test_admission_decision_has_one_production_constructor_owner() -> None:
    production_roots = (
        REPO_ROOT / "backend/apps/enterprise_document",
        REPO_ROOT / "backend/bootstrap",
        REPO_ROOT / "backend/application",
    )
    constructors: list[str] = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            if "/schemas/" in path.as_posix():
                continue
            text = path.read_text(encoding="utf-8")
            if "WriterAdmissionDecisionSchema(" in text:
                constructors.append(path.relative_to(REPO_ROOT).as_posix())
    assert constructors == [
        "backend/apps/enterprise_document/services/scheme_writer/writer_admission.py"
    ]


def test_static_03_target_initial_prompt_has_no_control_plane_authority() -> None:
    prompt = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/prompt_service.py",
        "SectionPromptService",
        "render_target_initial_section_prompt",
    )
    for forbidden in (
        "writer_gate_prompt_contract",
        "WriterAdmissionDecision",
        "WriterGateBinding",
        "WriterAdmissionEnforcement",
        "RecoveryControlFacts",
        "PreWriteRouteDecision",
        "PreWriteRoute",
        "selected_route",
        "generation_class",
        "NORMAL_WRITE",
        "FALLBACK_WRITE",
    ):
        assert forbidden not in prompt


def test_static_04_target_generation_has_no_second_admission_veto() -> None:
    source = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py",
        "SectionGenerationService",
        "generate_from_pre_write_invocation",
    )
    for forbidden in (
        "resolve_writer_gate_enforcement",
        "writer_gate_blocks",
        "WriterAdmissionPolicy",
        "WriterGateBinder",
        "WriterAdmissionEnforcement",
        "PreWriteRouter",
        "generation_allowed",
        "ordinary_generation_permitted",
    ):
        assert forbidden not in source


def test_static_11_12_13_writer_shape_has_no_split_extra_id_or_recovery_generation_class() -> None:
    writer_source = _source(
        "backend/apps/enterprise_document/services/scheme_writer/writer_invocation.py"
    )
    schema_source = _source(
        "backend/apps/enterprise_document/schemas/pre_write_writer.py"
    )
    prep_source = "\n".join(
        _source(path)
        for path in (
            "backend/apps/enterprise_document/services/scheme_writer/prompt_service.py",
            "backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py",
            "backend/apps/enterprise_document/services/scheme_writer/section_content_recovery.py",
            "backend/apps/enterprise_document/services/scheme_writer/model_service.py",
        )
    )
    for marker in (
        "class NormalWriter",
        "class FallbackWriter",
        "class FallbackGenerationService",
    ):
        assert marker not in writer_source
        assert marker not in prep_source
    assert "writer_invocation_id" not in schema_source
    assert "writer_invocation_id" not in writer_source
    generation_enum = _symbol_source(
        "backend/apps/enterprise_document/schemas/pre_write_writer.py",
        "WriterGenerationClass",
    )
    assert "NORMAL_WRITE" in generation_enum
    assert "FALLBACK_WRITE" in generation_enum
    assert "RECOVERY" not in generation_enum
    generation_mapper = writer_source[
        writer_source.index("def _generation_class(") : writer_source.index(
            "def _prompt_lock_payload("
        )
    ]
    assert "PreWriteRoute.RECOVERY" not in generation_mapper
    assert "RECOVERY route cannot form an initial Writer invocation" in generation_mapper


def test_static_07_legacy_generation_bools_cannot_authorize_target_initial_path() -> None:
    graph_path = "backend/apps/enterprise_document/workflows/section_execution_graph.py"
    coordinator_path = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "section_execution_coordinator.py"
    )
    target_methods = (
        (graph_path, "SectionExecutionGraph", "_evaluate_pre_write_admission"),
        (graph_path, "SectionExecutionGraph", "_bind_pre_write_gate"),
        (graph_path, "SectionExecutionGraph", "_evaluate_pre_write_enforcement"),
        (graph_path, "SectionExecutionGraph", "_commit_pre_write_route"),
        (graph_path, "SectionExecutionGraph", "_dispatch_pre_write_recovery"),
        (graph_path, "SectionExecutionGraph", "_execute_pre_write_recovery"),
        (graph_path, "SectionExecutionGraph", "_complete_pre_write_recovery_dispatch"),
        (graph_path, "SectionExecutionGraph", "_invoke_pre_write_writer"),
        (coordinator_path, "SectionExecutionCoordinator", "_execute_pre_write_target"),
        (coordinator_path, "SectionExecutionCoordinator", "invoke_pre_write_writer"),
    )
    for path, class_name, method_name in target_methods:
        source = _symbol_source(path, class_name, method_name)
        assert "generation_allowed" not in source, (path, method_name)
        assert "ordinary_generation_permitted" not in source, (path, method_name)


def test_static_09_gate_calculators_do_not_read_trace_as_truth_input() -> None:
    calculator_paths = (
        "backend/apps/enterprise_document/services/scheme_writer/writer_admission.py",
        "backend/apps/enterprise_document/services/scheme_writer/writer_gate_binding.py",
        "backend/apps/enterprise_document/services/scheme_writer/writer_admission_enforcement.py",
        "backend/apps/enterprise_document/services/scheme_writer/pre_write_recovery.py",
        "backend/apps/enterprise_document/services/scheme_writer/pre_write_router.py",
        "backend/apps/enterprise_document/services/scheme_writer/writer_invocation.py",
    )
    forbidden = (
        "PreWriteTraceEmitter",
        "SQLiteTraceSink",
        "TraceSink",
        "run_trace.sqlite3",
        "load_trace_events",
        "read_trace",
    )
    for path in calculator_paths:
        source = _source(path)
        for token in forbidden:
            assert token not in source, (path, token)


def test_static_14_postwrite_owners_do_not_recalculate_prewrite_truth() -> None:
    post_write_paths = (
        "backend/apps/enterprise_document/services/writer_artifact_integrity.py",
        "backend/apps/enterprise_document/services/document_gate.py",
        "backend/apps/enterprise_document/services/release_publication.py",
        "backend/apps/enterprise_document/services/scheme_writer/stage_service.py",
        "backend/apps/enterprise_document/services/scheme_writer/writer_governance.py",
    )
    forbidden = (
        "WriterAdmissionFactAssessor",
        "WriterAdmissionPolicy",
        "WriterGateBinder",
        "WriterAdmissionEnforcement",
        "PreWriteRouter",
    )
    for path in post_write_paths:
        source = _source(path)
        for token in forbidden:
            assert token not in source, (path, token)


def test_admission_policy_signature_cannot_receive_forbidden_authority_inputs() -> None:
    parameters = set(inspect.signature(WriterAdmissionPolicy.evaluate).parameters)
    assert parameters == {
        "self",
        "assessment",
        "contract",
        "supersedes_decision_id",
    }
    tree = ast.parse(inspect.getsource(WriterAdmissionPolicy.evaluate).lstrip())
    executable_names = {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "raw_evidence",
        "task",
        "claim_type",
        "model_name",
        "provider",
        "regex",
        "retrieval_score",
    ):
        assert forbidden not in executable_names


def test_binder_and_validator_public_inputs_have_no_admission_recalculation_surface() -> None:
    binder_parameters = set(inspect.signature(WriterGateBinder.bind).parameters)
    assert binder_parameters == {
        "decision",
        "writer_subject_ref",
        "write_instance_id",
    }
    validator_parameters = set(
        inspect.signature(WriterGateApplicabilityValidator.validate).parameters
    )
    assert validator_parameters == {
        "self",
        "binding",
        "current_writer_subject_ref",
        "current_write_instance_id",
        "current_decision",
        "current_active_evidence_bundle_sha256",
        "current_active_requirements_sha256",
        "current_active_binding_ref",
    }


def test_typed_enforcement_is_writer_specific_and_does_not_depend_on_shared_controller() -> None:
    relative = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "writer_admission_enforcement.py"
    )
    source = _source(relative)
    imported = _imported_names(relative)
    assert "GateEnforcementController" not in imported
    assert "GateEnforcementController" not in source
    assert set(inspect.signature(WriterAdmissionEnforcement.evaluate).parameters) == {
        "self",
        "applicability",
        "decision",
        "mode",
    }
    for forbidden in ("gate_passed", "generation_allowed", "ordinary_generation_permitted"):
        assert forbidden not in source


def test_default_run_id_no_longer_uses_second_level_timestamp_as_sole_identity() -> None:
    source = _source("backend/application/mainline_service.py")
    assert 'run_id = run_id or f"run_demo_{self._timestamp()}"' not in source
    assert 'run_id = run_id or self.id_generator.new_id("run")' in source
    assert "TimestampedUuidIdGenerator" in source


def test_missing_persisted_attempt_id_has_no_synthetic_write_instance_fallback() -> None:
    source = _source(
        "backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py"
    )
    active_identity = source[
        source.index("    def active_identity(") : source.index(
            "    @classmethod\n    def validate_result_metadata", source.index("    def active_identity(")
        )
    ]
    assert 'or f"{run_id}:{section_id}:attempt:{attempt_number}"' not in active_identity
    assert "section_state.attempt.attempt_id" in active_identity
    assert "canonical write_instance_id" in active_identity


def test_p2d_research_producer_agent_and_adapter_emit_no_target_workflow_command() -> None:
    reasoner = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_reasoners.py",
        "MainlineResearchReasoner",
    )
    fake_reasoner = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py",
        "_Round4FakeResearchReasoner",
    )
    agent = _symbol_source(
        "backend/apps/enterprise_document/agents/agent_rag_v1/research.py",
        "ResearchAgent",
    )
    adapter = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py",
        "MainlineSectionAgentAdapter",
        "research",
    )

    for name, source in {
        "MainlineResearchReasoner": reasoner,
        "_Round4FakeResearchReasoner": fake_reasoner,
        "ResearchAgent": agent,
        "MainlineSectionAgentAdapter.research": adapter,
    }.items():
        assert "ResearchRecommendedAction" not in source, name
        assert "ResearchStatus" not in source, name
        assert "PARTIAL_DELIVERY" not in source, name
        assert "HUMAN_REVIEW" not in source, name

    assert "emitted_signals" not in reasoner
    assert '"recommended_action": None' in adapter
    assert '"lifecycle_status"' not in adapter


def test_p2d_research_legacy_fields_are_compatibility_optional_not_target_required() -> None:
    from apps.enterprise_document.schemas.agent_rag_v1 import ResearchEvidencePackageSchema

    summary = ResearchEvidencePackageSchema(section_id="section-1")
    assert summary.research_status is None
    assert summary.recommended_action is None
    dumped = summary.model_dump(mode="json", exclude_none=True)
    assert "research_status" not in dumped
    assert "recommended_action" not in dumped


def test_p2d_archive_runtime_has_machine_checkable_fixture_fence() -> None:
    archive_files = (
        "backend/apps/enterprise_document/runtime/agent_rag_v1/workflow_orchestrator.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/standalone_adapters.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/runtime_factory.py",
        "backend/apps/enterprise_document/runtime/agent_rag_v1/application_service.py",
    )
    for path in archive_files:
        assert _has_true_marker(path, "LEGACY_FIXTURE_ONLY"), path


def test_p2d_archive_runtime_is_not_imported_by_production_target_roots() -> None:
    forbidden_modules = {
        "apps.enterprise_document.runtime.agent_rag_v1.workflow_orchestrator",
        "apps.enterprise_document.runtime.agent_rag_v1.standalone_adapters",
        "apps.enterprise_document.runtime.agent_rag_v1.runtime_factory",
        "apps.enterprise_document.runtime.agent_rag_v1.application_service",
    }
    archive_root = REPO_ROOT / "backend/apps/enterprise_document/runtime/agent_rag_v1"
    offenders: list[str] = []
    for path in (REPO_ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts or archive_root in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
                if node.module == "apps.enterprise_document.runtime.agent_rag_v1":
                    modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
            if any(module in forbidden_modules for module in modules):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break
    assert offenders == []


def test_p2d_graph_consumer_is_outside_lane_owned_static_scope() -> None:
    lane_owned_producers = {
        "backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_reasoners.py",
        "backend/apps/enterprise_document/agents/agent_rag_v1/research.py",
        "backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py",
    }
    graph = "backend/apps/enterprise_document/workflows/section_execution_graph.py"
    assert graph not in lane_owned_producers


def test_checkpoint_v3_codecs_are_behavior_neutral_and_do_not_calculate_gate_truth() -> None:
    paths = (
        "backend/apps/enterprise_document/schemas/section_execution_graph.py",
        "backend/apps/enterprise_document/schemas/scheme_generation_graph.py",
    )
    forbidden = {
        "WriterAdmissionPolicy",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "ResearchRecommendedAction",
        "_project_section_lifecycle",
        "_project_canonical_business_state",
    }
    for path in paths:
        source = _source(path)
        assert not (forbidden & set(source.split())), path
        for token in forbidden:
            assert token not in source, (path, token)

    section_source = _source(paths[0])
    scheme_source = _source(paths[1])
    assert "SECTION_EXECUTION_GRAPH_STATE_VERSION = 3" in section_source
    assert "SCHEME_GENERATION_GRAPH_STATE_VERSION = 3" in scheme_source
    assert "unsupported section graph state version" in section_source
    assert "unsupported scheme generation graph state version" in scheme_source


def test_trace_projects_typed_truth_and_does_not_reconstruct_canonical_authority() -> None:
    source = _source("backend/observability/pre_write_trace.py")
    for forbidden in (
        "WriterAdmissionPolicy",
        "EvidenceGatePolicy",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "resolve_decision_ref",
        "load_trace_events",
    ):
        assert forbidden not in source
    for required in (
        "WriterAdmissionDecisionRefSchema",
        "WriterGateBindingRefSchema",
        "PreWriteRouteDecisionRefSchema",
        "evidence_bundle_schema_version",
        "evidence_bundle_sha256",
    ):
        assert required in source


def test_option_d_route_store_dependency_and_authority_fences() -> None:
    route_store_path = "backend/core/runtime/pre_write_route_decision_store.py"
    router_path = (
        "backend/apps/enterprise_document/services/scheme_writer/pre_write_router.py"
    )
    coordinator_path = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "section_execution_coordinator.py"
    )
    writer_path = (
        "backend/apps/enterprise_document/services/scheme_writer/writer_invocation.py"
    )
    graph_path = (
        "backend/apps/enterprise_document/workflows/section_execution_graph.py"
    )
    supervisor_path = "backend/bootstrap/supervisor_factory.py"

    route_imports = _imported_names(route_store_path)
    for forbidden in (
        "pre_write_router",
        "scheme_writer",
        "section_execution_graph",
        "checkpoint",
    ):
        assert all(forbidden not in name for name in route_imports)

    router_source = _source(router_path)
    for forbidden in (
        "sqlite3",
        "SQLitePreWriteRouteDecisionStore",
        "PreWriteRouteDecisionStore",
        "checkpoint",
    ):
        assert forbidden not in router_source

    graph_commit = _symbol_source(
        graph_path,
        "SectionExecutionGraph",
        "_commit_pre_write_route",
    )
    assert "put_if_absent_or_same" not in graph_commit
    assert "resolve_exact_for_snapshot" not in graph_commit
    assert "reserve_writer_execution" not in graph_commit
    assert "execution_store" not in graph_commit
    assert "route_decision_store" not in graph_commit

    coordinator_commit = _symbol_source(
        coordinator_path,
        "SectionExecutionCoordinator",
        "commit_pre_write_route",
    )
    assert coordinator_commit.count("put_if_absent_or_same(") == 1
    assert coordinator_commit.count("resolve_exact_for_snapshot(") == 2

    writer_execute = _symbol_source(
        writer_path,
        "PreWriteWriterInvocationService",
        "execute_initial",
    )
    assert "put_if_absent_or_same(" not in writer_execute
    assert "reserve_writer_execution(" not in writer_execute
    assert "resolve_exact_for_snapshot(" in writer_execute
    assert "get_writer_execution(" in writer_execute
    assert "mark_writer_dispatched(" in writer_execute

    supervisor_source = _source(supervisor_path)
    assert supervisor_source.count("SQLitePreWriteRouteDecisionStore(") == 1
    assert "route_decision_store=route_decision_store" in supervisor_source
    assert (
        "route_decision_store.database_path != execution_store.database_path"
        in supervisor_source
    )

    production_route_store_constructors: list[str] = []
    for root in (
        REPO_ROOT / "backend/apps",
        REPO_ROOT / "backend/bootstrap",
        REPO_ROOT / "backend/application",
    ):
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "SQLitePreWriteRouteDecisionStore(" in source:
                production_route_store_constructors.append(
                    path.relative_to(REPO_ROOT).as_posix()
                )
    assert production_route_store_constructors == [supervisor_path]

    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (
            REPO_ROOT / "backend/apps",
            REPO_ROOT / "backend/bootstrap",
            REPO_ROOT / "backend/application",
        )
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )
    assert "WorkflowRouteDecision" not in production_source
    for target_path in (
        router_path,
        coordinator_path,
        writer_path,
        graph_path,
        supervisor_path,
    ):
        assert "EvidenceGatePolicy" not in _source(target_path)

    invocation_resolver = _symbol_source(
        "backend/core/runtime/pre_write_execution_store.py",
        "SQLitePreWriteExecutionStore",
        "resolve_writer_generation_invocation",
    )
    assert "writer_generation_invocation" in invocation_resolver
    for forbidden in (
        "generation_provenance",
        "checkpoint",
        "WriterInvocationBuilder",
        "current_input",
        "synthetic",
    ):
        assert forbidden not in invocation_resolver


def test_execution_store_owns_only_physical_execution_not_gate_business_truth() -> None:
    source = _source("backend/core/runtime/pre_write_execution_store.py")
    for forbidden in (
        "WriterAdmissionPolicy",
        "EvidenceGatePolicy",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "EvidenceBundleSchema",
        "RecoveryRuntimeStateSchema",
        "RecoveryControlFactsSchema",
        "selected_route",
        "rounds_used",
        "consecutive_no_progress_rounds",
    ):
        assert forbidden not in source
    for required in (
        "RecoveryDispatchClaim",
        "WriterExecutionRecord",
        "SQLitePreWriteExecutionStore",
    ):
        assert required in source


def test_p5_target_capabilities_are_singularly_activated_by_production_bootstrap() -> None:
    supervisor_source = _source("backend/bootstrap/supervisor_factory.py")
    observability_source = _source("backend/bootstrap/observability_factory.py")
    bootstrap_source = "\n".join((observability_source, supervisor_source))

    for required in (
        "PreWriteCoordinatorServices",
        "LLMCriterionSupportEvaluator",
        "SQLitePreWriteExecutionStore",
        "WriterAdmissionFactAssessor",
        "WriterAdmissionPolicy",
        "WriterGateBinder",
        "WriterGateApplicabilityValidator",
        "WriterAdmissionEnforcement",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "PreWriteWriterInvocationService",
        "PreWriteTraceEmitter",
        "SQLiteTraceSink",
    ):
        assert required in bootstrap_source

    assert supervisor_source.count("PreWriteCoordinatorServices(") == 1
    assert supervisor_source.count("attach_pre_write_services(") == 1
    assert supervisor_source.count('runs_dir / "execution_control.sqlite3"') >= 1
    assert supervisor_source.count('runs_dir / "langgraph_checkpoints.sqlite3"') >= 1
    assert 'runs_dir / "run_trace.sqlite3"' in observability_source

    for forbidden in (
        "EvidenceGatePolicy",
        "GateDecision(",
        "runtime.agent_rag_v1.runtime_factory",
        "runtime.agent_rag_v1.application_service",
    ):
        assert forbidden not in bootstrap_source

    for forbidden_runtime_read in (
        "settings.ENABLE_GATE_V2",
        "settings.LEGACY_GATE_FALLBACK",
        "settings.target_gate_enabled",
        "settings.use_legacy_gate",
        "settings.prewrite_v2_enabled",
        'getattr(settings, "ENABLE_GATE_V2"',
        'getattr(settings, "LEGACY_GATE_FALLBACK"',
        'getattr(settings, "target_gate_enabled"',
        'getattr(settings, "use_legacy_gate"',
        'getattr(settings, "prewrite_v2_enabled"',
    ):
        assert forbidden_runtime_read not in bootstrap_source

    config_source = _source("backend/core/config.py")
    for forbidden_flag in (
        "ENABLE_GATE_V2",
        "LEGACY_GATE_FALLBACK",
        "target_gate_enabled",
        "use_legacy_gate",
        "prewrite_v2_enabled",
    ):
        assert forbidden_flag not in config_source


def test_p3a_recovery_controller_has_no_legacy_budget_research_route_or_graph_authority() -> None:
    relative = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "pre_write_recovery.py"
    )
    source = _source(relative)
    imported = _imported_names(relative)
    for forbidden in (
        "WorkflowBudget",
        "ResearchRecommendedAction",
        "ResearchStatus",
        "PreWriteRouter",
        "SectionExecutionGraph",
        "SectionExecutionCoordinator",
        "writer_gate_blocks",
        "generation_allowed",
        "ordinary_generation_permitted",
    ):
        assert forbidden not in source
        assert forbidden not in imported


def test_p3a_recovery_query_renderer_method_has_only_typed_target_authority() -> None:
    method = _symbol_source(
        "backend/apps/enterprise_document/services/scheme_writer/section_retrieval_query_builder.py",
        "SectionRetrievalQueryBuilder",
        "build_recovery_target_query",
    )
    assert "RecoveryTargetProjection" in method
    assert "EvidenceRequirementsSnapshotSchema" in method
    for forbidden in (
        "ProjectInputSchema",
        "domain_hints",
        "user_query",
        "project_type",
        "project_name",
        "recovery=True",
    ):
        assert forbidden not in method


def test_p3a_router_is_the_only_route_matrix_owner_and_has_no_upstream_recalculation_dependency() -> None:
    relative = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "pre_write_router.py"
    )
    source = _source(relative)
    imported = _imported_names(relative)
    for forbidden in (
        "WriterAdmissionPolicy",
        "WriterGateApplicabilityValidator",
        "WriterAdmissionEnforcement",
        "PreWriteRecoveryController",
        "ResearchRecommendedAction",
        "SectionExecutionGraph",
        "SectionExecutionCoordinator",
        "PromptService",
        "WorkflowBudget",
    ):
        assert forbidden not in source
        assert forbidden not in imported
    assert "PreWriteRoute.NORMAL" in source
    assert "PreWriteRoute.RECOVERY" in source
    assert "PreWriteRoute.FALLBACK" in source
    assert '"selected_route"' not in _symbol_source(
        relative, "PreWriteRouter", "_effective_snapshot_key"
    )


def test_all_initial_writer_occurrences_converge_and_mixed_graph_callsite_is_classified() -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
        DocumentCitationRegistry,
    )

    coordinator_path = (
        "backend/apps/enterprise_document/services/scheme_writer/"
        "section_execution_coordinator.py"
    )
    graph_path = (
        "backend/apps/enterprise_document/workflows/section_execution_graph.py"
    )
    coordinator_source = _source(coordinator_path)
    graph_source = _source(graph_path)

    canonical_writer = _symbol_source(
        coordinator_path,
        "SectionExecutionCoordinator",
        "invoke_pre_write_writer",
    )
    graph_writer = _symbol_source(
        graph_path,
        "SectionExecutionGraph",
        "_invoke_pre_write_writer",
    )
    mixed_writer = _symbol_source(
        graph_path,
        "SectionExecutionGraph",
        "_generate_section",
    )
    section_state_producer = _symbol_source(
        graph_path,
        "SectionExecutionGraph",
        "_initialize_section_state",
    )
    legacy_execute = _symbol_source(
        coordinator_path,
        "SectionExecutionCoordinator",
        "execute",
    )

    assert canonical_writer.count(".execute_initial(") == 1
    assert ".invoke_pre_write_writer(" in graph_writer
    assert ".generate_section(" not in graph_writer
    assert mixed_writer.count(".generate_section(") == 1
    assert "downstream-only" in mixed_writer
    assert "initial Graph Writer occurrence must use invoke_pre_write_writer" in mixed_writer
    for required in (
        "AttemptStateSchema(",
        "SectionStateSchema(",
        "self.attempt_guard.snapshot_version(",
        "persisted SectionState identity is stale",
    ):
        assert required in section_state_producer

    execute_source = textwrap.dedent(legacy_execute)
    execute_tree = ast.parse(execute_source)
    execute_function = execute_tree.body[0]
    assert isinstance(execute_function, ast.FunctionDef)
    target_guard = next(
        node
        for node in execute_function.body
        if isinstance(node, ast.If)
        and ast.get_source_segment(execute_source, node.test) == "self.pre_write_ready"
    )
    assert len(target_guard.body) == 1
    assert isinstance(target_guard.body[0], ast.Return)
    target_return = target_guard.body[0].value
    assert isinstance(target_return, ast.Call)
    assert isinstance(target_return.func, ast.Attribute)
    assert target_return.func.attr == "_execute_pre_write_target"

    parent_by_node = {
        child: parent
        for parent in ast.walk(execute_function)
        for child in ast.iter_child_nodes(parent)
    }
    legacy_generate_calls = sorted(
        (
            node
            for node in ast.walk(execute_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "generate_section"
        ),
        key=lambda node: node.lineno,
    )
    assert len(legacy_generate_calls) == 3

    def ancestors(node: ast.AST) -> list[ast.AST]:
        values: list[ast.AST] = []
        while node in parent_by_node:
            node = parent_by_node[node]
            values.append(node)
        return values

    legacy_initial, downstream_self_rag, downstream_corrective = legacy_generate_calls
    assert not any(isinstance(node, ast.While) for node in ancestors(legacy_initial))
    assert any(isinstance(node, ast.While) for node in ancestors(downstream_self_rag))
    corrective_tests = [
        ast.get_source_segment(execute_source, node.test) or ""
        for node in ancestors(downstream_corrective)
        if isinstance(node, ast.If)
    ]
    assert any("recovery_generation_permitted" in test for test in corrective_tests)
    assert target_guard.lineno < legacy_initial.lineno
    assert all(
        call not in set(ast.walk(target_guard))
        for call in legacy_generate_calls
    )

    target_methods = (
        "_evaluate_pre_write_admission",
        "_bind_pre_write_gate",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
        "_dispatch_pre_write_recovery",
        "_execute_pre_write_recovery",
        "_complete_pre_write_recovery_dispatch",
        "_invoke_pre_write_writer",
    )
    forbidden = (
        "ResearchRecommendedAction",
        "RunningInvocationRecovery",
        "extract_writer_gate_binding",
        "build_gate_blocked_section",
        "build_insufficient_evidence_section",
    )
    for method_name in target_methods:
        method = _symbol_source(
            graph_path,
            "SectionExecutionGraph",
            method_name,
        )
        assert all(value not in method for value in forbidden)

    assert coordinator_source.count(".generate_section(") == 3
    assert graph_source.count(".generate_section(") == 1

    coordinator_writer = p4_e2e._P4WriterInvocation()
    coordinator = p4_e2e._p4_coordinator(
        writer=coordinator_writer,
        recovery_rounds_limit=2,
    )
    low_level_generation = Mock()
    coordinator.section_generation_service.generate_section = low_level_generation
    coordinator_result = coordinator.execute(
        p4_e2e._p4_coordinator_request(p4_e2e._p4_bundle(selected=True)),
        citation_registry=DocumentCitationRegistry(),
    )
    assert coordinator_result.section is not None
    assert coordinator_writer.routes == [p4_e2e.PreWriteRoute.NORMAL]
    assert low_level_generation.call_count == 0

    graph_writer_spy = p4_e2e._P4WriterInvocation()
    graph_coordinator = p4_e2e._p4_coordinator(
        writer=graph_writer_spy,
        recovery_rounds_limit=2,
    )
    graph, graph_state = p4_e2e._p4_run_to_route(
        graph_coordinator,
        p4_e2e._p4_bundle(selected=True),
    )
    graph_state = p4_e2e._p4_apply(
        graph,
        graph_state,
        "_invoke_pre_write_writer",
    )
    assert graph_writer_spy.routes == [p4_e2e.PreWriteRoute.NORMAL]

    completed_payload = graph.codec.restore_payload(graph_state)
    downstream_generation = Mock(return_value=completed_payload.section)
    graph.section_generation_service.generate_section = downstream_generation
    graph_state = p4_e2e._p4_apply(graph, graph_state, "_generate_section")
    assert graph.codec.restore_payload(graph_state).section is not None
    assert downstream_generation.call_count == 1
    assert graph_writer_spy.routes == [p4_e2e.PreWriteRoute.NORMAL]

    origin_counts = {
        "coordinator.execute": len(coordinator_writer.routes),
        "graph.initial": len(graph_writer_spy.routes),
    }
    assert origin_counts == {
        "coordinator.execute": 1,
        "graph.initial": 1,
    }
    assert sum(origin_counts.values()) == 2

    supervisor_source = _source("backend/bootstrap/supervisor_factory.py")
    assert supervisor_source.count("attach_pre_write_services(") == 1
    assert supervisor_source.count("PreWriteCoordinatorServices(") == 1
    assert "EvidenceGatePolicy" not in supervisor_source
    assert "LEGACY_GATE_FALLBACK" not in supervisor_source
    assert "ENABLE_GATE_V2" not in supervisor_source
