"""Restore the latest checkpoint and replay the incremental transaction log."""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    StatePatchRequestSchema,
)

from .checkpoint_store import JsonCheckpointStore
from .event_log import DomainEventLog
from .state_gateway import StateGateway


class EventReplayRecoveryError(RuntimeError):
    pass


class EventReplayRecoveryService:
    def __init__(
        self,
        *,
        checkpoints: JsonCheckpointStore,
        event_log: DomainEventLog,
        state_gateway: StateGateway,
    ) -> None:
        self.checkpoints = checkpoints
        self.event_log = event_log
        self.state_gateway = state_gateway

    def recover(self, task_id: str, run_id: str) -> MultiAgentSharedStateSchema:
        state = self.checkpoints.load_latest(task_id, run_id)
        if state is None:
            raise EventReplayRecoveryError("recovery checkpoint not found")
        events = self.event_log.list_events(task_id, run_id)
        expected_sequence = state.state_meta.event_sequence + 1
        for event in events:
            if event.sequence < expected_sequence:
                continue
            if event.sequence != expected_sequence:
                raise EventReplayRecoveryError(
                    "domain event sequence gap during recovery: "
                    f"expected={expected_sequence}, actual={event.sequence}"
                )
            request_payload = event.payload.get("request")
            if event.event_type != "state_patch_committed" or not request_payload:
                raise EventReplayRecoveryError(
                    f"event cannot be replayed: {event.event_id}"
                )
            request = StatePatchRequestSchema.model_validate(request_payload)
            result = self.state_gateway.commit(state, request)
            if result.event.event_id != event.event_id:
                raise EventReplayRecoveryError(
                    f"replayed event identity mismatch: {event.event_id}"
                )
            state = result.state
            expected_sequence += 1
        return state
