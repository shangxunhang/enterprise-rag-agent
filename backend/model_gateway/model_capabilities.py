"""Versioned, explicitly declared provider/model planning capabilities."""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.hashing import canonical_sha256


def normalize_endpoint(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model capability endpoint must be an HTTP(S) base URL without credentials, query or fragment")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


class ModelCapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    endpoint: str = ""
    context_window: int = Field(ge=256)
    max_output_tokens: int = Field(ge=1)
    source: str = Field(min_length=1)
    revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_limits(self):
        normalize_endpoint(self.endpoint)
        if self.max_output_tokens >= self.context_window:
            raise ValueError("Capability output must leave space for input")
        return self


class ModelCapabilityCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["model_capabilities_v1"]
    revision: str = Field(min_length=1)
    entries: list[ModelCapabilityEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identities(self):
        keys = [(e.provider, e.model, normalize_endpoint(e.endpoint)) for e in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate model capability identity")
        return self

    @classmethod
    def load(cls, path: Path) -> "ModelCapabilityCatalog":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def resolve(self, *, provider: str, model: str, endpoint: str = "") -> dict:
        identity = (provider, model, normalize_endpoint(endpoint))
        entry = next((e for e in self.entries if (e.provider, e.model, normalize_endpoint(e.endpoint)) == identity), None)
        if entry is None:
            raise ValueError("No declared capability for configured provider/model/endpoint")
        return {
            "context_window": entry.context_window,
            "provider_max_output_tokens": entry.max_output_tokens,
            "capability_source": {
                "schema_version": self.schema_version,
                "catalog_revision": self.revision,
                "catalog_sha256": canonical_sha256(self.model_dump(mode="json")),
                "entry": entry.model_dump(mode="json"),
                "entry_sha256": canonical_sha256(entry.model_dump(mode="json")),
            },
        }
