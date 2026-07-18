"""Resolve a workflow step to an isolated handler."""

from __future__ import annotations

from typing import Dict, Iterable

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.steps.base import WorkflowStepHandler
from agent.runtime.steps.unsupported_step import UnsupportedStepHandler
from agent.runtime.workflow_schema import WorkflowStepSchema
from schemas.agent import AgentResultSchema


class WorkflowStepDispatcher:
    def __init__(
        self,
        handlers: Iterable[WorkflowStepHandler],
        fallback: WorkflowStepHandler | None = None,
    ) -> None:
        self._handlers: Dict[str, WorkflowStepHandler] = {
            handler.step_type: handler for handler in handlers
        }
        self.fallback = fallback or UnsupportedStepHandler()

    def register(self, handler: WorkflowStepHandler) -> None:
        self._handlers[handler.step_type] = handler

    def execute(
        self,
        step: WorkflowStepSchema,
        state: SharedStateSchema,
    ) -> AgentResultSchema:
        return self._handlers.get(step.step_type, self.fallback).execute(step, state)
