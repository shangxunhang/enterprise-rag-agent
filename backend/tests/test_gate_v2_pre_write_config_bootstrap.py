"""P2-B-owned config and non-activating construction checks for Gate V2."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import core.config as config
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    LLMCriterionSupportEvaluator,
    SCHEME_GENERATION_MINIMAL_CRITERION_ID,
    SCHEME_GENERATION_MINIMAL_PROFILE_REF,
    SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID,
    SCHEME_GENERATION_MINIMAL_TASK_REF,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from core.runtime.pre_write_route_decision_store import SQLitePreWriteRouteDecisionStore
from core.runtime.side_effects import JsonlSideEffectCommitStore
from observability.pre_write_trace import PreWriteTraceEmitter
from observability.sqlite_trace_sink import SQLiteTraceSink
from task.task_manager import JsonlTaskManager
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)


GATE_KEYS = (
    "GATE_ENFORCEMENT_MODE",
    "GATE_RECOVERY_ROUNDS_LIMIT",
    "GATE_NO_PROGRESS_ROUNDS_LIMIT",
    "MAX_RECOVERY_ATTEMPTS",
)


def _clear_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in GATE_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_DOTENV_VALUES", {})
    monkeypatch.setattr(config, "_SETTINGS", None)


def test_gate_config_defaults_are_strict_two_one_and_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gate_env(monkeypatch)
    settings = config.get_settings(reload=True)
    assert settings.gate_enforcement_mode == "strict"
    assert settings.gate_recovery_rounds_limit == 2
    assert settings.gate_no_progress_rounds_limit == 1
    summary = settings.as_dict()
    assert summary["gate_enforcement_mode"] == "strict"
    assert summary["gate_recovery_rounds_limit"] == 2
    assert summary["gate_no_progress_rounds_limit"] == 1


def test_gate_config_precedence_is_env_over_dotenv_over_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setattr(
        config,
        "_DOTENV_VALUES",
        {
            "GATE_ENFORCEMENT_MODE": "shadow",
            "GATE_RECOVERY_ROUNDS_LIMIT": "4",
            "GATE_NO_PROGRESS_ROUNDS_LIMIT": "3",
        },
    )
    dotenv_settings = config.get_settings(reload=True)
    assert dotenv_settings.gate_enforcement_mode == "shadow"
    assert dotenv_settings.gate_recovery_rounds_limit == 4
    assert dotenv_settings.gate_no_progress_rounds_limit == 3

    monkeypatch.setenv("GATE_ENFORCEMENT_MODE", "STRICT")
    monkeypatch.setenv("GATE_RECOVERY_ROUNDS_LIMIT", "7")
    monkeypatch.setenv("GATE_NO_PROGRESS_ROUNDS_LIMIT", "5")
    env_settings = config.get_settings(reload=True)
    assert env_settings.gate_enforcement_mode == "strict"
    assert env_settings.gate_recovery_rounds_limit == 7
    assert env_settings.gate_no_progress_rounds_limit == 5


@pytest.mark.parametrize("value", ["", "disabled", "permit", "legacy", "1"])
def test_invalid_explicit_enforcement_mode_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("GATE_ENFORCEMENT_MODE", value)
    with pytest.raises(ValueError, match="GATE_ENFORCEMENT_MODE"):
        config.get_settings(reload=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GATE_RECOVERY_ROUNDS_LIMIT", "-1"),
        ("GATE_NO_PROGRESS_ROUNDS_LIMIT", "0"),
        ("GATE_RECOVERY_ROUNDS_LIMIT", "not-an-int"),
        ("GATE_NO_PROGRESS_ROUNDS_LIMIT", "1.5"),
        ("GATE_RECOVERY_ROUNDS_LIMIT", ""),
        ("GATE_NO_PROGRESS_ROUNDS_LIMIT", ""),
    ],
)
def test_invalid_gate_limits_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        config.get_settings(reload=True)


def test_max_recovery_attempts_is_not_an_alias_for_gate_recovery_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("MAX_RECOVERY_ATTEMPTS", "99")
    settings = config.get_settings(reload=True)
    assert settings.max_recovery_attempts == 99
    assert settings.gate_recovery_rounds_limit == 2

    monkeypatch.setenv("GATE_RECOVERY_ROUNDS_LIMIT", "0")
    settings = config.get_settings(reload=True)
    assert settings.max_recovery_attempts == 99
    assert settings.gate_recovery_rounds_limit == 0


def test_app_settings_has_no_gate_v2_enable_disable_or_fallback_business_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gate_env(monkeypatch)
    fields = set(config.AppSettings.__dataclass_fields__)
    assert "enable_gate_v2" not in fields
    assert "legacy_gate_fallback" not in fields
    assert "enable_fallback_write" not in fields


def _build_p5_supervisor(tmp_path: Path, *, trace_enabled: bool = False):
    project_root = Path(__file__).resolve().parents[2]
    settings = replace(
        config.get_settings(),
        data_root=tmp_path / "different-data-root",
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=trace_enabled,
        data_capture_enabled=False,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=2,
        gate_no_progress_rounds_limit=1,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    runs_dir = tmp_path / "effective-runs"
    with patch.dict(os.environ, environment):
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=False,
        )
        supervisor = SupervisorFactory().build(
            workflow=build_scheme_generation_workflow(
                created_at="2026-08-23T00:00:00+08:00"
            ),
            runs_dir=runs_dir,
            captures_dir=tmp_path / "captures",
            task_manager=JsonlTaskManager(tmp_path / "tasks"),
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=True,
        )
    return supervisor, settings, runs_dir.resolve()


def test_p5_target_bootstrap_uses_effective_runs_dir_and_attaches_single_target_graph(
    tmp_path: Path,
) -> None:
    supervisor, settings, runs_dir = _build_p5_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        coordinator = engine.stage_service.section_execution_coordinator
        services = coordinator.require_pre_write_services()
        execution_store = services.recovery_controller._execution_store
        assert isinstance(execution_store, SQLitePreWriteExecutionStore)
        route_store = services.route_decision_store
        assert isinstance(route_store, SQLitePreWriteRouteDecisionStore)
        assert services.writer_invocation.execution_store is execution_store
        assert services.writer_invocation.route_decision_store is route_store
        assert route_store.database_path == execution_store.database_path
        assert isinstance(services.trace_emitter, PreWriteTraceEmitter)
        assert isinstance(services.trace_emitter.trace_sink, SQLiteTraceSink)
        assert coordinator.pre_write_ready is True

        assert engine.checkpointer.path == runs_dir / "langgraph_checkpoints.sqlite3"
        assert execution_store.database_path == runs_dir / "execution_control.sqlite3"
        assert services.trace_emitter.trace_sink.database_path == runs_dir / "run_trace.sqlite3"
        assert len(
            {
                engine.checkpointer.path,
                execution_store.database_path,
                services.trace_emitter.trace_sink.database_path,
            }
        ) == 3
        assert settings.data_root / "runs" != runs_dir

        snapshot = services.requirements
        assert snapshot.source_profile_ref == SCHEME_GENERATION_MINIMAL_PROFILE_REF
        assert snapshot.source_task_ref == SCHEME_GENERATION_MINIMAL_TASK_REF
        assert len(snapshot.requirements) == 1
        assert snapshot.requirements[0].requirement_id == SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID
        assert len(snapshot.requirements[0].criteria) == 1
        assert snapshot.requirements[0].criteria[0].criterion_id == SCHEME_GENERATION_MINIMAL_CRITERION_ID
        assert isinstance(services.fact_assessor._evaluator, LLMCriterionSupportEvaluator)
        assert services.fact_assessor._evaluator.model_name == settings.default_model_name
        assert services.trace_emitter.trace_enabled is False

        assert isinstance(engine.side_effect_commit_store, JsonlSideEffectCommitStore)
        assert engine.side_effect_commit_store is not execution_store
        assert engine.side_effect_commit_store is not services.trace_emitter.trace_sink
    finally:
        supervisor.close()


def test_p5_startup_self_check_failure_returns_no_usable_runtime_and_unwinds_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"validate": 0}

    def fail_startup(**kwargs):
        del kwargs
        called["validate"] += 1
        raise RuntimeError("injected startup readiness failure")

    monkeypatch.setattr(SupervisorFactory, "_validate_target_startup", staticmethod(fail_startup))
    with pytest.raises(RuntimeError, match="startup readiness failure"):
        _build_p5_supervisor(tmp_path)
    assert called["validate"] == 1
    # Construction failed before a business engine/supervisor was returned.
    # Canonical schema initialization is itself a readiness check, so the
    # three pre-write/checkpoint domains plus the singular Wave-13 Post-write
    # database may exist.  No separate Gate/publication authority database is
    # allowed.
    sqlite_names = {path.name for path in (tmp_path / "effective-runs").glob("*.sqlite3")}
    assert sqlite_names == {
        "langgraph_checkpoints.sqlite3",
        "execution_control.sqlite3",
        "run_trace.sqlite3",
        "post_write.sqlite3",
    }


def test_p5_bootstrap_source_has_no_gate_feature_flag_or_legacy_fallback_selector() -> None:
    source = Path(SupervisorFactory.__module__.replace(".", "/") + ".py")
    del source
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "backend/bootstrap/supervisor_factory.py").read_text(encoding="utf-8")
    for forbidden in (
        "ENABLE_GATE_V2",
        "LEGACY_GATE_FALLBACK",
        "TARGET_GATE_ENABLED",
        "USE_LEGACY_GATE",
        "PREWRITE_V2_ENABLED",
        "if Target else Legacy",
        "try Target except Legacy",
    ):
        assert forbidden not in text
    assert "attach_pre_write_services" in text
    assert text.count("attach_pre_write_services") == 1


def test_p2b_nonactivating_store_construction_uses_three_store_separation(
    tmp_path: Path,
) -> None:
    trace = SQLiteTraceSink.for_data_root(tmp_path)
    execution = SQLitePreWriteExecutionStore.for_data_root(tmp_path)
    route = SQLitePreWriteRouteDecisionStore.for_data_root(tmp_path)
    checkpoint = (tmp_path / "runs" / "langgraph_checkpoints.sqlite3").resolve()
    assert trace.database_path == (tmp_path / "runs" / "run_trace.sqlite3").resolve()
    assert execution.database_path == (
        tmp_path / "runs" / "execution_control.sqlite3"
    ).resolve()
    assert route.database_path == execution.database_path
    assert len({trace.database_path, execution.database_path, route.database_path, checkpoint}) == 3
    # Lane B constructs its own capabilities only; no production checkpoint or
    # Target bootstrap activation is performed by these helpers.
    assert not checkpoint.exists()
