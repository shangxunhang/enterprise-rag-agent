"""Agent 工具权限、幂等、重试、原始产物与结果标准化边界。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping

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

from .artifact_store import FileArtifactStore
from .tool_store import ToolExecutionStore


ToolHandler = Callable[
    [ToolInvocationSchema],
    ToolHandlerOutputSchema | Mapping[str, Any],
]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    version: str
    handler: ToolHandler
    max_retries: int = 2


class ToolGateway:
    """所有 Agent 只能通过该网关执行已注册且双重授权的工具。"""

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
    ) -> None:
        self.execution_store = execution_store
        self.artifact_store = artifact_store
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        handler: ToolHandler,
        max_retries: int = 2,
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
    ) -> NormalizedToolResultSchema:
        fingerprint = self._fingerprint(
            tool_name=tool_name,
            arguments=arguments,
            input_snapshot_version=context.input_snapshot_version,
        )
        try:
            existing = self.execution_store.get(call_id)
        except KeyError:
            existing = None
        if existing is not None:
            provenance = existing.provenance
            if (
                provenance.tool_name != tool_name
                or provenance.input_fingerprint != fingerprint
                or provenance.input_snapshot_version
                != context.input_snapshot_version
            ):
                raise ValueError(
                    "tool call id reused with different invocation"
                )
            return existing

        registration = self._tools.get(tool_name)
        if registration is None:
            return self._persist_error(
                context,
                call_id=call_id,
                tool_name=tool_name,
                tool_version=None,
                fingerprint=fingerprint,
                status=ToolResultStatus.PERMANENT_ERROR,
                error_code="TOOL_NOT_REGISTERED",
            )
        if (
            tool_name not in self._ROLE_TOOLS[context.agent_role]
            or tool_name not in context.authorized_tool_names
        ):
            return self._persist_error(
                context,
                call_id=call_id,
                tool_name=tool_name,
                tool_version=registration.version,
                fingerprint=fingerprint,
                status=ToolResultStatus.REJECTED,
                error_code="TOOL_PERMISSION_DENIED",
            )

        invocation = ToolInvocationSchema(
            call_id=call_id,
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
        output: ToolHandlerOutputSchema | None = None
        last_error: Exception | None = None
        for _attempt in range(registration.max_retries + 1):
            try:
                raw_output = registration.handler(invocation)
                output = ToolHandlerOutputSchema.model_validate(raw_output)
                break
            except Exception as exc:
                last_error = exc
        if output is None:
            result = NormalizedToolResultSchema(
                call_id=call_id,
                status=ToolResultStatus.RETRYABLE_ERROR,
                data_type="tool_error",
                provenance=ToolProvenanceSchema(
                    input_snapshot_version=context.input_snapshot_version,
                    tool_name=tool_name,
                    tool_version=registration.version,
                    input_fingerprint=fingerprint,
                ),
                quality=ToolQualitySchema(
                    warnings=[
                        f"tool failed after {registration.max_retries + 1} attempts"
                    ]
                ),
                error=ToolResultErrorSchema(
                    error_code=(
                        type(last_error).__name__
                        if last_error is not None
                        else "TOOL_EXECUTION_FAILED"
                    )
                ),
            )
            self.execution_store.put(result)
            return result

        raw_artifact_ref = None
        if output.raw_result is not None:
            if self.artifact_store is None:
                return self._persist_error(
                    context,
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_version=registration.version,
                    fingerprint=fingerprint,
                    status=ToolResultStatus.PERMANENT_ERROR,
                    error_code="RAW_ARTIFACT_STORE_NOT_CONFIGURED",
                )
            raw_reference = self.artifact_store.put_json(
                output.raw_result,
                artifact_type="tool_raw_result",
                schema_version=f"{tool_name}_raw_{registration.version}",
                created_by="tool_gateway",
                task_id=context.task_id,
                section_id=context.section_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                artifact_ref=f"artifact://{context.task_id}/tool/{call_id}/raw",
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
            error=ToolResultErrorSchema(error_code=error_code),
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
        tool_name: str,
        tool_version: str | None,
        fingerprint: str,
        status: ToolResultStatus,
        error_code: str,
    ) -> NormalizedToolResultSchema:
        result = NormalizedToolResultSchema(
            call_id=call_id,
            status=status,
            data_type="tool_error",
            provenance=ToolProvenanceSchema(
                input_snapshot_version=context.input_snapshot_version,
                tool_name=tool_name,
                tool_version=tool_version,
                input_fingerprint=fingerprint,
            ),
            error=ToolResultErrorSchema(error_code=error_code),
        )
        self.execution_store.put(result)
        return result

    @staticmethod
    def _fingerprint(
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        input_snapshot_version: str,
    ) -> str:
        serialized = json.dumps(
            {
                "tool_name": tool_name,
                "arguments": dict(arguments),
                "input_snapshot_version": input_snapshot_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AgentToolSession:
    """仅在一次 Agent invocation 内存在的临时工具调用游标。"""

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
        )
        self.call_refs.append(result.call_id)
        return result
