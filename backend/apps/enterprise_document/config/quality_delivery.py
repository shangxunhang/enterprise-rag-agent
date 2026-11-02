"""Versioned semantic QualityDeliveryContract configuration.

This module owns stable product-level quality/procedure policy only. Runtime
Artifact/Claim identities and physical execution limits remain outside this
configuration and are resolved by the existing Requirement/Consistency owners.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.post_write.consistency_procedure import (
    ConsistencyProcedureDeclarationSchema,
    ConsistencyProcedureInputSchema,
    ConsistencyProcedureSourceRefSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementEntrySchema,
    RequirementRefSchema,
)
from core.hashing import canonical_sha256


class SemanticQualityDeliveryConfig(BaseModel):
    """Stable semantic policy used to construct the immutable quality Contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["semantic_quality_delivery_config_v1"] = (
        "semantic_quality_delivery_config_v1"
    )
    config_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    consistency_procedure: ConsistencyProcedureDeclarationSchema
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _hash_integrity(self) -> "SemanticQualityDeliveryConfig":
        payload = self.model_dump(mode="json", exclude={"config_hash"})
        if canonical_sha256(payload) != self.config_hash:
            raise ValueError("semantic quality delivery config hash mismatch")
        return self

    @property
    def source_ref(self) -> str:
        return (
            "quality-config://"
            + self.config_id
            + "/"
            + self.config_version
            + "/sha256/"
            + self.config_hash
        )

    @property
    def requirement_id(self) -> str:
        return "consistency-procedure://sha256/" + canonical_sha256(
            {
                "source_ref": self.source_ref,
                "declaration": self.consistency_procedure.model_dump(mode="json"),
            }
        )

    def requirement_entry(self) -> PostWriteRequirementEntrySchema:
        """Lower semantic config into the sole Requirement authority input."""
        return PostWriteRequirementEntrySchema(
            requirement_id=self.requirement_id,
            requirement_kind="consistency_procedure",
            statement=self.source_ref,
            constraints={
                "consistency_procedure": self.consistency_procedure.model_dump(
                    mode="json"
                )
            },
        )

    def procedure_input(
        self,
        *,
        requirement_ref: RequirementRefSchema,
    ) -> ConsistencyProcedureInputSchema:
        source = ConsistencyProcedureSourceRefSchema(
            requirement_ref=requirement_ref,
            requirement_id=self.requirement_id,
        )
        declaration = self.consistency_procedure.model_dump(mode="json")
        return ConsistencyProcedureInputSchema.build(
            **declaration,
            applicability_source_refs=(source,),
            force_include_source_refs=(
                (source,)
                if self.consistency_procedure.force_include_obligation_refs
                else ()
            ),
        )


def load_semantic_quality_delivery_config(
    path: str | Path,
) -> SemanticQualityDeliveryConfig:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"semantic quality delivery config not found: {resolved}"
        )
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("semantic quality delivery config root must be an object")
    return SemanticQualityDeliveryConfig.model_validate(payload)


__all__ = [
    "SemanticQualityDeliveryConfig",
    "load_semantic_quality_delivery_config",
]
