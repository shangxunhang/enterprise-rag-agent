"""Immutable Target binding and applicability carrier contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
)


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WriterSubjectRefSchema(_FrozenSchema):
    run_id: _NonEmptyStr
    document_id: _NonEmptyStr
    section_id: _NonEmptyStr


class WriterGateBindingSchema(_FrozenSchema):
    schema_version: Literal["writer_gate_binding_v3"] = "writer_gate_binding_v3"
    contract_version: Literal["writer_gate_binding_control_v1"] = (
        "writer_gate_binding_control_v1"
    )
    binding_id: _NonEmptyStr
    decision_ref: WriterAdmissionDecisionRefSchema
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr


class WriterGateBindingRefSchema(_FrozenSchema):
    binding_id: _NonEmptyStr
    binding_schema_version: Literal["writer_gate_binding_v3"] = (
        "writer_gate_binding_v3"
    )
    binding_contract_version: Literal["writer_gate_binding_control_v1"] = (
        "writer_gate_binding_control_v1"
    )


class BindingApplicabilityState(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    REFERENCE_INTEGRITY_FAILURE = "reference_integrity_failure"


class BindingApplicabilityResult(_FrozenSchema):
    """Typed Step-3 operation result; it is not a persisted business artifact."""

    state: BindingApplicabilityState
    decision: WriterAdmissionDecisionSchema | None = None
    binding: WriterGateBindingSchema | None = None

    @model_validator(mode="after")
    def _applicable_requires_resolved_pair(self) -> "BindingApplicabilityResult":
        if self.state is BindingApplicabilityState.APPLICABLE:
            if self.decision is None or self.binding is None:
                raise ValueError("applicable result requires decision and binding")
            expected = self.binding.decision_ref
            if (
                expected.decision_id != self.decision.decision_id
                or expected.decision_schema_version != self.decision.schema_version
                or expected.decision_contract_version != self.decision.contract_version
            ):
                raise ValueError("binding decision_ref must match resolved decision")
        return self


__all__ = [
    "BindingApplicabilityResult",
    "BindingApplicabilityState",
    "WriterGateBindingRefSchema",
    "WriterGateBindingSchema",
    "WriterSubjectRefSchema",
]
