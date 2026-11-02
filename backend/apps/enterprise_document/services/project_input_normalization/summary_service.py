"""把规范化项目事实确定性地表达为下游兼容摘要文本。"""

from __future__ import annotations

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


class ProjectInputSummaryService:
    """只格式化已知事实，不补齐缺失组织或硬件信息。"""
    @staticmethod
    def hardware_summary(project_input: ProjectInputSchema) -> str:
        """按已确认服务器、卡数和访问方式生成硬件摘要；缺失时明确说明未提供。"""
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

    @staticmethod
    def organization_summary(project_input: ProjectInputSchema) -> str:
        """组合总人数、部门数量和部门组描述，不从分组反推未声明总量。"""
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
