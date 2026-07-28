"""定义可重复离线 RAG 索引构建的严格配置协议。

配置把源数据、Chunker、Embedding、索引后端和输出目录分层建模，并同时提供完整
配置哈希和“索引兼容性身份”哈希。输出路径、build_id、备注和构建时间不改变索引
逻辑身份；数据集版本、Chunker、Embedding 或索引参数变化会产生新的 index_version。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.config.static_retrieval import ComponentConfig


def _sha256_json(payload: dict[str, Any]) -> str:
    """对字典的 canonical JSON 表示计算 SHA-256。

    Args:
        payload: 待指纹化的 JSON 兼容字典。

    Returns:
        键排序、紧凑序列化且保留中文后的十六进制 SHA-256。
    """

    # 排序键和固定分隔符消除 YAML/JSON 格式、缩进和键顺序噪声。
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def slug(value: str) -> str:
    """把任意名称转换为适合 index_version 的小写标识片段。

    非字母数字字符统一替换为下划线，去除首尾下划线；完全为空时返回
    ``unknown``，保证版本字符串始终可构造。
    """

    normalized = re.sub(
        r"[^a-zA-Z0-9]+",
        "_",
        str(value or "").strip(),
    ).strip("_").lower()
    return normalized or "unknown"


class SourceDatasetConfig(BaseModel):
    """描述离线构建的源数据集及默认检索作用域。

    Attributes:
        path: JSONL 源文件或兼容目录路径。
        format: 当前只支持 ``jsonl``。
        tenant_id: 源记录未提供 tenant_id 时使用的默认租户。
        kb_id: 源记录未提供 kb_id 时使用的默认知识库。

    记录级作用域始终优先于数据集级默认值，防止统一默认值覆盖已明确的数据隔离信息。
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    format: Literal["jsonl"] = "jsonl"
    tenant_id: str = "default"
    kb_id: str = "default"

    @field_validator("path")
    @classmethod
    def path_not_blank(cls, value: str) -> str:
        """去除源路径首尾空白并拒绝空值。"""

        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("source.path cannot be blank")
        return normalized


class EmbeddingBuildConfig(BaseModel):
    """定义离线索引使用的 Embedding 实现和向量协议。

    Attributes:
        mode: ``hash`` 仅用于烟测，``model`` 使用真实文本模型。
        name: 进入索引身份的逻辑组件名称。
        version: 进入索引身份的组件版本。
        model_name: 真实模型路径或名称；model 模式必须提供。
        device: 编码设备。
        batch_size: 文档批量编码大小。
        dim: 预期向量维度，最小为 8。
    """

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
        """要求真实模型模式显式提供模型路径或名称。"""

        if self.mode == "model" and not str(self.model_name or "").strip():
            raise ValueError(
                "embedding.model_name is required when mode=model"
            )
        return self


class IndexStorageConfig(BaseModel):
    """定义离线构建产物是否只写文件或同时写入 Milvus Lite。

    Attributes:
        backend: ``artifacts_only`` 只生成不可变文件，``milvus_lite`` 还创建在线库。
        metric_type: 向量检索度量名称。
        collection_name: Milvus 集合名称。
        recreate: 构建时是否重建集合。
        insert_batch_size: Milvus 批量插入行数。
        max_text_chars: Milvus 文本字段最大字符数。
    """

    model_config = ConfigDict(extra="forbid")

    backend: Literal["artifacts_only", "milvus_lite"] = "artifacts_only"
    metric_type: str = "COSINE"
    collection_name: str = "rag_child_chunks"
    recreate: bool = True
    insert_batch_size: int = Field(default=128, ge=1)
    max_text_chars: int = Field(default=8192, ge=256, le=65535)


class OutputConfig(BaseModel):
    """定义版本化索引产物根目录和 active pointer 位置。

    Builder 只负责创建不可变版本产物，禁止直接更新 active pointer。激活和回滚必须
    经过 ``IndexLifecycleManager`` 的验证、注册和原子切换流程。
    """

    model_config = ConfigDict(extra="forbid")

    root_dir: str = "data/processed/indexes"
    active_pointer: str = "data/processed/indexes/active_index.json"
    update_active_pointer: bool = False

    @model_validator(mode="after")
    def forbid_builder_activation(self) -> "OutputConfig":
        """禁止离线 Builder 在构建完成后隐式激活索引。"""

        if self.update_active_pointer:
            raise ValueError(
                "outputs.update_active_pointer is forbidden even for "
                "milvus_lite; activate only through IndexLifecycleManager"
            )
        return self


class OfflineIndexBuildConfig(BaseModel):
    """描述一次可重复的版本化离线索引构建。

    Attributes:
        schema_version: 构建配置协议版本。
        build_id: 本次操作身份，不参与索引兼容性身份。
        dataset_version: 源数据逻辑版本，源内容变化时必须提升。
        source: 数据集路径和默认作用域。
        chunker: 已注册且必须启用的 Chunker 组件配置。
        embedding: Embedding 组件和向量协议。
        index: 存储后端及集合参数。
        outputs: 版本目录和 active pointer 配置。
        deterministic_created_at: 写入 Manifest 的确定性时间，默认 Unix epoch。
        notes: 操作备注，不参与索引身份。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["offline_index_build_config_v1"] = (
        "offline_index_build_config_v1"
    )
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
        """规范化构建身份字段并拒绝空白值。"""

        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("build_id/dataset_version cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_chunker(self) -> "OfflineIndexBuildConfig":
        """要求离线构建绑定一个已启用的 Chunker。"""

        if not self.chunker.enabled:
            raise ValueError(
                "offline index build requires an enabled chunker"
            )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """返回完整配置的 JSON 兼容 canonical 投影。"""

        return self.model_dump(mode="json", exclude_none=True)

    def config_hash(self) -> str:
        """计算包括操作字段在内的完整配置哈希。"""

        return _sha256_json(self.canonical_dict())

    def index_identity_dict(self) -> dict[str, Any]:
        """返回真正决定索引兼容性的字段。

        输出路径、notes、build_id 和构建时间不会改变索引逻辑内容，因此被排除。
        source.path 和 source hash 也不直接进入身份；调用方必须在源内容变化时提升
        dataset_version，并由 Builder 的同版本源哈希冲突检查强制这一规则。
        """

        return {
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "chunker": self.chunker.model_dump(
                mode="json",
                exclude_none=True,
            ),
            "embedding": self.embedding.model_dump(
                mode="json",
                exclude_none=True,
            ),
            "index": self.index.model_dump(
                mode="json",
                exclude_none=True,
            ),
        }

    def index_identity_hash(self) -> str:
        """计算索引兼容性身份的 canonical SHA-256。"""

        return _sha256_json(self.index_identity_dict())

    def index_version(self) -> str:
        """构造含组件名称、版本和短哈希的可读索引版本。"""

        # 十二位身份哈希用于区分参数差异，同时保持目录名可读。
        short_hash = self.index_identity_hash()[:12]
        return "idx_%s_%s_%s_%s_%s" % (
            slug(self.chunker.name),
            slug(self.chunker.version),
            slug(self.embedding.name),
            slug(self.embedding.version),
            short_hash,
        )


class OfflineIndexConfigLoader:
    """按项目根目录规则加载 YAML/JSON 离线索引配置。"""

    @staticmethod
    def default_project_root() -> Path:
        """返回当前仓库根目录。

        本文件位于 ``backend/rag/offline/config.py``，向上三级得到项目根目录。
        """

        return Path(__file__).resolve().parents[3]

    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        """把用户路径解析为规范化绝对路径。

        绝对路径直接解析；相对路径基于显式 project_root，省略时基于默认仓库根目录。
        """

        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()

        root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else self.default_project_root()
        )
        return (root / candidate).resolve()

    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> OfflineIndexBuildConfig:
        """读取、解析并严格校验离线索引配置。

        Raises:
            FileNotFoundError: 配置文件不存在时抛出。
            ValueError: 扩展名不受支持或根节点不是对象时抛出。
            pydantic.ValidationError: 字段或交叉约束不满足时抛出。
        """

        # 第一步：解析实际配置文件并检查存在性。
        resolved = self.resolve_path(path, project_root=project_root)
        if not resolved.is_file():
            raise FileNotFoundError(
                f"offline index config not found: {resolved}"
            )

        # 第二步：按扩展名选择 YAML 或 JSON 解析器。
        text = resolved.read_text(encoding="utf-8")
        suffix = resolved.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        elif suffix == ".json":
            payload = json.loads(text)
        else:
            raise ValueError(
                "offline index config must be .yaml/.yml/.json"
            )

        # 第三步：根节点必须是对象，再交给严格配置模型校验。
        if not isinstance(payload, dict):
            raise ValueError(
                "offline index config root must be an object"
            )
        return OfflineIndexBuildConfig.model_validate(payload)
