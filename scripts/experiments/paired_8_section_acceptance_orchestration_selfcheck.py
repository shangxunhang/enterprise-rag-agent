"""Fake-runtime end-to-end self-check for paired acceptance orchestration.

This is acceptance tooling only. It proves the helper drives the existing
production graph through Target Post-write and stops immediately after
`evaluate_document_gate` without invoking persistence/finalization nodes.
"""

from __future__ import annotations

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
    DOCUMENT_GATE_NODE,
    PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE,
    invoke_through_document_gate,
)


def main() -> int:
    temp_parent = (PROJECT_ROOT.parent / "_paired8_selfcheck_tmp").resolve()
    temp_parent.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(
        tempfile.mkdtemp(
            prefix="paired8-orchestration-selfcheck-",
            dir=temp_parent,
        )
    ).resolve()
    runs_dir = runtime_root / "runs"
    captures_dir = runtime_root / "captures"
    tasks_dir = runtime_root / "tasks"
    for path in (runs_dir, captures_dir, tasks_dir):
        path.mkdir(parents=True, exist_ok=True)

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

        created_at = SystemClock().now_iso()
        run_id = "paired8_orchestration_selfcheck"
        task_id = "task_paired8_orchestration_selfcheck"
        task = TaskFactory().build(
            task_id=task_id,
            run_id=run_id,
            user_input="生成企业级 RAG-Agent 系统建设方案",
            created_at=created_at,
            project_input=None,
            allow_demo_defaults=True,
        )
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
            context_bundle = supervisor.context_factory.build(task)
            state = GraphStateSchema(
                task_id=task.task_id,
                run_id=task.run_id,
                task_type=task.task_type,
                user_input=task.user_input,
                tenant_id=task.tenant_id,
                task=task.model_dump(),
                workflow=selected_workflow.model_dump(),
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

            sliced = invoke_through_document_gate(
                engine=supervisor.workflow_engine,
                workflow=selected_workflow,
                graph_state=state,
            )

            payload = sliced.payload
            certification = sliced.certification
            inspection = sliced.inspection

            assert tuple(payload.executed_nodes) == (
                PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE
            )
            assert payload.executed_nodes[-1] == DOCUMENT_GATE_NODE
            assert len(payload.section_execution_results) == len(
                payload.document_plan.sections
            )
            assert len(payload.section_execution_results) > 0
            assert certification["run_lifecycle_status"] == "success"
            assert certification["run_completion_aggregate_result"] == "SUCCESS"
            assert certification["section_completion_count"] == len(
                payload.section_execution_results
            )
            assert (
                certification["source_workflow_status_is_terminal_authority"]
                is False
            )
            assert tuple(inspection.get("next_nodes") or ()) in {
                ("persist_deliverable",),
                ("persist_rejected",),
            }

            result = {
                "status": "PASS",
                "runtime_root": str(runtime_root),
                "executed_nodes": list(payload.executed_nodes),
                "next_nodes": list(inspection.get("next_nodes") or ()),
                "section_count": len(payload.section_execution_results),
                "certification": certification,
                "provider": "fake_llm",
                "production_code_modified": False,
                "tests_modified": False,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        finally:
            supervisor.close()


if __name__ == "__main__":
    raise SystemExit(main())
