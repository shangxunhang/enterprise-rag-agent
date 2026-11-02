"""Supervisor orchestration boundary for task routing and workflow execution."""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable, Dict, Iterable, Optional

from agent.agent_registry import AgentRegistry
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.services.context_factory import ContextBundleFactory
from agent.services.task_lifecycle import TaskLifecycleService
from agent.services.workflow_router import WorkflowCatalog, WorkflowRouter
from agent.services.workflow_trace import WorkflowTraceService
from contracts.observability import TraceSink
from contracts.task_state import TaskStateManager
from contracts.workflow_engine import WorkflowEnginePort
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy, classify_failure
from model_gateway.model_gateway import ModelGateway
from model_gateway.usage_ledger import merge_model_usage_snapshots
from observability.trace_context import TraceSpanHandle, activate_span
from schemas.agent import AgentResultSchema
from schemas.failure import FailureCategory
from schemas.graph import WorkflowEngineResultSchema
from schemas.human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from schemas.status import ExecutionStatus, is_failure
from schemas.task import TaskSchema
from schemas.workflow_stream import WorkflowStreamEventSchema


logger = logging.getLogger(__name__)


# PREPARED_NOT_APPLIED_NOT_RUN. Narrow command/ack/claim carriers in the existing
# Supervisor module; mainline_service imports/re-exports the command class.
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetVectorSchema, CanonicalBudgetStateSchema, _strict_topup_vector,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunLifecycleStatus, RunLifecycleRecordSchema, AttemptLifecycleRecordSchema,
)
from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema


class _BudgetApplicationCarrier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetTopupCommandSchema(_BudgetApplicationCarrier):
    """Explicit trusted application command; never model output/request.extra."""
    schema_version: Literal["budget_topup_user_command_v1"] = "budget_topup_user_command_v1"
    approval_id: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    command_source: Literal["APPLICATION", "CLI", "API"]
    issuer: str = Field(min_length=1)
    authorization_kind: Literal["SINGLE_USE", "PREAUTHORIZED"] = "SINGLE_USE"
    decision: Literal["APPROVED", "DENIED"] = "APPROVED"
    wait_event_ref: str = Field(min_length=1)
    wait_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delta: CanonicalBudgetVectorSchema
    cumulative_ceiling: CanonicalBudgetVectorSchema
    authorized_delta: CanonicalBudgetVectorSchema | None = None
    expires_at: datetime | None = None

    @field_validator("delta", "cumulative_ceiling", "authorized_delta", mode="before")
    @classmethod
    def _finite_vectors(cls, value):
        return None if value is None else _strict_topup_vector(value)

    @field_validator("*")
    @classmethod
    def _nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("trusted Budget command provenance cannot be blank")
        return value

    @model_validator(mode="after")
    def _command_shape(self):
        if self.decision == "APPROVED" and self.delta.is_zero():
            raise ValueError("approved Budget command requires a positive finite delta")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("Budget command authorization expiry must be timezone-aware")
        return self


class BudgetApprovalAcknowledgementSchema(_BudgetApplicationCarrier):
    schema_version: Literal["budget_topup_approval_ack_v1"] = "budget_topup_approval_ack_v1"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["ACCEPTED", "PENDING_AUTHORIZATION", "DENIED", "REJECTED", "LIMIT_REACHED"]
    reason: str | None
    authorization_ref: str | None = Field(min_length=1)
    authorization_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    wait_event_ref: str = Field(min_length=1)
    wait_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    grant_ref: str | None
    grant_sha256: str | None
    run_status: RunLifecycleStatus
    run_revision: StrictInt = Field(ge=0)
    child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_revision: StrictInt = Field(ge=0)
    baseline_limits: CanonicalBudgetVectorSchema
    effective_limits: CanonicalBudgetVectorSchema
    used: CanonicalBudgetVectorSchema
    active_reserved: CanonicalBudgetVectorSchema
    rejected_command_shape_sha256: str | None = Field(default=None,pattern=r"^[0-9a-f]{64}$")
    rejected_command_shape_json: str | None = None
    structural_error: str | None = None

    @model_validator(mode="after")
    def _accepted_pair(self):
        structural=self.reason=="STRUCTURAL_COMMAND_REJECTED"
        if structural:
            if (self.decision!="REJECTED" or self.authorization_ref is not None or self.authorization_sha256 is not None
                    or self.rejected_command_shape_sha256!=self.command_sha256
                    or not self.rejected_command_shape_json or not self.structural_error):
                raise ValueError("structural rejection must bind exact diagnostic shape without authorization")
            from hashlib import sha256
            if sha256(self.rejected_command_shape_json.encode("utf-8")).hexdigest()!=self.command_sha256:
                raise ValueError("structural rejection diagnostic fingerprint differs")
        elif (not self.authorization_ref or not self.authorization_sha256
                or self.rejected_command_shape_sha256 is not None or self.rejected_command_shape_json is not None
                or self.structural_error is not None):
            raise ValueError("ordinary decision requires native authorization and no structural diagnostic")
        if self.decision == "ACCEPTED":
            if not self.grant_ref or self.grant_sha256 is None or len(self.grant_sha256) != 64:
                raise ValueError("accepted Budget acknowledgement requires the exact native grant")
            if self.reason is not None:
                raise ValueError("accepted Budget acknowledgement has no rejection reason")
        elif self.grant_ref is not None or self.grant_sha256 is not None or not self.reason:
            raise ValueError("nonaccepted Budget acknowledgement cannot claim a native grant")
        return self


class BudgetDocumentOccurrenceIdentitySchema(_BudgetApplicationCarrier):
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    wait_event_ref: str = Field(min_length=1)
    wait_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    grant_ref: str = Field(min_length=1)
    grant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resume_event_ref: str = Field(min_length=1)
    resume_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resume_occurrence_id: str = Field(min_length=1)
    child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuation: BudgetDocumentContinuationSchema

    @model_validator(mode="after")
    def _continuation_pair(self):
        if (self.continuation.accepted_grant_ref != self.grant_ref
                or self.continuation.accepted_grant_sha256 != self.grant_sha256
                or self.continuation.resume_event_ref != self.resume_event_ref
                or self.continuation.resume_event_sha256 != self.resume_event_sha256):
            raise ValueError("Budget occurrence continuation/native identity differs")
        return self


class BudgetDocumentOccurrenceClaimSchema(_BudgetApplicationCarrier):
    schema_version: Literal["budget_document_occurrence_claim_v1"] = "budget_document_occurrence_claim_v1"
    identity: BudgetDocumentOccurrenceIdentitySchema
    generation: StrictInt = Field(ge=0)
    predecessor_claim_ref: str | None
    predecessor_claim_sha256: str | None
    claimant_pid: StrictInt = Field(gt=0)
    claimant_creation_filetime: StrictInt = Field(gt=0)
    source_epoch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_config: dict[str, Any]
    wait_ref: str = Field(min_length=1)
    wait_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _claim_parent(self):
        if self.generation == 0:
            if self.predecessor_claim_ref is not None or self.predecessor_claim_sha256 is not None:
                raise ValueError("Budget root claim cannot have a predecessor")
        elif (not self.predecessor_claim_ref or self.predecessor_claim_sha256 is None
                or len(self.predecessor_claim_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.predecessor_claim_sha256)):
            raise ValueError("Budget adopted claim requires exact prior claim ref/hash")
        return self


class BudgetDocumentOccurrenceResultSchema(_BudgetApplicationCarrier):
    schema_version: Literal["budget_document_occurrence_result_v1"] = "budget_document_occurrence_result_v1"
    identity: BudgetDocumentOccurrenceIdentitySchema
    claim_ref: str = Field(min_length=1)
    claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_epoch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_run: RunLifecycleRecordSchema
    native_current_children: tuple[AttemptLifecycleRecordSchema, ...]
    native_child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_budget: CanonicalBudgetStateSchema
    agent_result: AgentResultSchema

    @model_validator(mode="after")
    def _result_run(self):
        if (self.agent_result.task_id, self.agent_result.run_id) != (self.identity.task_id, self.identity.run_id):
            raise ValueError("Budget occurrence result belongs to another Run")
        if ((self.native_run.identity.task_id, self.native_run.identity.run_id)
                != (self.identity.task_id, self.identity.run_id)
                or (self.native_budget.task_id, self.native_budget.run_id)
                != (self.identity.task_id, self.identity.run_id)
                or any(row.identity.run_id != self.identity.run_id for row in self.native_current_children)):
            raise ValueError("Budget result native owner snapshot belongs to another Run")
        return self


class BudgetDocumentOccurrenceAcknowledgementSchema(_BudgetApplicationCarrier):
    schema_version: Literal["budget_document_occurrence_ack_v1"] = "budget_document_occurrence_ack_v1"
    identity: BudgetDocumentOccurrenceIdentitySchema
    decision: Literal["ACCEPTED"] = "ACCEPTED"
    run_status: RunLifecycleStatus
    run_revision: StrictInt = Field(ge=0)
    child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_revision: StrictInt = Field(ge=0)
    baseline_limits: CanonicalBudgetVectorSchema
    effective_limits: CanonicalBudgetVectorSchema
    used: CanonicalBudgetVectorSchema
    active_reserved: CanonicalBudgetVectorSchema
    claim_ref: str = Field(min_length=1)
    claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_result_ref: str = Field(min_length=1)
    agent_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_result: AgentResultSchema

    @model_validator(mode="after")
    def _result_run(self):
        if (self.agent_result.task_id, self.agent_result.run_id) != (self.identity.task_id, self.identity.run_id):
            raise ValueError("Budget acknowledgement result belongs to another Run")
        return self


_BUDGET_APPLICATION_NATIVE_CARRIERS = {
    "budget_topup_approval_ack_v1": BudgetApprovalAcknowledgementSchema,
    "budget_document_occurrence_claim_v1": BudgetDocumentOccurrenceClaimSchema,
    "budget_document_occurrence_result_v1": BudgetDocumentOccurrenceResultSchema,
    "budget_document_occurrence_ack_v1": BudgetDocumentOccurrenceAcknowledgementSchema,
}


# PREPARED_NOT_APPLIED_NOT_RUN. Replace the retained fragment 02 decoder when
# mechanically building the full candidate; preserve the old TXT fragment.
def _budget_decode_native_application_carrier(raw):
    from core.persistence.crash_safe_jsonl import canonical_json_sha256
    if not isinstance(raw, dict):
        raise ValueError("Budget application native artifact is not an object")
    carrier = _BUDGET_APPLICATION_NATIVE_CARRIERS.get(raw.get("schema_version"))
    if carrier is None:
        raise ValueError("Budget application native artifact has unknown schema")
    decoded = carrier.model_validate(raw).model_dump(mode="json")
    if canonical_json_sha256(decoded) != canonical_json_sha256(raw):
        raise ValueError("Budget native application carrier differs from its canonical typed wire")
    return decoded


# PREPARED_NOT_APPLIED_NOT_RUN. Exact intermediate result is passive proof even
# while its original claimant process is alive; it does not mint a completion ack.
class BudgetDocumentOccurrenceReadSchema(_BudgetApplicationCarrier):
    schema_version: Literal["budget_document_occurrence_read_v1"] = "budget_document_occurrence_read_v1"
    identity: BudgetDocumentOccurrenceIdentitySchema
    decision: Literal["ACCEPTED"] = "ACCEPTED"
    read_only: Literal[True] = True
    phase: Literal["NATIVE_RESULT_RECORDED"] = "NATIVE_RESULT_RECORDED"
    disposition: Literal[
        "NATIVE_RESULT_RECORDED_CLAIMANT_ACTIVE",
        "HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED",
        "HISTORICAL_RESULT_PROJECTION_NOT_AUTHORIZED",
    ]
    run_status: RunLifecycleStatus
    run_revision: StrictInt = Field(ge=0)
    child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_revision: StrictInt = Field(ge=0)
    baseline_limits: CanonicalBudgetVectorSchema
    effective_limits: CanonicalBudgetVectorSchema
    used: CanonicalBudgetVectorSchema
    active_reserved: CanonicalBudgetVectorSchema
    agent_result_ref: str = Field(min_length=1)
    agent_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_result: AgentResultSchema

    @model_validator(mode="after")
    def _result_run(self):
        if (self.agent_result.task_id, self.agent_result.run_id) != (self.identity.task_id, self.identity.run_id):
            raise ValueError("Budget native result read belongs to another Run")
        return self


# PREPARED_NOT_APPLIED_NOT_RUN: private native process helpers in supervisor_agent.py.
# OS closure is supporting evidence for exact source-bound cursor adoption, not
# positive no-effect by itself. No process kill, lease Store or timeout authority.
def _budget_native_process_identity(pid=None):
    import ctypes
    from ctypes import wintypes
    import os
    if os.name != "nt":
        raise RuntimeError("Budget continuation process closure requires the validated Windows host")
    requested = os.getpid() if pid is None else pid
    if type(requested) is not int or requested <= 0:
        raise ValueError("native process identity requires positive PID")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME))
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x00001000, False, requested)
    if not handle:
        if ctypes.get_last_error() == 87 and pid is not None:
            return {"pid": requested, "state": "ABSENT", "creation_filetime": None}
        raise RuntimeError("native process identity or closure is unproven")
    try:
        creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise RuntimeError("native process creation identity is unproven")
        birth = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        state = kernel.WaitForSingleObject(handle, 0)
        if state not in {0, 0x102}:
            raise RuntimeError("native process wait result is unproven")
        return {"pid": requested, "creation_filetime": birth,
                "state": "EXITED" if state == 0 else "ACTIVE"}
    finally:
        kernel.CloseHandle(handle)


def _budget_original_process_closed(claim):
    birth = claim.get("claimant_creation_filetime")
    pid = claim.get("claimant_pid")
    if type(birth) is not int or birth <= 0 or type(pid) is not int or pid <= 0:
        raise RuntimeError("Budget occurrence lacks positive original process identity")
    observed = _budget_native_process_identity(pid)
    if observed["state"] == "ABSENT":
        return observed
    if observed["creation_filetime"] != birth or observed["state"] == "EXITED":
        return observed  # PID reuse proves the original process is already closed.
    raise RuntimeError("INDETERMINATE: original Budget continuation claimant process remains active")


class SupervisorAgent:
    """Route one task, execute its workflow, and publish one terminal result."""

    agent_name = "SupervisorAgent"
    agent_type = "supervisor"
    SHUTDOWN_IDLE_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        agent_registry: AgentRegistry,
        workflows: Dict[str, WorkflowDefinitionSchema],
        run_trace_recorder: Optional[TraceSink] = None,
        task_manager: Optional[TaskStateManager] = None,
        model_gateway: Optional[ModelGateway] = None,
        supervisor_model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
        workflow_engine: WorkflowEnginePort | None = None,
        owned_resources: Iterable[Any] | None = None,
        retry_policy: RetryDecisionPolicy | None = None,
    ) -> None:
        # 工作流目录 它只是已注册工作流的索引
        self.catalog = WorkflowCatalog(workflows)
        # 统一模型调用网关
        self.model_gateway = model_gateway
        # 工作流路由器，决定当前 Task 应该走哪个已注册 Workflow。
        self.router = WorkflowRouter(
            self.catalog,
            model_gateway=model_gateway,
            model_name=supervisor_model_name,
            enable_llm_routing=enable_llm_routing,
            caller_agent=self.agent_name,
        )
        # 执行 Workflow 的引擎
        self.workflow_engine = workflow_engine or LangGraphWorkflowEngine(
            agent_registry=agent_registry,
            run_trace_recorder=run_trace_recorder,
        )
        # 把 TaskSchema 转换成统一的 ContextBundleSchema
        self.context_factory = ContextBundleFactory()
        # 任务生命周期管理服务
        self.lifecycle = TaskLifecycleService(task_manager)
        #  运行级和工作流级 Trace 服务
        self.trace = WorkflowTraceService(run_trace_recorder)
        self.error_factory = ErrorFactory()
        self.retry_policy = retry_policy or RetryDecisionPolicy()
        self._owned_resources = tuple(owned_resources or ())
        self._resource_close_lock = threading.Lock()
        self._resource_close_requested = False
        self._resources_closed = False
        self._resource_close_event = threading.Event()
        self._resource_close_event.set()
        self._shutdown_diagnostics: list[dict[str, Any]] = []
        self._prior_model_usage: Dict[str, Any] = {}

    # PREPARED_NOT_APPLIED_NOT_RUN. These functions will be methods in existing
    # SupervisorAgent; helpers retain existing ArtifactStore/Budget/Lifecycle owners.

    def attach_budget_continuation_owners(self, *, lifecycle_service, canonical_budget_provider, citation_grounding):
        engine = self.workflow_engine
        if (engine.lifecycle_service is not lifecycle_service
                or engine.budget_runtime is not canonical_budget_provider
                or engine.stage_service.target_post_write_composition.citation_grounding is not citation_grounding):
            raise ValueError("Budget continuation owner identity differs from Target engine")
        self._budget_continuation_owners = (lifecycle_service, canonical_budget_provider, citation_grounding)


    def _budget_owners(self):
        owners = getattr(self, "_budget_continuation_owners", None)
        if owners is None:
            raise ValueError("Target Budget continuation owners were not attached")
        life, provider, citation = owners
        if (self.workflow_engine.lifecycle_service is not life or self.workflow_engine.budget_runtime is not provider
                or self.workflow_engine.stage_service.target_post_write_composition.citation_grounding is not citation):
            raise ValueError("Target Budget continuation owner identity changed")
        return owners


    def _budget_read(self, store, ref, task):
        try:
            reference, raw = store.get_json_with_reference(ref, task_id=task.task_id, run_id=task.run_id)
            body = _budget_decode_native_application_carrier(raw)
            expected_type={
                "budget_topup_approval_ack_v1":"budget_topup_approval_ack",
                "budget_document_occurrence_claim_v1":"budget_document_occurrence_claim",
                "budget_document_occurrence_result_v1":"budget_document_occurrence_result",
                "budget_document_occurrence_ack_v1":"budget_document_occurrence_ack",
            }[body["schema_version"]]
            if (reference.schema_version != body["schema_version"] or reference.artifact_type != expected_type
                    or reference.created_by != "trusted-budget-application"):
                raise ValueError("Budget native application envelope schema/type/actor differs")
            return reference, body
        except FileNotFoundError:
            return None


    def _budget_put(self, store, ref, body, task, artifact_type):
        if body.get("schema_version") in _BUDGET_APPLICATION_NATIVE_CARRIERS:
            body = _budget_decode_native_application_carrier(body)
        return store.put_json(body, artifact_ref=ref, artifact_type=artifact_type,
            schema_version=body["schema_version"], created_by="trusted-budget-application",
            task_id=task.task_id, run_id=task.run_id)


    def _budget_wait(self, task, event_ref, event_sha256):
        from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentWaitPacketSchema
        life, provider, citation = self._budget_owners()
        pause = life.get_budget_transition(event_ref, event_sha256)
        if pause.kind != "PAUSE" or pause.resulting_run.identity.task_id != task.task_id or pause.resulting_run.identity.run_id != task.run_id:
            raise ValueError("Budget command names a different native PAUSE")
        reference, raw = citation.store.get_json_with_reference(pause.wait_ref, task_id=task.task_id, run_id=task.run_id)
        if reference.content_hash != "sha256:" + pause.wait_sha256:
            raise ValueError("Budget command native wait artifact hash differs")
        packet = BudgetDocumentWaitPacketSchema.model_validate(raw)
        identity = life.load_run_identity(task.run_id)
        if (task.canonical_request_sha256() != identity.canonical_request_sha256
                or packet.cursor.canonical_request_ref != "run-identity:" + task.run_id
                or packet.cursor.canonical_request_sha256 != identity.canonical_request_sha256
                or packet.child_preimage_sha256 != pause.child_preimage_sha256
                or tuple(packet.current_children) != tuple(pause.current_children)):
            raise ValueError("Budget command canonical request/full Child wait preimage differs")
        citation.resolve_budget_refusal(receipt_ref=packet.refusal.native_denial_ref,
            expected_sha256=packet.refusal.native_denial_sha256,
            identity={"task_id":task.task_id,"run_id":task.run_id,"section_id":packet.refusal.section_id})
        return life, provider(task.task_id, task.run_id, require_existing_account=True), citation, pause, packet


    # PREPARED_NOT_APPLIED_NOT_RUN. Current native PAUSED control only. No raw command
    # authority is created; only an immutable structural-rejection diagnostic.
    def pending_budget_authorization(self, task):
        life,provider,citation=self._budget_owners()
        workflow=self.catalog.get(task.task_type)
        packet=self.workflow_engine.resolve_budget_wait(workflow,run_id=task.run_id)
        pause=life.current_budget_transition(task.run_id)
        current=life.budget_preimage(task.run_id)
        if (pause is None or pause.kind!="PAUSE" or current.run.status is not RunLifecycleStatus.PAUSED
                or current.run!=pause.resulting_run or current.current_children!=pause.current_children
                or packet.authorization_decision!="PENDING_AUTHORIZATION"):
            raise ValueError("Pending authorization requires exact native current PAUSED wait")
        reference,raw=citation.store.get_json_with_reference(pause.wait_ref,task_id=task.task_id,run_id=task.run_id)
        if reference.content_hash!="sha256:"+pause.wait_sha256:
            raise ValueError("Pending authorization exact immutable wait packet differs")
        budget=provider(task.task_id,task.run_id,require_existing_account=True).canonical_state()
        return {"schema_version":packet.schema_version,"decision":packet.authorization_decision,"read_only":True,
            "decision_epoch":"ORIGINAL_IMMUTABLE_WAIT_PACKET",
            "accepted_grants_for_wait":[{"grant_ref":grant.grant_ref,"grant_sha256":grant.grant_sha256}
                for grant in budget.grant_history if grant.request.wait_event_ref==pause.event_ref],
            "wait_packet_ref":reference.artifact_ref,"wait_packet_sha256":pause.wait_sha256,"wait_packet":raw,
            "wait_event_ref":pause.event_ref,"wait_event_sha256":pause.event_sha256,
            "run_status":current.run.status.value,"run_revision":current.run.lifecycle_revision,
            "child_preimage_sha256":current.child_preimage_sha256,"budget_revision":budget.revision,
            "baseline_limits":budget.limits.model_dump(mode="json"),"effective_limits":budget.effective_limits.model_dump(mode="json"),
            "used":budget.used.model_dump(mode="json"),"active_reserved":budget.active_reserved.model_dump(mode="json"),
            "authorization_ref":None,"authorization_sha256":None,"grant_ref":None,"grant_sha256":None}


    def reject_budget_command_shape(self, task, command, error):
        import json
        from hashlib import sha256
        from datetime import datetime
        from enum import Enum
        from core.hashing import canonical_sha256
        from core.persistence.interprocess_lock import InterProcessFileLock
        if not isinstance(command,BudgetTopupCommandSchema):
            raise ValueError("structural acknowledgement requires the explicit typed trusted entry carrier")
        shape=command.model_dump(mode="python",warnings=False)
        def diagnostic_wire(value):
            if isinstance(value,datetime): return value.isoformat()
            if isinstance(value,Enum): return value.value
            if hasattr(value,"model_dump"): return value.model_dump(mode="python",warnings=False)
            raise TypeError("unsupported object in malformed trusted command diagnostic")
        # Nonfinite JSON tokens remain inside a diagnostic string. They never enter
        # a finite grant carrier or native Budget vector; exact raw shape is hashed.
        raw_shape=json.dumps(shape,ensure_ascii=False,sort_keys=True,default=diagnostic_wire,allow_nan=True)
        shape_sha256=sha256(raw_shape.encode("utf-8")).hexdigest()
        approval=shape.get("approval_id")
        approval_id=approval if isinstance(approval,str) and approval.strip() else "invalid-shape:"+shape_sha256
        life,provider,citation=self._budget_owners()
        store=citation.store
        key=canonical_sha256({"task_id":task.task_id,"run_id":task.run_id,"approval_id":approval_id})
        ack_ref=f"artifact://budget-topup/{task.task_id}/{task.run_id}/approval/{key}/ack"
        with InterProcessFileLock(store.root/"budget_approval_locks"/(key+".lock")):
            existing=self._budget_read(store,ack_ref,task)
            if existing is not None:
                if (existing[1]["decision"]!="REJECTED" or existing[1]["command_sha256"]!=shape_sha256
                        or existing[1]["rejected_command_shape_json"]!=raw_shape):
                    raise ValueError("Budget approval client identity changed its immutable structural decision")
                return {**existing[1],"ack_ref":existing[0].artifact_ref,
                    "ack_sha256":existing[0].content_hash.removeprefix("sha256:")}
            workflow=self.catalog.get(task.task_type)
            packet=self.workflow_engine.resolve_budget_wait(workflow,run_id=task.run_id)
            pause=life.current_budget_transition(task.run_id)
            current=life.budget_preimage(task.run_id)
            if (pause is None or pause.kind!="PAUSE" or current.run.status is not RunLifecycleStatus.PAUSED
                    or current.run!=pause.resulting_run or current.current_children!=pause.current_children):
                raise ValueError("Structural rejection acknowledgement requires exact native PAUSED preimage")
            # Observe Budget before holding the Lifecycle fence; never invert locks.
            budget=provider(task.task_id,task.run_id,require_existing_account=True).canonical_state()
            body={"schema_version":"budget_topup_approval_ack_v1","task_id":task.task_id,"run_id":task.run_id,
                "approval_id":approval_id,"command_sha256":shape_sha256,"decision":"REJECTED",
                "reason":"STRUCTURAL_COMMAND_REJECTED","authorization_ref":None,"authorization_sha256":None,
                "grant_ref":None,"grant_sha256":None,"wait_event_ref":pause.event_ref,"wait_event_sha256":pause.event_sha256,
                "run_status":current.run.status.value,"run_revision":current.run.lifecycle_revision,
                "child_preimage_sha256":current.child_preimage_sha256,"budget_revision":budget.revision,
                "baseline_limits":budget.limits.model_dump(mode="json"),"effective_limits":budget.effective_limits.model_dump(mode="json"),
                "used":budget.used.model_dump(mode="json"),"active_reserved":budget.active_reserved.model_dump(mode="json"),
                "rejected_command_shape_sha256":shape_sha256,"rejected_command_shape_json":raw_shape,
                "structural_error":str(error)}
            with life.budget_authorization_fence(task_id=task.task_id,run_id=task.run_id,
                    expected_lifecycle_revision=current.run.lifecycle_revision,wait_event_ref=pause.event_ref,
                    wait_event_sha256=pause.event_sha256,child_preimage_sha256=current.child_preimage_sha256,
                    semantic_operation_ref=pause.semantic_operation_ref,semantic_input_sha256=pause.semantic_input_sha256,
                    artifact_version=pause.artifact_version):
                reference=self._budget_put(store,ack_ref,body,task,"budget_topup_approval_ack")
            return {**body,"ack_ref":reference.artifact_ref,"ack_sha256":reference.content_hash.removeprefix("sha256:")}

    def approve_budget_topup(self, task, command):
        from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
            CANONICAL_BUDGET_DIMENSIONS, CanonicalBudgetVectorSchema,
            CanonicalBudgetTopupAuthorizationSchema, CanonicalBudgetTopupRequestSchema,
        )
        from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetTopupRejected
        from agent.runtime.graph_state import GraphStateSchema
        from core.hashing import canonical_sha256
        from core.persistence.interprocess_lock import InterProcessFileLock
        from core.persistence.crash_safe_jsonl import canonical_json_sha256
        if not isinstance(command, BudgetTopupCommandSchema):
            raise ValueError("the explicit typed BudgetTopupCommandSchema is required")
        command = BudgetTopupCommandSchema.model_validate(command.model_dump(mode="json"))
        life, manager, citation, pause, packet = self._budget_wait(task, command.wait_event_ref, command.wait_event_sha256)
        store = citation.store
        client_key = canonical_sha256({"task_id":task.task_id,"run_id":task.run_id,"approval_id":command.approval_id})
        ack_ref = f"artifact://budget-topup/{task.task_id}/{task.run_id}/approval/{client_key}/ack"
        command_sha256 = canonical_sha256(command.model_dump(mode="json"))
        with InterProcessFileLock(store.root / "budget_approval_locks" / (client_key + ".lock")):
            existing = self._budget_read(store, ack_ref, task)
            if existing is not None:
                if existing[1].get("command_sha256") != command_sha256:
                    raise ValueError("Budget approval client identity changed semantics")
                if (existing[1]["task_id"], existing[1]["run_id"], existing[1]["approval_id"],
                        existing[1]["wait_event_ref"], existing[1]["wait_event_sha256"]) != (
                        task.task_id, task.run_id, command.approval_id, pause.event_ref, pause.event_sha256):
                    raise ValueError("Budget approval acknowledgement native identity differs")
                if existing[1]["decision"] == "ACCEPTED":
                    native_grant = manager.resolve_topup_grant(existing[1]["grant_ref"],existing[1]["grant_sha256"])
                    if (native_grant.request.delta != command.delta
                            or native_grant.authorization.authorization_ref != existing[1]["authorization_ref"]
                            or native_grant.authorization.authorization_sha256 != existing[1]["authorization_sha256"]
                            or native_grant.request.wait_event_ref != pause.event_ref):
                        raise ValueError("Budget approval acknowledgement exact native grant differs")
                return {**existing[1], "ack_ref":existing[0].artifact_ref,"ack_sha256":existing[0].content_hash.removeprefix("sha256:")}
            state = manager.canonical_state()
            baseline_hash = canonical_json_sha256({"policy_ref":state.policy_ref,"policy_version":state.policy_version,
                                              "limits":state.limits.model_dump(mode="json")})
            allowance = command.authorized_delta
            if allowance is None:
                allowance = CanonicalBudgetVectorSchema(**{
                    name: max(0, getattr(command.cumulative_ceiling, name) - getattr(state.limits, name))
                    for name in CANONICAL_BUDGET_DIMENSIONS})
            raw_command = {"schema_version":"budget_topup_trusted_authorization_command_v1",
                "authorization_kind":command.authorization_kind,"authorization_id":command.authorization_id,
                "command_source":command.command_source,"issuer":command.issuer,"decision":command.decision,
                "task_id":task.task_id,"run_id":task.run_id,
                "canonical_request_ref":packet.cursor.canonical_request_ref,
                "canonical_request_sha256":packet.cursor.canonical_request_sha256,
                "semantic_operation_ref":packet.refusal.semantic_operation_ref,
                "semantic_input_sha256":packet.refusal.semantic_input_sha256,
                "artifact_version":str(packet.refusal.artifact_version),"baseline_policy_sha256":baseline_hash,
                "authorized_delta":allowance.model_dump(mode="json"),
                "cumulative_ceiling":command.cumulative_ceiling.model_dump(mode="json"),
                "expires_at":command.expires_at.isoformat() if command.expires_at is not None else None,
                "revoked":False,
                "single_use_wait_event_ref":pause.event_ref if command.authorization_kind=="SINGLE_USE" else None,
                "single_use_native_denial_sha256":packet.refusal.native_denial_sha256 if command.authorization_kind=="SINGLE_USE" else None,
                "single_use_child_preimage_sha256":pause.child_preimage_sha256 if command.authorization_kind=="SINGLE_USE" else None}
            auth_key = canonical_sha256({"task_id":task.task_id,"run_id":task.run_id,"authorization_id":command.authorization_id})
            auth_ref = f"artifact://budget-topup/{task.task_id}/{task.run_id}/authorization/{auth_key}"
            with InterProcessFileLock(store.root / "budget_authorization_locks" / (auth_key + ".lock")):
                auth_native = self._budget_put(store, auth_ref, raw_command, task, "budget_topup_authorization_command")
            def resolver(request):
                native, raw = store.get_json_with_reference(request.authorization_ref, task_id=task.task_id, run_id=task.run_id)
                if native.content_hash != "sha256:" + request.authorization_sha256 or raw != raw_command:
                    raise ValueError("Budget trusted command exact immutable identity differs")
                if allowance.is_zero():
                    raise CanonicalBudgetTopupRejected(
                        "LIMIT_REACHED", "finite authorization permits no positive top-up allowance")
                values = {name:value for name,value in raw.items() if name != "schema_version"}
                return CanonicalBudgetTopupAuthorizationSchema(**values,
                    authorization_ref=native.artifact_ref,authorization_sha256=native.content_hash.removeprefix("sha256:"))
            old = [grant for grant in state.grant_history
                   if grant.authorization.authorization_ref == auth_native.artifact_ref
                   and grant.authorization.authorization_sha256 == auth_native.content_hash.removeprefix("sha256:")
                   and grant.request.wait_event_ref == pause.event_ref]
            if len(old) > 1:
                raise ValueError("Budget approval native debit is ambiguous")
            current = life.budget_preimage(task.run_id)
            if not old and current.run.status is not RunLifecycleStatus.PAUSED:
                raise ValueError("new Budget approval requires the exact native PAUSED hold")
            workflow = self.catalog.get(task.task_type)
            if (current.run == pause.resulting_run and current.current_children == pause.current_children
                    and current.run.status is RunLifecycleStatus.PAUSED):
                with InterProcessFileLock(store.root / "budget_wait_projection_locks" / (canonical_sha256(pause.event_ref)+".lock")):
                    current = life.budget_preimage(task.run_id)
                    if (current.run == pause.resulting_run and current.current_children == pause.current_children
                            and current.run.status is RunLifecycleStatus.PAUSED):
                        inspection = self.workflow_engine.inspect(workflow, run_id=task.run_id)
                        graph_state = GraphStateSchema.model_validate(inspection["graph_state"])
                        self.workflow_engine.ensure_budget_wait_checkpoint(workflow, graph_state)
                        observed = self.workflow_engine.resolve_budget_wait(workflow, run_id=task.run_id)
                        if observed != packet:
                            raise ValueError("Budget command pending native cursor changed")
            grant = None
            if command.decision == "DENIED":
                decision, reason = "DENIED", "explicit trusted user declined finite top-up"
            else:
                failure = store.get_json(packet.refusal.native_denial_ref, task_id=task.task_id,run_id=task.run_id,
                                         section_id=packet.refusal.section_id)
                required = failure["failure"]["phase_proof"]["native_denial"]["requested"]
                request = CanonicalBudgetTopupRequestSchema(
                    approval_id=command.approval_id,authorization_ref=auth_native.artifact_ref,
                    authorization_sha256=auth_native.content_hash.removeprefix("sha256:"),
                    task_id=task.task_id,run_id=task.run_id,
                    canonical_request_ref=packet.cursor.canonical_request_ref,canonical_request_sha256=packet.cursor.canonical_request_sha256,
                    semantic_operation_ref=packet.refusal.semantic_operation_ref,semantic_input_sha256=packet.refusal.semantic_input_sha256,
                    artifact_version=str(packet.refusal.artifact_version),native_denial_ref=packet.refusal.native_denial_ref,
                    native_denial_sha256=packet.refusal.native_denial_sha256,
                    phase_proof_ref=packet.refusal.phase_proof_ref,phase_proof_sha256=packet.refusal.phase_proof_sha256,
                    wait_event_ref=pause.event_ref,wait_event_sha256=pause.event_sha256,child_preimage_sha256=pause.child_preimage_sha256,
                    baseline_policy_sha256=baseline_hash,expected_budget_revision=old[0].request.expected_budget_revision if old else state.revision,
                    expected_lifecycle_revision=pause.resulting_run.lifecycle_revision,delta=command.delta,required=required)
                try:
                    grant = manager.apply_topup(request, authorization_resolver=resolver,
                        paused_authorization_fence=life.budget_authorization_fence)
                    decision, reason = "ACCEPTED", None
                except CanonicalBudgetTopupRejected as rejection:
                    decision, reason = rejection.decision, rejection.reason
            current = life.budget_preimage(task.run_id)
            after_budget = manager.canonical_state()
            body = {"schema_version":"budget_topup_approval_ack_v1","task_id":task.task_id,"run_id":task.run_id,
                "approval_id":command.approval_id,"command_sha256":command_sha256,"decision":decision,"reason":reason,
                "authorization_ref":auth_native.artifact_ref,"authorization_sha256":auth_native.content_hash.removeprefix("sha256:"),
                "wait_event_ref":pause.event_ref,"wait_event_sha256":pause.event_sha256,
                "grant_ref":grant.grant_ref if grant else None,"grant_sha256":grant.grant_sha256 if grant else None,
                "run_status":current.run.status.value,"run_revision":current.run.lifecycle_revision,
                "child_preimage_sha256":current.child_preimage_sha256,"budget_revision":after_budget.revision,
                "baseline_limits":after_budget.limits.model_dump(mode="json"),"effective_limits":after_budget.effective_limits.model_dump(mode="json"),
                "used":after_budget.used.model_dump(mode="json"),"active_reserved":after_budget.active_reserved.model_dump(mode="json"),
                "rejected_command_shape_sha256":None,"rejected_command_shape_json":None,"structural_error":None}
            reference = self._budget_put(store, ack_ref, body, task, "budget_topup_approval_ack")
            return {**body,"ack_ref":reference.artifact_ref,"ack_sha256":reference.content_hash.removeprefix("sha256:")}


    def resume_budget_topup(self, task, *, accepted_grant_ref, accepted_grant_sha256):
        from agent.runtime.graph_state import GraphStateSchema
        from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema
        from core.hashing import canonical_sha256
        from core.persistence.interprocess_lock import InterProcessFileLock
        life, provider, citation = self._budget_owners()
        manager = provider(task.task_id, task.run_id, require_existing_account=True)
        grant = manager.resolve_topup_grant(accepted_grant_ref, accepted_grant_sha256)
        life, manager, citation, pause, packet = self._budget_wait(task, grant.request.wait_event_ref, grant.request.wait_event_sha256)
        current = life.budget_preimage(task.run_id)
        workflow = self.catalog.get(task.task_type)
        if (current.run == pause.resulting_run and current.current_children == pause.current_children
                and current.run.status is RunLifecycleStatus.PAUSED):
            with InterProcessFileLock(citation.store.root / "budget_wait_projection_locks" / (canonical_sha256(pause.event_ref)+".lock")):
                current = life.budget_preimage(task.run_id)
                if (current.run == pause.resulting_run and current.current_children == pause.current_children
                        and current.run.status is RunLifecycleStatus.PAUSED):
                    inspection = self.workflow_engine.inspect(workflow, run_id=task.run_id)
                    graph_state = GraphStateSchema.model_validate(inspection["graph_state"])
                    self.workflow_engine.ensure_budget_wait_checkpoint(workflow, graph_state)
                    latest = self.workflow_engine.inspect(workflow, run_id=task.run_id)
                    self.workflow_engine.validate_budget_wait_for_resume(workflow,
                        GraphStateSchema.model_validate(latest["graph_state"]), wait=packet)
        resume = life.resume_budget_wait(task.run_id, pause.resulting_run.lifecycle_revision,
            expected_current_children=pause.current_children,
            operation_key="document-budget-resume:" + canonical_sha256({"wait_event_ref":pause.event_ref,"grant_ref":grant.grant_ref}),
            wait_event_ref=pause.event_ref,wait_event_sha256=pause.event_sha256,
            grant_ref=grant.grant_ref,grant_sha256=grant.grant_sha256)
        continuation = BudgetDocumentContinuationSchema(
            predecessor_result_ref=grant.request.native_denial_ref,predecessor_result_sha256=grant.request.native_denial_sha256,
            accepted_grant_ref=grant.grant_ref,accepted_grant_sha256=grant.grant_sha256,
            resume_event_ref=resume.event_ref,resume_event_sha256=resume.event_sha256)
        key = canonical_sha256({"wait_event_ref":pause.event_ref,"resume_occurrence_id":resume.resume_occurrence_id})
        store = citation.store
        base = f"artifact://budget-topup/{task.task_id}/{task.run_id}/occurrence/{key}"
        identity = {"task_id":task.task_id,"run_id":task.run_id,"wait_event_ref":pause.event_ref,
            "wait_event_sha256":pause.event_sha256,"grant_ref":grant.grant_ref,"grant_sha256":grant.grant_sha256,
            "resume_event_ref":resume.event_ref,"resume_event_sha256":resume.event_sha256,
            "resume_occurrence_id":resume.resume_occurrence_id,
            "child_preimage_sha256":resume.child_preimage_sha256,
            "continuation":continuation.model_dump(mode="json")}
        # Native cached result/ack reads precede worker-closure checks and cannot execute.
        with InterProcessFileLock(store.root / "citation_budget_occurrence_locks" / (key + ".lock")):
            claims=self._budget_claim_frontier(store,base,task,identity)
            exact_ack=self._budget_read(store,base+"/ack",task)
            exact_result=self._budget_read(store,base+"/result",task)
            claim=claims[-1] if claims else None
            if exact_result is not None:
                if (claim is None or exact_result[1]["identity"]!=identity
                        or exact_result[1]["claim_ref"]!=claim[0].artifact_ref
                        or exact_result[1]["claim_sha256"]!=claim[0].content_hash.removeprefix("sha256:")
                        or exact_result[1]["source_epoch_sha256"]!=claim[1]["source_epoch_sha256"]):
                    raise ValueError("Budget occurrence result differs from exact producing claim frontier")
                from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetStateSchema
                from core.persistence.crash_safe_jsonl import canonical_json_sha256
                recorded_budget=CanonicalBudgetStateSchema.model_validate(exact_result[1]["native_budget"])
                manager.resolve_state_revision(recorded_budget.revision,
                    expected_sha256=canonical_json_sha256(recorded_budget.model_dump(mode="json")))
            if exact_ack is not None:
                if (claim is None or exact_result is None or exact_ack[1]["identity"]!=identity
                        or exact_ack[1]["claim_ref"]!=claim[0].artifact_ref
                        or exact_ack[1]["claim_sha256"]!=claim[0].content_hash.removeprefix("sha256:")
                        or exact_ack[1]["agent_result_ref"]!=exact_result[0].artifact_ref
                        or exact_ack[1]["agent_result_sha256"]!=exact_result[0].content_hash.removeprefix("sha256:")
                        or exact_ack[1]["agent_result"]!=exact_result[1]["agent_result"]):
                    raise ValueError("Budget occurrence acknowledgement exact native claim/result differs")
                if not self._budget_result_snapshot_current(life,exact_result[1]):
                    return self._budget_passive_occurrence_result(task,identity,exact_result,life,manager,
                        disposition="HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED")
                return {**exact_ack[1],"ack_ref":exact_ack[0].artifact_ref,
                    "ack_sha256":exact_ack[0].content_hash.removeprefix("sha256:")}
            if exact_result is not None:
                if not self._budget_result_snapshot_current(life,exact_result[1]):
                    return self._budget_passive_occurrence_result(task,identity,exact_result,life,manager,
                        disposition="HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED")
                try:
                    _budget_original_process_closed(claim[1])
                except RuntimeError:
                    return self._budget_passive_occurrence_result(task,identity,exact_result,life,manager,
                        disposition="NATIVE_RESULT_RECORDED_CLAIMANT_ACTIVE")
                return self._budget_project_occurrence_result(task,identity,exact_result,claim,life,manager,store,base)
            # Missing result supplies no execution authority. Existing latest claimant
            # must positively be closed before any new generation or engine entrance.
            if claim is not None:
                _budget_original_process_closed(claim[1])
            inspection=self.workflow_engine.inspect(workflow,run_id=task.run_id)
            graph_state=GraphStateSchema.model_validate(inspection["graph_state"])
            cursor=self.workflow_engine.validate_budget_execution_cursor(workflow,graph_state=graph_state,
                continuation=continuation,original_claim=claim[1] if claim else None)
            if cursor["phase"] not in {"PRE_CITATION_INTENT_SAFE","COMPLETED","GRAPH_COMPLETED"}:
                raise RuntimeError("INDETERMINATE: Budget cursor has no closed safe or completed native occurrence")
            claim=self._budget_append_claim(store,base,task,identity,cursor,claims)
            if cursor["phase"]=="GRAPH_COMPLETED":
                # The native graph completed before the application result write. Read
                # its existing terminal result; do not invoke or start a new graph.
                result=AgentResultSchema.model_validate(cursor["terminal_agent_result"])
            else:
                life.assert_run_committable(task.run_id)
                self.lifecycle.mark_running(task)
                execution=self.workflow_engine.resume_budget_document(workflow,cursor["graph_state"],continuation)
                result=self._result_from_continuation(task=task,workflow=workflow,state=cursor["graph_state"],
                    execution=execution,resumed=True)
            if (result.task_id,result.run_id)!=(task.task_id,task.run_id):
                raise ValueError("Budget graph native result belongs to another Run")
            # Preserve actual owner facts before recording this occurrence's immutable
            # result. A later lawful new PAUSE or cancellation is a distinct control.
            native=life.budget_preimage(task.run_id)
            budget=manager.canonical_state()
            result_body={"schema_version":"budget_document_occurrence_result_v1","identity":identity,
                "claim_ref":claim[0].artifact_ref,"claim_sha256":claim[0].content_hash.removeprefix("sha256:"),
                "source_epoch_sha256":claim[1]["source_epoch_sha256"],"native_run":native.run.model_dump(mode="json"),
                "native_current_children":[row.model_dump(mode="json") for row in native.current_children],
                "native_child_preimage_sha256":native.child_preimage_sha256,
                "native_budget":budget.model_dump(mode="json"),"agent_result":result.model_dump(mode="json")}
            self._budget_put(store,base+"/result",result_body,task,"budget_document_occurrence_result")
            exact_result=self._budget_read(store,base+"/result",task)
            if exact_result is None:
                raise ValueError("Budget occurrence result did not read back from the immutable native store")
            return self._budget_project_occurrence_result(task,identity,exact_result,claim,life,manager,store,base)


    def submit_budget_topup(self, task, command, *, continue_document=True):
        acknowledgement = self.approve_budget_topup(task,command)
        if acknowledgement["decision"] != "ACCEPTED" or not continue_document:
            return acknowledgement
        return self.resume_budget_topup(task,accepted_grant_ref=acknowledgement["grant_ref"],
            accepted_grant_sha256=acknowledgement["grant_sha256"])


    # Existing shared human-review validator: after `if cancel: return`, before any
    # Task RUNNING, reject this typed purpose. Both ordinary and stream review use it.
    # for item in inspection.get("interrupts") or []:
    #     value = item.get("value") if isinstance(item,dict) else None
    #     if isinstance(value,dict) and value.get("pause_purpose") == "BUDGET_AUTHORIZATION":
    #         raise ValueError("Budget authorization requires the explicit typed Budget command")


    def _budget_passive_occurrence_result(self, task, identity, native_result, life, manager, *, disposition):
        current, budget = life.budget_preimage(task.run_id), manager.canonical_state()
        body = BudgetDocumentOccurrenceReadSchema(
            identity=identity, disposition=disposition, run_status=current.run.status,
            run_revision=current.run.lifecycle_revision,child_preimage_sha256=current.child_preimage_sha256,
            budget_revision=budget.revision,baseline_limits=budget.limits,effective_limits=budget.effective_limits,
            used=budget.used,active_reserved=budget.active_reserved,
            agent_result_ref=native_result[0].artifact_ref,
            agent_result_sha256=native_result[0].content_hash.removeprefix("sha256:"),
            agent_result=native_result[1]["agent_result"]).model_dump(mode="json")
        return body


    def inspect_budget_state(self, task):
        life, provider, _citation = self._budget_owners()
        native = life.budget_preimage(task.run_id)
        budget = provider(task.task_id, task.run_id, require_existing_account=True).canonical_state()
        return {"run":native.run.model_dump(mode="json"),
            "current_children":[row.model_dump(mode="json") for row in native.current_children],
            "child_preimage_sha256":native.child_preimage_sha256,
            "budget":budget.model_dump(mode="json"),"effective_limits":budget.effective_limits.model_dump(mode="json")}

    # PREPARED_NOT_APPLIED_NOT_RUN. Existing Supervisor methods; immutable native
    # generations reuse the same approved claim carrier and existing Store.
    def _budget_claim_frontier(self, store, base, task, identity):
        refs = [ref for ref in store.list_references()
            if ref.artifact_ref == base+"/claim" or ref.artifact_ref.startswith(base+"/claims/")]
        claims = {}
        for listed in refs:
            native = self._budget_read(store,listed.artifact_ref,task)
            if native is None or native[0] != listed or native[1].get("identity") != identity:
                raise ValueError("Budget claimant chain native identity differs")
            reference, body = native
            generation = body["generation"]
            exact_ref = base+"/claim" if generation==0 else base+"/claims/"+str(generation)
            if (reference.artifact_ref != exact_ref or generation in claims
                    or reference.artifact_type != "budget_document_occurrence_claim"
                    or reference.created_by != "trusted-budget-application"):
                raise ValueError("Budget claimant chain has a fork or invalid native slot")
            claims[generation]=native
        if sorted(claims) != list(range(len(claims))):
            raise ValueError("Budget claimant chain has a generation gap")
        ordered=[claims[index] for index in range(len(claims))]
        bindings=("source_epoch_sha256","checkpoint_state_sha256","checkpoint_config","wait_ref","wait_sha256")
        for index, (reference, body) in enumerate(ordered):
            if index==0:
                if body["predecessor_claim_ref"] is not None or body["predecessor_claim_sha256"] is not None:
                    raise ValueError("Budget root claim has an unexpected predecessor")
            else:
                parent_ref,parent_body=ordered[index-1]
                if (body["predecessor_claim_ref"] != parent_ref.artifact_ref
                        or body["predecessor_claim_sha256"] != parent_ref.content_hash.removeprefix("sha256:")
                        or any(body[name]!=parent_body[name] for name in bindings)):
                    raise ValueError("Budget claimant chain exact predecessor/source binding differs")
        return ordered


    def _budget_append_claim(self, store, base, task, identity, cursor, claims):
        process=_budget_native_process_identity()
        generation=len(claims)
        previous=claims[-1][0] if claims else None
        body={"schema_version":"budget_document_occurrence_claim_v1","identity":identity,
            "generation":generation,"predecessor_claim_ref":previous.artifact_ref if previous else None,
            "predecessor_claim_sha256":previous.content_hash.removeprefix("sha256:") if previous else None,
            "claimant_pid":process["pid"],"claimant_creation_filetime":process["creation_filetime"],
            "source_epoch_sha256":cursor["source_epoch_sha256"],
            "checkpoint_state_sha256":cursor["checkpoint_state_sha256"],"checkpoint_config":cursor["checkpoint_config"],
            "wait_ref":cursor["wait_ref"],"wait_sha256":cursor["wait_sha256"]}
        ref=base+"/claim" if generation==0 else base+"/claims/"+str(generation)
        native=self._budget_put(store,ref,body,task,"budget_document_occurrence_claim")
        current=self._budget_claim_frontier(store,base,task,identity)
        if len(current)!=generation+1 or current[-1][0]!=native:
            raise ValueError("Budget claimant chain append did not read back exact unique frontier")
        return current[-1]


    def _budget_result_snapshot_current(self, life, body):
        current=life.budget_preimage(body["identity"]["run_id"])
        return (current.run.model_dump(mode="json")==body["native_run"]
            and [row.model_dump(mode="json") for row in current.current_children]==body["native_current_children"]
            and current.child_preimage_sha256==body["native_child_preimage_sha256"])


    def _budget_project_occurrence_result(self, task, identity, native_result, claim, life, manager, store, base):
        from contextlib import nullcontext
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RUN_TERMINAL_STATUSES
        from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
            LifecycleRevisionConflict,LifecycleCurrentnessError,
        )
        body=native_result[1]
        if (body["claim_ref"]!=claim[0].artifact_ref
                or body["claim_sha256"]!=claim[0].content_hash.removeprefix("sha256:")
                or body["identity"]!=identity):
            raise ValueError("Budget result is not bound to the actual producing claimant")
        if not self._budget_result_matches_native_control(body):
            return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                disposition="HISTORICAL_RESULT_PROJECTION_NOT_AUTHORIZED")
        if not self._budget_result_snapshot_current(life,body):
            return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                disposition="HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED")
        observed=life.budget_preimage(task.run_id)
        if (observed.run.model_dump(mode="json") != body["native_run"]
                or [row.model_dump(mode="json") for row in observed.current_children] != body["native_current_children"]
                or observed.child_preimage_sha256 != body["native_child_preimage_sha256"]):
            return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                disposition="HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED")
        projection_fence=nullcontext()
        if observed.run.status is RunLifecycleStatus.PAUSED:
            wait=life.current_budget_transition(task.run_id)
            if wait is None or wait.kind!="PAUSE" or wait.resulting_run!=observed.run:
                return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                    disposition="HISTORICAL_RESULT_PROJECTION_NOT_AUTHORIZED")
            projection_fence=life.budget_authorization_fence(task_id=task.task_id,run_id=task.run_id,
                expected_lifecycle_revision=observed.run.lifecycle_revision,
                wait_event_ref=wait.event_ref,wait_event_sha256=wait.event_sha256,
                child_preimage_sha256=observed.child_preimage_sha256,
                semantic_operation_ref=wait.semantic_operation_ref,semantic_input_sha256=wait.semantic_input_sha256,
                artifact_version=wait.artifact_version)
        elif observed.run.status not in RUN_TERMINAL_STATUSES:
            return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                disposition="HISTORICAL_RESULT_PROJECTION_NOT_AUTHORIZED")
        # Never acquire Budget while the Lifecycle projection fence is held.
        self._budget_restore_exact_result_usage(body,manager)
        budget=manager.canonical_state()
        result=AgentResultSchema.model_validate(body["agent_result"])
        try:
            with projection_fence:
                # For PAUSED the exact owner preimage remains locked. Terminal
                # canonical records and their current Children are immutable.
                self.lifecycle.mark_terminal(task,result)
                ack={"schema_version":"budget_document_occurrence_ack_v1","identity":identity,
                    "decision":"ACCEPTED","run_status":observed.run.status.value,
                    "run_revision":observed.run.lifecycle_revision,"child_preimage_sha256":observed.child_preimage_sha256,
                    "budget_revision":budget.revision,"baseline_limits":budget.limits.model_dump(mode="json"),
                    "effective_limits":budget.effective_limits.model_dump(mode="json"),
                    "used":budget.used.model_dump(mode="json"),"active_reserved":budget.active_reserved.model_dump(mode="json"),
                    "claim_ref":claim[0].artifact_ref,"claim_sha256":claim[0].content_hash.removeprefix("sha256:"),
                    "agent_result_ref":native_result[0].artifact_ref,
                    "agent_result_sha256":native_result[0].content_hash.removeprefix("sha256:"),
                    "agent_result":result.model_dump(mode="json")}
                native_ack=self._budget_put(store,base+"/ack",ack,task,"budget_document_occurrence_ack")
                self._budget_usage_projection_required=True
        except (LifecycleRevisionConflict,LifecycleCurrentnessError):
            return self._budget_passive_occurrence_result(task,identity,native_result,life,manager,
                disposition="HISTORICAL_RESULT_CURRENT_CONTROL_CHANGED")
        return {**ack,"ack_ref":native_ack.artifact_ref,"ack_sha256":native_ack.content_hash.removeprefix("sha256:")}


    # PREPARED_NOT_APPLIED_NOT_RUN. Passive typed result/native control comparison.
    def _budget_result_matches_native_control(self, body):
        from schemas.status import ExecutionStatus
        from application.terminal_outcome_projector import terminal_payload
        from application.terminal_outcome_schema import TerminalOutcomeResultV1
        result=AgentResultSchema.model_validate(body["agent_result"])
        status=RunLifecycleStatus(body["native_run"]["status"])
        if status is RunLifecycleStatus.PAUSED:
            return result.status is ExecutionStatus.PAUSED
        if status is RunLifecycleStatus.CANCELLED:
            return result.status is ExecutionStatus.CANCELLED
        if status is RunLifecycleStatus.FAILED:
            return result.status in {ExecutionStatus.FAILED,ExecutionStatus.RETRYABLE_FAILED}
        if status is RunLifecycleStatus.SUCCESS:
            payload=terminal_payload(result)
            raw=payload.get("terminal_outcome")
            if raw is None:
                return False
            owner=TerminalOutcomeResultV1.model_validate(raw)
            return owner.run_status is status and owner.execution_status is result.status
        return False

    # PREPARED_NOT_APPLIED_NOT_RUN. Method in existing Supervisor; no grant or
    # pending DOCUMENT engine invocation. Exact native packet recovery only.
    def recover_budget_pause_from_wait_packet(self, task, *, wait_ref, wait_sha256):
        from core.hashing import canonical_sha256
        from core.persistence.interprocess_lock import InterProcessFileLock
        life,provider,citation=self._budget_owners()
        native_identity=life.load_run_identity(task.run_id)
        if (native_identity.task_id!=task.task_id
                or native_identity.canonical_request_sha256!=task.canonical_request_sha256()):
            raise ValueError("Budget wait recovery canonical Task/Run identity differs")
        # Loading an existing account is passive and cannot manufacture baseline.
        provider(task.task_id,task.run_id,require_existing_account=True).canonical_state()
        if (not isinstance(wait_ref,str) or not wait_ref.strip()
                or not isinstance(wait_sha256,str) or len(wait_sha256)!=64
                or any(char not in "0123456789abcdef" for char in wait_sha256)):
            raise ValueError("Budget wait recovery requires exact immutable packet ref/hash")
        workflow=self.catalog.get(task.task_type)
        with InterProcessFileLock(citation.store.root/"budget_wait_projection_locks"/(canonical_sha256(wait_ref)+".lock")):
            observation=self.workflow_engine.recover_budget_pause_from_wait_packet(workflow,
                task_id=task.task_id,run_id=task.run_id,wait_ref=wait_ref,wait_sha256=wait_sha256)
        return {"pause_purpose":"BUDGET_AUTHORIZATION","wait_ref":wait_ref,"wait_sha256":wait_sha256,
            "checkpoint_observation":observation,"canonical_budget_state":self.inspect_budget_state(task)}

    # PREPARED_NOT_APPLIED_NOT_RUN. Existing operational observer only. Native
    # Budget remains the charging authority and is neither changed nor inferred.
    def _budget_restore_exact_result_usage(self, body, manager):
        snapshot=getattr(self.model_gateway,"usage_snapshot",None)
        session=dict(snapshot()) if callable(snapshot) else {}
        if any(int(session.get(name,0) or 0)!=0 for name in ("logical_calls","provider_attempts","total_tokens")):
            return  # Fresh in-process provider accounting is already in its ledger.
        current=manager.canonical_state()
        if current.model_dump(mode="json")!=body["native_budget"]:
            return  # A later actual epoch must never be replaced by old observation.
        result=AgentResultSchema.model_validate(body["agent_result"])
        observed=result.result.get("model_usage")
        if isinstance(observed,dict):
            self.set_prior_model_usage(observed)  # Exact snapshot, never additive replay.

    def model_usage_snapshot(self) -> Dict[str, Any]:
        """Return provider-attempt and logical-call usage for this run."""

        if self.model_gateway is None:
            return dict(self._prior_model_usage)
        snapshot = getattr(self.model_gateway, "usage_snapshot", None)
        current = dict(snapshot()) if callable(snapshot) else {}
        return merge_model_usage_snapshots(self._prior_model_usage, current)

    def set_prior_model_usage(self, snapshot: Dict[str, Any]) -> None:
        """Seed a resumed runtime with its already-persisted usage baseline."""

        self._prior_model_usage = dict(snapshot or {})

    def defer_until_idle(self, callback: Callable[[], None]) -> bool:
        """Run a finalization callback after any timed-out worker exits."""

        defer = getattr(self.workflow_engine, "defer_until_idle", None)
        if callable(defer):
            return bool(defer(callback))
        callback()
        return False

    def close(self) -> None:
        """Bound shutdown and return only after owned resources are closed."""

        wait_for_concurrent_close = False
        with self._resource_close_lock:
            if self._resources_closed:
                return
            if self._resource_close_requested:
                wait_for_concurrent_close = True
            else:
                self._resource_close_requested = True
                self._resource_close_event.clear()

        if wait_for_concurrent_close:
            if not self._resource_close_event.wait(
                timeout=self.SHUTDOWN_IDLE_TIMEOUT_SECONDS
            ):
                raise TimeoutError("concurrent Supervisor shutdown did not finish")
            with self._resource_close_lock:
                if self._resources_closed:
                    return
            raise RuntimeError("concurrent Supervisor shutdown failed")

        try:
            cancel_active = getattr(
                self.workflow_engine,
                "cancel_active_workers",
                None,
            )
            if callable(cancel_active):
                cancel_active("supervisor_close")

            wait_for_idle = getattr(self.workflow_engine, "wait_for_idle", None)
            if callable(wait_for_idle) and not wait_for_idle(
                self.SHUTDOWN_IDLE_TIMEOUT_SECONDS
            ):
                # Never destructively close a dependency while a timed-out
                # native worker may still be using it.  The engine owns the
                # idle boundary and will run this idempotent finalizer as soon
                # as the last worker physically exits.
                defer_until_idle = getattr(
                    self.workflow_engine,
                    "defer_until_idle",
                    None,
                )
                if callable(defer_until_idle):
                    defer_until_idle(self._close_owned_resources)
                raise TimeoutError(
                    "workflow workers did not become idle before shutdown; "
                    "resource closure was deferred until idle"
                )

            failures = self._close_owned_resources()
            if failures:
                raise failures[0]
        finally:
            with self._resource_close_lock:
                if not self._resources_closed:
                    self._resource_close_requested = False
                self._resource_close_event.set()

    @property
    def shutdown_diagnostics(self) -> tuple[dict[str, Any], ...]:
        """Return structured best-effort shutdown failures for observability."""

        with self._resource_close_lock:
            return tuple(dict(item) for item in self._shutdown_diagnostics)

    def _close_owned_resources(self) -> tuple[Exception, ...]:
        """Idempotently close resources after the workflow engine is idle."""

        with self._resource_close_lock:
            if self._resources_closed:
                return ()

        seen: set[int] = set()
        diagnostics: list[dict[str, Any]] = []
        failures: list[Exception] = []
        for resource in reversed(self._owned_resources):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    diagnostic = {
                        "event_type": "owned_resource_close_failed",
                        "resource_type": type(resource).__name__,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                    diagnostics.append(diagnostic)
                    failures.append(exc)
                    logger.warning(
                        "owned resource close failed: resource_type=%s",
                        type(resource).__name__,
                        exc_info=True,
                    )

        with self._resource_close_lock:
            self._shutdown_diagnostics.extend(diagnostics)
            # A failed resource remains owned and may still be recovered by its
            # own finalizer.  Successful shutdown is published only when every
            # unique resource accepted close without an exception.
            self._resources_closed = not failures
        return tuple(failures)

    def inspect(
        self,
        *,
        task_id: str,
        run_id: str,
        task_type: str,
    ) -> dict[str, Any]:
        """Inspect an existing durable run without creating or routing a task."""

        if not self.workflow_engine.capabilities.inspect:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support inspect"
            )
        workflow = self.catalog.get(task_type)
        inspection = self.workflow_engine.inspect(workflow, run_id=run_id)
        graph_state = inspection.get("graph_state") or {}
        if graph_state and (
            graph_state.get("task_id") != task_id
            or graph_state.get("run_id") != run_id
        ):
            raise ValueError("checkpoint task/run identity mismatch")
        task_state = None
        if self.lifecycle.task_manager is not None:
            task_state = self.lifecycle.task_manager.get_task(task_id)
            if task_state.run_id != run_id or task_state.task_type != task_type:
                raise ValueError("task state identity does not match checkpoint")
        return {
            **inspection,
            "task_id": task_id,
            "task_state": (
                task_state.model_dump(mode="json")
                if task_state is not None
                else None
            ),
        }

    def _maybe_export_review_report(self, *, workflow, task, result) -> None:
        """Attach a derived file ref only after the Graph execution returned."""
        try:
            from apps.enterprise_document.services.agent_rag_v1.deterministic_review_report import (
                try_export_after_workflow,
            )
            reference = try_export_after_workflow(
                engine=self.workflow_engine, workflow=workflow, task=task, result=result,
            )
            if reference is not None:
                result.result["target_review_report"] = reference
        except Exception as exc:
            # Report failures have no Node, retry, quality or lifecycle authority.
            try:
                logger.warning("Deterministic review report unavailable: %s", type(exc).__name__)
            except Exception:
                pass

    def _result_from_continuation(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        state: GraphStateSchema,
        execution: Any,
        resumed: bool = True,
        routing_metadata: dict[str, Any] | None = None,
    ) -> AgentResultSchema:
        workflow_complete = bool(execution.metadata.get("workflow_complete"))
        failed_results = [
            item for item in execution.node_results if is_failure(item.status)
        ]
        final_error = next(
            (
                item.error
                for item in failed_results
                if item.error is not None
            ),
            execution.error,
        )
        state.status = execution.status
        state.context_bundle.runtime.status = execution.status
        model_usage = self.model_usage_snapshot()
        review_status = HumanReviewStatus(
            execution.metadata.get(
                "human_review_status",
                HumanReviewStatus.NOT_REQUIRED.value,
            )
        )
        raw_request = execution.metadata.get("human_review_request")
        raw_decision = execution.metadata.get("human_review_decision")
        result = AgentResultSchema(
            result_id=(
                (
                    f"result_{task.run_id}_supervisor_resume_"
                    f"{execution.metadata.get('resume_count', 0)}"
                )
                if resumed
                else f"result_{task.run_id}_supervisor"
            ),
            task_id=task.task_id,
            run_id=task.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=execution.status,
            result_type="workflow_result",
            result={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": workflow_complete,
                "routing": dict(routing_metadata or {}),
                "sub_agent_results": [
                    item.model_dump() for item in execution.node_results
                ],
                "shared_state": state.model_dump(),
                "workflow_execution": execution.model_dump(),
                "generated_draft": state.generated_draft,
                "rejected_artifact": state.rejected_artifact,
                "deliverable_final_result": state.deliverable_final_result,
                "model_usage": model_usage,
            },
            error=final_error,
            error_message=final_error.message if final_error else None,
            human_review_required=bool(
                execution.metadata.get("human_review_required", False)
            ),
            human_review_recommended=bool(
                execution.metadata.get("human_review_recommended", False)
            ),
            human_review_status=review_status,
            human_review_request=(
                HumanReviewRequestSchema.model_validate(raw_request)
                if raw_request
                else None
            ),
            human_review_decision=(
                HumanReviewDecisionSchema.model_validate(raw_decision)
                if raw_decision
                else None
            ),
            metadata={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": workflow_complete,
                "workflow_engine": execution.engine_name,
                "resumed": resumed,
                "resume_count": execution.metadata.get("resume_count", 0),
                "review_decision": (
                    raw_decision.get("decision")
                    if isinstance(raw_decision, dict)
                    else None
                ),
                "model_usage": model_usage,
                "human_review_status": review_status.value,
            },
        )

        self._maybe_export_review_report(workflow=workflow, task=task, result=result)
        return result

    def _continue_paused(
        self,
        task: TaskSchema,
        *,
        resume_value: dict[str, Any],
        cancel: bool,
    ) -> AgentResultSchema:
        workflow, inspection, state = self._prepare_paused_continuation(task)
        self._validate_human_review_binding(
            task=task,
            inspection=inspection,
            resume_value=resume_value,
            cancel=cancel,
        )

        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "resume": True,
                "decision": resume_value.get("decision"),
                "resume_count": inspection.get("resume_count", 0),
            },
        )
        with activate_span(run_span):
            self.lifecycle.mark_running(task)
            self.trace.record(
                task=task,
                workflow=workflow,
                event_type="workflow_resumed",
                status=ExecutionStatus.RUNNING,
                payload={
                    "decision": resume_value.get("decision"),
                    "feedback_present": bool(resume_value.get("feedback")),
                },
            )
            with activate_span(workflow_span):
                execution = (
                    self.workflow_engine.cancel(
                        workflow,
                        state,
                        reason=str(
                            resume_value.get("feedback")
                            or "workflow_cancelled"
                        ),
                    )
                    if cancel
                    else self.workflow_engine.resume(
                        workflow,
                        state,
                        resume_value,
                    )
                )
            result = self._result_from_continuation(
                task=task,
                workflow=workflow,
                state=state,
                execution=execution,
                resumed=True,
            )
            self.lifecycle.mark_terminal(task, result)
            self.trace.finish_workflow(
                task=task,
                workflow=workflow,
                handle=workflow_span,
                result=result,
            )
            self.trace.finish_run(
                task=task,
                component_name=self.agent_name,
                handle=run_span,
                result=result,
            )
            return result

    @staticmethod
    def _validate_human_review_binding(
        *,
        task: TaskSchema,
        inspection: dict[str, Any],
        resume_value: dict[str, Any],
        cancel: bool,
    ) -> None:
        """Reject stale decisions before the task lifecycle enters RUNNING."""

        if cancel:
            return
        if inspection.get("pause_purpose") == "BUDGET_AUTHORIZATION" or any(
            isinstance(item, dict) and isinstance(item.get("value"), dict)
            and item["value"].get("pause_purpose") == "BUDGET_AUTHORIZATION"
            for item in inspection.get("interrupts") or []
        ):
            raise ValueError("Budget authorization requires the explicit typed Budget command")
        expected_snapshot = ""
        for interrupt_item in inspection.get("interrupts") or []:
            value = (
                interrupt_item.get("value")
                if isinstance(interrupt_item, dict)
                else None
            )
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            expected_snapshot = str(
                value.get("input_snapshot_version")
                or metadata.get("input_snapshot_version")
                or ""
            )
            if expected_snapshot:
                break
        if not expected_snapshot:
            raise ValueError("durable human review snapshot is unavailable")
        if resume_value.get("task_id") != task.task_id:
            raise ValueError("human review task identity mismatch")
        if resume_value.get("run_id") != task.run_id:
            raise ValueError("human review run identity mismatch")
        provided_snapshot = resume_value.get("input_snapshot_version")
        if not isinstance(provided_snapshot, str) or not provided_snapshot:
            raise ValueError("human review input_snapshot_version is required")
        if provided_snapshot != expected_snapshot:
            raise ValueError("human review decision snapshot is stale")

    def _prepare_paused_continuation(
        self,
        task: TaskSchema,
    ) -> tuple[
        WorkflowDefinitionSchema,
        dict[str, Any],
        GraphStateSchema,
    ]:
        """Validate durable identity and return the original paused graph state."""

        workflow = self.catalog.get(task.task_type)
        inspection = self.inspect(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
        )
        task_state = inspection.get("task_state") or {}
        if task_state.get("status") != ExecutionStatus.PAUSED.value:
            raise ValueError("only a paused task can be resumed or cancelled")
        if not inspection.get("interrupts"):
            raise ValueError("paused task has no durable interrupt to continue")
        if not inspection.get("checkpoint_config"):
            raise RuntimeError("paused task has no durable checkpoint to continue")
        raw_state = inspection.get("graph_state")
        if not raw_state:
            raise RuntimeError("checkpoint inspection did not return graph state")
        state = GraphStateSchema.model_validate(raw_state)
        if (
            state.task_id != task.task_id
            or state.run_id != task.run_id
            or state.task_type != task.task_type
        ):
            raise ValueError("resume graph-state identity mismatch")
        return workflow, inspection, state

    def _stream_reconciliation_result(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        cause: BaseException,
        consumer_disconnected: bool,
        cancel_requested: bool,
    ) -> AgentResultSchema:
        """Reconcile a non-terminal stream with durable checkpoint truth."""

        inspection: dict[str, Any] = {}
        inspection_error: Exception | None = None
        try:
            inspection = self.workflow_engine.inspect(
                workflow,
                run_id=task.run_id,
            )
        except Exception as exc:
            inspection_error = exc
            logger.warning(
                "stream checkpoint reconciliation inspect failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )

        interrupts = list(inspection.get("interrupts") or [])
        raw_graph_state = inspection.get("graph_state") or {}
        checkpoint_status: ExecutionStatus | None = None
        if isinstance(raw_graph_state, dict) and raw_graph_state.get("status"):
            try:
                checkpoint_status = ExecutionStatus(raw_graph_state["status"])
            except ValueError:
                checkpoint_status = None

        manager = self.lifecycle.task_manager
        current_status = ExecutionStatus.RUNNING
        if manager is not None:
            current_status = manager.get_task(task.task_id).status

        if current_status != ExecutionStatus.RUNNING:
            reconciled_status = current_status
        elif interrupts:
            reconciled_status = ExecutionStatus.PAUSED
        elif checkpoint_status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            reconciled_status = checkpoint_status
        else:
            category = (
                FailureCategory.TRANSIENT
                if consumer_disconnected
                else classify_failure(cause)
            )
            if category == FailureCategory.CANCELLED:
                reconciled_status = ExecutionStatus.CANCELLED
            else:
                retryable = self.retry_policy.should_retry(category)
                reconciled_status = (
                    ExecutionStatus.RETRYABLE_FAILED
                    if retryable
                    else ExecutionStatus.FAILED
                )

        category = (
            FailureCategory.TRANSIENT
            if consumer_disconnected
            else classify_failure(cause)
        )
        retryable = reconciled_status == ExecutionStatus.RETRYABLE_FAILED
        error = None
        if reconciled_status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            error_category = (
                FailureCategory.CANCELLED
                if reconciled_status == ExecutionStatus.CANCELLED
                else category
            )
            error = self.error_factory.create(
                error_code=(
                    "STREAM_CONSUMER_DISCONNECTED"
                    if consumer_disconnected
                    else "WORKFLOW_STREAM_INTERRUPTED"
                ),
                error_type=type(cause).__name__,
                message=(
                    "workflow stream consumer disconnected before terminal delivery"
                    if consumer_disconnected
                    else str(cause) or "workflow stream interrupted"
                ),
                component=self.agent_name,
                recoverable=(
                    retryable or reconciled_status == ExecutionStatus.PAUSED
                ),
                retryable=retryable,
                failure_category=error_category,
                failed_node="workflow_stream",
                agent_name=self.agent_name,
                stack_trace=(
                    None
                    if consumer_disconnected
                    else "".join(
                        traceback.format_exception(
                            type(cause),
                            cause,
                            cause.__traceback__,
                        )
                    )
                ),
                details={
                    "consumer_disconnected": consumer_disconnected,
                    "cancel_requested": cancel_requested,
                    "checkpoint_status": (
                        checkpoint_status.value
                        if checkpoint_status is not None
                        else None
                    ),
                    "durable_interrupt_count": len(interrupts),
                    "inspection_error": (
                        type(inspection_error).__name__
                        if inspection_error is not None
                        else None
                    ),
                },
            )

        review_request = None
        if reconciled_status == ExecutionStatus.PAUSED and interrupts:
            raw_request = interrupts[0]
            if isinstance(raw_request, dict) and "value" in raw_request:
                raw_request = raw_request["value"]
            if isinstance(raw_request, dict):
                try:
                    review_request = HumanReviewRequestSchema.model_validate(
                        raw_request
                    )
                except ValueError:
                    review_request = None

        review_status = (
            HumanReviewStatus.PENDING
            if reconciled_status == ExecutionStatus.PAUSED
            else (
                HumanReviewStatus.CANCELLED
                if reconciled_status == ExecutionStatus.CANCELLED
                else HumanReviewStatus.NOT_REQUIRED
            )
        )
        review_decision = (
            HumanReviewDecisionSchema(
                decision=HumanReviewStatus.CANCELLED,
                feedback=str(cause) or "workflow_cancelled",
                metadata={"stream_reconciled": True},
            )
            if review_status == HumanReviewStatus.CANCELLED
            else None
        )
        result = AgentResultSchema(
            result_id=f"result_{task.run_id}_supervisor_stream_reconciled",
            task_id=task.task_id,
            run_id=task.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=reconciled_status,
            result_type="workflow_stream_reconciliation",
            result={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": reconciled_status
                in {ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS},
                "sub_agent_results": [],
                "model_usage": self.model_usage_snapshot(),
                "stream_reconciliation": {
                    "consumer_disconnected": consumer_disconnected,
                    "cancel_requested": cancel_requested,
                    "checkpoint_status": (
                        checkpoint_status.value
                        if checkpoint_status is not None
                        else None
                    ),
                    "durable_interrupt_count": len(interrupts),
                },
            },
            error=error,
            error_message=error.message if error is not None else None,
            human_review_required=(
                review_status == HumanReviewStatus.PENDING
            ),
            human_review_status=review_status,
            human_review_request=review_request,
            human_review_decision=review_decision,
            metadata={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "stream_reconciled": True,
                "consumer_disconnected": consumer_disconnected,
                "cancel_requested": cancel_requested,
            },
        )
        if current_status == ExecutionStatus.RUNNING:
            self.lifecycle.mark_terminal(task, result)
        return result

    def _record_stream_diagnostic(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        result: AgentResultSchema,
        consumer_disconnected: bool,
        cause: BaseException,
    ) -> None:
        try:
            self.trace.record(
                task=task,
                workflow=workflow,
                event_type=(
                    "stream_abandoned"
                    if consumer_disconnected
                    else "stream_interrupted"
                ),
                status=result.status,
                payload={
                    "consumer_disconnected": consumer_disconnected,
                    "error_type": type(cause).__name__,
                    "error_message": str(cause),
                    "reconciled_status": result.status.value,
                },
            )
        except Exception:
            logger.warning(
                "stream diagnostic trace write failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )

    def _finish_stream_traces(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        workflow_span: TraceSpanHandle,
        run_span: TraceSpanHandle,
        result: AgentResultSchema,
        primary_error: BaseException | None,
    ) -> None:
        finish_error: Exception | None = None
        try:
            self.trace.finish_workflow(
                task=task,
                workflow=workflow,
                handle=workflow_span,
                result=result,
            )
        except Exception as exc:
            finish_error = exc
            logger.warning(
                "workflow trace finalization failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )
        try:
            self.trace.finish_run(
                task=task,
                component_name=self.agent_name,
                handle=run_span,
                result=result,
            )
        except Exception as exc:
            if finish_error is None:
                finish_error = exc
            logger.warning(
                "run trace finalization failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )
        if finish_error is not None and primary_error is None:
            raise finish_error

    def _stream_with_lifecycle(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        state: GraphStateSchema,
        stream_factory: Callable[[], Iterable[WorkflowStreamEventSchema]],
        run_span: TraceSpanHandle,
        workflow_span: TraceSpanHandle,
        resumed: bool,
        routing_metadata: dict[str, Any] | None = None,
        continuation_payload: dict[str, Any] | None = None,
        cancel_requested: bool = False,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Own task, checkpoint reconciliation and trace closure for a stream."""

        final_result: AgentResultSchema | None = None
        trace_result: AgentResultSchema | None = None
        primary_error: BaseException | None = None
        with activate_span(run_span):
            try:
                self.lifecycle.mark_running(task)
                if resumed:
                    self.trace.record(
                        task=task,
                        workflow=workflow,
                        event_type="workflow_resumed",
                        status=ExecutionStatus.RUNNING,
                        payload=dict(continuation_payload or {}),
                    )
                with activate_span(workflow_span):
                    for stream_event in stream_factory():
                        raw_execution = stream_event.payload.get("result")
                        if raw_execution is None:
                            yield stream_event
                            continue
                        if final_result is not None:
                            raise RuntimeError(
                                "workflow stream emitted multiple terminal results"
                            )
                        execution = WorkflowEngineResultSchema.model_validate(
                            raw_execution
                        )
                        final_result = self._result_from_continuation(
                            task=task,
                            workflow=workflow,
                            state=state,
                            execution=execution,
                            resumed=resumed,
                            routing_metadata=routing_metadata,
                        )
                        self.lifecycle.mark_terminal(task, final_result)
                        trace_result = final_result
                        yield stream_event.model_copy(
                            update={
                                "payload": {
                                    **stream_event.payload,
                                    "workflow_engine_result": raw_execution,
                                    "result": final_result.model_dump(mode="json"),
                                }
                            }
                        )
                if final_result is None:
                    raise RuntimeError(
                        "workflow stream ended without a terminal result"
                    )
            except GeneratorExit as exc:
                primary_error = exc
                if final_result is None:
                    trace_result = self._stream_reconciliation_result(
                        task=task,
                        workflow=workflow,
                        cause=exc,
                        consumer_disconnected=True,
                        cancel_requested=cancel_requested,
                    )
                    self._record_stream_diagnostic(
                        task=task,
                        workflow=workflow,
                        result=trace_result,
                        consumer_disconnected=True,
                        cause=exc,
                    )
                raise
            except BaseException as exc:
                primary_error = exc
                if final_result is None:
                    trace_result = self._stream_reconciliation_result(
                        task=task,
                        workflow=workflow,
                        cause=exc,
                        consumer_disconnected=False,
                        cancel_requested=cancel_requested,
                    )
                    self._record_stream_diagnostic(
                        task=task,
                        workflow=workflow,
                        result=trace_result,
                        consumer_disconnected=False,
                        cause=exc,
                    )
                raise
            finally:
                if trace_result is not None:
                    self._finish_stream_traces(
                        task=task,
                        workflow=workflow,
                        workflow_span=workflow_span,
                        run_span=run_span,
                        result=trace_result,
                        primary_error=primary_error,
                    )

    def _stream_continue_paused(
        self,
        task: TaskSchema,
        *,
        resume_value: dict[str, Any],
        cancel: bool,
        runnable_config: dict[str, Any] | None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream one validated durable continuation through the stable protocol."""

        if not self.workflow_engine.capabilities.stream_events:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support streaming"
            )
        workflow, inspection, state = self._prepare_paused_continuation(task)
        self._validate_human_review_binding(
            task=task,
            inspection=inspection,
            resume_value=resume_value,
            cancel=cancel,
        )
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "resume": True,
                "decision": resume_value.get("decision"),
                "resume_count": inspection.get("resume_count", 0),
                "stream": True,
            },
        )
        def stream_factory() -> Iterable[WorkflowStreamEventSchema]:
            if cancel:
                cancel_active = getattr(
                    self.workflow_engine,
                    "cancel_active_workers",
                    None,
                )
                if callable(cancel_active):
                    cancel_active(
                        str(
                            resume_value.get("feedback")
                            or "workflow_cancelled"
                        )
                    )
            return self.workflow_engine.stream_events(
                workflow,
                state,
                resume_value=resume_value,
                runnable_config=runnable_config,
            )

        yield from self._stream_with_lifecycle(
            task=task,
            workflow=workflow,
            state=state,
            stream_factory=stream_factory,
            run_span=run_span,
            workflow_span=workflow_span,
            resumed=True,
            continuation_payload={
                "decision": resume_value.get("decision"),
                "feedback_present": bool(resume_value.get("feedback")),
            },
            cancel_requested=cancel,
        )

    def resume(
        self,
        task: TaskSchema,
        *,
        decision: str,
        input_snapshot_version: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
    ) -> AgentResultSchema:
        """Resume the original task/run from its durable human-review interrupt."""

        normalized = str(decision).strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("resume decision must be approved or rejected")
        if (
            not isinstance(input_snapshot_version, str)
            or not input_snapshot_version.strip()
        ):
            raise ValueError("input_snapshot_version is required")
        return self._continue_paused(
            task,
            resume_value={
                "decision": normalized,
                "task_id": task.task_id,
                "run_id": task.run_id,
                "input_snapshot_version": input_snapshot_version,
                "feedback": feedback,
                "reviewer_id": reviewer_id,
            },
            cancel=False,
        )

    def cancel(
        self,
        task: TaskSchema,
        *,
        reason: str = "workflow_cancelled",
    ) -> AgentResultSchema:
        """Cancel a paused durable run through the rejected-artifact path."""

        return self._continue_paused(
            task,
            resume_value={
                "decision": HumanReviewStatus.CANCELLED.value,
                "feedback": reason,
            },
            cancel=True,
        )

    def recovery_plan(self, task: TaskSchema) -> dict[str, Any]:
        """Preflight durable recovery without mutating task or checkpoint state."""

        workflow = self.catalog.get(task.task_type)
        plan = getattr(self.workflow_engine, "recovery_plan", None)
        if not callable(plan):
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support recovery"
            )
        return dict(plan(workflow, run_id=task.run_id))

    def recover(
        self,
        task: TaskSchema,
        *,
        recovery_attempt: int,
        recovery_reason: str,
        recovery_policy: str,
    ) -> AgentResultSchema:
        """Continue an authorized durable checkpoint after its CAS event."""

        workflow = self.catalog.get(task.task_type)
        inspection = self.inspect(task_id=task.task_id,run_id=task.run_id,task_type=task.task_type)
        if inspection.get("pause_purpose") == "BUDGET_AUTHORIZATION" or any(
            isinstance(item, dict) and isinstance(item.get("value"), dict)
            and item["value"].get("pause_purpose") == "BUDGET_AUTHORIZATION"
            for item in inspection.get("interrupts") or []
        ):
            # Reject before the old recovery exception/Task failure projector.
            raise ValueError("Budget authorization requires the explicit typed Budget command")
        recover = getattr(self.workflow_engine, "recover", None)
        if not callable(recover):
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support recovery"
            )

        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "recovery": True,
                "recovery_attempt": recovery_attempt,
                "recovery_reason": recovery_reason,
                "recovery_policy": recovery_policy,
            },
        )
        with activate_span(run_span):
            try:
                inspection = self.inspect(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    task_type=task.task_type,
                )
                if inspection.get("interrupts"):
                    raise ValueError(
                        "human review is waiting; inspect the run and use resume_run"
                    )
                raw_state = inspection.get("graph_state")
                if not isinstance(raw_state, dict):
                    raise RuntimeError(
                        "durable checkpoint does not contain graph state"
                    )
                state = GraphStateSchema.model_validate(raw_state)
                with activate_span(workflow_span):
                    execution = recover(
                        workflow,
                        state,
                        recovery_attempt=recovery_attempt,
                        recovery_reason=recovery_reason,
                        recovery_policy=recovery_policy,
                    )
                result = self._result_from_continuation(
                    task=task,
                    workflow=workflow,
                    state=state,
                    execution=execution,
                    resumed=True,
                )
                result = result.model_copy(
                    update={
                        "metadata": {
                            **dict(result.metadata),
                            "recovered": True,
                            "recovery_attempt": recovery_attempt,
                            "recovery_reason": recovery_reason,
                            "recovery_policy": recovery_policy,
                        }
                    },
                    deep=True,
                )
            except Exception as exc:
                failure_category = classify_failure(exc)
                retryable = self.retry_policy.should_retry(failure_category)
                error = self.error_factory.create(
                    error_code="WORKFLOW_RECOVERY_FAILED",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    user_visible_message="任务恢复失败。",
                    recoverable=retryable,
                    retryable=retryable,
                    failure_category=failure_category,
                    failed_node=self.agent_name,
                    component=self.agent_name,
                    agent_name=self.agent_name,
                    stack_trace=traceback.format_exc(),
                )
                result = AgentResultSchema(
                    result_id=(
                        f"result_{task.run_id}_supervisor_recovery_"
                        f"{recovery_attempt}_failed"
                    ),
                    task_id=task.task_id,
                    run_id=task.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=(
                        ExecutionStatus.RETRYABLE_FAILED
                        if retryable
                        else ExecutionStatus.FAILED
                    ),
                    result_type="workflow_result",
                    result={"model_usage": self.model_usage_snapshot()},
                    error=error,
                    error_message=error.message,
                    human_review_required=False,
                    human_review_recommended=bool(error.requires_human),
                    human_review_status=HumanReviewStatus.NOT_REQUIRED,
                    metadata={
                        "recovered": True,
                        "recovery_attempt": recovery_attempt,
                        "recovery_reason": recovery_reason,
                        "recovery_policy": recovery_policy,
                    },
                )
            self.lifecycle.mark_terminal(task, result)
            self.trace.finish_workflow(
                task=task,
                workflow=workflow,
                handle=workflow_span,
                result=result,
            )
            self.trace.finish_run(
                task=task,
                component_name=self.agent_name,
                handle=run_span,
                result=result,
            )
            return result

    def stream_resume(
        self,
        task: TaskSchema,
        *,
        decision: str,
        input_snapshot_version: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream approval or rejection of the original paused task/run."""

        normalized = str(decision).strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("resume decision must be approved or rejected")
        if (
            not isinstance(input_snapshot_version, str)
            or not input_snapshot_version.strip()
        ):
            raise ValueError("input_snapshot_version is required")
        return self._stream_continue_paused(
            task,
            resume_value={
                "decision": normalized,
                "task_id": task.task_id,
                "run_id": task.run_id,
                "input_snapshot_version": input_snapshot_version,
                "feedback": feedback,
                "reviewer_id": reviewer_id,
            },
            cancel=False,
            runnable_config=runnable_config,
        )

    def stream_cancel(
        self,
        task: TaskSchema,
        *,
        reason: str = "workflow_cancelled",
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream cancellation through the original durable interrupt."""

        return self._stream_continue_paused(
            task,
            resume_value={
                "decision": HumanReviewStatus.CANCELLED.value,
                "feedback": reason,
            },
            cancel=True,
            runnable_config=runnable_config,
        )

    def stream(
        self,
        task: TaskSchema,
        *,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream one initial run through the application-owned event protocol.

        LangGraph's raw stream records remain inside the workflow engine.  The
        final event carries the same ``AgentResultSchema`` boundary returned by
        :meth:`run`, so callers never have to infer completion from log text.
        """

        if not self.workflow_engine.capabilities.stream_events:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support streaming"
            )
        routing = self.router.route(task)
        workflow = self.catalog.get(routing.task_type)
        context_bundle = self.context_factory.build(task)
        state = GraphStateSchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            user_input=task.user_input,
            tenant_id=task.tenant_id,
            task=task.model_dump(),
            workflow=workflow.model_dump(),
            requirements={
                **task.generation_requirements,
                "project_input": task.project_input,
            },
            context_bundle=context_bundle,
            created_at=task.created_at,
            updated_at=task.updated_at or task.created_at,
            extra={
                "routing": routing.metadata,
                "supervisor_model_response": (
                    routing.model_response.model_dump()
                    if routing.model_response
                    else None
                ),
            },
        )
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "task": task.model_dump(),
                "workflow": workflow.model_dump(),
                "routing": routing.metadata,
                "stream": True,
            },
        )
        yield from self._stream_with_lifecycle(
            task=task,
            workflow=workflow,
            state=state,
            stream_factory=lambda: self.workflow_engine.stream_events(
                workflow,
                state,
                runnable_config=runnable_config,
            ),
            run_span=run_span,
            workflow_span=workflow_span,
            resumed=False,
            routing_metadata=routing.metadata,
        )

    def run(self, task: TaskSchema) -> AgentResultSchema:
        """Execute the selected workflow and own its lifecycle and trace."""

        workflow: Optional[WorkflowDefinitionSchema] = None
        workflow_span: Optional[TraceSpanHandle] = None
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)

        with activate_span(run_span):
            try:
                routing = self.router.route(task)
                workflow = self.catalog.get(routing.task_type)
                self.lifecycle.mark_running(task)
                context_bundle = self.context_factory.build(task)
                state = GraphStateSchema(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    task_type=task.task_type,
                    user_input=task.user_input,
                    tenant_id=task.tenant_id,
                    task=task.model_dump(),
                    workflow=workflow.model_dump(),
                    requirements={
                        **task.generation_requirements,
                        "project_input": task.project_input,
                    },
                    context_bundle=context_bundle,
                    created_at=task.created_at,
                    updated_at=task.updated_at or task.created_at,
                    extra={
                        "routing": routing.metadata,
                        "supervisor_model_response": (
                            routing.model_response.model_dump()
                            if routing.model_response
                            else None
                        ),
                    },
                )
                workflow_span = self.trace.start_workflow(
                    task=task,
                    workflow=workflow,
                    payload={
                        "task": task.model_dump(),
                        "workflow": workflow.model_dump(),
                        "routing": routing.metadata,
                    },
                )

                with activate_span(workflow_span):
                    execution = self.workflow_engine.execute(workflow, state)

                workflow_complete = bool(
                    execution.metadata.get("workflow_complete")
                )
                failed_results = [
                    item for item in execution.node_results if is_failure(item.status)
                ]
                final_status = execution.status
                final_error = next(
                    (
                        item.error
                        for item in failed_results
                        if item.error is not None
                    ),
                    None,
                )
                if (
                    is_failure(final_status)
                    and not workflow_complete
                    and final_error is None
                ):
                    final_error = self.error_factory.create(
                        error_code="WORKFLOW_INCOMPLETE",
                        error_type="WorkflowStateError",
                        message="Workflow 节点未完整结束。",
                        recoverable=False,
                        retryable=False,
                        failure_category=FailureCategory.VALIDATION,
                        failed_node=state.current_step,
                        component=self.agent_name,
                        agent_name=self.agent_name,
                        step_name=state.current_step,
                    )

                state.status = final_status
                state.context_bundle.runtime.status = final_status
                model_usage = self.model_usage_snapshot()
                review_required = bool(
                    execution.metadata.get("human_review_required", False)
                )
                review_recommended = bool(
                    execution.metadata.get("human_review_recommended", False)
                )
                review_status = HumanReviewStatus(
                    execution.metadata.get(
                        "human_review_status",
                        (
                            HumanReviewStatus.PENDING.value
                            if final_status == ExecutionStatus.PAUSED
                            else HumanReviewStatus.NOT_REQUIRED.value
                        ),
                    )
                )
                raw_review_request = execution.metadata.get(
                    "human_review_request"
                )
                review_request = (
                    HumanReviewRequestSchema.model_validate(raw_review_request)
                    if raw_review_request
                    else None
                )
                raw_review_decision = execution.metadata.get(
                    "human_review_decision"
                )
                review_decision = (
                    HumanReviewDecisionSchema.model_validate(raw_review_decision)
                    if raw_review_decision
                    else None
                )
                result = AgentResultSchema(
                    result_id=f"result_{task.run_id}_supervisor",
                    task_id=task.task_id,
                    run_id=task.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=final_status,
                    result_type="workflow_result",
                    result={
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.workflow_version,
                        "workflow_complete": workflow_complete,
                        "routing": routing.metadata,
                        "sub_agent_results": [
                            item.model_dump() for item in execution.node_results
                        ],
                        "shared_state": state.model_dump(),
                        "workflow_execution": execution.model_dump(),
                        "generated_draft": state.generated_draft,
                        "rejected_artifact": state.rejected_artifact,
                        "deliverable_final_result": state.deliverable_final_result,
                        "model_usage": model_usage,
                    },
                    error=final_error,
                    error_message=final_error.message if final_error else None,
                    human_review_required=review_required,
                    human_review_recommended=review_recommended,
                    human_review_status=review_status,
                    human_review_request=review_request,
                    human_review_decision=review_decision,
                    metadata={
                        "routing_mode": routing.metadata.get("routing_mode"),
                        "selected_task_type": routing.metadata.get(
                            "selected_task_type"
                        ),
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.workflow_version,
                        "workflow_complete": workflow_complete,
                        "workflow_engine": execution.engine_name,
                        "graph_state_schema": state.schema_version,
                        "graph_revision": state.graph_revision,
                        "model_usage": model_usage,
                        "human_review_required": review_required,
                        "human_review_recommended": review_recommended,
                        "human_review_status": review_status.value,
                    },
                )
                self.lifecycle.mark_terminal(task, result)
                self.trace.finish_workflow(
                    task=task,
                    workflow=workflow,
                    handle=workflow_span,
                    result=result,
                )
                self.trace.finish_run(
                    task=task,
                    component_name=self.agent_name,
                    handle=run_span,
                    result=result,
                )
                self._maybe_export_review_report(workflow=workflow, task=task, result=result)
                return result
            except Exception as exc:
                model_usage = self.model_usage_snapshot()
                failure_category = classify_failure(exc)
                retryable = self.retry_policy.should_retry(failure_category)
                error = self.error_factory.create(
                    error_code="SUPERVISOR_EXECUTION_FAILED",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    user_visible_message="任务主流程执行失败。",
                    recoverable=retryable,
                    retryable=retryable,
                    failure_category=failure_category,
                    failed_node=self.agent_name,
                    component=self.agent_name,
                    agent_name=self.agent_name,
                    stack_trace=traceback.format_exc(),
                )
                result = AgentResultSchema(
                    result_id=f"result_{task.run_id}_supervisor_failed",
                    task_id=task.task_id,
                    run_id=task.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=(
                        ExecutionStatus.RETRYABLE_FAILED
                        if retryable
                        else ExecutionStatus.FAILED
                    ),
                    result_type="workflow_result",
                    result={"model_usage": model_usage},
                    error=error,
                    error_message=error.message,
                    human_review_required=False,
                    human_review_recommended=bool(error.requires_human),
                    human_review_status=HumanReviewStatus.NOT_REQUIRED,
                    metadata={"model_usage": model_usage},
                )
                self.lifecycle.mark_terminal(task, result)
                if workflow is not None and workflow_span is not None:
                    self.trace.finish_workflow(
                        task=task,
                        workflow=workflow,
                        handle=workflow_span,
                        result=result,
                    )
                self.trace.finish_run(
                    task=task,
                    component_name=self.agent_name,
                    handle=run_span,
                    result=result,
                )
                if workflow is not None:
                    self._maybe_export_review_report(workflow=workflow, task=task, result=result)
                return result
