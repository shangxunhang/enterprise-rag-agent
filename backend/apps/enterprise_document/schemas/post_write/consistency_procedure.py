"""Stable Consistency Contract input; Artifact-specific members live separately."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from core.hashing import canonical_sha256
from .foundation import ValidationTargetSelectorSchema
from .requirement import RequirementRefSchema

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
LogicalObligation = Annotated[str, StringConstraints(pattern=r"^consistency-obligation://[^\s]+$")]


class _Frozen(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConsistencyProcedureSourceRefSchema(_Frozen):
    """Exact entry in the existing Requirement authority; not a new source Store."""
    requirement_ref: RequirementRefSchema
    requirement_id: Text


class ConsistencyProcedureDeclarationSchema(_Frozen):
    """Explicit user-authored declaration in a consistency_procedure Requirement."""
    document_global_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNRESOLVED"]
    document_global_selectors: tuple[ValidationTargetSelectorSchema, ...] = ()
    applicability_reason: Text | None = None
    force_include_obligation_refs: tuple[LogicalObligation, ...] = ()
    force_include_selectors: tuple[ValidationTargetSelectorSchema, ...] = ()

    @model_validator(mode="after")
    def _logical_shape(self):
        for selectors in (self.document_global_selectors, self.force_include_selectors):
            keys = [selector.target_ref for selector in selectors]
            if keys != sorted(set(keys)):
                raise ValueError("logical selectors must be canonical ordered-unique")
            for selector in selectors:
                if (selector.selector_kind != "consistency_all_included_assertions"
                        or selector.selector_contract_version != "consistency-logical-selector-v1"
                        or not selector.target_ref.startswith("consistency-obligation://")):
                    raise ValueError("unsupported stable logical selector; runtime Claim/member refs are forbidden")
        if self.force_include_obligation_refs != tuple(sorted(set(self.force_include_obligation_refs))):
            raise ValueError("force-include obligations must be canonical ordered-unique")
        if set(self.force_include_obligation_refs) != {s.target_ref for s in self.force_include_selectors}:
            raise ValueError("force-include obligations require exact logical selectors")
        if self.document_global_applicability == "APPLICABLE" and not self.document_global_selectors:
            raise ValueError("APPLICABLE requires exact logical selectors")
        if self.document_global_applicability != "APPLICABLE" and self.document_global_selectors:
            raise ValueError("global selectors require APPLICABLE")
        if self.document_global_applicability == "NOT_APPLICABLE" and not self.applicability_reason:
            raise ValueError("NOT_APPLICABLE requires explicit reason")
        return self


class _ProcedureBody(ConsistencyProcedureDeclarationSchema):
    procedure_contract_version: Literal["consistency-procedure-input-v1"] = "consistency-procedure-input-v1"
    applicability_source_refs: tuple[ConsistencyProcedureSourceRefSchema, ...] = ()
    force_include_source_refs: tuple[ConsistencyProcedureSourceRefSchema, ...] = ()

    @model_validator(mode="after")
    def _source_shape(self):
        for refs in (self.applicability_source_refs, self.force_include_source_refs):
            keys = [canonical_sha256(ref.model_dump(mode="json")) for ref in refs]
            if keys != sorted(set(keys)):
                raise ValueError("procedure sources must be canonical ordered-unique")
        if self.document_global_applicability != "UNRESOLVED" and not self.applicability_source_refs:
            raise ValueError("determinate applicability requires exact authoritative source")
        if bool(self.force_include_obligation_refs) != bool(self.force_include_source_refs):
            raise ValueError("force-includes require exact authoritative source")
        return self


class ConsistencyProcedureInputSchema(_ProcedureBody):
    procedure_input_hash: Sha256

    @classmethod
    def build(cls, **kwargs):
        body = _ProcedureBody.model_validate(kwargs)
        return cls(**body.model_dump(), procedure_input_hash=canonical_sha256(body.model_dump(mode="json")))

    @model_validator(mode="after")
    def _hash(self):
        body = self.model_dump(mode="json", exclude={"procedure_input_hash"})
        if self.procedure_input_hash != canonical_sha256(body):
            raise ValueError("Consistency procedure input hash mismatch")
        return self
