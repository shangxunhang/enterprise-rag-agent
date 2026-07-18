"""Strict configuration for reproducible offline index builds."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.config.pipeline_config import ComponentConfig


def _sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return normalized or "unknown"


class SourceDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    format: Literal["jsonl"] = "jsonl"

    @field_validator("path")
    @classmethod
    def path_not_blank(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("source.path cannot be blank")
        return str(value).strip()


class EmbeddingBuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["hash", "model"] = "hash"
    name: str = "hash_embedding"
    version: str = "v1"
    model_name: str | None = None
    device: str = "cpu"
    batch_size: int = Field(default=32, ge=1)
    dim: int = Field(default=64, ge=8)

    @model_validator(mode="after")
    def validate_model(self) -> "EmbeddingBuildConfig":
        if self.mode == "model" and not str(self.model_name or "").strip():
            raise ValueError("embedding.model_name is required when mode=model")
        return self


class IndexStorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["artifacts_only", "milvus_lite"] = "artifacts_only"
    metric_type: str = "COSINE"
    collection_name: str = "rag_child_chunks"
    recreate: bool = True
    insert_batch_size: int = Field(default=128, ge=1)
    max_text_chars: int = Field(default=8192, ge=256, le=65535)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_dir: str = "data/processed/indexes"
    active_pointer: str = "data/processed/indexes/active_index.json"
    update_active_pointer: bool = False


class OfflineIndexBuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["offline_index_build_config_v1"] = "offline_index_build_config_v1"
    build_id: str
    dataset_version: str
    source: SourceDatasetConfig
    chunker: ComponentConfig
    embedding: EmbeddingBuildConfig
    index: IndexStorageConfig = Field(default_factory=IndexStorageConfig)
    outputs: OutputConfig = Field(default_factory=OutputConfig)
    deterministic_created_at: str = "1970-01-01T00:00:00+00:00"
    notes: str | None = None

    @field_validator("build_id", "dataset_version")
    @classmethod
    def not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("build_id/dataset_version cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_chunker(self) -> "OfflineIndexBuildConfig":
        if not self.chunker.enabled:
            raise ValueError("offline index build requires an enabled chunker")
        if self.outputs.update_active_pointer and self.index.backend != "milvus_lite":
            raise ValueError(
                "only a milvus_lite build can update the online active-index pointer"
            )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def config_hash(self) -> str:
        return _sha256_json(self.canonical_dict())

    def index_identity_dict(self) -> dict[str, Any]:
        """Fields that define index compatibility, excluding operational metadata.

        Changing output paths, notes, build_id or build timestamp must not create
        a logically different index. Dataset versions must be bumped whenever
        source contents change; source-hash collision checks enforce that rule.
        """
        return {
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "chunker": self.chunker.model_dump(mode="json", exclude_none=True),
            "embedding": self.embedding.model_dump(mode="json", exclude_none=True),
            "index": self.index.model_dump(mode="json", exclude_none=True),
        }

    def index_identity_hash(self) -> str:
        return _sha256_json(self.index_identity_dict())

    def index_version(self) -> str:
        short_hash = self.index_identity_hash()[:12]
        return "idx_%s_%s_%s_%s_%s" % (
            slug(self.chunker.name),
            slug(self.chunker.version),
            slug(self.embedding.name),
            slug(self.embedding.version),
            short_hash,
        )


class OfflineIndexConfigLoader:
    @staticmethod
    def default_project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def resolve_path(self, path: str | Path, *, project_root: str | Path | None = None) -> Path:
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        root = Path(project_root).expanduser().resolve() if project_root else self.default_project_root()
        return (root / candidate).resolve()

    def load(self, path: str | Path, *, project_root: str | Path | None = None) -> OfflineIndexBuildConfig:
        resolved = self.resolve_path(path, project_root=project_root)
        if not resolved.is_file():
            raise FileNotFoundError(f"offline index config not found: {resolved}")
        text = resolved.read_text(encoding="utf-8")
        if resolved.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        elif resolved.suffix.lower() == ".json":
            payload = json.loads(text)
        else:
            raise ValueError("offline index config must be .yaml/.yml/.json")
        if not isinstance(payload, dict):
            raise ValueError("offline index config root must be an object")
        return OfflineIndexBuildConfig.model_validate(payload)
