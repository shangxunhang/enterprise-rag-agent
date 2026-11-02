"""Hard budget for one section-level grounded generation workflow."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator

from model_gateway.call_boundary import ModelCallBudgetExceeded


class TaskDrainingBudgetExceeded(RuntimeError):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = int(limit)
        super().__init__(f"task budget draining: {resource} limit={limit}")


@dataclass
class TaskDrainingBudget:
    """Checkpoint-safe admission control for new Section atomic operations.

    A Section admitted before draining may finish.  Once the configured limit is
    reached, subsequent Section starts are rejected before any RAG/Model call.
    """

    max_section_starts: int = 1_000_000
    started_section_ids: list[str] = field(default_factory=list)
    completed_section_ids: list[str] = field(default_factory=list)
    active_section_ids: list[str] = field(default_factory=list)
    draining: bool = False
    draining_reason: str | None = None

    @classmethod
    def from_policy_metadata(cls, metadata: dict | None) -> "TaskDrainingBudget":
        raw = dict(metadata or {})
        nested = raw.get("task_budget")
        if isinstance(nested, dict):
            raw = dict(nested)
        return cls(
            max_section_starts=max(
                0,
                int(raw.get("max_section_starts", 1_000_000)),
            )
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict | None,
        *,
        policy_metadata: dict | None = None,
    ) -> "TaskDrainingBudget":
        raw = dict(snapshot or {})
        if not raw:
            return cls.from_policy_metadata(policy_metadata)
        return cls(
            max_section_starts=max(
                0,
                int(raw.get("max_section_starts", 1_000_000)),
            ),
            started_section_ids=list(
                dict.fromkeys(str(item) for item in raw.get("started_section_ids", []))
            ),
            completed_section_ids=list(
                dict.fromkeys(str(item) for item in raw.get("completed_section_ids", []))
            ),
            active_section_ids=list(
                dict.fromkeys(str(item) for item in raw.get("active_section_ids", []))
            ),
            draining=bool(raw.get("draining", False)),
            draining_reason=(
                str(raw.get("draining_reason") or "").strip() or None
            ),
        )

    def begin_section(self, section_id: str) -> None:
        section_id = str(section_id).strip()
        if not section_id:
            raise ValueError("task budget requires section_id")
        if section_id in self.completed_section_ids:
            return
        if section_id in self.active_section_ids:
            return
        if self.draining:
            raise TaskDrainingBudgetExceeded(
                self.draining_reason or "draining",
                self.max_section_starts,
            )
        if len(self.started_section_ids) >= self.max_section_starts:
            self.draining = True
            self.draining_reason = "section_starts"
            raise TaskDrainingBudgetExceeded(
                "section_starts",
                self.max_section_starts,
            )
        self.started_section_ids.append(section_id)
        self.active_section_ids.append(section_id)

    def finish_section(self, section_id: str) -> None:
        section_id = str(section_id).strip()
        self.active_section_ids = [
            item for item in self.active_section_ids if item != section_id
        ]
        if section_id and section_id not in self.completed_section_ids:
            self.completed_section_ids.append(section_id)
        if len(self.started_section_ids) >= self.max_section_starts:
            self.draining = True
            self.draining_reason = self.draining_reason or "section_starts"

    def abort_section(self, section_id: str) -> None:
        section_id = str(section_id).strip()
        self.active_section_ids = [
            item for item in self.active_section_ids if item != section_id
        ]

    def snapshot(self) -> dict[str, object]:
        return {
            "budget_semantics": "task_section_admission_v1",
            "max_section_starts": self.max_section_starts,
            "started_section_ids": list(self.started_section_ids),
            "completed_section_ids": list(self.completed_section_ids),
            "active_section_ids": list(self.active_section_ids),
            "draining": self.draining,
            "draining_reason": self.draining_reason,
        }


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

    @classmethod
    def from_snapshot(cls, snapshot: dict | None) -> "WorkflowBudget":
        """Restore a checkpointed budget without resetting consumed counters."""

        raw = dict(snapshot or {})
        budget = cls.from_policy_metadata(raw)
        budget.retrieval_rounds = max(0, int(raw.get("retrieval_rounds", 0)))
        budget.rewrite_rounds = max(0, int(raw.get("rewrite_rounds", 0)))
        budget.llm_calls = max(
            0,
            int(raw.get("llm_calls", raw.get("logical_model_calls", 0))),
        )
        budget.reserved_tokens = max(0, int(raw.get("reserved_tokens", 0)))
        return budget

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
_ACTIVE_TASK_BUDGET: ContextVar[TaskDrainingBudget | None] = ContextVar(
    "enterprise_document_task_draining_budget",
    default=None,
)


def current_workflow_budget() -> WorkflowBudget | None:
    return _ACTIVE_BUDGET.get()


def current_task_draining_budget() -> TaskDrainingBudget | None:
    return _ACTIVE_TASK_BUDGET.get()


@contextmanager
def activate_workflow_budget(budget: WorkflowBudget) -> Iterator[WorkflowBudget]:
    token: Token[WorkflowBudget | None] = _ACTIVE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_BUDGET.reset(token)


@contextmanager
def activate_task_draining_budget(
    budget: TaskDrainingBudget,
) -> Iterator[TaskDrainingBudget]:
    token: Token[TaskDrainingBudget | None] = _ACTIVE_TASK_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_TASK_BUDGET.reset(token)
