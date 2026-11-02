"""Single-section native Fake Target: review-copy survives Publisher error.

No paid models. Use a fresh E: pytest --basetemp so SIF-13 measures a
real, sufficiently provisioned durable output volume; guard is NOT bypassed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.post_write.release import (
    PostWriteReleasePublicationService,
)
from test_phase7_document_closure_mainline import _run_fake


def _project() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate({
        "schema_version": "project_input_v3",
        "task_id": "task-ch1-review-native-20261015",
        "task_type": "scheme_generation",
        "project_name": "模拟政务云建设项目",
        "project_type": "government_cloud",
        "user_query": (
            "仅编制通信工程政务云建设方案第一章《项目背景》。"
            "本项目计划新建服务器12台；采购、安装、部署、投产状态均未知。"
            "事实不足时标明待核实，不把参考项目当作当前项目。"
        ),
        "business_goal": "形成基于合法资料的政务云建设项目背景与建设动因。",
        "generation_requirements": {
            "required_sections": ["项目背景"],
            "need_citation": False,
            "citation_required_sections": [],
            "need_human_review": True,
            "min_section_chars": 80,
        },
        "output_schema": {
            "document_title": "模拟政务云建设方案",
            "output_format": "markdown",
            "required_sections": ["项目背景"],
        },
        "missing_information": ["采购状态", "部署状态", "投产状态"],
    })


@pytest.mark.parametrize("publisher_breaks", [False, True])
def test_native_single_section_review_file_available_with_or_without_publication(
    tmp_path, publisher_breaks,
):
    project = _project()
    run_id = "ch1-review-publisher-failure" if publisher_breaks else "ch1-review-ok"
    patchers = (
        (patch.object(
            PostWriteReleasePublicationService,
            "publish",
            side_effect=RuntimeError("injected publisher transport failure"),
        ),)
        if publisher_breaks else ()
    )
    summary, _factory = _run_fake(
        tmp_path / run_id, run_id=run_id, project_input=project,
        patchers=patchers,
    )
    source = next(
        row for row in summary["supervisor_result"]["result"]["sub_agent_results"]
        if row["agent_name"] == "SchemeWriterAgent"
    )
    result = source["result"]
    review = result["target_review_draft"]
    assert summary["target_review_draft"] == review
    assert review is not None, source.get("metadata")
    path = Path(review["review_file_path"])
    assert path.is_file()
    assert "项目背景" in path.read_text(encoding="utf-8")
    assert review["formal_release_authorized"] is False
    assert review["quality_state"] in {"PASS", "FAIL", "UNRESOLVED"}
    assert review["delivery_kind"] == "HUMAN_REVIEW_COPY_NOT_FORMAL_RELEASE"
    if publisher_breaks:
        assert result["target_release_publication"] is None
        assert summary["target_release_publication"] is None
        assert summary["terminal_outcome"]["publication_exists"] is False
        assert summary["status"] == "partial_success"
        assert any(
            item["code"] == "TARGET_RELEASE_PUBLICATION_FAILED"
            for item in summary["terminal_outcome"]["diagnostics"]
        )
        assert source["metadata"]["release_status"] == "publication_unverified"
        assert any(
            item["warning_code"] == "TARGET_RELEASE_PUBLICATION_FAILED"
            for item in source["warnings"]
        )
    else:
        assert result["target_release_publication"] is not None
        assert summary["target_release_publication"] is not None
