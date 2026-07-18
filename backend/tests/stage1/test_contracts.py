"""Stage-1 regression tests split by responsibility."""

from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_executor import WorkflowExecutor
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from eval.agent.hard_gate import evaluate_scheme_draft
from schemas.agent import AgentResultSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus

NOW = "2026-07-14T00:00:00+00:00"

def test_project_input_preserves_caller_defined_sections() -> None:
    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_custom",
            "tenant_id": "tenant_a",
            "project_name": "自定义项目",
            "task_type": "scheme_generation",
            "user_query": "生成定制报告",
            "source_materials": [{"material_type": "policy", "doc_ids": ["doc_1"]}],
            "generation_requirements": {
                "required_sections": ["现状", "风险"],
                "citation_required_sections": ["风险"],
            },
            "output_schema": {
                "document_title": "自定义报告",
                "required_sections": ["现状", "风险"],
            },
            "metadata": {"request_source": "api"},
        }
    )

    assert item.tenant_id == "tenant_a"
    assert item.generation_requirements.required_sections == ["现状", "风险"]
    assert item.output_schema.required_sections == ["现状", "风险"]
    assert item.output_schema.document_title == "自定义报告"


def test_production_project_input_rejects_demo_defaults() -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "run_demo_back.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        module.build_project_input(
            task_id="task_prod",
            user_input="生成报告",
            raw_project_input={
                "task_id": "task_prod",
                "task_type": "scheme_generation",
                "user_query": "生成报告",
            },
            allow_demo_defaults=False,
        )
    except ValueError as exc:
        assert "required_sections" in str(exc)
    else:
        raise AssertionError("production input must not receive demo sections")

    item = module.build_project_input(
        task_id="task_prod",
        user_input="生成报告",
        raw_project_input={
            "task_id": "task_prod",
            "task_type": "scheme_generation",
            "user_query": "生成报告",
            "generation_requirements": {"required_sections": ["自定义章节"]},
            "output_schema": {"document_title": "自定义报告"},
        },
        allow_demo_defaults=False,
    )
    assert item.generation_requirements.required_sections == ["自定义章节"]
    assert item.output_schema.required_sections == ["自定义章节"]
    assert item.output_schema.document_title == "自定义报告"


def test_task_and_rag_filters_preserve_source_material_ids() -> None:
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    script = Path(__file__).resolve().parents[3] / "scripts" / "run_demo_back.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_sources", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = {
        "task_id": "task_sources",
        "task_type": "scheme_generation",
        "user_query": "根据指定资料生成报告",
        "source_materials": [
            {
                "material_type": "knowledge_base",
                "file_ids": ["file_1"],
                "doc_ids": ["doc_1", "doc_2"],
                "metadata": {"kb_id": "kb_1"},
            }
        ],
        "generation_requirements": {"required_sections": ["正文"]},
        "output_schema": {"required_sections": ["正文"]},
    }
    task = module.build_task(
        task_id="task_sources",
        run_id="run_sources",
        user_input=payload["user_query"],
        created_at=NOW,
        project_input=payload,
        allow_demo_defaults=False,
    )
    assert task.file_ids == ["file_1"]
    assert task.doc_ids == ["doc_1", "doc_2"]
    assert task.kb_ids == ["kb_1"]

    class ExecutorStub:
        def __init__(self):
            self.call = None

        def execute(self, call):
            self.call = call
            return SimpleNamespace(model_dump=lambda: {}, success=False)

    executor = ExecutorStub()
    agent = SchemeWriterAgent(tool_executor=executor)
    state = SimpleNamespace(
        run_id="run_sources",
        task_id="task_sources",
        task={"kb_ids": ["kb_1"]},
        updated_at=None,
        created_at=NOW,
        tool_results={},
    )
    project_input = ProjectInputSchema.model_validate(payload)
    agent._call_rag_tool(state, project_input)

    assert executor.call.tool_input["filters"]["doc_ids"] == ["doc_1", "doc_2"]
    assert executor.call.tool_input["filters"]["file_ids"] == ["file_1"]
    assert executor.call.tool_input["kb_ids"] == ["kb_1"]


def test_document_plan_is_derived_from_project_input_without_fixed_sections() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_plan",
            "task_type": "scheme_generation",
            "user_query": "生成自定义报告",
            "generation_requirements": {
                "required_sections": ["现状", "结论"],
                "citation_required_sections": ["结论"],
            },
            "output_schema": {
                "document_title": "自定义报告",
                "required_sections": ["现状", "结论"],
            },
        }
    )
    plan = SchemeWriterAgent._build_document_plan(
        run_id="run_plan",
        document_id="document_plan",
        project_input=item,
        required_sections=item.generation_requirements.required_sections,
        created_at=NOW,
    )

    assert [entry.section_title for entry in plan.sections] == ["现状", "结论"]
    assert [entry.citation_required for entry in plan.sections] == [False, True]
    assert plan.planning_source == "project_input"


def test_project_input_rejects_inconsistent_section_contracts() -> None:
    try:
        ProjectInputSchema.model_validate(
            {
                "task_id": "task_bad_sections",
                "task_type": "scheme_generation",
                "user_query": "生成报告",
                "generation_requirements": {"required_sections": ["A", "B"]},
                "output_schema": {"required_sections": ["A", "C"]},
            }
        )
    except ValueError as exc:
        assert "must match" in str(exc)
    else:
        raise AssertionError("inconsistent section contracts must fail")


def test_production_entry_does_not_import_demo_module() -> None:
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[3]
    pipeline_source = (project_root / "scripts" / "run_pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "from run_demo import" not in pipeline_source
    assert "import run_demo" not in pipeline_source
    assert "from mainline_runtime import run_mainline" in pipeline_source


def test_demo_partial_success_never_fabricates_sub_agent_failure() -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "run_demo_back.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_status_contract", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = {
        "status": "partial_success",
        "supervisor_result": {
            "error": None,
            "result": {
                "sub_agent_results": [
                    {
                        "agent_name": "ProjectInputNormalizerAgent",
                        "status": ExecutionStatus.SUCCESS,
                        "error": None,
                    },
                    {
                        "agent_name": "SchemeWriterAgent",
                        "status": ExecutionStatus.PARTIAL_SUCCESS,
                        "error": None,
                    },
                ]
            },
        },
    }

    assert module._effective_runtime_error(summary) == {}


def test_demo_failed_status_can_still_recover_real_sub_agent_error() -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "run_demo_back.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_failed_contract", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = {
        "status": "failed",
        "supervisor_result": {
            "error": None,
            "result": {
                "sub_agent_results": [
                    {
                        "agent_name": "ProjectInputNormalizerAgent",
                        "status": ExecutionStatus.SUCCESS,
                        "error": None,
                    },
                    {
                        "agent_name": "SchemeWriterAgent",
                        "status": ExecutionStatus.FAILED,
                        "error": {
                            "error_code": "DOCUMENT_HARD_GATE_FAILED",
                            "message": "hard gate failed",
                            "failed_node": "document_hard_gate",
                        },
                    },
                ]
            },
        },
    }

    error = module._effective_runtime_error(summary)
    assert error["error_code"] == "DOCUMENT_HARD_GATE_FAILED"
    assert error["failed_node"] == "document_hard_gate"

