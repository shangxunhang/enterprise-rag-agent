# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_sha256_json、slug、SourceDatasetConfig、EmbeddingBuildConfig、IndexStorageConfig、OutputConfig、OfflineIndexBuildConfig、OfflineIndexConfigLoader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Strict configuration for reproducible offline index builds."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.config.static_retrieval import ComponentConfig


# 阅读注释（函数）：处理 sha256 JSON 相关逻辑。
def _sha256_json(payload: dict[str, Any]) -> str:
    """处理 sha256 JSON 相关逻辑。

    参数:
        payload: 跨层传递的数据载荷。

    返回:
        str

    阅读提示:
        主要直接调用：hexdigest, hashlib.sha256, encode, json.dumps。
    """
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# 阅读注释（函数）：处理 slug 相关逻辑。
def slug(value: str) -> str:
    """处理 slug 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：lower, strip, re.sub, str。
    """
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return normalized or "unknown"


# 阅读注释（类）：封装 source 数据集 配置，集中封装相关状态、依赖和行为。
class SourceDatasetConfig(BaseModel):
    """封装 source 数据集 配置，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    path: str
    format: Literal["jsonl"] = "jsonl"

    # 阅读注释（函数）：处理 路径 not blank 相关逻辑。
    @field_validator("path")
    @classmethod
    def path_not_blank(cls, value: str) -> str:
        """处理 路径 not blank 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strip, str, ValueError, field_validator。
        """
        if not str(value or "").strip():
            raise ValueError("source.path cannot be blank")
        return str(value).strip()


# 阅读注释（类）：封装 embedding build 配置，集中封装相关状态、依赖和行为。
class EmbeddingBuildConfig(BaseModel):
    """封装 embedding build 配置，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["hash", "model"] = "hash"
    name: str = "hash_embedding"
    version: str = "v1"
    model_name: str | None = None
    device: str = "cpu"
    batch_size: int = Field(default=32, ge=1)
    dim: int = Field(default=64, ge=8)

    # 阅读注释（函数）：校验 模型。
    @model_validator(mode="after")
    def validate_model(self) -> "EmbeddingBuildConfig":
        """校验 模型。

        返回:
            'EmbeddingBuildConfig'

        阅读提示:
            主要直接调用：strip, str, ValueError, model_validator。
        """
        if self.mode == "model" and not str(self.model_name or "").strip():
            raise ValueError("embedding.model_name is required when mode=model")
        return self


# 阅读注释（类）：封装 索引 存储 配置，集中封装相关状态、依赖和行为。
class IndexStorageConfig(BaseModel):
    """封装 索引 存储 配置，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    backend: Literal["artifacts_only", "milvus_lite"] = "artifacts_only"
    metric_type: str = "COSINE"
    collection_name: str = "rag_child_chunks"
    recreate: bool = True
    insert_batch_size: int = Field(default=128, ge=1)
    max_text_chars: int = Field(default=8192, ge=256, le=65535)


# 阅读注释（类）：封装 输出 配置，集中封装相关状态、依赖和行为。
class OutputConfig(BaseModel):
    """封装 输出 配置，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    root_dir: str = "data/processed/indexes"
    active_pointer: str = "data/processed/indexes/active_index.json"
    update_active_pointer: bool = False


# 阅读注释（类）：封装 离线 索引 build 配置，集中封装相关状态、依赖和行为。
class OfflineIndexBuildConfig(BaseModel):
    """封装 离线 索引 build 配置，集中封装相关状态、依赖和行为。"""
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

    # 阅读注释（函数）：处理 not blank 相关逻辑。
    @field_validator("build_id", "dataset_version")
    @classmethod
    def not_blank(cls, value: str) -> str:
        """处理 not blank 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strip, str, ValueError, field_validator。
        """
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("build_id/dataset_version cannot be blank")
        return normalized

    # 阅读注释（函数）：处理 require chunker 相关逻辑。
    @model_validator(mode="after")
    def require_chunker(self) -> "OfflineIndexBuildConfig":
        """处理 require chunker 相关逻辑。

        返回:
            'OfflineIndexBuildConfig'

        阅读提示:
            主要直接调用：ValueError, model_validator。
        """
        if not self.chunker.enabled:
            raise ValueError("offline index build requires an enabled chunker")
        if self.outputs.update_active_pointer and self.index.backend != "milvus_lite":
            raise ValueError(
                "only a milvus_lite build can update the online active-index pointer"
            )
        return self

    # 阅读注释（函数）：处理 canonical 字典 相关逻辑。
    def canonical_dict(self) -> dict[str, Any]:
        """处理 canonical 字典 相关逻辑。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：self.model_dump。
        """
        return self.model_dump(mode="json", exclude_none=True)

    # 阅读注释（函数）：处理 配置 hash 相关逻辑。
    def config_hash(self) -> str:
        """处理 配置 hash 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：_sha256_json, self.canonical_dict。
        """
        return _sha256_json(self.canonical_dict())

    # 阅读注释（函数）：处理 索引 identity 字典 相关逻辑。
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

    # 阅读注释（函数）：处理 索引 identity hash 相关逻辑。
    def index_identity_hash(self) -> str:
        """处理 索引 identity hash 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：_sha256_json, self.index_identity_dict。
        """
        return _sha256_json(self.index_identity_dict())

    # 阅读注释（函数）：处理 索引 版本 相关逻辑。
    def index_version(self) -> str:
        """处理 索引 版本 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：self.index_identity_hash, slug。
        """
        short_hash = self.index_identity_hash()[:12]
        return "idx_%s_%s_%s_%s_%s" % (
            slug(self.chunker.name),
            slug(self.chunker.version),
            slug(self.embedding.name),
            slug(self.embedding.version),
            short_hash,
        )


# 阅读注释（类）：封装 离线 索引 配置 loader，集中封装相关状态、依赖和行为。
class OfflineIndexConfigLoader:
    """封装 离线 索引 配置 loader，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 default 项目 root 相关逻辑。
    @staticmethod
    def default_project_root() -> Path:
        """处理 default 项目 root 相关逻辑。

        返回:
            Path

        阅读提示:
            主要直接调用：resolve, Path。
        """
        return Path(__file__).resolve().parents[3]

    # 阅读注释（函数）：解析并确定 路径。
    def resolve_path(self, path: str | Path, *, project_root: str | Path | None = None) -> Path:
        """解析并确定 路径。

        参数:
            path: 目标文件或目录路径。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            Path

        阅读提示:
            主要直接调用：expanduser, Path, candidate.is_absolute, candidate.resolve, resolve, self.default_project_root。
        """
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        root = Path(project_root).expanduser().resolve() if project_root else self.default_project_root()
        return (root / candidate).resolve()

    # 阅读注释（函数）：加载 OfflineIndexConfigLoader。
    def load(self, path: str | Path, *, project_root: str | Path | None = None) -> OfflineIndexBuildConfig:
        """加载 OfflineIndexConfigLoader。

        参数:
            path: 目标文件或目录路径。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            OfflineIndexBuildConfig

        阅读提示:
            主要直接调用：self.resolve_path, resolved.is_file, FileNotFoundError, resolved.read_text, resolved.suffix.lower, yaml.safe_load, json.loads, ValueError。
        """
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
