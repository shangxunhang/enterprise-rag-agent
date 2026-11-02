"""Derived scoped verification identity; no truth, persistence or write barrier."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import VerificationResultRefSchema
from .foundation import ValidationScope
from core.hashing import canonical_sha256

class VerificationGenerationScopeRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(min_length=1)
    validation_scope: ValidationScope
    target_ref: str = Field(min_length=1)

class VerificationGenerationEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_ref: ClaimRefSchema
    claim_head_revision: int = Field(ge=1)
    verification_result_ref: VerificationResultRefSchema

    @model_validator(mode="after")
    def _exact_claim(self):
        if self.verification_result_ref.claim_ref != self.claim_ref:
            raise ValueError("generation ResultRef targets another Claim")
        return self

def generation_entry_key(entry):
    return entry.claim_ref.claim_id, canonical_sha256(entry.claim_ref.model_dump(mode="json"))

class VerificationGenerationManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation_scope_ref: VerificationGenerationScopeRefV1
    entries: tuple[VerificationGenerationEntryV1, ...]
    generation_contract_version: Literal["verification-generation-v1"] = "verification-generation-v1"
    verification_generation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _canonical_identity(self):
        keys = [generation_entry_key(entry) for entry in self.entries]
        ids = [entry.claim_ref.claim_id for entry in self.entries]
        if keys != sorted(keys) or len(ids) != len(set(ids)):
            raise ValueError("generation entries must be sorted and unique")
        if any(entry.claim_ref.claim_occurrence_ref.run_id != self.generation_scope_ref.run_id for entry in self.entries):
            raise ValueError("generation Claim belongs to another Run")
        if canonical_sha256(self.model_dump(mode="json", exclude={"verification_generation_sha256"})) != self.verification_generation_sha256:
            raise ValueError("verification generation hash mismatch")
        return self

    @classmethod
    def build(cls, scope_ref, entries=()):
        entries = tuple(sorted(entries, key=generation_entry_key))
        material = dict(generation_scope_ref=scope_ref.model_dump(mode="json"),
            entries=[entry.model_dump(mode="json") for entry in entries], generation_contract_version="verification-generation-v1")
        return cls(**material, verification_generation_sha256=canonical_sha256(material))
