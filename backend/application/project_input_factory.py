# =============================================================================
# 中文阅读说明：应用层主链编排模块。
# 主要定义：_derive_document_title、ProjectInputFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Build canonical ProjectInput at an external application boundary."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.enterprise_document.schemas.project_input_schema import (
    ProjectInputDemoSeedV1Schema,
    ProjectInputSchema,
)


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


# 阅读注释（函数）：处理 derive 文档 title 相关逻辑。
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


# 阅读注释（类）：封装 项目 输入 工厂，负责根据配置装配并返回运行实例。
class ProjectInputFactory:
    """封装 项目 输入 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 ProjectInputFactory。
    def build(
        self,
        task_id: str,
        user_input: str,
        raw_project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = False,
    ) -> ProjectInputSchema:
        """构建 ProjectInputFactory。

        参数:
            task_id: 任务唯一标识。
            user_input: user 输入，具体约束请结合类型标注和调用方确认。
            raw_project_input: raw 项目 输入，具体约束请结合类型标注和调用方确认。
            allow_demo_defaults: allow 演示 defaults，具体约束请结合类型标注和调用方确认。

        返回:
            ProjectInputSchema

        阅读提示:
            主要直接调用：dict, strip, str, payload.get, ValueError, payload.setdefault, generation_requirements.setdefault, list。
        """
        if not allow_demo_defaults:
            # Production ingress is the canonical contract itself. Validate
            # before reading/defaulting/filtering any caller field.
            canonical = ProjectInputSchema.model_validate(
                raw_project_input,
                strict=True,
            )
            if canonical.task_id != task_id:
                raise ValueError(
                    f"task_id mismatch: runtime={task_id}, ProjectInput={canonical.task_id}"
                )
            if canonical.user_query != user_input:
                raise ValueError(
                    "user_input must match canonical ProjectInput.user_query"
                )
            return canonical

        # Demo defaults are an explicitly selected, versioned adapter. The
        # strict seed schema validates all supplied fields before enrichment.
        demo_seed = ProjectInputDemoSeedV1Schema.model_validate(
            raw_project_input if raw_project_input is not None else {},
            strict=True,
        )
        payload: Dict[str, Any] = demo_seed.model_dump(exclude_none=True)
        payload.setdefault("task_id", task_id)
        payload.setdefault("tenant_id", "default")
        payload.setdefault("user_query", user_input)
        payload.setdefault("source_materials", [])

        generation_requirements = dict(payload.get("generation_requirements") or {})
        output_schema = dict(payload.get("output_schema") or {})
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

        payload["generation_requirements"] = generation_requirements
        payload["output_schema"] = output_schema
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("input_source", "run_demo_cli")
        payload["metadata"] = metadata
        return ProjectInputSchema.model_validate(payload, strict=True)
