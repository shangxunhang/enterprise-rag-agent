"""Frozen NORMAL/MISSING structural self-check for paired 8-section acceptance.

Uses the exact frozen ProjectInput v3 fixtures, FakeLLM/FakeRAG, the production
Supervisor composition, and the acceptance-only document-gate orchestration
helper. This verifies orchestration and canonical terminal ownership only; it
must not be used to certify real-provider business behavior.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(BACKEND_ROOT))

from agent.runtime.graph_state import GraphStateSchema
from application.task_factory import TaskFactory
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.runtime.clock import SystemClock
from task.task_manager import JsonlTaskManager

from paired_8_section_acceptance_orchestration import (
    PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE,
    invoke_through_document_gate,
)


FIXTURE_ROOT = (
    PROJECT_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "src_rev_02_business_acceptance"
)


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _state(supervisor, task, workflow, routing) -> GraphStateSchema:
    context_bundle = supervisor.context_factory.build(task)
    return GraphStateSchema(
        task_id=task.task_id,
        run_id=task.run_id,
        task_type=task.task_type,
        user_input=task.user_input,
        tenant_id=task.tenant_id,
        task=task.model_dump(),
        workflow=workflow.model_dump(),
        requirements={
            **task.generation_requirements,
            "project_input": task.project_input,
        },
        context_bundle=context_bundle,
        created_at=task.created_at,
        updated_at=task.updated_at or task.created_at,
        extra={
            "routing": routing.metadata,
            "supervisor_model_response": (
                routing.model_response.model_dump()
                if routing.model_response
                else None
            ),
        },
    )


def _run_variant(
    *,
    variant: str,
    raw: dict,
    root: Path,
    settings,
) -> dict:
    created_at = SystemClock().now_iso()
    run_id = f"paired8_structural_{variant}"
    task = TaskFactory().build(
        task_id=raw["task_id"],
        run_id=run_id,
        user_input=raw["user_query"],
        created_at=created_at,
        project_input=raw,
        allow_demo_defaults=False,
    )

    variant_root = root / variant
    runs_dir = variant_root / "runs"
    captures_dir = variant_root / "captures"
    tasks_dir = variant_root / "tasks"
    for path in (runs_dir, captures_dir, tasks_dir):
        path.mkdir(parents=True, exist_ok=True)

    task_manager = JsonlTaskManager(output_dir=tasks_dir)
    task_manager.create_task(task)
    workflow = build_scheme_generation_workflow(created_at=created_at)
    options = RuntimeOptions.resolve(
        settings,
        PROJECT_ROOT,
        enable_agent_self_rag=False,
    )
    supervisor = SupervisorFactory().build(
        workflow=workflow,
        runs_dir=runs_dir,
        captures_dir=captures_dir,
        task_manager=task_manager,
        settings=settings,
        options=options,
        allow_legacy_unscoped_rag=True,
        retrieval_access_scope=None,
    )

    try:
        routing = supervisor.router.route(task)
        selected_workflow = supervisor.catalog.get(routing.task_type)
        supervisor.lifecycle.mark_running(task)
        sliced = invoke_through_document_gate(
            engine=supervisor.workflow_engine,
            workflow=selected_workflow,
            graph_state=_state(supervisor, task, selected_workflow, routing),
        )

        payload = sliced.payload
        certification = sliced.certification
        source_statuses = [
            {
                "section_id": result.section.section_id,
                "workflow_status": (
                    result.canonical_section_source_binding.workflow_status
                    if result.canonical_section_source_binding is not None
                    else None
                ),
            }
            for result in payload.section_execution_results
        ]

        assert payload.project_input.schema_version == "project_input_v3"
        assert len(payload.document_plan.sections) == 8
        assert len(payload.section_execution_results) == 8
        assert tuple(payload.executed_nodes) == (
            PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE
        )
        assert certification["run_lifecycle_status"] == "success"
        assert certification["run_completion_aggregate_result"] == "SUCCESS"
        assert certification["section_completion_count"] == 8
        assert tuple(sliced.inspection.get("next_nodes") or ()) in {
            ("persist_deliverable",),
            ("persist_rejected",),
        }

        return {
            "variant": variant,
            "status": "PASS",
            "task_id": task.task_id,
            "run_id": run_id,
            "project_input_schema_version": payload.project_input.schema_version,
            "section_count": len(payload.section_execution_results),
            "executed_nodes": list(payload.executed_nodes),
            "next_nodes": list(sliced.inspection.get("next_nodes") or ()),
            "certification": certification,
            "source_statuses_diagnostic_only": source_statuses,
        }
    finally:
        supervisor.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        choices=("normal", "missing", "both"),
        default="both",
    )
    args = parser.parse_args()
    temp_parent = (PROJECT_ROOT.parent / "_paired8_selfcheck_tmp").resolve()
    temp_parent.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(
        tempfile.mkdtemp(
            prefix="paired8-frozen-pair-selfcheck-",
            dir=temp_parent,
        )
    ).resolve()

    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
        "SECTION_MAX_CONCURRENCY": "1",
    }

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        settings = replace(
            get_settings(reload=True),
            data_root=runtime_root,
            default_model_name="fake_llm",
            supervisor_model_name="fake_llm",
            enable_llm_routing=False,
            trace_enabled=True,
            data_capture_enabled=False,
            gate_enforcement_mode="strict",
        )

        variants = (
            ("normal", "missing")
            if args.variant == "both"
            else (args.variant,)
        )
        results = [
            _run_variant(
                variant=variant,
                raw=_load(f"{variant}_project_input.json"),
                root=runtime_root,
                settings=settings,
            )
            for variant in variants
        ]

    assert all(item["status"] == "PASS" for item in results)
    assert all(item["section_count"] == 8 for item in results)
    print(
        json.dumps(
            {
                "status": "PASS",
                "scope": "FROZEN_PAIR_STRUCTURAL_ONLY_FAKE_PROVIDER",
                "runtime_root": str(runtime_root),
                "variants": results,
                "business_acceptance_certified": False,
                "real_provider_used": False,
                "production_code_modified": False,
                "tests_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
