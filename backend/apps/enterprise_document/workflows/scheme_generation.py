"""建设方案主工作流的节点、读写边界和失败提交策略。"""

from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema


def build_scheme_generation_workflow(created_at: str) -> WorkflowDefinitionSchema:
    """构造“项目输入规范化 → 分章节方案生成”的确定性两节点工作流。

    每个节点显式声明读取键、逻辑输出和物理写路径。方案生成节点允许在文档硬门禁失败时
    提交证据、模型响应、草稿和拒绝产物，便于诊断；``deliverable_final_result`` 不在失败
    写路径中，因此被拒草稿绝不会借失败提交暴露为可交付结果。
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
                output_keys=[
                    "scheme_writer_output",
                    "generated_draft",
                    "rejected_artifact",
                    "deliverable_final_result",
                ],
                write_paths=[
                    "context_bundle.generation",
                    "contexts.scheme_writer_input",
                    "contexts.scheme_writer_output",
                    "contexts.rag_tool_output",
                    "generated_outputs",
                    "tool_results",
                    "generated_draft",
                    "rejected_artifact",
                    "deliverable_final_result",
                ],
                order=2,
                on_failure="fail_task",
                commit_policy="allow_partial_on_failure",
                failure_write_paths=[
                    "context_bundle.generation",
                    "contexts.scheme_writer_input",
                    "contexts.scheme_writer_output",
                    "contexts.rag_tool_output",
                    "generated_outputs",
                    "tool_results",
                    "generated_draft",
                    "rejected_artifact",
                ],
                failure_commit_error_codes=["DOCUMENT_HARD_GATE_FAILED"],
            ),
        ],
        created_at=created_at,
    )
