"""Hard budget contracts for section-level grounded generation."""

from __future__ import annotations

import pytest

from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
    current_workflow_budget,
)


def _budget() -> WorkflowBudget:
    return WorkflowBudget(
        max_retrieval_rounds=1,
        max_rewrite_rounds=1,
        max_total_llm_calls=2,
        max_total_tokens=100,
    )


def test_workflow_budget_enforces_each_hard_limit() -> None:
    budget = _budget()
    budget.consume_retrieval_round()
    budget.consume_rewrite_round()
    budget.reserve_llm_call(max_tokens=40)
    budget.reserve_llm_call(max_tokens=60)

    with pytest.raises(WorkflowBudgetExceeded, match="retrieval_rounds"):
        budget.consume_retrieval_round()
    with pytest.raises(WorkflowBudgetExceeded, match="rewrite_rounds"):
        budget.consume_rewrite_round()
    with pytest.raises(WorkflowBudgetExceeded, match="llm_calls"):
        budget.reserve_llm_call(max_tokens=1)

    assert budget.snapshot()["reserved_tokens"] == 100


def test_token_exhaustion_is_atomic() -> None:
    budget = _budget()
    budget.reserve_llm_call(max_tokens=80)

    with pytest.raises(WorkflowBudgetExceeded, match="tokens"):
        budget.reserve_llm_call(max_tokens=21)

    assert budget.llm_calls == 1
    assert budget.reserved_tokens == 80


def test_active_budget_is_scoped_and_restored() -> None:
    budget = _budget()
    assert current_workflow_budget() is None

    with activate_workflow_budget(budget):
        assert current_workflow_budget() is budget

    assert current_workflow_budget() is None
