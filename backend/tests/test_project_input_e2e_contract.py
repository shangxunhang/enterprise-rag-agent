from __future__ import annotations

import json
from pathlib import Path

from application.task_factory import TaskFactory
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    SchemeSectionSchema,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import (
    DocumentAssembler,
)
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)


EXPECTED_SECTIONS = [
    "项目背景",
    "建设目标",
    "总体架构",
    "计算资源建设方案",
    "存储资源建设方案",
    "网络资源建设方案",
    "安全设计",
    "实施与验收建议",
]
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "e2e"
    / "gov_cloud_framework_project_input.json"
)


def test_explicit_gov_cloud_project_input_preserves_eight_section_contract() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    project_input = ProjectInputSchema.model_validate(payload)

    plan = DocumentPlanningService.build_document_plan(
        run_id="gov_cloud_contract",
        document_id="doc_gov_cloud_contract",
        project_input=project_input,
        required_sections=project_input.generation_requirements.required_sections,
        created_at="2026-08-05T05:00:00+00:00",
    )
    generated_sections = [
        SchemeSectionSchema(
            section_id=item.section_id,
            section_title=item.section_title,
            section_order=item.section_order,
            content=f"{item.section_title}测试正文。",
        )
        for item in plan.sections
    ]
    draft = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id=project_input.task_id,
            run_id="gov_cloud_contract",
            document_id="doc_gov_cloud_contract",
            document_title=project_input.output_schema.document_title or "项目建设方案",
            required_sections=EXPECTED_SECTIONS,
            sections=generated_sections,
            citation_required_sections=[],
            created_at="2026-08-05T05:00:00+00:00",
            updated_at="2026-08-05T05:00:00+00:00",
        )
    ).draft

    generation_titles = project_input.generation_requirements.required_sections
    output_titles = project_input.output_schema.required_sections
    plan_titles = [item.section_title for item in plan.sections]
    draft_titles = [item.section_title for item in draft.sections]

    assert generation_titles == EXPECTED_SECTIONS
    assert output_titles == EXPECTED_SECTIONS
    assert plan_titles == EXPECTED_SECTIONS
    assert draft_titles == EXPECTED_SECTIONS
    assert len(draft_titles) == 8
    assert "技术方案" not in draft_titles
    assert "资源配置" not in draft_titles
    assert "待补充事项" not in draft_titles
    assert draft_titles[3:6] == [
        "计算资源建设方案",
        "存储资源建设方案",
        "网络资源建设方案",
    ]
    assert project_input.missing_information == [
        "项目现状",
        "用户规模",
        "计算资源数量",
        "存储容量",
        "网络带宽",
        "设备品牌型号",
        "投资金额",
        "实施工期",
    ]
    assert project_input.metadata["allow_demo_defaults"] is False


def test_current_project_id_is_persisted_for_durable_retrieval_scope() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    # This test creates a new Task from the checked-in example; explicitly mark
    # its caller-authored goal as upgraded instead of silently accepting V1.
    payload["schema_version"] = "project_input_v3"
    task = TaskFactory().build(
        task_id=payload["task_id"],
        run_id="gov_cloud_scope_persistence",
        user_input=payload["user_query"],
        created_at="2026-08-09T13:00:00+00:00",
        project_input=payload,
        allow_demo_defaults=False,
    )

    assert task.project_input["current_project_id"] == "gov_cloud_framework"
    assert task.metadata["current_project_id"] == "gov_cloud_framework"
