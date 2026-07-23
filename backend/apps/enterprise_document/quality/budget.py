"""Hard budget for one section-level grounded generation workflow."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

from model_gateway.call_boundary import ModelCallBudgetExceeded


class WorkflowBudgetExceeded(ModelCallBudgetExceeded):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = int(limit)
        super().__init__(f"workflow budget exhausted: {resource} limit={limit}")


@dataclass
class WorkflowBudget:
    """Mutable safety fuse for logical calls in one section quality loop.

    ``llm_calls`` is kept as a compatibility field name.  It counts one
    business ``ModelCallBoundary`` invocation, not each availability-fallback
    provider attempt.  Actual attempts/tokens/cost belong to ModelUsageLedger.
    """

    max_retrieval_rounds: int
    max_rewrite_rounds: int
    max_total_llm_calls: int
    max_total_tokens: int
    human_review_on_exhaustion: bool = True
    retrieval_rounds: int = 0
    rewrite_rounds: int = 0
    llm_calls: int = 0
    reserved_tokens: int = 0

    @classmethod
    def from_policy_metadata(cls, metadata: dict | None) -> "WorkflowBudget":
        raw = dict(metadata or {})
        return cls(
            max_retrieval_rounds=max(0, int(raw.get("max_retrieval_rounds", 1))),
            max_rewrite_rounds=max(0, int(raw.get("max_rewrite_rounds", 1))),
            max_total_llm_calls=max(1, int(raw.get("max_total_llm_calls", 35))),
            max_total_tokens=max(256, int(raw.get("max_total_tokens", 24000))),
            human_review_on_exhaustion=bool(
                raw.get("human_review_on_exhaustion", True)
            ),
        )

    def reserve_llm_call(self, *, max_tokens: int) -> None:
        requested_tokens = max(1, int(max_tokens))
        if self.llm_calls >= self.max_total_llm_calls:
            raise WorkflowBudgetExceeded("llm_calls", self.max_total_llm_calls)
        if self.reserved_tokens + requested_tokens > self.max_total_tokens:
            raise WorkflowBudgetExceeded("tokens", self.max_total_tokens)
        self.llm_calls += 1
        self.reserved_tokens += requested_tokens

    def consume_retrieval_round(self) -> None:
        if self.retrieval_rounds >= self.max_retrieval_rounds:
            raise WorkflowBudgetExceeded(
                "retrieval_rounds", self.max_retrieval_rounds
            )
        self.retrieval_rounds += 1

    def consume_rewrite_round(self) -> None:
        if self.rewrite_rounds >= self.max_rewrite_rounds:
            raise WorkflowBudgetExceeded("rewrite_rounds", self.max_rewrite_rounds)
        self.rewrite_rounds += 1

    def snapshot(self) -> dict[str, int | bool | str]:
        return {
            "budget_semantics": "logical_model_call_v1",
            "token_budget_semantics": "reserved_output_allowance_v1",
            "max_retrieval_rounds": self.max_retrieval_rounds,
            "max_rewrite_rounds": self.max_rewrite_rounds,
            "max_total_llm_calls": self.max_total_llm_calls,
            "max_total_tokens": self.max_total_tokens,
            "retrieval_rounds": self.retrieval_rounds,
            "rewrite_rounds": self.rewrite_rounds,
            "llm_calls": self.llm_calls,
            "logical_model_calls": self.llm_calls,
            "reserved_tokens": self.reserved_tokens,
            "human_review_on_exhaustion": self.human_review_on_exhaustion,
        }


_ACTIVE_BUDGET: ContextVar[WorkflowBudget | None] = ContextVar(
    "enterprise_document_workflow_budget",
    default=None,
)


def current_workflow_budget() -> WorkflowBudget | None:
    return _ACTIVE_BUDGET.get()


@contextmanager
def activate_workflow_budget(budget: WorkflowBudget) -> Iterator[WorkflowBudget]:
    token: Token[WorkflowBudget | None] = _ACTIVE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_BUDGET.reset(token)
