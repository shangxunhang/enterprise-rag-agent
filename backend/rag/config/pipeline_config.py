"""Configuration schemas for the framework-independent online RAG pipeline.

The configuration layer decides *which* components are used. Component
contracts decide *how* those components can be called. Keeping both concerns
separate is what makes the runtime genuinely configuration-driven.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ComponentConfig(BaseModel):
    """Declarative reference to one registered component implementation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "v1"
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("component name/version cannot be blank")
        return normalized


class OnlineRAGPipelineConfig(BaseModel):
    """External configuration for an online RAG pipeline profile.

    Query transformation, retrieval, source fusion, multi-query fusion,
    parent-child enrichment, reranking, evidence grading, context packing and
    answer checking and repair are externally configured.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["online_rag_pipeline_config_v5"] = (
        "online_rag_pipeline_config_v5"
    )
    profile_id: str
    profile_version: str = "v1"

    query_transformers: list[ComponentConfig] = Field(default_factory=list)
    retrievers: list[ComponentConfig] = Field(default_factory=list)
    fusion: ComponentConfig
    query_fusion: ComponentConfig
    candidate_enricher: ComponentConfig
    reranker: ComponentConfig
    evidence_grader: ComponentConfig
    context_packer: ComponentConfig
    generation_checker: ComponentConfig
    repair_strategy: ComponentConfig

    @field_validator("profile_id", "profile_version")
    @classmethod
    def _profile_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("profile id/version cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _require_enabled_pipeline_components(self) -> "OnlineRAGPipelineConfig":
        if not any(item.enabled for item in self.query_transformers):
            raise ValueError(
                "online RAG profile must configure at least one enabled "
                "query_transformer"
            )
        enabled_retrievers = [item for item in self.retrievers if item.enabled]
        if not enabled_retrievers:
            raise ValueError(
                "online RAG profile must configure at least one enabled retriever"
            )
        retriever_keys = [(item.name, item.version) for item in enabled_retrievers]
        if len(retriever_keys) != len(set(retriever_keys)):
            raise ValueError("online RAG profile contains duplicate retriever plugins")
        for field_name in (
            "fusion",
            "query_fusion",
            "candidate_enricher",
            "reranker",
            "evidence_grader",
            "context_packer",
            "generation_checker",
            "repair_strategy",
        ):
            component = getattr(self, field_name)
            if not component.enabled:
                raise ValueError(
                    f"online RAG profile requires enabled {field_name} component"
                )
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


class PipelineConfigLoader:
    """Load and strictly validate YAML/JSON pipeline profiles.

    Relative paths are resolved against ``project_root``. When callers do not
    provide one, the repository root containing this module is used instead of
    the process working directory. This keeps PyCharm, pytest and CLI startup
    behavior identical.
    """

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
    ) -> OnlineRAGPipelineConfig:
        config_path = self.resolve_path(path, project_root=project_root)
        if not config_path.exists():
            raise FileNotFoundError(f"RAG pipeline config not found: {config_path}")
        if not config_path.is_file():
            raise ValueError(f"RAG pipeline config is not a file: {config_path}")

        suffix = config_path.suffix.lower()
        raw_text = config_path.read_text(encoding="utf-8")
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(raw_text)
        elif suffix == ".json":
            payload = json.loads(raw_text)
        else:
            raise ValueError(
                f"Unsupported RAG pipeline config format: {suffix!r}; "
                "expected .yaml, .yml or .json"
            )
        if not isinstance(payload, dict):
            raise ValueError("RAG pipeline config root must be an object")
        return OnlineRAGPipelineConfig.model_validate(payload)
