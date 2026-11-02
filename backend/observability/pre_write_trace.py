"""Required Gate V2 pre-write Trace projections.

The emitter is intentionally stateless and non-authoritative: callers provide
already-produced canonical refs, hashes, enum/status projections and technical
occurrence data.  This module never evaluates Evidence, Admission, Binding
applicability, Recovery progress, Routing, Fallback eligibility, or Writer
authorization.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
import json
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionAssessmentState,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionOutcome,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
    RecoveryControlSnapshotRefSchema,
    RecoveryExhaustionReason,
    RecoveryRoundOutcome,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionStatus,
    WriterGenerationClass,
    WriterLogicalExecutionKey,
)
from contracts.observability import TraceSink
from schemas.trace import TraceCorrelationSchema


PRE_WRITE_ADMISSION_DECISION_EVENT = "pre_write_admission_decision_v1"
PRE_WRITE_BINDING_COMMITTED_EVENT = "pre_write_binding_committed_v1"
PRE_WRITE_BINDING_APPLICABILITY_EVENT = "pre_write_binding_applicability_v1"
PRE_WRITE_ENFORCEMENT_EVENT = "pre_write_enforcement_v1"
PRE_WRITE_RECOVERY_EVENT = "pre_write_recovery_v1"
PRE_WRITE_EVIDENCE_PROMOTION_EVENT = "pre_write_evidence_promotion_v1"
PRE_WRITE_ROUTE_DECISION_EVENT = "pre_write_route_decision_v1"
PRE_WRITE_WRITER_EXECUTION_EVENT = "pre_write_writer_execution_v1"

REQUIRED_PRE_WRITE_EVENT_TYPES = frozenset(
    {
        PRE_WRITE_ADMISSION_DECISION_EVENT,
        PRE_WRITE_BINDING_COMMITTED_EVENT,
        PRE_WRITE_BINDING_APPLICABILITY_EVENT,
        PRE_WRITE_ENFORCEMENT_EVENT,
        PRE_WRITE_RECOVERY_EVENT,
        PRE_WRITE_EVIDENCE_PROMOTION_EVENT,
        PRE_WRITE_ROUTE_DECISION_EVENT,
        PRE_WRITE_WRITER_EXECUTION_EVENT,
    }
)

_MAX_REQUIRED_PAYLOAD_BYTES = 32 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TypedProjection = TypeVar("_TypedProjection", bound=BaseModel)


def _project(value: Any) -> Any:
    """Convert already-produced typed values into a JSON-compatible projection."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return _project(value.value)
    if isinstance(value, Mapping):
        return {str(key): _project(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_project(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        "required pre-write Trace accepts only already-produced JSON/typed projections; "
        f"unsupported value type: {type(value).__name__}"
    )


def _require_typed_ref(
    value: _TypedProjection,
    expected_type: type[_TypedProjection],
    *,
    field_name: str,
) -> dict[str, Any]:
    """Project an already-produced typed ref without manufacturing missing versions."""

    if not isinstance(value, expected_type):
        raise TypeError(
            f"{field_name} must be an already-produced {expected_type.__name__}; "
            f"got {type(value).__name__}"
        )
    projected = _project(value)
    assert isinstance(projected, dict)
    return projected


def _optional_typed_ref(
    value: _TypedProjection | None,
    expected_type: type[_TypedProjection],
    *,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_typed_ref(value, expected_type, field_name=field_name)


def _require_sha256(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be lowercase sha256")
    return normalized


def _evidence_trace_ref(value: Mapping[str, Any], *, field_name: str) -> dict[str, str]:
    """Validate a compact already-produced Evidence identity projection.

    EvidenceBundle currently has a schema version but no standalone canonical ref
    model.  Trace therefore requires the producer to supply both schema version
    and the externally computed canonical SHA-256; it will not infer either.
    """

    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping with explicit Evidence identity")
    expected_keys = {"evidence_bundle_schema_version", "evidence_bundle_sha256"}
    if set(value) != expected_keys:
        raise ValueError(
            f"{field_name} must contain exactly {sorted(expected_keys)}; "
            "Trace will not fabricate an Evidence version/ref"
        )
    schema_version = str(value["evidence_bundle_schema_version"]).strip()
    sha256 = _require_sha256(
        str(value["evidence_bundle_sha256"]),
        field_name=f"{field_name}.evidence_bundle_sha256",
    )
    if not schema_version:
        raise ValueError(f"{field_name}.evidence_bundle_schema_version must be non-empty")
    return {
        "evidence_bundle_schema_version": schema_version,
        "evidence_bundle_sha256": sha256,
    }


def _require_lifecycle_phase(phase: str) -> Literal["start", "end", "error"]:
    if phase not in {"start", "end", "error"}:
        raise ValueError("required lifecycle Trace phase must be start, end, or error")
    return phase  # type: ignore[return-value]


def _bounded_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = _project(payload)
    if not isinstance(projected, dict):
        raise TypeError("required pre-write Trace payload must be an object")
    serialized = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > _MAX_REQUIRED_PAYLOAD_BYTES:
        raise ValueError("required pre-write Trace payload exceeds bounded projection limit")
    return projected


class PreWriteTraceEmitter:
    """Emit the eight frozen required Gate control observations."""

    def __init__(self, trace_sink: TraceSink, *, trace_enabled: bool = True) -> None:
        self.trace_sink = trace_sink
        # This flag governs optional diagnostics only. Required methods below
        # deliberately never branch on it.
        self.trace_enabled = bool(trace_enabled)

    def _emit(
        self,
        *,
        task_id: str,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        phase: str = "event",
        trace_id: str | None = None,
        call_id: str | None = None,
        status: str | None = None,
        correlation: TraceCorrelationSchema | None = None,
    ) -> Any:
        if event_type not in REQUIRED_PRE_WRITE_EVENT_TYPES:
            raise ValueError(f"unsupported required pre-write event type: {event_type}")
        return self.trace_sink.record(
            task_id=task_id,
            run_id=run_id,
            event_type=event_type,
            component_type="gate",
            component_name="pre_write_gate_v2",
            payload=_bounded_payload(payload),
            phase=phase,
            trace_id=trace_id,
            call_id=call_id,
            status=status,
            correlation=correlation or TraceCorrelationSchema(),
            tags=["gate_v2", "pre_write", "required_control_trace"],
        )

    def emit_admission_decision(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        active_evidence_bundle_sha256: str,
        fact_assessment_ref: WriterAdmissionFactAssessmentRefSchema,
        decision_ref: WriterAdmissionDecisionRefSchema,
        assessment_lifecycle_state: WriterAdmissionAssessmentState,
        admission_outcome: WriterAdmissionOutcome | None,
        trace_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_ADMISSION_DECISION_EVENT,
            trace_id=trace_id,
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "active_evidence_bundle_sha256": _require_sha256(
                    active_evidence_bundle_sha256,
                    field_name="active_evidence_bundle_sha256",
                ),
                "fact_assessment_ref": _require_typed_ref(
                    fact_assessment_ref,
                    WriterAdmissionFactAssessmentRefSchema,
                    field_name="fact_assessment_ref",
                ),
                "decision_ref": _require_typed_ref(
                    decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="decision_ref",
                ),
                "assessment_lifecycle_state": assessment_lifecycle_state,
                "admission_outcome": admission_outcome,
            },
        )

    def emit_binding_committed(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision_ref: WriterAdmissionDecisionRefSchema,
        binding_ref: WriterGateBindingRefSchema,
        trace_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_BINDING_COMMITTED_EVENT,
            trace_id=trace_id,
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "decision_ref": _require_typed_ref(
                    decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="decision_ref",
                ),
                "binding_ref": _require_typed_ref(
                    binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="binding_ref",
                ),
            },
        )

    def emit_binding_applicability(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        binding_ref: WriterGateBindingRefSchema,
        binding_applicability_state: BindingApplicabilityState,
        resolved_decision_ref: WriterAdmissionDecisionRefSchema | None = None,
        technical_diagnostic_code: str | None = None,
        technical_diagnostic_ref: Any | None = None,
        trace_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_BINDING_APPLICABILITY_EVENT,
            trace_id=trace_id,
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "binding_ref": _require_typed_ref(
                    binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="binding_ref",
                ),
                "binding_applicability_state": binding_applicability_state,
                "resolved_decision_ref": _optional_typed_ref(
                    resolved_decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="resolved_decision_ref",
                ),
                "technical_diagnostic_code": technical_diagnostic_code,
                "technical_diagnostic_ref": technical_diagnostic_ref,
            },
        )

    def emit_enforcement(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        binding_applicability_state: BindingApplicabilityState,
        effective_enforcement_mode: Literal["strict", "shadow"],
        enforcement_basis: EnforcementBasis,
        runtime_config_contract_version: str,
        decision_ref: WriterAdmissionDecisionRefSchema | None = None,
        binding_ref: WriterGateBindingRefSchema | None = None,
        action: EnforcementAction | None = None,
        would_block_in_strict: bool | None = None,
        technical_diagnostic_code: str | None = None,
        technical_diagnostic_ref: Any | None = None,
        trace_id: str | None = None,
    ) -> Any:
        if effective_enforcement_mode not in {"strict", "shadow"}:
            raise ValueError("effective_enforcement_mode must be strict or shadow")
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_ENFORCEMENT_EVENT,
            trace_id=trace_id,
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "decision_ref": _optional_typed_ref(
                    decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="decision_ref",
                ),
                "binding_ref": _optional_typed_ref(
                    binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="binding_ref",
                ),
                "binding_applicability_state": binding_applicability_state,
                "effective_enforcement_mode": effective_enforcement_mode,
                "enforcement_basis": enforcement_basis,
                "action": action,
                "would_block_in_strict": would_block_in_strict,
                "runtime_config_contract_version": runtime_config_contract_version,
                "technical_diagnostic_code": technical_diagnostic_code,
                "technical_diagnostic_ref": technical_diagnostic_ref,
            },
        )

    def emit_recovery(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision_ref: WriterAdmissionDecisionRefSchema,
        binding_ref: WriterGateBindingRefSchema,
        fact_assessment_ref: WriterAdmissionFactAssessmentRefSchema,
        requirements_sha256: str,
        recovery_state_revision: int,
        recovery_rounds_used: int,
        recovery_rounds_limit: int,
        consecutive_no_progress_rounds: int,
        no_progress_rounds_limit: int,
        recovery_config_contract_version: str,
        logical_dispatch_occurrence: Any,
        phase: Literal["start", "end", "error"],
        last_round_outcome: RecoveryRoundOutcome | None = None,
        exhaustion_state: RecoveryExhaustionReason | None = None,
        technical_diagnostic: Any | None = None,
        trace_id: str | None = None,
        call_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_RECOVERY_EVENT,
            phase=_require_lifecycle_phase(phase),
            trace_id=trace_id,
            call_id=call_id,
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "decision_ref": _require_typed_ref(
                    decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="decision_ref",
                ),
                "binding_ref": _require_typed_ref(
                    binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="binding_ref",
                ),
                "fact_assessment_ref": _require_typed_ref(
                    fact_assessment_ref,
                    WriterAdmissionFactAssessmentRefSchema,
                    field_name="fact_assessment_ref",
                ),
                "requirements_sha256": _require_sha256(
                    requirements_sha256,
                    field_name="requirements_sha256",
                ),
                "recovery_state_revision": recovery_state_revision,
                "recovery_rounds_used": recovery_rounds_used,
                "recovery_rounds_limit": recovery_rounds_limit,
                "consecutive_no_progress_rounds": consecutive_no_progress_rounds,
                "no_progress_rounds_limit": no_progress_rounds_limit,
                "recovery_config_contract_version": recovery_config_contract_version,
                "logical_dispatch_occurrence": logical_dispatch_occurrence,
                "last_round_outcome": last_round_outcome,
                "exhaustion_state": exhaustion_state,
                "technical_diagnostic": technical_diagnostic,
            },
        )

    def emit_evidence_promotion(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        previous_active_evidence_ref: Mapping[str, Any],
        new_active_evidence_ref: Mapping[str, Any],
        new_fact_assessment_ref: WriterAdmissionFactAssessmentRefSchema,
        new_decision_ref: WriterAdmissionDecisionRefSchema,
        new_binding_ref: WriterGateBindingRefSchema,
        recovery_state_revision: int,
        trace_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_EVIDENCE_PROMOTION_EVENT,
            trace_id=trace_id,
            correlation=TraceCorrelationSchema(
                evidence_refs=[
                    "evidence_bundle_sha256:"
                    + _evidence_trace_ref(
                        previous_active_evidence_ref,
                        field_name="previous_active_evidence_ref",
                    )["evidence_bundle_sha256"],
                    "evidence_bundle_sha256:"
                    + _evidence_trace_ref(
                        new_active_evidence_ref,
                        field_name="new_active_evidence_ref",
                    )["evidence_bundle_sha256"],
                ],
            ),
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "previous_active_evidence_ref": _evidence_trace_ref(
                    previous_active_evidence_ref,
                    field_name="previous_active_evidence_ref",
                ),
                "new_active_evidence_ref": _evidence_trace_ref(
                    new_active_evidence_ref,
                    field_name="new_active_evidence_ref",
                ),
                "new_fact_assessment_ref": _require_typed_ref(
                    new_fact_assessment_ref,
                    WriterAdmissionFactAssessmentRefSchema,
                    field_name="new_fact_assessment_ref",
                ),
                "new_decision_ref": _require_typed_ref(
                    new_decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="new_decision_ref",
                ),
                "new_binding_ref": _require_typed_ref(
                    new_binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="new_binding_ref",
                ),
                "recovery_state_revision": recovery_state_revision,
            },
        )

    def emit_route_decision(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        route_decision_ref: PreWriteRouteDecisionRefSchema,
        decision_ref: WriterAdmissionDecisionRefSchema,
        binding_ref: WriterGateBindingRefSchema,
        selected_route: PreWriteRoute,
        recovery_state_revision: int | None = None,
        recovery_control_facts_ref: RecoveryControlSnapshotRefSchema | None = None,
        trace_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_ROUTE_DECISION_EVENT,
            trace_id=trace_id,
            correlation=TraceCorrelationSchema(
                prewrite_route_decision_ref=_require_typed_ref(
                    route_decision_ref,
                    PreWriteRouteDecisionRefSchema,
                    field_name="route_decision_ref",
                ),
                decision_refs=[route_decision_ref.route_decision_id],
            ),
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "route_decision_ref": _require_typed_ref(
                    route_decision_ref,
                    PreWriteRouteDecisionRefSchema,
                    field_name="route_decision_ref",
                ),
                "decision_ref": _require_typed_ref(
                    decision_ref,
                    WriterAdmissionDecisionRefSchema,
                    field_name="decision_ref",
                ),
                "binding_ref": _require_typed_ref(
                    binding_ref,
                    WriterGateBindingRefSchema,
                    field_name="binding_ref",
                ),
                "recovery_state_revision": recovery_state_revision,
                "recovery_control_facts_ref": _optional_typed_ref(
                    recovery_control_facts_ref,
                    RecoveryControlSnapshotRefSchema,
                    field_name="recovery_control_facts_ref",
                ),
                "selected_route": selected_route,
            },
        )

    def emit_writer_execution(
        self,
        *,
        task_id: str,
        run_id: str,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        route_decision_ref: PreWriteRouteDecisionRefSchema,
        generation_class: WriterGenerationClass,
        evidence_bundle_sha256: str,
        context_package_id: str,
        context_sha256: str,
        writer_logical_execution_key: WriterLogicalExecutionKey,
        phase: Literal["start", "end", "error"],
        writer_status: WriterExecutionStatus | None = None,
        failure_kind: WriterExecutionFailureKind | None = None,
        artifact_ref: Any | None = None,
        model_call_ids: Sequence[str] | None = None,
        trace_id: str | None = None,
        call_id: str | None = None,
    ) -> Any:
        return self._emit(
            task_id=task_id,
            run_id=run_id,
            event_type=PRE_WRITE_WRITER_EXECUTION_EVENT,
            phase=_require_lifecycle_phase(phase),
            trace_id=trace_id,
            call_id=call_id,
            status=_project(writer_status) if writer_status is not None else None,
            correlation=TraceCorrelationSchema(
                prewrite_route_decision_ref=_require_typed_ref(
                    route_decision_ref,
                    PreWriteRouteDecisionRefSchema,
                    field_name="route_decision_ref",
                ),
                decision_refs=[route_decision_ref.route_decision_id],
                input_refs=[
                    f"context_package:{context_package_id}:{context_sha256}"
                ],
                evidence_refs=[
                    f"evidence_bundle_sha256:{evidence_bundle_sha256}"
                ],
            ),
            payload={
                "writer_subject_ref": _require_typed_ref(
                    writer_subject_ref,
                    WriterSubjectRefSchema,
                    field_name="writer_subject_ref",
                ),
                "write_instance_id": write_instance_id,
                "route_decision_ref": _require_typed_ref(
                    route_decision_ref,
                    PreWriteRouteDecisionRefSchema,
                    field_name="route_decision_ref",
                ),
                "generation_class": generation_class,
                "evidence_bundle_sha256": _require_sha256(
                    evidence_bundle_sha256,
                    field_name="evidence_bundle_sha256",
                ),
                "context_package_id": context_package_id,
                "context_sha256": _require_sha256(
                    context_sha256,
                    field_name="context_sha256",
                ),
                "writer_logical_execution_key": _require_typed_ref(
                    writer_logical_execution_key,
                    WriterLogicalExecutionKey,
                    field_name="writer_logical_execution_key",
                ),
                "model_call_ids": list(model_call_ids or ()),
                "writer_status": writer_status,
                "failure_kind": failure_kind,
                "artifact_ref": artifact_ref,
            },
        )

    def emit_optional_diagnostic(
        self,
        *,
        task_id: str,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> Any | None:
        """A bounded helper whose suppression is the only TRACE_ENABLED behavior."""

        if not self.trace_enabled:
            return None
        if event_type in REQUIRED_PRE_WRITE_EVENT_TYPES:
            raise ValueError("required Gate event must use its dedicated emitter method")
        return self.trace_sink.record(
            task_id=task_id,
            run_id=run_id,
            event_type=event_type,
            component_type="gate",
            component_name="pre_write_gate_v2",
            payload=_bounded_payload(payload),
            tags=["gate_v2", "pre_write", "optional_diagnostic"],
        )


__all__ = [
    "PRE_WRITE_ADMISSION_DECISION_EVENT",
    "PRE_WRITE_BINDING_APPLICABILITY_EVENT",
    "PRE_WRITE_BINDING_COMMITTED_EVENT",
    "PRE_WRITE_ENFORCEMENT_EVENT",
    "PRE_WRITE_EVIDENCE_PROMOTION_EVENT",
    "PRE_WRITE_RECOVERY_EVENT",
    "PRE_WRITE_ROUTE_DECISION_EVENT",
    "PRE_WRITE_WRITER_EXECUTION_EVENT",
    "PreWriteTraceEmitter",
    "REQUIRED_PRE_WRITE_EVENT_TYPES",
]
