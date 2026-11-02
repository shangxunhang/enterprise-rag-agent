"""从规范化 Agent 结果和共享状态恢复方案生成用例输入。"""


from typing import List, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema, TableAnalysisSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    read_saved_project_input,
)


class SchemeInputService:
    """优先读取真实规范化 Agent 输出，并兼容历史 FakeTableAgent 注册名。"""
    def read_inputs(
        self, shared_state: SharedStateSchema
    ) -> Tuple[ProjectInputSchema, TableAnalysisSchema, List[StructuredFactSchema]]:
        """读取并重新校验 ProjectInput、兼容表格视图和结构化事实。

        ProjectInput 缺失时立即失败；表格分析允许为空结构，事实优先使用规范化 Agent 输出，
        否则回退到共享状态的 canonical ``structured_facts``。
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
        project_input = read_saved_project_input(raw_project_input)

        raw_analysis = payload.get("table_analysis") or {}
        table_analysis = TableAnalysisSchema.model_validate(raw_analysis)

        structured_facts = [
            StructuredFactSchema.model_validate(item)
            for item in (payload.get("structured_facts") or shared_state.structured_facts)
        ]
        return project_input, table_analysis, structured_facts
