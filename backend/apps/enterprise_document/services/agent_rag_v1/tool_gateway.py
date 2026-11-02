"""Agent Tool runtime boundary for authorization, Budget, retry and normalization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping
from uuid import uuid4

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    NormalizedToolResultSchema,
    ToolHandlerOutputSchema,
    ToolInvocationSchema,
    ToolProvenanceSchema,
    ToolQualitySchema,
    ToolResultErrorSchema,
    ToolResultStatus,
)
from core.retry_policy import classify_failure
from schemas.failure import FailureCategory, FailureOrigin

from .artifact_store import FileArtifactStore
from .model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from .tool_store import ToolExecutionStore


ToolHandler = Callable[
    [ToolInvocationSchema],
    ToolHandlerOutputSchema | Mapping[str, Any],
]
ToolCurrentnessValidator = Callable[[AgentExecutionContextSchema], None]


_RETRYABLE_TOOL_FAILURES = {
    FailureCategory.AVAILABILITY,
    FailureCategory.TRANSIENT,
    FailureCategory.TIMEOUT,
    FailureCategory.RESOURCE_EXHAUSTED,
}


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    version: str
    handler: ToolHandler
    # Target default = one physical attempt. Explicit opt-in may raise this.
    max_retries: int = 0


ToolAuthorizationValidator = Callable[
    [AgentExecutionContextSchema, RegisteredTool, Mapping[str, Any]],
    None,
]


class ToolGateway:
    """Single Agent-facing Tool runtime boundary.

    ToolGateway owns operational execution only. It never promotes normalized Tool
    output into Fact/Evidence/Artifact/Claim truth and never creates SIF-11 retry
    successors.
    """

    _ROLE_TOOLS = {
        AgentRole.PLANNER: {
            "template.read",
            "input.summary",
            "source.inventory",
            "binding.query",
            "plan.validate",
        },
        AgentRole.RESEARCH: {
            "internal.search",
            "material.read",
            "excel.fact.query",
            "evidence.filter",
            "knowledge.retrieve",
        },
        AgentRole.WRITER: {
            "excel.fact.query",
            "evidence.package.read",
            "terminology.read",
        },
        AgentRole.VERIFIER: {
            "excel.fact.validate",
            "claim.binding.query",
            "evidence.validate",
            "cross_section.query",
        },
    }

    def __init__(
        self,
        *,
        execution_store: ToolExecutionStore,
        artifact_store: FileArtifactStore | None = None,
        budget_runtime: CanonicalInvocationBudgetAdapter | None = None,
        authorization_validator: ToolAuthorizationValidator | None = None,
        currentness_validator: ToolCurrentnessValidator | None = None,
    ) -> None:
        self.execution_store = execution_store
        self.artifact_store = artifact_store
        self.budget_runtime = budget_runtime
        self.authorization_validator = authorization_validator
        self.currentness_validator = currentness_validator
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        handler: ToolHandler,
        max_retries: int = 0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("tool max_retries cannot be negative")
        registration = RegisteredTool(
            name=name,
            version=version,
            handler=handler,
            max_retries=max_retries,
        )
        existing = self._tools.get(name)
        if existing is not None and existing != registration:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = registration

    def invoke(
        self,
        context: AgentExecutionContextSchema,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_operation_id: str | None = None,
    ) -> NormalizedToolResultSchema:
        # Authorization is re-evaluated before any operational result reuse.
        registration = self._tools.get(tool_name)
        target_operation_identity = bool(str(tool_operation_id or "").strip())
        operation_id = str(tool_operation_id or call_id or "").strip()
        if not operation_id:
            raise ValueError("tool_operation_id/call_id is required")

        if registration is None:
            fingerprint = self._fingerprint(
                tool_name=tool_name,
                tool_version="unregistered",
                arguments=arguments,
                input_snapshot_version=context.input_snapshot_version,
            )
            return self._persist_error(
                context,
                call_id=call_id,
                tool_operation_id=operation_id,
                tool_name=tool_name,
                tool_version=None,
                fingerprint=fingerprint,
                status=ToolResultStatus.PERMANENT_ERROR,
                error_code="TOOL_NOT_REGISTERED",
                failure_category=FailureCategory.CONFIGURATION,
                failure_origin=FailureOrigin.TOOL_RUNTIME,
                persist=False,
            )

        fingerprint = self._fingerprint(
            tool_name=tool_name,
            tool_version=registration.version,
            arguments=arguments,
            input_snapshot_version=context.input_snapshot_version,
        )
        if (
            tool_name not in self._ROLE_TOOLS[context.agent_role]
            or tool_name not in context.authorized_tool_names
        ):
            return self._persist_error(
                context,
                call_id=call_id,
                tool_operation_id=operation_id,
                tool_name=tool_name,
                tool_version=registration.version,
                fingerprint=fingerprint,
                status=ToolResultStatus.REJECTED,
                error_code="TOOL_PERMISSION_DENIED",
                failure_category=FailureCategory.VALIDATION,
                failure_origin=FailureOrigin.TOOL_RUNTIME,
                persist=False,
            )

        # authorized_tool_names remains a migration projection only. Target
        # tool_operation_id execution/replay also requires an invocation-scoped
        # authorization validator supplied by the owning composition.
        if target_operation_identity and self.authorization_validator is None:
            return self._persist_error(
                context,
                call_id=call_id,
                tool_operation_id=operation_id,
                tool_name=tool_name,
                tool_version=registration.version,
                fingerprint=fingerprint,
                status=ToolResultStatus.REJECTED,
                error_code="TOOL_CAPABILITY_GRANT_REQUIRED",
                failure_category=FailureCategory.VALIDATION,
                failure_origin=FailureOrigin.TOOL_RUNTIME,
                persist=False,
            )
        if self.authorization_validator is not None:
            try:
                self.authorization_validator(context, registration, arguments)
            except Exception:
                return self._persist_error(
                    context,
                    call_id=call_id,
                    tool_operation_id=operation_id,
                    tool_name=tool_name,
                    tool_version=registration.version,
                    fingerprint=fingerprint,
                    status=ToolResultStatus.REJECTED,
                    error_code="TOOL_CAPABILITY_GRANT_REJECTED",
                    failure_category=FailureCategory.VALIDATION,
                    failure_origin=FailureOrigin.TOOL_RUNTIME,
                    persist=False,
                )

        # Target tool_operation_id replay/execution must also be fenced by a
        # current Run/Attempt validator supplied by lifecycle composition.
        # Legacy call_id-only callers retain compatibility until Step 4
        # integration wires the canonical validators.
        if target_operation_identity and self.currentness_validator is None:
            return self._persist_error(
                context,
                call_id=call_id,
                tool_operation_id=operation_id,
                tool_name=tool_name,
                tool_version=registration.version,
                fingerprint=fingerprint,
                status=ToolResultStatus.STALE,
                error_code="TOOL_CURRENTNESS_GUARD_REQUIRED",
                failure_category=FailureCategory.VALIDATION,
                failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
                persist=False,
            )
        if self.currentness_validator is not None:
            try:
                self.currentness_validator(context)
            except Exception:
                return self._persist_error(
                    context,
                    call_id=call_id,
                    tool_operation_id=operation_id,
                    tool_name=tool_name,
                    tool_version=registration.version,
                    fingerprint=fingerprint,
                    status=ToolResultStatus.STALE,
                    error_code="TOOL_CURRENTNESS_REJECTED",
                    failure_category=FailureCategory.VALIDATION,
                    failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
                    persist=False,
                )

        try:
            existing = self.execution_store.get(operation_id)
        except KeyError:
            existing = None
        if existing is not None:
            if (
                existing.tool_operation_id not in {None, operation_id}
                or existing.tool_semantic_fingerprint not in {None, fingerprint}
                or existing.provenance.tool_name != tool_name
                or existing.provenance.tool_version != registration.version
                or existing.provenance.input_fingerprint != fingerprint
                or existing.provenance.input_snapshot_version
                != context.input_snapshot_version
            ):
                raise ValueError(
                    "tool operation identity reused with different semantic invocation"
                )
            return existing

        budget_reservation = None
        if self.budget_runtime is not None:
            budget_reservation = self.budget_runtime.reserve_tool_logical_call(
                task_id=context.task_id,
                run_id=context.run_id,
                tool_operation_id=operation_id,
                scope_ref=str(context.section_id or f"run:{context.run_id}"),
                parent_logical_operation_id=context.logical_operation_id,
                attempt_id=context.attempt_id,
            )

        physical_attempt_refs: list[str] = []
        output: ToolHandlerOutputSchema | None = None
        last_error: Exception | None = None
        last_category = FailureCategory.UNKNOWN
        logical_started = False

        for attempt_index in range(registration.max_retries + 1):
            tool_attempt_id = (
                f"tool_attempt:{operation_id}:{attempt_index + 1}:{uuid4().hex[:12]}"
            )
            physical_attempt_refs.append(tool_attempt_id)
            invocation = ToolInvocationSchema(
                call_id=call_id,
                tool_operation_id=operation_id,
                parent_logical_operation_id=context.logical_operation_id,
                tool_attempt_id=tool_attempt_id,
                physical_attempt_ordinal=attempt_index + 1,
                tool_semantic_fingerprint=fingerprint,
                expected_tool_binding_version=registration.version,
                agent_role=context.agent_role,
                tool_name=tool_name,
                arguments=dict(arguments),
                task_id=context.task_id,
                run_id=context.run_id,
                section_id=context.section_id,
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
            )
            if (
                not logical_started
                and self.budget_runtime is not None
                and budget_reservation is not None
            ):
                self.budget_runtime.settle_tool_logical_call(
                    budget_reservation.reservation_ref.reservation_id
                )
            logical_started = True
            try:
                raw_output = registration.handler(invocation)
                output = ToolHandlerOutputSchema.model_validate(raw_output)
                break
            except Exception as exc:
                last_error = exc
                last_category = classify_failure(exc)
                may_retry = (
                    attempt_index < registration.max_retries
                    and last_category in _RETRYABLE_TOOL_FAILURES
                )
                if not may_retry:
                    break

        budget_settlement = None
        if self.budget_runtime is not None and budget_reservation is not None:
            terminal = self.budget_runtime.budget_manager.resolve_reservation(
                budget_reservation.reservation_ref.reservation_id
            )
            if terminal.status == "consumed":
                # Rebuild the typed settlement through idempotent consume.
                budget_settlement = self.budget_runtime.settle_tool_logical_call(
                    budget_reservation.reservation_ref.reservation_id
                )

        if output is None:
            retryable_hint = last_category in _RETRYABLE_TOOL_FAILURES
            result = NormalizedToolResultSchema(
                call_id=call_id,
                tool_operation_id=operation_id,
                tool_semantic_fingerprint=fingerprint,
                physical_attempt_refs=physical_attempt_refs,
                budget_reservation_ref=(
                    budget_reservation.reservation_ref
                    if budget_reservation is not None
                    else None
                ),
                budget_settlement=budget_settlement,
                status=(
                    ToolResultStatus.RETRYABLE_ERROR
                    if retryable_hint
                    else ToolResultStatus.PERMANENT_ERROR
                ),
                data_type="tool_error",
                provenance=ToolProvenanceSchema(
                    input_snapshot_version=context.input_snapshot_version,
                    tool_name=tool_name,
                    tool_version=registration.version,
                    input_fingerprint=fingerprint,
                ),
                quality=ToolQualitySchema(
                    warnings=[
                        f"tool failed after {len(physical_attempt_refs)} physical attempts"
                    ]
                ),
                error=ToolResultErrorSchema(
                    error_code=(
                        type(last_error).__name__
                        if last_error is not None
                        else "TOOL_EXECUTION_FAILED"
                    ),
                    failure_category=last_category,
                    failure_origin=FailureOrigin.TOOL_RUNTIME,
                    retryable_hint=retryable_hint,
                    source_component="ToolGateway",
                ),
            )
            self.execution_store.put(result)
            return result

        raw_artifact_ref = None
        if output.raw_result is not None:
            if self.artifact_store is None:
                result = NormalizedToolResultSchema(
                    call_id=call_id,
                    tool_operation_id=operation_id,
                    tool_semantic_fingerprint=fingerprint,
                    physical_attempt_refs=physical_attempt_refs,
                    budget_reservation_ref=(
                        budget_reservation.reservation_ref
                        if budget_reservation is not None
                        else None
                    ),
                    budget_settlement=budget_settlement,
                    status=ToolResultStatus.PERMANENT_ERROR,
                    data_type="tool_error",
                    provenance=ToolProvenanceSchema(
                        input_snapshot_version=context.input_snapshot_version,
                        tool_name=tool_name,
                        tool_version=registration.version,
                        input_fingerprint=fingerprint,
                    ),
                    error=ToolResultErrorSchema(
                        error_code="RAW_ARTIFACT_STORE_NOT_CONFIGURED",
                        failure_category=FailureCategory.CONFIGURATION,
                        failure_origin=FailureOrigin.LOCAL_STORAGE,
                        retryable_hint=False,
                        source_component="ToolGateway",
                    ),
                )
                self.execution_store.put(result)
                return result
            raw_reference = self.artifact_store.put_json(
                output.raw_result,
                artifact_type="tool_raw_result",
                schema_version=f"{tool_name}_raw_{registration.version}",
                created_by="tool_gateway",
                task_id=context.task_id,
                section_id=context.section_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                artifact_ref=f"artifact://{context.task_id}/tool/{operation_id}/raw",
            )
            raw_artifact_ref = raw_reference.artifact_ref

        status = output.status
        if status == ToolResultStatus.SUCCESS and not output.data:
            status = ToolResultStatus.EMPTY
        error_code = output.error_code
        if status in {
            ToolResultStatus.RETRYABLE_ERROR,
            ToolResultStatus.PERMANENT_ERROR,
            ToolResultStatus.REJECTED,
            ToolResultStatus.STALE,
        } and not error_code:
            error_code = "TOOL_HANDLER_ERROR"

        result = NormalizedToolResultSchema(
            call_id=call_id,
            tool_operation_id=operation_id,
            tool_semantic_fingerprint=fingerprint,
            physical_attempt_refs=physical_attempt_refs,
            budget_reservation_ref=(
                budget_reservation.reservation_ref
                if budget_reservation is not None
                else None
            ),
            budget_settlement=budget_settlement,
            status=status,
            data_type=output.data_type,
            data=output.data,
            provenance=ToolProvenanceSchema(
                source_refs=output.source_refs,
                raw_artifact_ref=raw_artifact_ref,
                input_snapshot_version=context.input_snapshot_version,
                tool_name=tool_name,
                tool_version=registration.version,
                input_fingerprint=fingerprint,
            ),
            quality=ToolQualitySchema(warnings=output.warnings),
            error=ToolResultErrorSchema(
                error_code=error_code,
                failure_category=(
                    FailureCategory.TRANSIENT
                    if status == ToolResultStatus.RETRYABLE_ERROR
                    else FailureCategory.UNKNOWN
                ),
                failure_origin=(
                    FailureOrigin.TOOL_RUNTIME
                    if error_code is not None
                    else FailureOrigin.UNKNOWN
                ),
                retryable_hint=status == ToolResultStatus.RETRYABLE_ERROR,
                source_component=("ToolGateway" if error_code is not None else None),
            ),
        )
        self.execution_store.put(result)
        return result

    def session(
        self,
        context: AgentExecutionContextSchema,
    ) -> "AgentToolSession":
        return AgentToolSession(gateway=self, context=context)

    def _persist_error(
        self,
        context: AgentExecutionContextSchema,
        *,
        call_id: str,
        tool_operation_id: str,
        tool_name: str,
        tool_version: str | None,
        fingerprint: str,
        status: ToolResultStatus,
        error_code: str,
        failure_category: FailureCategory,
        failure_origin: FailureOrigin,
        persist: bool = True,
    ) -> NormalizedToolResultSchema:
        result = NormalizedToolResultSchema(
            call_id=call_id,
            tool_operation_id=tool_operation_id,
            tool_semantic_fingerprint=fingerprint,
            status=status,
            data_type="tool_error",
            provenance=ToolProvenanceSchema(
                input_snapshot_version=context.input_snapshot_version,
                tool_name=tool_name,
                tool_version=tool_version,
                input_fingerprint=fingerprint,
            ),
            error=ToolResultErrorSchema(
                error_code=error_code,
                failure_category=failure_category,
                failure_origin=failure_origin,
                retryable_hint=False,
                source_component="ToolGateway",
            ),
        )
        if persist:
            self.execution_store.put(result)
        return result

    @staticmethod
    def _fingerprint(
        *,
        tool_name: str,
        tool_version: str,
        arguments: Mapping[str, Any],
        input_snapshot_version: str,
    ) -> str:
        serialized = json.dumps(
            {
                "tool_name": tool_name,
                "tool_binding_version": tool_version,
                "arguments": dict(arguments),
                "input_snapshot_version": input_snapshot_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AgentToolSession:
    """Invocation-local convenience cursor; never a Tool semantic identity owner."""

    def __init__(
        self,
        *,
        gateway: ToolGateway,
        context: AgentExecutionContextSchema,
    ) -> None:
        self.gateway = gateway
        self.context = context
        self._call_count = 0
        self.call_refs: list[str] = []

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        tool_operation_id: str | None = None,
    ) -> NormalizedToolResultSchema:
        self._call_count += 1
        call_id = (
            f"{self.context.invocation_id}:tool:{self._call_count:03d}:"
            f"{tool_name}"
        )
        result = self.gateway.invoke(
            self.context,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            tool_operation_id=tool_operation_id,
        )
        self.call_refs.append(result.call_id)
        return result
