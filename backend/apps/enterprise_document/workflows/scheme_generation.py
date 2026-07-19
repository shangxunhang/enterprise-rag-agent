# =============================================================================
# 中文阅读说明：方案生成 Workflow 定义：声明节点、执行顺序、输入输出和失败策略。
# 主要定义：build_scheme_generation_workflow。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema


# 阅读注释（函数）：构建 scheme 生成 工作流。
def build_scheme_generation_workflow(created_at: str) -> WorkflowDefinitionSchema:
    """构建 scheme 生成 工作流。

    参数:
        created_at: created at，具体约束请结合类型标注和调用方确认。

    返回:
        WorkflowDefinitionSchema

    阅读提示:
        主要直接调用：WorkflowDefinitionSchema, WorkflowStepSchema。
    """
    return WorkflowDefinitionSchema(
        workflow_id="scheme_generation_v2",
        workflow_name="建设方案分章节生成流程",
        task_type="scheme_generation",
        workflow_version="v2.0",
        steps=[
            WorkflowStepSchema(
                step_id="step_001",
                step_name="project_input_normalization",
                step_type="agent",
                target_name="ProjectInputNormalizerAgent",
                input_keys=["project_input"],
                output_keys=["normalized_project_input", "structured_facts"],
                write_paths=[
                    "contexts.project_input",
                    "contexts.table_agent_output",
                    "structured_facts",
                ],
                order=1,
                on_failure="fail_task",
            ),
            WorkflowStepSchema(
                step_id="step_002",
                step_name="section_generation",
                step_type="agent",
                target_name="SchemeWriterAgent",
                input_keys=["normalized_project_input", "structured_facts"],
                output_keys=["scheme_writer_output", "scheme_draft"],
                write_paths=[
                    "context_bundle.evidence",
                    "context_bundle.generation",
                    "contexts.scheme_writer_input",
                    "contexts.scheme_writer_output",
                    "contexts.rag_tool_output",
                    "generated_outputs",
                    "tool_results",
                    "final_result",
                ],
                order=2,
                on_failure="fail_task",
                commit_policy="allow_partial_on_failure",
                failure_write_paths=[
                    "context_bundle.evidence",
                    "context_bundle.generation",
                    "contexts.scheme_writer_input",
                    "contexts.scheme_writer_output",
                    "contexts.rag_tool_output",
                    "generated_outputs",
                    "tool_results",
                    "final_result",
                ],
                failure_commit_error_codes=["DOCUMENT_HARD_GATE_FAILED"],
            ),
        ],
        created_at=created_at,
    )
