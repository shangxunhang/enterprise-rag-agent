"""Build canonical ProjectInput at an external application boundary."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


DEFAULT_SCHEME_SECTIONS = [
    "项目概述",
    "建设目标",
    "建设内容",
    "技术方案",
    "资源配置",
    "安全设计",
    "实施与验收",
    "待补充事项",
]


_DOCUMENT_TITLE_PATTERN = re.compile(
    r"([A-Za-z0-9\u4e00-\u9fff·（）()_-]{2,80}?"
    r"(?:建设方案|实施方案|解决方案|可行性研究报告|可研报告|投标文件|标书|报告))"
)


def _derive_document_title(payload: Dict[str, Any], user_input: str) -> str:
    """Derive a neutral title without injecting a fixed demo business domain."""

    project_name = str(payload.get("project_name") or "").strip()
    if project_name:
        if project_name.endswith(
            ("方案", "报告", "投标文件", "标书", "可行性研究报告")
        ):
            return project_name
        return f"{project_name}建设方案"

    query = str(user_input or "").strip().strip("。！？!? ")
    query = re.sub(
        r"^(?:请|麻烦|帮我|请帮我|请为我)?(?:生成|编写|撰写|制定|输出|形成)"
        r"(?:一个|一份|一套)?",
        "",
        query,
    ).strip()
    match = _DOCUMENT_TITLE_PATTERN.search(query)
    if match:
        return match.group(1)
    return "项目建设方案"


class ProjectInputFactory:
    def build(
        self,
        task_id: str,
        user_input: str,
        raw_project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = True,
    ) -> ProjectInputSchema:
        payload: Dict[str, Any] = dict(raw_project_input or {})
        if not allow_demo_defaults and not str(payload.get("task_id") or "").strip():
            raise ValueError("ProjectInput.task_id is required")
        payload.setdefault("task_id", task_id)
        payload.setdefault("tenant_id", "default")
        payload.setdefault("user_query", user_input)
        payload.setdefault("source_materials", [])

        generation_requirements = dict(payload.get("generation_requirements") or {})
        output_schema = dict(payload.get("output_schema") or {})
        if allow_demo_defaults:
            payload.setdefault("task_type", "scheme_generation")
            generation_requirements.setdefault(
                "required_sections", list(DEFAULT_SCHEME_SECTIONS)
            )
            generation_requirements.setdefault("need_citation", True)
            generation_requirements.setdefault(
                "citation_required_sections",
                ["建设内容", "技术方案", "安全设计"],
            )
            output_schema.setdefault(
                "document_title", _derive_document_title(payload, user_input)
            )
            output_schema.setdefault("output_format", "markdown")
            output_schema.setdefault(
                "required_sections", list(DEFAULT_SCHEME_SECTIONS)
            )
        else:
            if not str(payload.get("task_type") or "").strip():
                raise ValueError("ProjectInput.task_type is required")
            caller_sections = (
                generation_requirements.get("required_sections")
                or output_schema.get("required_sections")
                or []
            )
            if not caller_sections:
                raise ValueError(
                    "ProjectInput must provide generation_requirements.required_sections "
                    "or output_schema.required_sections"
                )

        payload["generation_requirements"] = generation_requirements
        payload["output_schema"] = output_schema
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault(
            "input_source",
            "run_demo_cli" if allow_demo_defaults else "project_input_file",
        )
        payload["metadata"] = metadata
        return ProjectInputSchema.model_validate(payload)
