from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema


def build_scheme_generation_workflow(created_at: str) -> WorkflowDefinitionSchema:
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
