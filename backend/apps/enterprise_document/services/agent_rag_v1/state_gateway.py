"""分域乐观锁、字段级 Patch、迁移校验与 Domain Event 原子边界。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1 import (
    DomainEventSchema,
    MultiAgentSharedStateSchema,
    PatchOperation,
    SectionDomainVersionsSchema,
    StateActorRole,
    StatePatchRequestSchema,
    WorkflowErrorCategory,
    WorkflowErrorSchema,
    WorkflowErrorScope,
    WorkflowErrorSource,
    WorkflowRetryability,
)

from .event_log import DomainEventLog
from .permissions import StateWritePermissionError, WritePermissionValidator
from .transitions import StateTransitionError, StateTransitionValidator


class StateGatewayError(RuntimeError):
    """StateGateway 的所有拒绝都携带规范 WorkflowError。"""

    def __init__(self, workflow_error: WorkflowErrorSchema) -> None:
        self.workflow_error = workflow_error
        super().__init__(workflow_error.message)


@dataclass(frozen=True)
class StateCommitResult:
    state: MultiAgentSharedStateSchema
    event: DomainEventSchema
    committed: bool


def _decode_pointer(path: str) -> list[str]:
    return [
        segment.replace("~1", "/").replace("~0", "~")
        for segment in path.lstrip("/").split("/")
    ]


def _list_index(segment: str, length: int, *, allow_append: bool) -> int:
    if allow_append and segment == "-":
        return length
    try:
        index = int(segment)
    except ValueError as exc:
        raise ValueError(f"list patch segment is not an index: {segment}") from exc
    if index < 0 or index >= length:
        raise ValueError(f"list patch index out of range: {index}")
    return index


def _apply_operation(document: Any, operation: Any) -> None:
    segments = _decode_pointer(operation.path)
    parent = document
    for segment in segments[:-1]:
        if isinstance(parent, dict):
            if segment not in parent:
                raise ValueError(f"patch parent path does not exist: {operation.path}")
            parent = parent[segment]
        elif isinstance(parent, list):
            parent = parent[_list_index(segment, len(parent), allow_append=False)]
        else:
            raise ValueError(f"patch parent is not a container: {operation.path}")

    leaf = segments[-1]
    if isinstance(parent, dict):
        if operation.operation == PatchOperation.ADD:
            if leaf in parent:
                raise ValueError(f"add target already exists: {operation.path}")
            parent[leaf] = deepcopy(operation.value)
        elif operation.operation == PatchOperation.REPLACE:
            if leaf not in parent:
                raise ValueError(f"replace target does not exist: {operation.path}")
            parent[leaf] = deepcopy(operation.value)
        else:
            if leaf not in parent:
                raise ValueError(f"remove target does not exist: {operation.path}")
            parent.pop(leaf)
        return

    if isinstance(parent, list):
        if operation.operation == PatchOperation.ADD:
            index = _list_index(leaf, len(parent), allow_append=True)
            parent.insert(index, deepcopy(operation.value))
        else:
            index = _list_index(leaf, len(parent), allow_append=False)
            if operation.operation == PatchOperation.REPLACE:
                parent[index] = deepcopy(operation.value)
            else:
                parent.pop(index)
        return

    raise ValueError(f"patch target parent is not a container: {operation.path}")


class StateGateway:
    """提交成功时同时产生新状态、递增分域版本并追加 Domain Event。"""

    _ROOT_DOMAIN_FIELDS = {
        "task_context": "task_context",
        "execution_plan": "execution_plan",
        "document_validation": "document_validation",
        "artifact_state": "artifact_state",
        "runtime_control": "runtime_control",
        "final_result": "final_result",
    }
    _ROOT_VERSION_FIELDS = {
        "task_context": "task_context",
        "execution_plan": "execution_plan",
        "document_validation": "document_validation",
        "artifact_state": "artifact_state",
        "runtime_control": "runtime_control",
        "final_result": "final_result",
    }
    _SECTION_DOMAIN_FIELDS = {
        "section.research": "research",
        "section.writing": "writing",
        "section.claims": "claims",
        "section.verification": "verification",
        "section.execution": "execution",
    }

    def __init__(
        self,
        *,
        event_log: DomainEventLog,
        permission_validator: WritePermissionValidator | None = None,
        transition_validator: StateTransitionValidator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.event_log = event_log
        self.permission_validator = (
            permission_validator or WritePermissionValidator()
        )
        self.transition_validator = (
            transition_validator or StateTransitionValidator()
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def commit(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> StateCommitResult:
        existing = self._existing_request_event(state, request)
        self._validate_identity(state, request)
        self._validate_attempt(state, request)
        try:
            self.permission_validator.validate(request)
        except StateWritePermissionError as exc:
            self._raise(
                request,
                WorkflowErrorCategory.PERMISSION_VIOLATION,
                "STATE_WRITE_PERMISSION_DENIED",
                str(exc),
                WorkflowRetryability.NOT_RETRYABLE,
            )

        current_version = self._domain_version(state, request)
        if existing is not None and current_version == existing.domain_version:
            replayed = state.model_copy(deep=True)
            replayed.state_meta.event_sequence = max(
                replayed.state_meta.event_sequence,
                existing.sequence,
            )
            replayed.state_meta.updated_at = max(
                replayed.state_meta.updated_at,
                existing.occurred_at,
            )
            return StateCommitResult(
                state=replayed,
                event=existing,
                committed=False,
            )
        if existing is not None and current_version != existing.domain_version - 1:
            self._raise(
                request,
                WorkflowErrorCategory.STATE_VERSION_CONFLICT,
                "PATCH_REPLAY_VERSION_CONFLICT",
                "committed patch cannot be replayed onto current domain version",
                WorkflowRetryability.RESUME_FROM_STAGE,
            )
        if current_version != request.expected_domain_version:
            self._raise(
                request,
                WorkflowErrorCategory.STATE_VERSION_CONFLICT,
                "STATE_DOMAIN_VERSION_CONFLICT",
                "state domain version conflict: "
                f"expected={request.expected_domain_version}, "
                f"actual={current_version}",
                WorkflowRetryability.RERUN_CURRENT_NODE,
            )

        candidate = state.model_copy(deep=True)
        before_domain = self._domain_model(candidate, request)
        before_status = self._domain_status(request, before_domain)
        document = before_domain.model_dump(mode="python")
        try:
            for operation in request.operations:
                _apply_operation(document, operation)
            after_domain = before_domain.__class__.model_validate(document)
            self._validate_transition(request, before_status, after_domain)
            self._set_domain_model(candidate, request, after_domain)
            next_version = current_version + 1
            self._set_domain_version(candidate, request, next_version)
        except (ValueError, ValidationError, StateTransitionError) as exc:
            self._raise(
                request,
                WorkflowErrorCategory.SCHEMA_VIOLATION,
                "STATE_PATCH_INVALID",
                str(exc),
                WorkflowRetryability.NOT_RETRYABLE,
            )

        if existing is None:
            event = DomainEventSchema(
                event_id=self._event_id(state, request, next_version),
                event_type="state_patch_committed",
                sequence=state.state_meta.event_sequence + 1,
                task_id=state.state_meta.task_id,
                run_id=state.state_meta.run_id,
                section_id=request.section_id,
                actor_role=request.actor.agent_role,
                execution_id=request.actor.execution_id,
                attempt_id=request.actor.attempt_id,
                input_snapshot_version=request.input_snapshot_version,
                target_domain=request.target_domain,
                domain_version=next_version,
                patch_request_id=request.request_id,
                payload={"request": request.model_dump(mode="json")},
                occurred_at=self.clock(),
            )
            try:
                committed = self.event_log.append(event)
            except (OSError, ValueError) as exc:
                self._raise(
                    request,
                    WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE,
                    "DOMAIN_EVENT_COMMIT_FAILED",
                    str(exc),
                    WorkflowRetryability.RETRY_SAME_OPERATION,
                )
        else:
            event = existing
            committed = False

        candidate.state_meta.event_sequence = max(
            candidate.state_meta.event_sequence,
            event.sequence,
        )
        candidate.state_meta.updated_at = max(
            candidate.state_meta.updated_at,
            event.occurred_at,
        )
        return StateCommitResult(state=candidate, event=event, committed=committed)

    def _existing_request_event(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> DomainEventSchema | None:
        events = self.event_log.list_events(
            state.state_meta.task_id,
            state.state_meta.run_id,
        )
        matches = [
            event for event in events if event.patch_request_id == request.request_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("patch request has multiple committed events")
        event = matches[0]
        if event.payload.get("request") != request.model_dump(mode="json"):
            self._raise(
                request,
                WorkflowErrorCategory.STATE_VERSION_CONFLICT,
                "PATCH_REQUEST_ID_COLLISION",
                "patch request id was reused with different content",
                WorkflowRetryability.NOT_RETRYABLE,
            )
        return event

    def _validate_identity(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> None:
        if request.task_id != state.state_meta.task_id:
            self._raise(
                request,
                WorkflowErrorCategory.PERMISSION_VIOLATION,
                "STATE_TASK_ID_MISMATCH",
                "patch task_id does not match Shared State",
                WorkflowRetryability.NOT_RETRYABLE,
            )
        if request.input_snapshot_version != state.state_meta.input_snapshot_version:
            self._raise(
                request,
                WorkflowErrorCategory.STALE_EXECUTION,
                "STALE_INPUT_SNAPSHOT",
                "patch input snapshot is stale",
                WorkflowRetryability.RESUME_FROM_STAGE,
            )

    def _validate_attempt(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> None:
        if not request.target_domain.startswith("section."):
            return
        assert request.section_id is not None
        section = state.section_states.get(request.section_id)
        if section is None:
            self._raise(
                request,
                WorkflowErrorCategory.SCHEMA_VIOLATION,
                "SECTION_STATE_NOT_FOUND",
                f"section state not found: {request.section_id}",
                WorkflowRetryability.NOT_RETRYABLE,
            )
        if request.actor.agent_role == StateActorRole.ROUTER:
            return
        execution = section.execution
        if (
            execution.active_attempt_id
            and execution.active_attempt_id != request.actor.attempt_id
        ) or (
            execution.execution_id
            and execution.execution_id != request.actor.execution_id
        ):
            self._raise(
                request,
                WorkflowErrorCategory.STALE_EXECUTION,
                "STALE_SECTION_EXECUTION",
                "patch execution or attempt is no longer active",
                WorkflowRetryability.NOT_RETRYABLE,
            )

    def _domain_model(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> Any:
        if request.target_domain in self._ROOT_DOMAIN_FIELDS:
            return getattr(state, self._ROOT_DOMAIN_FIELDS[request.target_domain])
        field = self._SECTION_DOMAIN_FIELDS.get(request.target_domain)
        if field is None or request.section_id is None:
            raise ValueError(f"unsupported state domain: {request.target_domain}")
        return getattr(state.section_states[request.section_id], field)

    def _set_domain_model(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
        value: Any,
    ) -> None:
        if request.target_domain in self._ROOT_DOMAIN_FIELDS:
            setattr(state, self._ROOT_DOMAIN_FIELDS[request.target_domain], value)
            return
        assert request.section_id is not None
        setattr(
            state.section_states[request.section_id],
            self._SECTION_DOMAIN_FIELDS[request.target_domain],
            value,
        )

    def _domain_version(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
    ) -> int:
        field = self._ROOT_VERSION_FIELDS.get(request.target_domain)
        if field is not None:
            return int(getattr(state.domain_versions, field))
        assert request.section_id is not None
        section_versions = state.domain_versions.sections.get(
            request.section_id,
            SectionDomainVersionsSchema(),
        )
        return int(
            getattr(
                section_versions,
                self._SECTION_DOMAIN_FIELDS[request.target_domain],
            )
        )

    def _set_domain_version(
        self,
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
        value: int,
    ) -> None:
        field = self._ROOT_VERSION_FIELDS.get(request.target_domain)
        if field is not None:
            setattr(state.domain_versions, field, value)
            return
        assert request.section_id is not None
        section_versions = state.domain_versions.sections.setdefault(
            request.section_id,
            SectionDomainVersionsSchema(),
        )
        setattr(
            section_versions,
            self._SECTION_DOMAIN_FIELDS[request.target_domain],
            value,
        )

    def _validate_transition(
        self,
        request: StatePatchRequestSchema,
        before_status: Any,
        after_domain: Any,
    ) -> None:
        after_status = self._domain_status(request, after_domain)
        if before_status is None or after_status is None:
            return
        if request.target_domain == "section.execution":
            self.transition_validator.validate_section(before_status, after_status)
        elif request.target_domain == "task_context":
            self.transition_validator.validate_task(before_status, after_status)

    @staticmethod
    def _domain_status(
        request: StatePatchRequestSchema,
        domain: Any,
    ) -> Any:
        if request.target_domain == "task_context":
            return getattr(domain, "task_status", None)
        return getattr(domain, "status", None)

    @staticmethod
    def _event_id(
        state: MultiAgentSharedStateSchema,
        request: StatePatchRequestSchema,
        domain_version: int,
    ) -> str:
        return (
            f"{state.state_meta.task_id}:{state.state_meta.run_id}:"
            f"{request.request_id}:v{domain_version}"
        )

    @staticmethod
    def _raise(
        request: StatePatchRequestSchema,
        category: WorkflowErrorCategory,
        error_code: str,
        message: str,
        retryability: WorkflowRetryability,
    ) -> None:
        raise StateGatewayError(
            WorkflowErrorSchema(
                error_id=f"state-error:{request.request_id}:{error_code}",
                source=WorkflowErrorSource.STATE_GATEWAY,
                category=category,
                scope=(
                    WorkflowErrorScope.SECTION
                    if request.section_id
                    else WorkflowErrorScope.TASK
                ),
                retryability=retryability,
                affected_section_ids=(
                    [request.section_id] if request.section_id else []
                ),
                error_code=error_code,
                message=message,
            )
        )
