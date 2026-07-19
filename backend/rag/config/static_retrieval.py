"""Static retrieval topology and replaceable component implementations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ComponentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "v1"
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("component name/version cannot be blank")
        return normalized


class ContextGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    long_context_threshold_ratio: float = Field(default=0.65, gt=0.0, le=1.0)
    model_context_window: int = Field(default=8192, ge=512)
    prompt_reserved_tokens: int = Field(default=1536, ge=0)
    section_token_budget: int = Field(default=4096, ge=256)
    max_evidence_items: int = Field(default=5, ge=1, le=100)
    max_context_chars: int = Field(default=12000, ge=256)


class StaticRetrievalSpec(BaseModel):
    """One stable retrieval skeleton; it contains no intent combinations."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["static_retrieval_spec_v1"] = "static_retrieval_spec_v1"
    spec_id: str
    spec_version: str = "v1"
    query_transformers: list[ComponentConfig]
    retrievers: list[ComponentConfig]
    source_fusion: ComponentConfig
    query_fusion: ComponentConfig
    candidate_enricher: ComponentConfig
    reranker: ComponentConfig
    evidence_assessor: ComponentConfig
    corrective_retrieval_gate: ComponentConfig
    corrective_query_planner: ComponentConfig
    context_packers: list[ComponentConfig]
    context_gate: ContextGateConfig = Field(default_factory=ContextGateConfig)

    @field_validator("spec_id", "spec_version")
    @classmethod
    def spec_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("static retrieval spec id/version cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_topology(self) -> "StaticRetrievalSpec":
        transformer_names = {
            item.name for item in self.query_transformers if item.enabled
        }
        expected_transformers = {"identity", "multi_query", "hyde"}
        if transformer_names != expected_transformers:
            raise ValueError(
                "static retrieval spec must expose exactly identity, multi_query and hyde"
            )
        retriever_names = [item.name for item in self.retrievers if item.enabled]
        if len(retriever_names) != len(set(retriever_names)):
            raise ValueError("static retrieval spec contains duplicate retriever")
        if set(retriever_names) != {"milvus_dense_child", "bm25_child"}:
            raise ValueError(
                "static retrieval spec requires exactly dense and keyword retrievers"
            )
        context_names = {item.name for item in self.context_packers if item.enabled}
        if context_names != {"default", "lost_in_middle"}:
            raise ValueError(
                "static retrieval spec must expose default and lost_in_middle packers"
            )
        for field_name in (
            "source_fusion",
            "query_fusion",
            "candidate_enricher",
            "reranker",
            "evidence_assessor",
            "corrective_retrieval_gate",
            "corrective_query_planner",
        ):
            if not getattr(self, field_name).enabled:
                raise ValueError(f"static retrieval spec requires enabled {field_name}")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def config_hash(self) -> str:
        payload = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class StaticRetrievalSpecLoader:
    @staticmethod
    def default_project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else self.default_project_root()
        )
        return (root / candidate).resolve()

    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> StaticRetrievalSpec:
        resolved = self.resolve_path(path, project_root=project_root)
        if not resolved.is_file():
            raise FileNotFoundError(f"static retrieval spec not found: {resolved}")
        raw = resolved.read_text(encoding="utf-8")
        payload = json.loads(raw) if resolved.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(payload, dict):
            raise ValueError("static retrieval spec root must be an object")
        return StaticRetrievalSpec.model_validate(payload)
