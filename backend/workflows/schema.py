"""旧导入路径兼容层；活动工作流定义位于 enterprise_document 应用内。"""

from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)

__all__ = ["build_scheme_generation_workflow"]
