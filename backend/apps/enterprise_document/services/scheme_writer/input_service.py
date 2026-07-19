# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeInputService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import List, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema, TableAnalysisSchema


# 阅读注释（类）：封装 scheme 输入 服务，封装一组可复用的业务能力。
class SchemeInputService:
    """封装 scheme 输入 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：读取 inputs。
    def _read_inputs(
        self, shared_state: SharedStateSchema
    ) -> Tuple[ProjectInputSchema, TableAnalysisSchema, List[StructuredFactSchema]]:
        """读取 inputs。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            Tuple[ProjectInputSchema, TableAnalysisSchema, List[StructuredFactSchema]]

        阅读提示:
            主要直接调用：SharedStateReader, reader.get_agent_result, normalizer_result.get, payload.get, get, ValueError, ProjectInputSchema.model_validate, TableAnalysisSchema.model_validate。
        """
        reader = SharedStateReader()
        normalizer_result = (
            reader.get_agent_result(shared_state, "ProjectInputNormalizerAgent")
            or reader.get_agent_result(shared_state, "FakeTableAgent")
            or {}
        )
        payload = normalizer_result.get("result") or {}
        raw_project_input = (
            payload.get("project_input")
            or shared_state.context_bundle.business.project_input
            or (shared_state.requirements or {}).get("project_input")
        )
        if not raw_project_input:
            raise ValueError("PROJECT_INPUT_REQUIRED: no ProjectInput found in workflow state")
        project_input = ProjectInputSchema.model_validate(raw_project_input)

        raw_analysis = payload.get("table_analysis") or {}
        table_analysis = TableAnalysisSchema.model_validate(raw_analysis)

        structured_facts = [
            StructuredFactSchema.model_validate(item)
            for item in (payload.get("structured_facts") or shared_state.structured_facts)
        ]
        return project_input, table_analysis, structured_facts
