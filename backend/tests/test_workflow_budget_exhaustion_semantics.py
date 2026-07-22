from __future__ import annotations

import pytest

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
    current_workflow_budget,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionExecutionRequestSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.semantic_section_judge import SemanticSectionJudge
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus


class _BudgetTriggerGenerationService:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.runtime_support = SchemeWriterRuntimeSupport()
        self.generation_quality_metadata = {
            "max_retrieval_rounds": (
                0 if mode in {"retrieval_rounds", "failed_retrieval_rounds"} else 1
            ),
            "max_rewrite_rounds": 1,
            "max_total_llm_calls": 1 if mode == "llm_calls" else 8,
            "max_total_tokens": 256 if mode == "tokens" else 24000,
            "human_review_on_exhaustion": True,
        }

    @staticmethod
    def _seed_best_output(shared_state: SharedStateSchema, section_order: int) -> None:
        section_id = f"section_{shared_state.run_id}_{section_order:03d}"
        model_call_id = f"model_call_{shared_state.run_id}_{section_id}"
        shared_state.generated_outputs[model_call_id] = {
            "model_call_id": model_call_id,
            "success": True,
            "content": "这是预算耗尽前已经得到的当前最好版本。",
            "finish_reason": "stop",
        }

    def generate_section(self, shared_state: SharedStateSchema, **kwargs) -> SchemeSectionSchema:
        section_order = int(kwargs["section_order"])
        section_title = str(kwargs["section_title"])
        section_id = f"section_{shared_state.run_id}_{section_order:03d}"
        budget = current_workflow_budget()
        assert budget is not None

        if self.mode in {"retrieval_rounds", "failed_retrieval_rounds"}:
            failed = self.mode == "failed_retrieval_rounds"
            error = (
                self.runtime_support.error(
                    "SECTION_HARD_GATE_FAILED",
                    "pre-existing section hard failure",
                    node=section_id,
                    retryable=True,
                    user_message="章节存在独立硬失败。",
                )
                if failed
                else None
            )
            return SchemeSectionSchema(
                section_id=section_id,
                section_title=section_title,
                section_order=section_order,
                content="初始章节正文。",
                status=(ExecutionStatus.FAILED if failed else ExecutionStatus.SUCCESS),
                error=error,
                eval_result=SectionEvalSchema(
                    passed=not failed,
                    checks={"citation_bound": True, "model_success": not failed},
                    failures=["model_success"] if failed else [],
                    warnings=[],
                ),
                extra={
                    "generation_check": {
                        "is_supported": False,
                        "need_rewrite": False,
                        "need_retrieve_more": True,
                    }
                },
            )

        self._seed_best_output(shared_state, section_order)
        if self.mode == "rewrite_rounds":
            budget.consume_rewrite_round()
            budget.consume_rewrite_round()
        elif self.mode == "llm_calls":
            budget.reserve_llm_call(max_tokens=1)
            budget.reserve_llm_call(max_tokens=1)
        elif self.mode == "tokens":
            budget.reserve_llm_call(max_tokens=200)
            budget.reserve_llm_call(max_tokens=100)
        else:  # pragma: no cover - protects the test double from silent misuse.
            raise AssertionError(f"unknown mode: {self.mode}")
        raise AssertionError("budget trigger must raise before this line")


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_budget",
        run_id="run_budget",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_budget",
                run_id="run_budget",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-07-22T00:00:00+00:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_budget",
            "task_type": "scheme_generation",
            "user_query": "生成建设方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": [],
                "need_citation": False,
            },
        }
    )


def _request(state: SharedStateSchema) -> SectionExecutionRequestSchema:
    return SectionExecutionRequestSchema(
        shared_state=state,
        document_id="document_run_budget",
        project_input=_project_input(),
        section_plan=SectionPlanSchema(
            section_id="section_plan_001",
            section_title="技术方案",
            section_order=1,
            citation_required=False,
        ),
        document_rag_context=RAGContextSchema(
            context_text="已有文档级证据。",
            used_context_chars=8,
            context_item_count=1,
        ),
        document_retrieved_chunks=[],
        document_citations=[],
        document_evidence_assessment={"status": "sufficient"},
        document_tool_call_ids=["rag_document"],
        section_retrieval_enabled=False,
        corrective_retrieval_enabled=False,
    )


@pytest.mark.parametrize(
    ("mode", "resource"),
    [
        ("retrieval_rounds", "retrieval_rounds"),
        ("rewrite_rounds", "rewrite_rounds"),
        ("llm_calls", "llm_calls"),
        ("tokens", "tokens"),
    ],
)
def test_budget_exhaustion_becomes_controlled_partial_success(
    mode: str,
    resource: str,
) -> None:
    state = _shared_state()
    generation_service = _BudgetTriggerGenerationService(mode)
    coordinator = SectionExecutionCoordinator(
        evidence_service=object(),  # Retrieval paths are intentionally disabled in this test.
        query_builder=object(),
        section_generation_service=generation_service,
        runtime_support=generation_service.runtime_support,
        generation_quality_metadata=generation_service.generation_quality_metadata,
    )

    result = coordinator.execute(
        _request(state),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.status == ExecutionStatus.PARTIAL_SUCCESS
    assert result.need_human_review is True
    assert result.error is None
    assert result.section.extra["workflow_budget_exhausted"] is True
    assert result.section.extra["need_human_review"] is True
    assert result.section.extra["workflow_budget_exhaustion"]["resource"] == resource
    assert result.evidence.metadata["workflow_budget_exhaustion"]["resource"] == resource

    warnings = [
        item
        for item in result.section.warnings
        if item.warning_code == "WORKFLOW_BUDGET_EXHAUSTED"
    ]
    assert len(warnings) == 1
    assert warnings[0].details["resource"] == resource
    assert warnings[0].details["need_human_review"] is True
    assert result.section.eval_result is not None
    assert result.section.eval_result.checks["workflow_budget_available"] is False

    if mode == "retrieval_rounds":
        assert result.section.content == "初始章节正文。"
    else:
        assert result.section.content == "这是预算耗尽前已经得到的当前最好版本。"


def test_budget_exhaustion_does_not_downgrade_existing_hard_failure() -> None:
    state = _shared_state()
    generation_service = _BudgetTriggerGenerationService("failed_retrieval_rounds")
    coordinator = SectionExecutionCoordinator(
        evidence_service=object(),
        query_builder=object(),
        section_generation_service=generation_service,
        runtime_support=generation_service.runtime_support,
        generation_quality_metadata=generation_service.generation_quality_metadata,
    )

    result = coordinator.execute(
        _request(state),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.status == ExecutionStatus.FAILED
    assert result.section.error is not None
    assert result.section.error.error_code == "SECTION_HARD_GATE_FAILED"
    assert result.section.eval_result is not None
    assert result.section.eval_result.passed is False
    assert result.section.eval_result.failures == ["model_success"]
    assert result.section.extra["workflow_budget_exhausted"] is True
    assert any(
        warning.warning_code == "WORKFLOW_BUDGET_EXHAUSTED"
        for warning in result.section.warnings
    )


def test_semantic_gate_model_call_consumes_active_section_budget() -> None:
    class _FailIfCalledGateway:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            raise AssertionError("semantic model must not run after budget exhaustion")

    gateway = _FailIfCalledGateway()
    judge = SemanticSectionJudge(
        model_gateway=gateway,  # type: ignore[arg-type]
        model_name="deepseek_api",
        enabled=True,
    )
    budget = WorkflowBudget(
        max_retrieval_rounds=1,
        max_rewrite_rounds=1,
        max_total_llm_calls=1,
        max_total_tokens=4096,
    )

    with activate_workflow_budget(budget):
        # Simulate the initial section generation having already consumed the
        # only available LLM call. The semantic gate must share this same budget
        # regardless of whether the routed model is local or an external API.
        budget.reserve_llm_call(max_tokens=256)
        with pytest.raises(WorkflowBudgetExceeded, match="llm_calls"):
            judge.judge(
                task_id="task_budget",
                run_id="run_budget",
                created_at="2026-07-22T00:00:00+00:00",
                section_id="section_run_budget_001",
                section_title="技术方案",
                content="这是待审查章节。",
                project_input=_project_input(),
                citations=[],
                required_sections=["技术方案"],
                deterministic_candidates=[],
                overlong=False,
            )

    assert gateway.calls == 0
    assert budget.llm_calls == 1
