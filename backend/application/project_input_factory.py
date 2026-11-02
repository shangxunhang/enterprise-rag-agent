"""在应用入口构造规范 ``ProjectInput``。

生产路径要求调用方直接提交完整且严格合法的 ``ProjectInputSchema``，工厂不会过滤、
修补或默认任何字段。只有显式启用 demo 适配器时，才允许从受限 seed Schema 补齐
演示默认值；两条路径刻意分离，防止演示便利逻辑进入真实业务输入边界。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, Optional

from apps.enterprise_document.schemas.project_input_schema import (
    ProjectInputDemoSeedV1Schema,
    ProjectInputSchema,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    CURRENT_PROJECT_INPUT_VERSION,
    PROJECT_INPUT_V1,
    PROJECT_INPUT_V2,
    normalize_new_project_input,
)


# demo 方案生成在调用方未指定章节时使用的默认章节顺序。
# 该列表只属于显式 demo 适配器，不是生产输入的隐式业务规则。
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


# 从自然语言需求中提取已经包含文档类型后缀的候选标题。
# 字符范围限制标题长度和常见中英文项目名称字符，避免把整段请求当作文档标题。
_DOCUMENT_TITLE_PATTERN = re.compile(
    r"([A-Za-z0-9\u4e00-\u9fff·（）()_-]{2,80}?"
    r"(?:建设方案|实施方案|解决方案|可行性研究报告|可研报告|投标文件|标书|报告))"
)


def _derive_document_title(payload: Dict[str, Any], user_input: str) -> str:
    """为 demo 输入推导中性文档标题，不注入固定行业领域。

    Args:
        payload: 已通过 demo seed 校验并转换出的输入字典。
        user_input: 用户原始生成需求。

    Returns:
        优先使用项目名称，其次从用户请求提取文档标题，最后回退为
        ``项目建设方案``。
    """

    # 第一步：优先读取结构化项目名称，因为它比自然语言提取更可信。
    project_name = str(payload.get("project_name") or "").strip()
    if project_name:
        # 已经带文档类型后缀时直接使用，避免产生“方案建设方案”等重复标题。
        if project_name.endswith(
            ("方案", "报告", "投标文件", "标书", "可行性研究报告")
        ):
            return project_name

        # 只有裸项目名称时补充 demo 默认的“建设方案”后缀。
        return f"{project_name}建设方案"

    # 第二步：规范化自然语言请求，去除句末标点和首尾空白。
    query = str(user_input or "").strip().strip("。！？!? ")

    # 第三步：删除常见请求动词和量词，只保留可能包含标题的主体部分。
    query = re.sub(
        r"^(?:请|麻烦|帮我|请帮我|请为我)?(?:生成|编写|撰写|制定|输出|形成)"
        r"(?:一个|一份|一套)?",
        "",
        query,
    ).strip()

    # 第四步：提取首个符合标题字符和文档类型约束的片段。
    match = _DOCUMENT_TITLE_PATTERN.search(query)
    if match:
        return match.group(1)

    # 第五步：无法可靠推导时使用中性标题，不猜测行业、地点或项目事实。
    return "项目建设方案"


class ProjectInputFactory:
    """把外部原始输入转换为唯一规范的 ``ProjectInputSchema``。"""

    def build(
        self,
        task_id: str,
        user_input: str,
        raw_project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = False,
    ) -> ProjectInputSchema:
        """校验生产输入，或通过显式 demo 适配器构造规范输入。

        Args:
            task_id: 当前运行已分配的任务标识。
            user_input: 当前入口接收的用户需求文本。
            raw_project_input: 调用方提交的项目输入对象。
            allow_demo_defaults: 是否启用受版本控制的 demo 默认值适配器。

        Returns:
            经过严格 Pydantic 校验的规范项目输入。

        Raises:
            ValueError: 生产输入中的任务标识或用户请求与运行入口不一致时抛出；
                Pydantic 校验错误会直接向上传播。
        """

        if not allow_demo_defaults:
            # 生产路径第一步：在读取、默认或过滤任何字段前，直接严格校验完整契约。
            canonical = normalize_new_project_input(
                raw_project_input,
                strict=True,
            )

            # 生产路径第二步：核对外层运行身份，防止载荷声明另一个 task_id。
            if canonical.task_id != task_id:
                raise ValueError(
                    f"task_id mismatch: runtime={task_id}, ProjectInput={canonical.task_id}"
                )

            # 生产路径第三步：要求入口文本与规范 user_query 完全一致，避免双事实源。
            if canonical.user_query != user_input:
                raise ValueError(
                    "user_input must match canonical ProjectInput.user_query"
                )

            # 生产路径不做任何业务默认，直接返回调用方提供的规范对象。
            return canonical

        # demo 路径第一步：先用受限且有版本的 seed Schema 校验所有调用方字段。
        source_version = (
            raw_project_input.get("schema_version")
            if isinstance(raw_project_input, Mapping)
            else getattr(raw_project_input, "schema_version", None)
        )
        if source_version == PROJECT_INPUT_V1:
            raise ValueError(
                "explicit project_input_v1 requires caller-approved conversion before new Task ingress"
            )
        if source_version == PROJECT_INPUT_V2:
            raise ValueError(
                "explicit project_input_v2 requires caller-approved conversion before new Task ingress"
            )
        demo_seed = ProjectInputDemoSeedV1Schema.model_validate(
            raw_project_input if raw_project_input is not None else {},
            strict=True,
        )

        # demo 路径第二步：转换为可补充默认值的普通字典，并排除显式空值。
        payload: Dict[str, Any] = demo_seed.model_dump(exclude_none=True)
        payload["schema_version"] = CURRENT_PROJECT_INPUT_VERSION

        # demo 路径第三步：补齐运行身份、默认租户、用户请求和空素材集合。
        payload.setdefault("task_id", task_id)
        payload.setdefault("tenant_id", "default")
        payload.setdefault("user_query", user_input)
        payload.setdefault("source_materials", [])

        # demo 路径第四步：复制嵌套字典，避免原地修改 seed dump 中共享的对象引用。
        generation_requirements = dict(payload.get("generation_requirements") or {})
        output_schema = dict(payload.get("output_schema") or {})

        # demo 路径第五步：补充演示任务类型和默认章节、引用要求。
        payload.setdefault("task_type", "scheme_generation")
        generation_requirements.setdefault(
            "required_sections", list(DEFAULT_SCHEME_SECTIONS)
        )
        generation_requirements.setdefault("need_citation", True)
        generation_requirements.setdefault(
            "citation_required_sections",
            ["建设内容", "技术方案", "安全设计"],
        )

        # demo 路径第六步：补充可解释的标题、输出格式和输出章节契约。
        output_schema.setdefault(
            "document_title", _derive_document_title(payload, user_input)
        )
        output_schema.setdefault("output_format", "markdown")
        output_schema.setdefault(
            "required_sections", list(DEFAULT_SCHEME_SECTIONS)
        )

        # demo 路径第七步：写回已经独立补全的嵌套结构。
        payload["generation_requirements"] = generation_requirements
        payload["output_schema"] = output_schema

        # demo 路径第八步：标记输入来源，便于轨迹和数据采集区分演示数据。
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("input_source", "run_demo_cli")
        payload["metadata"] = metadata

        # demo 路径第九步：对补全后的最终载荷再次执行严格规范 Schema 校验。
        return normalize_new_project_input(payload, strict=True)
