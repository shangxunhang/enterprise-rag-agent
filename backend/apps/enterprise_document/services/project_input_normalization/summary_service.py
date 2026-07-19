# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：ProjectInputSummaryService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Pure presentation of normalized project facts."""

from __future__ import annotations

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


# 阅读注释（类）：封装 项目 输入 summary 服务，封装一组可复用的业务能力。
class ProjectInputSummaryService:
    """封装 项目 输入 summary 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：处理 hardware summary 相关逻辑。
    @staticmethod
    def hardware_summary(project_input: ProjectInputSchema) -> str:
        """处理 hardware summary 相关逻辑。

        参数:
            project_input: 规范化后的项目输入。

        返回:
            str

        阅读提示:
            主要直接调用：parts.append, join。
        """
        if not project_input.hardware_resources:
            return "当前未提供硬件资源信息。"
        parts: list[str] = []
        for item in project_input.hardware_resources:
            card_text = ""
            if item.total_cards is not None:
                card_text = f"，共{item.total_cards}张卡"
            elif item.cards_per_server is not None:
                card_text = f"，每台{item.cards_per_server}张卡"
            access_text = f"，访问方式为{item.access_mode}" if item.access_mode else ""
            parts.append(f"{item.server_count}台{item.device_model}{card_text}{access_text}")
        return "；".join(parts) + "。"

    # 阅读注释（函数）：处理 organization summary 相关逻辑。
    @staticmethod
    def organization_summary(project_input: ProjectInputSchema) -> str:
        """处理 organization summary 相关逻辑。

        参数:
            project_input: 规范化后的项目输入。

        返回:
            str

        阅读提示:
            主要直接调用：parts.append, group.description.rstrip, join。
        """
        parts: list[str] = []
        if project_input.total_staff is not None:
            parts.append(f"总人数{project_input.total_staff}人")
        if project_input.functional_department_count is not None:
            parts.append(f"职能部门{project_input.functional_department_count}个")
        if project_input.business_department_count is not None:
            parts.append(f"业务部门{project_input.business_department_count}个")
        for group in project_input.department_groups:
            if group.description:
                parts.append(group.description.rstrip("。"))
            else:
                scale = ""
                if group.approximate_staff_per_department is not None:
                    scale = f"，每个约{group.approximate_staff_per_department}人"
                elif group.max_staff_per_department is not None:
                    scale = f"，每个不超过{group.max_staff_per_department}人"
                parts.append(f"{group.group_name}{group.department_count}个{scale}")
        return "；".join(parts) + ("。" if parts else "")
