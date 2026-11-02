"""Resolve stable Contract obligations through existing exact authority owners.

No defaults are populated here. The canonical-universe reader is supplied by
the application Claim projection owner; this service never extracts Claims.
"""
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.consistency_procedure import (
    ConsistencyProcedureDeclarationSchema, Sha256,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema, QualityDeliveryContractRefSchema,
    ValidationTargetSelectorSchema, ValidationIntermediateKind,
)
from apps.enterprise_document.schemas.post_write.quality import ConsistencyAssertionSchema
from .consistency import ConsistencyIntegrityError


class ConsistencyResolvedSelectorSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selector: ValidationTargetSelectorSchema
    member_assertion_refs: tuple[str, ...]


class _RuntimeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["consistency_procedure_lowering_v1"] = "consistency_procedure_lowering_v1"
    validation_run_id: str
    contract_ref: QualityDeliveryContractRefSchema
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256
    canonical_source_hash: Sha256
    assertion_refs: tuple[str, ...]
    assertion_sections: dict[str, str]
    document_global_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNRESOLVED"]
    reason: str
    global_work: tuple[ConsistencyResolvedSelectorSchema, ...] = ()
    force_include_work: tuple[ConsistencyResolvedSelectorSchema, ...] = ()


class ConsistencyProcedureLoweringSchema(_RuntimeBody):
    runtime_input_hash: Sha256

    @classmethod
    def build(cls, **kwargs):
        body = _RuntimeBody.model_validate(kwargs)
        return cls(**body.model_dump(), runtime_input_hash=canonical_sha256(body.model_dump(mode="json")))

    @model_validator(mode="after")
    def _integrity(self):
        if self.runtime_input_hash != canonical_sha256(self.model_dump(mode="json", exclude={"runtime_input_hash"})):
            raise ValueError("Consistency runtime lowering hash mismatch")
        if self.artifact_state_ref.artifact_scope.value != "DOCUMENT":
            raise ValueError("global lowering requires exact Document Artifact")
        if self.assertion_refs != tuple(sorted(set(self.assertion_refs))) or set(self.assertion_sections) != set(self.assertion_refs):
            raise ValueError("runtime canonical member manifest differs")
        for work in (*self.global_work, *self.force_include_work):
            if work.member_assertion_refs != self.assertion_refs:
                raise ValueError("all-included selector must bind the complete exact current universe")
        if self.document_global_applicability == "APPLICABLE" and not self.global_work:
            raise ValueError("applicable global lowering lacks selector work")
        if self.document_global_applicability != "APPLICABLE" and self.global_work:
            raise ValueError("non-applicable global lowering carries work")
        return self

    def required_relation_members(self):
        """Return only explicit relation-member force work.

        A document-global ``consistency_all_included_assertions`` selector binds
        the complete discovery universe.  It does not assert that the entire
        universe is one material N-ary relation.  Global selector completion is
        independently accounted through ``required_logical_obligations``.
        """
        return tuple(sorted({
            work.member_assertion_refs
            for work in self.force_include_work
            if len(work.member_assertion_refs) >= 2
        }))

    def required_logical_obligations(self):
        return tuple(sorted({work.selector.target_ref for work in (*self.global_work, *self.force_include_work)}))


class ConsistencyProcedureInputResolver:
    def __init__(self, *, contract_store, requirement_store, canonical_universe_reader: Callable, intermediate_runtime):
        self.contract_store = contract_store
        self.requirement_store = requirement_store
        self.canonical_universe_reader = canonical_universe_reader
        self.runtime = intermediate_runtime

    def _declarations(self, refs):
        values = []
        for source in refs:
            snapshot = self.requirement_store.resolve_exact(source.requirement_ref)
            entry = next((entry for entry in snapshot.requirements if entry.requirement_id == source.requirement_id), None)
            if entry is None or entry.requirement_kind != "consistency_procedure":
                raise ConsistencyIntegrityError("exact Consistency source declaration is missing; other dimension authority is forbidden")
            if set(entry.constraints) != {"consistency_procedure"}:
                raise ConsistencyIntegrityError("Consistency authority declaration shape differs")
            values.append(ConsistencyProcedureDeclarationSchema.model_validate(entry.constraints["consistency_procedure"]))
        return tuple(values)

    def resolve(self, *, contract_ref, artifact_state_ref, validation_run_id, validation_subject_hash):
        contract = self.contract_store.resolve_exact(contract_ref)
        assertions, sections, source_hash = self.canonical_universe_reader(artifact_state_ref, validation_subject_hash)
        assertions = tuple(ConsistencyAssertionSchema.model_validate(item) for item in assertions)
        if (artifact_state_ref.artifact_scope.value != "DOCUMENT"
                or any(a.artifact_state_ref != artifact_state_ref or a.validation_subject_hash != validation_subject_hash for a in assertions)):
            raise ConsistencyIntegrityError("foreign/stale Artifact or canonical assertion universe")
        refs = tuple(sorted(a.assertion_ref for a in assertions))
        if len(refs) != len(set(refs)) or set(sections) != set(refs) or any(not s for s in sections.values()):
            raise ConsistencyIntegrityError("canonical assertion/Section manifest differs")
        source = contract.consistency_procedure_input
        global_work, force_work = (), ()
        state, reason = "UNRESOLVED", "MISSING_CONSISTENCY_PROCEDURE_INPUT"
        if source is not None:
            declarations = self._declarations(source.applicability_source_refs)
            if declarations and any(
                d.document_global_applicability != source.document_global_applicability
                or d.applicability_reason != source.applicability_reason
                for d in declarations
            ):
                raise ConsistencyIntegrityError("Contract applicability differs from exact authority")
            authorized = {selector.model_dump_json() for d in declarations for selector in d.document_global_selectors}
            if {selector.model_dump_json() for selector in source.document_global_selectors} != authorized:
                raise ConsistencyIntegrityError("Contract global selector is not authorized by exact source")
            force_declarations = self._declarations(source.force_include_source_refs)
            authorized_force = {selector.model_dump_json() for d in force_declarations for selector in d.force_include_selectors}
            authorized_obligations = {ref for d in (*declarations, *force_declarations) for ref in d.force_include_obligation_refs}
            if (set(source.force_include_obligation_refs) != authorized_obligations
                    or any(selector.model_dump_json() not in authorized_force for selector in source.force_include_selectors)):
                raise ConsistencyIntegrityError("Contract force-includes differ from exact authority")
            state, reason = source.document_global_applicability, source.applicability_reason or "EXPLICIT_CONTRACT_APPLICABILITY"
            global_work = tuple(ConsistencyResolvedSelectorSchema(selector=selector, member_assertion_refs=refs) for selector in source.document_global_selectors)
            force_work = tuple(ConsistencyResolvedSelectorSchema(selector=selector, member_assertion_refs=refs) for selector in source.force_include_selectors)
        return ConsistencyProcedureLoweringSchema.build(
            validation_run_id=validation_run_id, contract_ref=contract_ref, artifact_state_ref=artifact_state_ref,
            validation_subject_hash=validation_subject_hash, canonical_source_hash=source_hash,
            assertion_refs=refs, assertion_sections=sections, document_global_applicability=state,
            reason=reason, global_work=global_work, force_include_work=force_work,
        )

    def commit(self, lowering):
        exact = ConsistencyProcedureLoweringSchema.model_validate(lowering)
        return self.runtime.commit(validation_run_id=exact.validation_run_id,
            producer_ref="consistency-procedure-input-resolver:v1",
            target_ref="consistency-procedure-input://" + exact.runtime_input_hash,
            typed_intermediate=exact, intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
            occurred_at_epoch=0.0)

    def replay(self, ref, **current):
        exact = self.runtime.resolve_typed(ref, ConsistencyProcedureLoweringSchema)
        if ref.validation_run_id != exact.validation_run_id or exact != self.resolve(**current):
            raise ConsistencyIntegrityError("committed procedure lowering differs from exact current Contract/Artifact/source")
        return exact
