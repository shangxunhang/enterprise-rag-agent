"""从共享状态的兼容位置读取并校验调用方提供的 ProjectInput。"""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


class ProjectInputReader:
    """只接受可验证的项目输入，不在缺失时注入 demo 业务事实。"""
    def __init__(self, state_reader: SharedStateReader | None = None) -> None:
        """注入共享状态兼容读取器。"""
        self.state_reader = state_reader or SharedStateReader()

    def read(self, state: SharedStateSchema) -> ProjectInputSchema:
        """按事实源优先级读取首个非空候选并重新执行 Schema 校验。"""
        for candidate in self.state_reader.project_input_candidates(state):
            if isinstance(candidate, ProjectInputSchema):
                return candidate
            if hasattr(candidate, "model_dump"):
                candidate = candidate.model_dump()
            if isinstance(candidate, dict) and candidate:
                return ProjectInputSchema.model_validate(candidate)
        raise ValueError(
            "PROJECT_INPUT_REQUIRED: caller must provide a validated "
            "ProjectInputSchema; the workflow no longer injects demo business facts."
        )
