"""四个业务 Agent 的统一、跨调用无状态执行外壳。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Protocol, Type

from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentExecutionResultSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    PatchOperation,
    StateActorRole,
    StatePatchActorSchema,
    StatePatchOperationSchema,
    StatePatchRequestSchema,
    WorkflowErrorCategory,
    WorkflowErrorSchema,
    WorkflowErrorScope,
    WorkflowErrorSource,
    WorkflowRetryability,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.services.agent_rag_v1 import (
    AgentToolSession,
    ToolGateway,
)


class AgentReasoner(Protocol):
    """可由 ModelGateway、规则实现或测试 fake 提供的单次推理端口。"""

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: AgentToolSession,
    ) -> Mapping[str, Any] | ContractSchema:
        ...


class AgentReasonerFailure(RuntimeError):
    def __init__(self, workflow_error: WorkflowErrorSchema) -> None:
        self.workflow_error = workflow_error
        super().__init__(workflow_error.message)


class StatelessBusinessAgent(ABC):
    """实例只保存进程级依赖，不保存任何 Task、Section 或重试状态。"""

    role: AgentRole
    payload_type: AgentPayloadType
    input_schema: Type[ContractSchema]
    payload_schema: Type[ContractSchema]

    def __init__(
        self,
        *,
        reasoner: AgentReasoner,
        tool_gateway: ToolGateway,
    ) -> None:
        self.reasoner = reasoner
        self.tool_gateway = tool_gateway

    def run(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
    ) -> AgentExecutionResultSchema:
        """Run the Standalone-compatible contract including patch proposals."""

        return self._run_internal(
            context,
            invocation_input,
            include_patch_proposals=True,
        )

    def run_projected(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
    ) -> AgentExecutionResultSchema:
        """Run for a production node whose fixed projector owns state writes.

        The reasoner and typed payload validation are identical to ``run``. The
        result deliberately carries no ``StatePatchRequest`` so an Agent cannot
        acquire direct authority over the production GraphState.
        """

        return self._run_internal(
            context,
            invocation_input,
            include_patch_proposals=False,
        )

    def _run_internal(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        *,
        include_patch_proposals: bool,
    ) -> AgentExecutionResultSchema:
        tools = self.tool_gateway.session(context)
        try:
            invocation_input = self.input_schema.model_validate(invocation_input)
            self._validate_context(context, invocation_input)
            raw_payload = self.reasoner.invoke(
                context=context,
                invocation_input=invocation_input,
                tools=tools,
            )
            payload = self.payload_schema.model_validate(raw_payload)
            self._validate_payload(context, invocation_input, payload)
            patches = (
                self._build_patches(context, invocation_input, payload)
                if include_patch_proposals
                else []
            )
            if include_patch_proposals:
                self._validate_patch_paths(context, patches)
            status = self._result_status(payload)
            return AgentExecutionResultSchema(
                invocation_id=context.invocation_id,
                agent_role=self.role,
                execution_mode=context.execution_mode,
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                status=status,
                payload_type=self.payload_type,
                payload=payload,
                proposed_state_patches=patches,
                emitted_signals=self._emitted_signals(payload),
                artifact_refs=self._artifact_refs(payload),
                tool_call_refs=list(tools.call_refs),
                budget_usage={},
                trace_ref=self._trace_ref(context),
            )
        except AgentReasonerFailure as exc:
            workflow_error = exc.workflow_error
        except (ValidationError, ValueError) as exc:
            workflow_error = self._error(
                context,
                category=WorkflowErrorCategory.AGENT_OUTPUT_INVALID,
                retryability=WorkflowRetryability.NOT_RETRYABLE,
                error_code="AGENT_OUTPUT_INVALID",
                message=str(exc),
            )
        except Exception as exc:
            workflow_error = self._error(
                context,
                category=WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE,
                retryability=WorkflowRetryability.RETRY_SAME_OPERATION,
                error_code="AGENT_INVOCATION_FAILED",
                message=f"{type(exc).__name__}: {exc}",
            )
        return AgentExecutionResultSchema(
            invocation_id=context.invocation_id,
            agent_role=self.role,
            execution_mode=context.execution_mode,
            execution_id=context.execution_id,
            attempt_id=context.attempt_id,
            input_snapshot_version=context.input_snapshot_version,
            status=AgentInvocationStatus.FAILED,
            payload_type=self.payload_type,
            payload=None,
            tool_call_refs=list(tools.call_refs),
            error=workflow_error,
            trace_ref=self._trace_ref(context),
        )

    def _validate_context(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
    ) -> None:
        if context.agent_role != self.role:
            raise ValueError(
                f"context role {context.agent_role.value} cannot invoke "
                f"{self.role.value} agent"
            )
        input_section_id = getattr(invocation_input, "section_id", None)
        if input_section_id and input_section_id != context.section_id:
            raise ValueError("invocation input section does not match context")
        allowed_refs = {
            context.task_context_ref,
            context.source_snapshot_ref,
            context.execution_plan_ref,
            context.section_state_ref,
            *context.authorized_artifact_refs,
        } - {None}
        unauthorized = self._input_artifact_refs(invocation_input) - allowed_refs
        if unauthorized:
            raise ValueError(
                "invocation input contains unauthorized artifact refs: "
                + ", ".join(sorted(unauthorized))
            )

    def _validate_payload(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        payload: ContractSchema,
    ) -> None:
        del context, invocation_input, payload

    @abstractmethod
    def _build_patches(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        payload: ContractSchema,
    ) -> list[StatePatchRequestSchema]:
        ...

    @abstractmethod
    def _result_status(
        self,
        payload: ContractSchema,
    ) -> AgentInvocationStatus:
        ...

    @abstractmethod
    def _emitted_signals(self, payload: ContractSchema) -> list[str]:
        ...

    def _patch(
        self,
        context: AgentExecutionContextSchema,
        *,
        target_domain: str,
        section_id: str | None,
        values: Mapping[str, Any],
        reason_code: str,
    ) -> StatePatchRequestSchema:
        if target_domain not in context.expected_domain_versions:
            raise ValueError(
                f"missing expected domain version: {target_domain}"
            )
        return StatePatchRequestSchema(
            request_id=f"{context.invocation_id}:{target_domain}:patch",
            task_id=context.task_id,
            section_id=section_id,
            actor=StatePatchActorSchema(
                agent_role=StateActorRole(self.role.value),
                execution_id=context.execution_id,
                attempt_id=context.attempt_id,
            ),
            input_snapshot_version=context.input_snapshot_version,
            expected_domain_version=context.expected_domain_versions[
                target_domain
            ],
            target_domain=target_domain,
            operations=[
                StatePatchOperationSchema(
                    operation=PatchOperation.REPLACE,
                    path=f"/{field}",
                    value=value,
                )
                for field, value in values.items()
            ],
            reason_code=reason_code,
        )

    @staticmethod
    def _validate_patch_paths(
        context: AgentExecutionContextSchema,
        patches: list[StatePatchRequestSchema],
    ) -> None:
        allowed = set(context.writable_state_paths)
        for patch in patches:
            domain_prefix = "/" + patch.target_domain.replace(".", "/")
            for operation in patch.operations:
                if (
                    operation.path not in allowed
                    and domain_prefix + operation.path not in allowed
                ):
                    raise ValueError(
                        "agent proposed unauthorized writable path: "
                        f"{patch.target_domain}{operation.path}"
                    )

    @staticmethod
    def _input_artifact_refs(value: ContractSchema) -> set[str]:
        refs: set[str] = set()

        def visit(item: Any, key: str = "") -> None:
            if isinstance(item, dict):
                for child_key, child in item.items():
                    visit(child, str(child_key))
            elif isinstance(item, list):
                for child in item:
                    visit(child, key)
            elif (
                isinstance(item, str)
                and item
                and (key.endswith("_ref") or key.endswith("_refs"))
            ):
                refs.add(item)

        visit(value.model_dump(mode="python"))
        return refs

    @staticmethod
    def _artifact_refs(payload: ContractSchema) -> list[str]:
        refs: set[str] = set()

        def visit(item: Any, key: str = "") -> None:
            if isinstance(item, dict):
                for child_key, child in item.items():
                    visit(child, str(child_key))
            elif isinstance(item, list):
                for child in item:
                    visit(child, key)
            elif (
                isinstance(item, str)
                and item.startswith("artifact://")
                and (key.endswith("_ref") or key.endswith("_refs"))
            ):
                refs.add(item)

        visit(payload.model_dump(mode="python"))
        return sorted(refs)

    @staticmethod
    def _trace_ref(context: AgentExecutionContextSchema) -> str:
        trace_ref = context.trace_context.get("trace_ref")
        return str(trace_ref or f"trace://{context.invocation_id}")

    @staticmethod
    def _error(
        context: AgentExecutionContextSchema,
        *,
        category: WorkflowErrorCategory,
        retryability: WorkflowRetryability,
        error_code: str,
        message: str,
    ) -> WorkflowErrorSchema:
        return WorkflowErrorSchema(
            error_id=f"agent-error:{context.invocation_id}:{error_code}",
            source=WorkflowErrorSource.AGENT,
            category=category,
            scope=(
                WorkflowErrorScope.SECTION
                if context.section_id
                else WorkflowErrorScope.TASK
            ),
            retryability=retryability,
            affected_section_ids=(
                [context.section_id] if context.section_id else []
            ),
            error_code=error_code,
            message=message,
        )
