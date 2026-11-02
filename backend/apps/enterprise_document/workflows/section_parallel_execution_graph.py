"""LangGraph-owned fan-out for explicitly independent document sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from operator import add
from threading import Event, Lock
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from apps.enterprise_document.schemas.scheme_writer_schema import (
    ParallelSectionExecutionEnvelopeSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.quality.budget import WorkflowBudgetExceeded
from core.runtime.langchain_config import merge_runnable_config
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    current_execution_control,
)
from core.runtime.recovery import (
    activate_durable_recovery,
    current_durable_recovery,
)


class _ParallelSectionState(TypedDict):
    envelopes: Annotated[list[dict], add]


@dataclass
class ParallelSectionBudgetAllocator:
    """Atomically preallocate non-overlapping per-section budget slices."""

    max_total_llm_calls: int
    max_total_tokens: int
    _reserved_llm_calls: int = field(default=0, init=False, repr=False)
    _reserved_tokens: int = field(default=0, init=False, repr=False)
    _reservations: dict[str, dict[str, int]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def reserve_batch(
        self,
        section_ids: list[str],
        *,
        llm_calls_per_section: int,
        tokens_per_section: int,
    ) -> dict[str, dict[str, int]]:
        """Reserve a whole deterministic batch or reserve nothing."""

        call_slice = max(1, int(llm_calls_per_section))
        token_slice = max(256, int(tokens_per_section))
        ordered_ids = list(dict.fromkeys(section_ids))
        with self._lock:
            new_ids = [
                section_id
                for section_id in ordered_ids
                if section_id not in self._reservations
            ]
            requested_calls = len(new_ids) * call_slice
            requested_tokens = len(new_ids) * token_slice
            if (
                self._reserved_llm_calls + requested_calls
                > self.max_total_llm_calls
            ):
                raise WorkflowBudgetExceeded(
                    "parallel_llm_calls",
                    self.max_total_llm_calls,
                )
            if self._reserved_tokens + requested_tokens > self.max_total_tokens:
                raise WorkflowBudgetExceeded(
                    "parallel_tokens",
                    self.max_total_tokens,
                )
            for section_id in new_ids:
                self._reservations[section_id] = {
                    "max_total_llm_calls": call_slice,
                    "max_total_tokens": token_slice,
                }
            self._reserved_llm_calls += requested_calls
            self._reserved_tokens += requested_tokens
            return {
                section_id: dict(self._reservations[section_id])
                for section_id in ordered_ids
            }

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "max_total_llm_calls": self.max_total_llm_calls,
                "max_total_tokens": self.max_total_tokens,
                "reserved_llm_calls": self._reserved_llm_calls,
                "reserved_tokens": self._reserved_tokens,
                "reserved_sections": len(self._reservations),
            }


class SectionParallelExecutionGraph:
    """Schedule one safe section batch through LangGraph's executor.

    Dependency planning stays outside this class.  Every item passed here must
    already be dependency-free and explicitly opt out of global-context reads.
    """

    def execute_batch(
        self,
        section_plans: list[SectionPlanSchema],
        *,
        max_concurrency: int,
        execute: Callable[
            [SectionPlanSchema],
            ParallelSectionExecutionEnvelopeSchema,
        ],
    ) -> list[ParallelSectionExecutionEnvelopeSchema]:
        if len(section_plans) < 2:
            return [execute(item) for item in section_plans]
        if max_concurrency < 2:
            return [execute(item) for item in section_plans]
        for section in section_plans:
            if (
                not section.can_run_in_parallel
                or section.requires_global_context
                or section.depends_on
            ):
                raise ValueError(
                    "parallel batch contains a section outside the safe boundary"
                )

        cancellation_requested = Event()
        recovery_context = current_durable_recovery()
        builder = StateGraph(_ParallelSectionState)
        for index, section_plan in enumerate(section_plans, start=1):
            node_name = f"parallel_section_{index:03d}"

            def run_section(
                _state: _ParallelSectionState,
                *,
                plan: SectionPlanSchema = section_plan,
            ) -> dict[str, list[dict]]:
                execution_control = current_execution_control()
                if (
                    execution_control is not None
                    and execution_control.should_stop
                ):
                    cancellation_requested.set()
                    execution_control.checkpoint()
                if cancellation_requested.is_set():
                    raise WorkflowExecutionCancelled(
                        "parallel section batch cancelled before branch start"
                    )
                try:
                    if recovery_context is None:
                        envelope = execute(plan)
                    else:
                        # LangGraph may schedule branches on worker threads;
                        # explicitly carry the recovery boundary into each one.
                        with activate_durable_recovery(recovery_context):
                            envelope = execute(plan)
                    if (
                        execution_control is not None
                        and execution_control.should_stop
                    ):
                        execution_control.checkpoint()
                except BaseException:
                    cancellation_requested.set()
                    raise
                return {
                    "envelopes": [
                        envelope.model_dump(
                            mode="json",
                            round_trip=True,
                        )
                    ]
                }

            builder.add_node(node_name, run_section)
            builder.add_edge(START, node_name)
            builder.add_edge(node_name, END)
        # This scheduler is an ephemeral fan-out nested inside the durable
        # ``execute_sections`` business node.  ``False`` explicitly prevents
        # inheriting the parent's checkpointer; only the section subgraphs own
        # durable section namespaces.
        graph = builder.compile(checkpointer=False)
        result = graph.invoke(
            {"envelopes": []},
            # This ephemeral graph has no checkpointer; explicitly keep it out
            # of the parent's inherited synchronous checkpoint path.
            durability="async",
            config=merge_runnable_config(
                base={
                    "run_name": "parallel_section_batch",
                    "tags": [
                        "enterprise-rag-agent",
                        "langgraph",
                        "section-parallelism",
                    ],
                    "metadata": {
                        "parallel_section_count": len(section_plans),
                        "section_max_concurrency": max_concurrency,
                    },
                },
                supplied={"max_concurrency": max_concurrency},
            ),
        )
        restored = [
            ParallelSectionExecutionEnvelopeSchema.model_validate(item)
            for item in result["envelopes"]
        ]
        return sorted(
            restored,
            key=lambda item: (
                item.result.section.section_order,
                item.section_id,
            ),
        )
